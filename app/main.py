import io
import json
import logging
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from . import config, feeds, printer, render, services

app = Flask(__name__)
log = logging.getLogger("thermalnews")
logging.basicConfig(level=logging.INFO)

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
    try:
        items = build_items(reprint)
        if not items:
            say("nothing new to print")
            return
        img = render.render_receipt(items)
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
        state["running"] = False
        state["finished"] = time.time()


@app.get("/")
def index():
    return render_template("index.html", feeds=config.FEEDS, model=config.OLLAMA_MODEL,
                           printer=config.PRINTER_HOST or "(not set)")


@app.post("/print")
def start():
    with _lock:
        if state["running"]:
            return jsonify(error="already running"), 409
        state.update(running=True, log=[], finished=None, preview=None)
    body = request.get_json(silent=True) or {}
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
    try:
        from PIL import Image, ImageDraw
        img = render.render_receipt([{
            "source": "thermalnews", "title": "Test print OK",
            "summary": "If you can read this and scan the QR code, printing works.",
            "short_url": "https://example.com"}], header="TEST")
        printer.send(img)
        return jsonify(ok=True)
    except Exception as e:
        return jsonify(error=str(e)), 500
