import io
import json
import logging
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from . import config, feeds, printer, render, services

app = Flask(__name__)
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("thermalnews")
log.info(
    "starting: printer=%s:%s protocol=%s width=%s | ollama=%s model=%s | sink=%s token=%s | "
    "%d feeds, %d/feed, max age %dh | data=%s log_level=%s",
    config.PRINTER_HOST or "(unset)", config.PRINTER_PORT, config.PRINTER_PROTOCOL,
    config.PRINTER_WIDTH_DOTS, config.OLLAMA_URL, config.OLLAMA_MODEL,
    config.SINK_URL or "(unset)", "set" if config.SINK_TOKEN else "NOT SET",
    len(config.FEEDS), config.ITEMS_PER_FEED, config.MAX_AGE_HOURS, config.DATA_DIR, config.LOG_LEVEL,
)
for f in config.FEEDS:
    log.info("feed configured: %s", f)

_lock = threading.Lock()
state = {"running": False, "log": [], "finished": None}
_seen_file = Path(config.DATA_DIR) / "printed.json"


def _seen():
    try:
        return set(json.loads(_seen_file.read_text()))
    except Exception:
        return set()


def _remember(ids):
    try:
        _seen_file.parent.mkdir(parents=True, exist_ok=True)
        _seen_file.write_text(json.dumps(sorted(_seen() | set(ids))[-2000:]))
    except OSError as e:
        log.warning("could not save printed list: %s", e)


def say(msg):
    log.info(msg)
    state["log"].append(f"{time.strftime('%H:%M:%S')}  {msg}")


def build_items(reprint=False):
    say(f"fetching {len(config.FEEDS)} feeds")
    stories, errors = feeds.fetch_stories()
    for e in errors:
        say(f"feed error: {e}")
    if not reprint:
        seen = _seen()
        stories = [s for s in stories if s["id"] not in seen]
    say(f"{len(stories)} stories to process")
    items = []
    for s in stories:
        try:
            summary = services.summarise(s)
        except Exception as e:
            say(f"ollama failed for '{s['title'][:40]}': {e}")
            summary = s["text"][:280] or "(no summary available)"
        try:
            short = services.shorten(s["link"])
        except Exception as e:
            say(f"sink failed for '{s['title'][:40]}': {e} - using full URL")
            short = s["link"]
        items.append({**s, "summary": summary, "short_url": short})
        say(f"ready: {s['title'][:60]}")
    return items


def run_job(reprint, dry_run):
    t0 = time.time()
    log.info("job started (reprint=%s dry_run=%s)", reprint, dry_run)
    try:
        items = build_items(reprint)
        if not items:
            say("nothing new to print")
            return
        t1 = time.time()
        img = render.render_receipt(items)
        log.info("rendered receipt %dx%d for %d stories in %.1fs", img.size[0], img.size[1], len(items), time.time() - t1)
        if dry_run:
            img.save(Path(config.DATA_DIR) / "last.png") if Path(config.DATA_DIR).exists() else None
            state["preview"] = img
            say("dry run complete - see preview")
            return
        printer.send(img)
        _remember(i["id"] for i in items)
        say(f"printed {len(items)} stories")
    except Exception as e:
        log.exception("job failed")
        say(f"ERROR: {e}")
    finally:
        log.info("job finished in %.1fs", time.time() - t0)
        state["running"] = False
        state["finished"] = time.time()


@app.get("/")
def index():
    return render_template("index.html", feeds=config.FEEDS, model=config.OLLAMA_MODEL,
                           printer=config.PRINTER_HOST or "(not set)",
                           protocol=config.PRINTER_PROTOCOL)


@app.post("/print")
def start():
    with _lock:
        if state["running"]:
            return jsonify(error="already running"), 409
        state.update(running=True, log=[], finished=None, preview=None)
    body = request.get_json(silent=True) or {}
    log.info("print requested from %s", request.remote_addr)
    threading.Thread(target=run_job, args=(bool(body.get("reprint")), bool(body.get("dry_run"))),
                     daemon=True).start()
    return jsonify(ok=True)


@app.get("/status")
def status():
    return jsonify(running=state["running"], log=state["log"],
                   has_preview=state.get("preview") is not None)


@app.get("/preview.png")
def preview():
    img = state.get("preview")
    if img is None:
        return "no preview", 404
    buf = io.BytesIO(); img.save(buf, "PNG"); buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.post("/test")
def test():
    protocol = (request.get_json(silent=True) or {}).get("protocol") or config.PRINTER_PROTOCOL
    if protocol not in ("starline", "escpos"):
        log.warning("test print: rejected unknown protocol %r", protocol)
        return jsonify(error=f"unknown protocol {protocol!r}"), 400
    target = f"{config.PRINTER_HOST or '(PRINTER_HOST not set)'}:{config.PRINTER_PORT}"
    log.info("test print requested from %s: target=%s protocol=%s", request.remote_addr, target, protocol)
    try:
        img = render.render_receipt([{
            "source": "thermalnews", "title": "Test print OK",
            "summary": "If you can read this and scan the QR code, printing works.",
            "short_url": "https://example.com"}], header="TEST")
        size, protocol = printer.send(img, protocol)
        log.info("test print: %d bytes to %s as %s", size, target, protocol)
        return jsonify(ok=True, message=f"Sent {size} bytes to {target} as {protocol}. "
                       "If nothing printed, the printer accepted the connection but did not "
                       "understand it: try the other protocol.")
    except Exception as e:
        log.exception("test print failed")
        return jsonify(error=f"{target}: {e}"), 500
