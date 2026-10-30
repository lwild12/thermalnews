import logging
import select
import socket
import time

from PIL import Image

from . import config

log = logging.getLogger("thermalnews.printer")


def _raster_rows(img):
    img = img.convert("1")
    w, h = img.size
    w8 = (w + 7) // 8
    data = img.point(lambda p: 0 if p else 1, "1").tobytes()  # 1 = black dot
    return w8, h, data


def encode_starline(img):
    """Star Line Mode raster graphics: ESC * r A ... b nL nH data ... ESC * r B."""
    w8, h, data = _raster_rows(img)
    out = bytearray(b"\x1b@\x1b*rA\x1b*rP0\x00")
    for y in range(h):
        out += b"b" + bytes([w8 & 0xFF, w8 >> 8]) + data[y * w8:(y + 1) * w8]
    out += b"\x1b*rB"
    out += b"\x1bd\x03"  # feed and full cut
    return bytes(out)


def encode_escpos(img):
    """ESC/POS GS v 0 raster, chunked so the printer buffer isn't overrun."""
    w8, h, data = _raster_rows(img)
    out = bytearray(b"\x1b@")
    step = 128
    for y in range(0, h, step):
        n = min(step, h - y)
        out += b"\x1dv0\x00" + bytes([w8 & 0xFF, w8 >> 8, n & 0xFF, n >> 8])
        out += data[y * w8:(y + n) * w8]
    out += b"\n\n\n\x1dV\x42\x00"  # feed + partial cut
    return bytes(out)


def _drain(sock, wait=0.0):
    """Read anything the printer sends back. Returns False if it closed the connection."""
    while select.select([sock], [], [], wait)[0]:
        try:
            data = sock.recv(4096)
        except OSError as e:
            log.warning("printer: read error: %s", e)
            return False
        if not data:
            return False
        log.info("printer: sent back %d bytes: %s", len(data), data[:32].hex(" "))
        wait = 0.0
    return True


def send(imgs, protocol=None):
    """Send one image (or a list, each cut separately) over a single connection.

    Returns (bytes_sent, protocol used).

    Data is paced (chunk size and delay are settings) and the connection is held
    open afterwards, because some printer network boards lose data that arrives
    faster than the head prints, or abort the job when the connection closes early.
    """
    if not config.PRINTER_HOST:
        raise RuntimeError("PRINTER_HOST is not set")
    protocol = protocol or config.PRINTER_PROTOCOL
    imgs = imgs if isinstance(imgs, (list, tuple)) else [imgs]
    encode = encode_escpos if protocol == "escpos" else encode_starline
    payload = b"".join(encode(i) for i in imgs)
    chunk, delay = config.PRINTER_CHUNK_BYTES, config.PRINTER_CHUNK_DELAY_MS / 1000
    log.info("printer: encoded %d receipt(s) %s as %s, %d bytes; connecting to %s:%s "
             "(chunk %dB, delay %dms, hold %ds)", len(imgs), "+".join(f"{i.size[0]}x{i.size[1]}" for i in imgs),
             protocol, len(payload),
             config.PRINTER_HOST, config.PRINTER_PORT, chunk, config.PRINTER_CHUNK_DELAY_MS,
             config.PRINTER_HOLD_SECONDS)
    t0 = time.time()
    with socket.create_connection((config.PRINTER_HOST, config.PRINTER_PORT), timeout=10) as s:
        log.info("printer: connected in %.2fs, sending", time.time() - t0)
        sent, open_ = 0, True
        for i in range(0, len(payload), chunk):
            s.sendall(payload[i:i + chunk])
            sent = min(i + chunk, len(payload))
            if delay:
                time.sleep(delay)
            if not _drain(s):
                open_ = False
                log.error("printer: closed the connection after %d of %d bytes (%.1fs)",
                          sent, len(payload), time.time() - t0)
                raise RuntimeError(f"printer closed the connection after {sent} of {len(payload)} bytes")
        log.info("printer: all %d bytes sent in %.2fs, holding connection open %ds",
                 len(payload), time.time() - t0, config.PRINTER_HOLD_SECONDS)
        end = time.time() + config.PRINTER_HOLD_SECONDS
        while time.time() < end:
            if not _drain(s, min(0.25, max(0.0, end - time.time()))):
                log.info("printer: closed the connection %.1fs after the last byte", time.time() - (end - config.PRINTER_HOLD_SECONDS))
                break
    log.info("printer: finished in %.2fs", time.time() - t0)
    return len(payload), protocol
