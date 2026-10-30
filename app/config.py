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
# "per_feed": one printout (with its own cut) per feed; "combined": everything in one printout
PRINT_MODE = os.environ.get("PRINT_MODE", "per_feed").lower()
MAX_STORIES = int(os.environ.get("MAX_STORIES", "0"))  # total cap across all feeds, 0 = no cap

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2")
OLLAMA_TIMEOUT = int(os.environ.get("OLLAMA_TIMEOUT", "180"))

SINK_URL = os.environ.get("SINK_URL", "").rstrip("/")
SINK_TOKEN = os.environ.get("SINK_TOKEN", "")
SINK_EXPIRY_DAYS = int(os.environ.get("SINK_EXPIRY_DAYS", "7"))  # 0 = links never expire

PRINTER_HOST = os.environ.get("PRINTER_HOST", "")
PRINTER_PORT = int(os.environ.get("PRINTER_PORT", "9100"))
# "starline" (TSP100 default mode) or "escpos"
PRINTER_PROTOCOL = os.environ.get("PRINTER_PROTOCOL", "starline").lower()
PRINTER_WIDTH_DOTS = int(os.environ.get("PRINTER_WIDTH_DOTS", "576"))  # 80mm @ 203dpi
# Pacing: some printers drop data if it arrives faster than the head prints it.
PRINTER_CHUNK_BYTES = int(os.environ.get("PRINTER_CHUNK_BYTES", "512"))
PRINTER_CHUNK_DELAY_MS = int(os.environ.get("PRINTER_CHUNK_DELAY_MS", "15"))
PRINTER_HOLD_SECONDS = int(os.environ.get("PRINTER_HOLD_SECONDS", "5"))  # keep connection open after sending

DATA_DIR = os.environ.get("DATA_DIR", "/data")
FONT_DIR = os.environ.get("FONT_DIR", "/usr/share/fonts/truetype/dejavu")

LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO").upper()  # DEBUG for full detail


# --- settings editable from the web UI -------------------------------------
# Saved to DATA_DIR/settings.json and applied over the environment values.
# Modules read config.X at call time, so apply() takes effect immediately.
import json  # noqa: E402
import logging  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402
from urllib.parse import urlparse  # noqa: E402

_log = logging.getLogger("thermalnews.config")

EDITABLE = (
    "OLLAMA_URL", "OLLAMA_MODEL",
    "SINK_URL", "SINK_TOKEN", "SINK_EXPIRY_DAYS",
    "FEEDS", "ITEMS_PER_FEED", "MAX_AGE_HOURS", "MAX_STORIES", "PRINT_MODE",
    "PRINTER_HOST", "PRINTER_PORT", "PRINTER_PROTOCOL", "PRINTER_WIDTH_DOTS",
    "PRINTER_CHUNK_BYTES", "PRINTER_CHUNK_DELAY_MS", "PRINTER_HOLD_SECONDS",
)
INT_RANGES = {
    "PRINTER_PORT": (1, 65535), "PRINTER_WIDTH_DOTS": (64, 2048),
    "PRINTER_CHUNK_BYTES": (16, 65536), "PRINTER_CHUNK_DELAY_MS": (0, 1000),
    "PRINTER_HOLD_SECONDS": (0, 120),
    "SINK_EXPIRY_DAYS": (0, 3650), "ITEMS_PER_FEED": (1, 20),
    "MAX_AGE_HOURS": (1, 720), "MAX_STORIES": (0, 100),
}
MAX_FEEDS = 30
ENV_DEFAULTS = {k: (list(globals()[k]) if k == "FEEDS" else globals()[k]) for k in EDITABLE}
OVERRIDDEN = []  # keys currently coming from settings.json


def settings_path():
    return Path(DATA_DIR) / "settings.json"


def validate(raw):
    """Clean and validate a dict of editable settings; raises ValueError."""
    out = {}
    for key, val in raw.items():
        if key not in EDITABLE:
            raise ValueError(f"unknown setting {key}")
        if key in INT_RANGES:
            try:
                val = int(val)
            except (TypeError, ValueError):
                raise ValueError(f"{key} must be a number")
            lo, hi = INT_RANGES[key]
            if not lo <= val <= hi:
                raise ValueError(f"{key} must be between {lo} and {hi}")
        elif key == "FEEDS":
            items = val if isinstance(val, list) else str(val).replace(",", "\n").splitlines()
            val = []
            for url in (i.strip() for i in items):
                if not url or url in val:
                    continue
                u = urlparse(url)
                if u.scheme not in ("http", "https") or not u.netloc:
                    raise ValueError(f"not a feed URL (needs http:// or https://): {url}")
                val.append(url)
            if not val:
                raise ValueError("add at least one feed URL")
            if len(val) > MAX_FEEDS:
                raise ValueError(f"at most {MAX_FEEDS} feeds")
        else:
            val = str(val).strip()
        if key in ("OLLAMA_URL", "SINK_URL"):
            val = val.rstrip("/")
            if val or key == "OLLAMA_URL":
                u = urlparse(val)
                if u.scheme not in ("http", "https") or not u.netloc:
                    raise ValueError(f"{key} must be a full http(s) URL, e.g. http://192.168.1.10:11434")
        if key == "OLLAMA_MODEL" and not val:
            raise ValueError("OLLAMA_MODEL is required")
        if key == "PRINT_MODE":
            val = val.lower()
            if val not in ("per_feed", "combined"):
                raise ValueError("PRINT_MODE must be per_feed or combined")
        if key == "PRINTER_PROTOCOL":
            val = val.lower()
            if val not in ("starline", "escpos"):
                raise ValueError("PRINTER_PROTOCOL must be starline or escpos")
        if key == "PRINTER_HOST" and any(c in val for c in " /"):
            raise ValueError("PRINTER_HOST must be a hostname or IP, not a URL")
        out[key] = val
    return out


def apply(values):
    globals().update(values)


def load_overrides():
    """Apply DATA_DIR/settings.json on top of the environment. Never raises."""
    path = settings_path()
    try:
        saved = validate(json.loads(path.read_text()))
    except FileNotFoundError:
        return
    except (OSError, ValueError) as e:
        _log.error("ignoring unreadable settings file %s: %s", path, e)
        return
    apply(saved)
    OVERRIDDEN[:] = sorted(saved)


def save_overrides(updates):
    """Merge validated updates into settings.json (0600, atomic) and apply them."""
    path = settings_path()
    try:
        current = json.loads(path.read_text())
    except (OSError, ValueError):
        current = {}
    merged = {**current, **updates}
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".settings-")
    with os.fdopen(fd, "w") as f:
        json.dump(merged, f, indent=2)
    os.chmod(tmp, 0o600)  # holds the Sink token
    os.replace(tmp, path)
    apply(updates)
    OVERRIDDEN[:] = sorted(merged)


def reset_overrides():
    """Delete settings.json and go back to the environment values."""
    try:
        settings_path().unlink()
    except FileNotFoundError:
        pass
    apply(ENV_DEFAULTS)
    OVERRIDDEN.clear()


load_overrides()
