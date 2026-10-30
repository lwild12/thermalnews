import textwrap
import time
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

from . import config


def _font(name, size):
    for d in (config.FONT_DIR, "/usr/share/fonts/dejavu", "/usr/share/fonts/TTF"):
        p = Path(d) / name
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def _wrap(draw, text, font, width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width:
            line = trial
        else:
            if line:
                lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _qr_image(url):
    """A QR code drawn at a whole number of printer dots per module (config.QR_MODULE_DOTS).

    Lowest error correction and a 2-module quiet zone keep it as small as the link allows;
    whole-dot modules print crisply, where scaling by a fraction makes uneven squares.
    """
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L, border=2, box_size=1)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()  # includes the quiet zone
    n, m = len(matrix), config.QR_MODULE_DOTS
    img = Image.new("L", (n * m, n * m), 255)
    d = ImageDraw.Draw(img)
    for r, row in enumerate(matrix):
        for c, black in enumerate(row):
            if black:
                d.rectangle([c * m, r * m, (c + 1) * m - 1, (r + 1) * m - 1], fill=0)
    return img


def render_receipt(items, header="NEWS DIGEST", show_source=True):
    """items: list of dicts with title, source, summary, short_url. Returns 1-bit image."""
    W, pad = config.PRINTER_WIDTH_DOTS, 12
    inner = W - 2 * pad
    h1, h2, body, small = (
        _font("DejaVuSans-Bold.ttf", 34), _font("DejaVuSans-Bold.ttf", 26),
        _font("DejaVuSans.ttf", 24), _font("DejaVuSans.ttf", 20),
    )
    scratch = ImageDraw.Draw(Image.new("L", (W, 10)))

    blocks = [("h1", header), ("small", time.strftime("%a %d %b %Y  %H:%M")), ("rule", "")]
    for it in items:
        if show_source:
            blocks.append(("small", it["source"].upper()))
        blocks += [("h2", it["title"]), ("body", it["summary"]), ("qr", it["short_url"]), ("rule", "")]

    fonts = {"h1": h1, "h2": h2, "body": body, "small": small}
    laid, y = [], pad
    for kind, text in blocks:
        if kind == "rule":
            laid.append((kind, text, y)); y += 24
        elif kind == "qr":
            qr_img = _qr_image(text)
            laid.append((kind, qr_img, y)); y += qr_img.size[1] + 4
        else:
            f = fonts[kind]
            lines = _wrap(scratch, text, f, inner) if kind != "small" else [
                text if scratch.textlength(text, font=f) <= inner else text[:60]]
            laid.append((kind, lines, y))
            y += len(lines) * (f.size + 6) + 6
    img = Image.new("L", (W, y + 40), 255)
    d = ImageDraw.Draw(img)
    for kind, content, yy in laid:
        if kind == "rule":
            d.line([(pad, yy + 8), (W - pad, yy + 8)], fill=0, width=2)
        elif kind == "qr":
            img.paste(content, ((W - content.size[0]) // 2, yy))
        else:
            f = fonts[kind]
            for i, line in enumerate(content):
                x = (W - d.textlength(line, font=f)) / 2 if kind == "h1" else pad
                d.text((x, yy + i * (f.size + 6)), line, font=f, fill=0)
    return img.convert("1")
