"""Check the printer accepts raw TCP 9100 before running the full app.

    python test_printer.py 192.168.1.50 [starline|escpos]
"""
import os
import sys

os.environ["PRINTER_HOST"] = sys.argv[1]
os.environ["PRINTER_PROTOCOL"] = sys.argv[2] if len(sys.argv) > 2 else "starline"

from app import printer, render  # noqa: E402

printer.send(render.render_receipt([{
    "source": "test", "title": "Printer test",
    "summary": "If this prints, raw port 9100 works in this mode.",
    "short_url": "https://example.com"}], header="TEST"))
print("sent")
