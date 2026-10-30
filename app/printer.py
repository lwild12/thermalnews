import fcntl
import logging
import select
import socket
import struct
import termios
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


def _unsent(sock):
    """Bytes still queued in our kernel for this socket (not yet acknowledged by the printer)."""
    try:
        return struct.unpack("i", fcntl.ioctl(sock, termios.TIOCOUTQ, b"\0\0\0\0"))[0]
    except OSError:
        return 0  # not Linux: fall back to the fixed hold time only


def send(imgs, protocol=None):
    """Send one image (or a list, each cut separately) over a single connection.

    Returns (bytes_sent, protocol used).

    - A small send buffer means "sent" tracks what the printer has actually taken, so progress
      and the stall timeout are meaningful.
    - A printer that is printing or cutting stops reading for a while (TCP backpressure), which is
      normal; we only fail if it takes nothing for PRINTER_STALL_SECONDS.
    - We never close the connection until every byte has been acknowledged, then hold it open
      PRINTER_HOLD_SECONDS, because some printer network boards drop the rest of a job when the
      connection closes early.
    - Data is also paced (chunk size and delay settings).
    """
    if not config.PRINTER_HOST:
        raise RuntimeError("PRINTER_HOST is not set")
    protocol = protocol or config.PRINTER_PROTOCOL
    imgs = imgs if isinstance(imgs, (list, tuple)) else [imgs]
    encode = encode_escpos if protocol == "escpos" else encode_starline
    payload = b"".join(encode(i) for i in imgs)
    total = len(payload)
    chunk, delay = config.PRINTER_CHUNK_BYTES, config.PRINTER_CHUNK_DELAY_MS / 1000
    stall = config.PRINTER_STALL_SECONDS
    log.info("printer: encoded %d receipt(s) %s as %s, %d bytes; connecting to %s:%s "
             "(chunk %dB, delay %dms, hold %ds, stall timeout %ds)", len(imgs),
             "+".join(f"{i.size[0]}x{i.size[1]}" for i in imgs), protocol, total,
             config.PRINTER_HOST, config.PRINTER_PORT, chunk, config.PRINTER_CHUNK_DELAY_MS,
             config.PRINTER_HOLD_SECONDS, stall)
    t0 = time.time()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 32768)
    s.settimeout(10)
    try:
        s.connect((config.PRINTER_HOST, config.PRINTER_PORT))
        s.settimeout(stall)
        log.info("printer: connected in %.2fs, sending", time.time() - t0)
        sent, last_log = 0, time.time()
        for i in range(0, total, chunk):
            try:
                s.sendall(payload[i:i + chunk])
            except (socket.timeout, TimeoutError):
                log.error("printer: took no data for %ds after %d of %d bytes", stall, sent, total)
                raise RuntimeError(f"the printer stopped accepting data for {stall}s after {sent} of {total} "
                                   "bytes. Check it is online with paper loaded and the cover closed")
            sent = min(i + chunk, total)
            if delay:
                time.sleep(delay)
            if not _drain(s):
                log.error("printer: closed the connection after %d of %d bytes (%.1fs)", sent, total, time.time() - t0)
                raise RuntimeError(f"printer closed the connection after {sent} of {total} bytes")
            if time.time() - last_log >= 5:
                log.info("printer: %d of %d bytes (%d%%) after %.0fs", sent, total, 100 * sent // total, time.time() - t0)
                last_log = time.time()
        log.info("printer: all %d bytes handed over in %.1fs; waiting for the printer to take the rest",
                 total, time.time() - t0)
        end = time.time() + stall
        while _unsent(s) > 0:
            if time.time() > end:
                raise RuntimeError(f"the printer stopped accepting data for {stall}s with {_unsent(s)} "
                                   "bytes still unsent. Check it is online with paper loaded and the cover closed")
            if not _drain(s, 0.2):
                raise RuntimeError("printer closed the connection before the job was fully sent")
        log.info("printer: every byte acknowledged after %.1fs, holding the connection open %ds",
                 time.time() - t0, config.PRINTER_HOLD_SECONDS)
        hold_end = time.time() + config.PRINTER_HOLD_SECONDS
        while time.time() < hold_end:
            if not _drain(s, min(0.25, max(0.0, hold_end - time.time()))):
                log.info("printer: closed the connection %.1fs after the last byte",
                         config.PRINTER_HOLD_SECONDS - (hold_end - time.time()))
                break
    finally:
        s.close()
    log.info("printer: finished in %.1fs", time.time() - t0)
    return total, protocol
