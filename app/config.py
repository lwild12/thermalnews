import os


def _list(name, default=""):
    raw = os.environ.get(name, default)
    return [x.strip() for x in raw.replace("\n", ",").split(",") if x.strip()]


FEEDS = _list(
    "FEEDS",
    "https://feeds.bbci.co.uk/news/rss.xml,https://hnrss.org/frontpage",
)
ITEMS_PER_FEED = int(os.environ.get("ITEMS_PER_FEED", "3"))
MAX_AGE_HOURS = int(os.environ.get("MAX_AGE_HOURS", "24"))

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "180"))

SINK_URL = os.environ.get("SINK_URL", "").rstrip("/")
SINK_TOKEN = os.environ.get("SINK_TOKEN", "")

PRINTER_HOST = os.environ.get("PRINTER_HOST", "")
PRINTER_PORT = int(os.environ.get("PRINTER_PORT", "9100"))
# "starline" (TSP100 default mode) or "escpos"
PRINTER_PROTOCOL = os.environ.get("PRINTER_PROTOCOL", "starline").lower()
PRINTER_WIDTH_DOTS = int(os.environ.get("PRINTER_WIDTH_DOTS", "576"))  # 80mm @ 203dpi

DATA_DIR = os.environ.get("DATA_DIR", "/data")
FONT_DIR = os.environ.get("FONT_DIR", "/usr/share/fonts/truetype/dejavu")
