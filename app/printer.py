import logging
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


def send(img, protocol=None):
    """Send img to the printer; returns (bytes_sent, protocol used)."""
    if not config.PRINTER_HOST:
        raise RuntimeError("PRINTER_HOST is not set")
    protocol = protocol or config.PRINTER_PROTOCOL
    payload = (encode_escpos if protocol == "escpos" else encode_starline)(img)
    log.info("printer: encoded %dx%d image as %s, %d bytes; connecting to %s:%s",
             img.size[0], img.size[1], protocol, len(payload), config.PRINTER_HOST, config.PRINTER_PORT)
    t0 = time.time()
    with socket.create_connection((config.PRINTER_HOST, config.PRINTER_PORT), timeout=10) as s:
        log.info("printer: connected in %.2fs, sending", time.time() - t0)
        s.sendall(payload)
        s.shutdown(socket.SHUT_WR)  # tell the printer there is no more data
        s.settimeout(2)
        try:
            s.recv(1)  # give it a moment to drain before we close
        except OSError:
            pass
    log.info("printer: sent %d bytes in %.2fs", len(payload), time.time() - t0)
    return len(payload), protocol
