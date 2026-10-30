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


def render_receipt(items, header="NEWS DIGEST", show_source=True):
    """items: list of dicts with title, source, summary, short_url. Returns 1-bit image."""
    W, pad = config.PRINTER_WIDTH_DOTS, 12
    inner = W - 2 * pad
    h1, h2, body, small = (
        _font("DejaVuSans-Bold.ttf", 34), _font("DejaVuSans-Bold.ttf", 26),
        _font("DejaVuSans.ttf", 24), _font("DejaVuSans.ttf", 20),
    )
    scratch = ImageDraw.Draw(Image.new("L", (W, 10)))
    qr_size = 220

    blocks = [("h1", header), ("small", time.strftime("%a %d %b %Y  %H:%M")), ("rule", "")]
    for it in items:
        if show_source:
            blocks.append(("small", it["source"].upper()))
        blocks += [("h2", it["title"]), ("body", it["summary"]), ("qr", it["short_url"]),
                   ("small", it["short_url"]), ("rule", "")]

    fonts = {"h1": h1, "h2": h2, "body": body, "small": small}
    laid, y = [], pad
    for kind, text in blocks:
        if kind == "rule":
            laid.append((kind, text, y)); y += 24
        elif kind == "qr":
            laid.append((kind, text, y)); y += qr_size + 8
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
            qr = qrcode.QRCode(border=1, box_size=8)
            qr.add_data(content); qr.make(fit=True)
            q = qr.make_image(fill_color="black", back_color="white").convert("L")
            q = q.resize((qr_size, qr_size), Image.NEAREST)
            img.paste(q, ((W - qr_size) // 2, yy))
        else:
            f = fonts[kind]
            for i, line in enumerate(content):
                x = (W - d.textlength(line, font=f)) / 2 if kind == "h1" else pad
                d.text((x, yy + i * (f.size + 6)), line, font=f, fill=0)
    return img.convert("1")
