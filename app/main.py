import io
import json
import logging
import os
import threading
import time
from pathlib import Path

from flask import Flask, jsonify, render_template, request, send_file

from . import config, feeds, printer, render, scheduler, services

app = Flask(__name__)
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("thermalnews")


class _QuietStatus(logging.Filter):
    """Drop successful /status polls (one per second while a job runs) from gunicorn's access log."""

    def filter(self, record):
        msg = record.getMessage()
        return not ('"GET /status ' in msg and '" 200 ' in msg)


logging.getLogger("gunicorn.access").addFilter(_QuietStatus())
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
if config.OVERRIDDEN:
    log.info("settings from %s override the environment for: %s",
             config.settings_path(), ", ".join(config.OVERRIDDEN))

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
    stories, errors, skipped = feeds.fetch_stories(set() if reprint else _seen())
    for e in errors:
        say(f"feed error: {e}")
    if skipped:
        say(f"skipped {skipped} stories already printed (tick 'include stories already printed' to reprint)")
    say(f"{len(stories)} new stories to process")
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


def group_receipts(items):
    """[(header, items, show_source)] - one per feed (in configured order) or one combined."""
    if config.PRINT_MODE == "combined":
        return [("NEWS DIGEST", items, True)]
    order = {u: n for n, u in enumerate(config.FEEDS)}
    groups = {}
    for it in items:
        groups.setdefault(it["feed"], []).append(it)
    return [(g[0]["source"].upper(), g, False)
            for _, g in sorted(groups.items(), key=lambda kv: order.get(kv[0], len(order)))]


def stack(imgs, gap=40):
    """One tall image for the browser preview, with a gap where the printer would cut."""
    from PIL import Image
    out = Image.new("1", (max(i.size[0] for i in imgs), sum(i.size[1] for i in imgs) + gap * (len(imgs) - 1)), 1)
    y = 0
    for i in imgs:
        out.paste(i, (0, y)); y += i.size[1] + gap
    return out


def run_job(reprint, dry_run, source="button"):
    t0 = time.time()
    log.info("job started by %s (reprint=%s dry_run=%s, layout=%s)", source, reprint, dry_run, config.PRINT_MODE)
    if source == "schedule":
        say("scheduled run")
    outcome = {"ok": True, "text": "nothing new to print"}
    try:
        items = build_items(reprint)
        if not items:
            say("nothing new to print")
            return
        t1 = time.time()
        groups = group_receipts(items)
        imgs = [render.render_receipt(g, header=h, show_source=ss) for h, g, ss in groups]
        say("receipts: " + "; ".join(f"{h.title()} ({len(g)} stories)" for h, g, _ in groups))
        log.info("rendered %d receipt(s) for %d stories in %.1fs", len(imgs), len(items), time.time() - t1)
        if dry_run:
            preview = stack(imgs)
            state["preview"] = preview
            if Path(config.DATA_DIR).exists():
                preview.save(Path(config.DATA_DIR) / "last.png")
            say("dry run complete - see preview")
            return
        printer.send(imgs)
        _remember(i["id"] for i in items)  # only after the whole send succeeded
        say(f"printed {len(items)} stories as {len(imgs)} printout(s)")
        outcome["text"] = f"printed {len(items)} stories"
    except Exception as e:
        log.exception("job failed")
        say(f"ERROR: {e}")
        outcome = {"ok": False, "text": f"ERROR: {e}"}
    finally:
        log.info("job finished in %.1fs", time.time() - t0)
        if not dry_run:
            state["last_run"] = {**outcome, "at": time.time(), "source": source}
        state["running"] = False
        state["finished"] = time.time()


@app.get("/")
def index():
    return render_template("index.html")


def start_job(reprint=False, dry_run=False, source="button"):
    """Start a print job in the background. Returns False if one is already running."""
    with _lock:
        if state["running"]:
            return False
        state.update(running=True, log=[], finished=None, preview=None)
    threading.Thread(target=run_job, args=(reprint, dry_run, source), daemon=True).start()
    return True


@app.post("/print")
def start():
    body = request.get_json(silent=True) or {}
    log.info("print requested from %s", request.remote_addr)
    if not start_job(bool(body.get("reprint")), bool(body.get("dry_run"))):
        return jsonify(error="already running"), 409
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


# --- settings API ------------------------------------------------------------
FORM_KEYS = {
    "ollama_url": "OLLAMA_URL", "ollama_model": "OLLAMA_MODEL",
    "sink_url": "SINK_URL", "sink_token": "SINK_TOKEN",
    "printer_host": "PRINTER_HOST", "printer_port": "PRINTER_PORT",
    "printer_protocol": "PRINTER_PROTOCOL", "printer_width": "PRINTER_WIDTH_DOTS",
    "printer_chunk": "PRINTER_CHUNK_BYTES", "printer_delay": "PRINTER_CHUNK_DELAY_MS",
    "printer_hold": "PRINTER_HOLD_SECONDS", "sink_expiry": "SINK_EXPIRY_DAYS",
    "feeds": "FEEDS", "items_per_feed": "ITEMS_PER_FEED", "max_age": "MAX_AGE_HOURS",
    "max_stories": "MAX_STORIES", "print_mode": "PRINT_MODE",
    "schedule_enabled": "SCHEDULE_ENABLED", "schedule_times": "SCHEDULE_TIMES",
    "schedule_days": "SCHEDULE_DAYS", "schedule_timezone": "SCHEDULE_TIMEZONE",
}


def current_settings():
    out = {form: getattr(config, key) for form, key in FORM_KEYS.items() if form != "sink_token"}
    out["sink_token_set"] = bool(config.SINK_TOKEN)  # the token itself never goes to the browser
    out["overridden"] = list(config.OVERRIDDEN)
    out["feeds"] = "\n".join(config.FEEDS)  # textarea: one URL per line
    out["feeds_count"] = len(config.FEEDS)
    out["schedule_times"] = ", ".join(config.SCHEDULE_TIMES)
    out["schedule"] = scheduler.describe()
    last = state.get("last_run")
    out["last_run"] = (f"{time.strftime('%a %d %b %H:%M', time.localtime(last['at']))} "
                       f"({last['source']}): {last['text']}") if last else None
    return out


@app.get("/api/settings")
def get_settings():
    return jsonify(current_settings())


@app.post("/api/settings")
def save_settings():
    body = request.get_json(silent=True) or {}
    raw = {FORM_KEYS[k]: v for k, v in body.items() if k in FORM_KEYS}
    # token: blank = keep what is saved; clear_sink_token removes it
    if not raw.get("SINK_TOKEN"):
        raw.pop("SINK_TOKEN", None)
    if body.get("clear_sink_token"):
        raw["SINK_TOKEN"] = ""
    try:
        clean = config.validate(raw)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    if state["running"]:
        return jsonify(error="a print job is running; try again when it finishes"), 409
    eff = {k: clean.get(k, getattr(config, k)) for k in ("SCHEDULE_ENABLED", "SCHEDULE_TIMES", "SCHEDULE_DAYS")}
    if eff["SCHEDULE_ENABLED"] and not eff["SCHEDULE_TIMES"]:
        return jsonify(error="add at least one time to turn the schedule on"), 400
    if eff["SCHEDULE_ENABLED"] and not eff["SCHEDULE_DAYS"]:
        return jsonify(error="pick at least one day to turn the schedule on"), 400
    schedule_changed = any(k.startswith("SCHEDULE_") and clean[k] != getattr(config, k) for k in clean)
    try:
        config.save_overrides(clean)
    except OSError as e:
        log.exception("could not save settings")
        return jsonify(error=f"could not write {config.settings_path()}: {e}"), 500
    if schedule_changed:
        scheduler.skip_past_slots()  # editing the schedule must not fire a time that already passed today
    log.info("settings saved from %s: %s", request.remote_addr,
             ", ".join(f"{k}={'***' if k == 'SINK_TOKEN' else v}" for k, v in clean.items()))
    return jsonify(current_settings())


@app.post("/api/settings/reset")
def reset_settings():
    if state["running"]:
        return jsonify(error="a print job is running; try again when it finishes"), 409
    config.reset_overrides()
    log.info("settings reset to environment values by %s", request.remote_addr)
    return jsonify(current_settings())


@app.post("/api/test/ollama")
def test_ollama():
    """Check Ollama is reachable and the model is pulled (GET /api/tags)."""
    import requests
    t0 = time.time()
    try:
        r = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=10)
        r.raise_for_status()
        names = [m.get("name", "") for m in r.json().get("models", [])]
    except Exception as e:
        log.error("ollama test failed: %s", e)
        return jsonify(error=f"{config.OLLAMA_URL}: {e}"), 502
    want = config.OLLAMA_MODEL
    found = want in names or f"{want}:latest" in names
    log.info("ollama test: %s reachable in %.1fs, %d models, %r %s",
             config.OLLAMA_URL, time.time() - t0, len(names), want, "found" if found else "MISSING")
    if not found:
        return jsonify(error=f"Ollama is reachable but model '{want}' is not pulled. "
                             f"Available: {', '.join(names) or 'none'}. Run: ollama pull {want}"), 404
    return jsonify(message=f"Ollama OK at {config.OLLAMA_URL}; model '{want}' is available.")


@app.post("/api/test/sink")
def test_sink():
    """Create one real short link, so it proves the URL and token work."""
    if not (config.SINK_URL and config.SINK_TOKEN):
        return jsonify(error="Sink URL and token are both needed"), 400
    try:
        short = services.shorten("https://example.com/thermalnews-test")
    except Exception as e:
        return jsonify(error=f"{config.SINK_URL}: {e}"), 502
    exp = f"expires in {config.SINK_EXPIRY_DAYS} days" if config.SINK_EXPIRY_DAYS else "never expires"
    return jsonify(message=f"Sink OK: created {short} ({exp}).")


@app.post("/api/test/feeds")
def test_feeds():
    """Fetch every saved feed and report what is in it."""
    lines, bad = [], 0
    for url in config.FEEDS:
        try:
            lines.append(f"OK    {url}\n      {feeds.check(url)}")
        except Exception as e:
            bad += 1
            log.error("feed test: %s: %s", url, e)
            lines.append(f"FAIL  {url}\n      {e}")
    return jsonify(message="\n".join(lines)) if not bad else (jsonify(error="\n" + "\n".join(lines)), 502)


@app.post("/api/test/schedule")
def test_schedule():
    """Describe what the saved schedule will do (nothing is printed)."""
    d = scheduler.describe()
    if not d["enabled"]:
        return jsonify(message=f"Schedule is OFF. (It is {d['now']} in {d['timezone']}.)")
    nxt = "\n".join("  " + n for n in d["next"]) or "  (no upcoming runs: check the days)"
    return jsonify(message=f"Schedule is ON. It is {d['now']} in {d['timezone']}. Next runs:\n{nxt}")


if os.environ.get("SCHEDULER", "1") != "0":
    scheduler.start(lambda: start_job(source="schedule"))
