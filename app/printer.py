import socket

from PIL import Image

from . import config


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


def send(img):
    if not config.PRINTER_HOST:
        raise RuntimeError("PRINTER_HOST is not set")
    payload = (encode_escpos if config.PRINTER_PROTOCOL == "escpos" else encode_starline)(img)
    with socket.create_connection((config.PRINTER_HOST, config.PRINTER_PORT), timeout=10) as s:
        s.sendall(payload)
