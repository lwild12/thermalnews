"""Scheduled printing: a background thread that starts a normal print job at set times.

Times are in config.SCHEDULE_TIMEZONE. Each (date, time) slot fires at most once, even across
restarts (fired slots are kept in DATA_DIR/schedule_state.json), and a slot that was missed by
more than SCHEDULE_GRACE_MINUTES (box off, or a job was busy the whole time) is skipped rather
than printing hours late.
"""
import fcntl
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import config

log = logging.getLogger("thermalnews.scheduler")

_lock = threading.Lock()
_fired = {}      # slot (ISO string) -> "ran" | "missed" | "skipped"
_last = None     # {"slot": ..., "at": ...} of the last scheduled run that was started
_lock_file = None
_clock = lambda tz: datetime.now(tz)  # noqa: E731  (tests replace this)


def _state_path():
    return Path(config.DATA_DIR) / "schedule_state.json"


def _load():
    global _last
    try:
        saved = json.loads(_state_path().read_text())
        _fired.update({k: "ran" for k in saved.get("fired", [])})
        _last = saved.get("last")
    except (OSError, ValueError):
        pass


def _save():
    keys = sorted(_fired)[-60:]
    for k in list(_fired):
        if k not in keys:
            del _fired[k]
    try:
        _state_path().parent.mkdir(parents=True, exist_ok=True)
        _state_path().write_text(json.dumps({"fired": keys, "last": _last}))
    except OSError as e:
        log.warning("could not save schedule state: %s", e)


def _slots(day, tz):
    """The configured times on a date, as aware datetimes."""
    out = []
    for t in config.SCHEDULE_TIMES:
        h, m = (int(x) for x in t.split(":"))
        out.append(datetime.combine(day, dtime(h, m), tzinfo=tz))
    return sorted(out)


def tick(start_job, now=None):
    """Check whether any slot is due and start the job. Returns the slot started, or None."""
    if not config.SCHEDULE_ENABLED:
        return None
    tz = ZoneInfo(config.SCHEDULE_TIMEZONE)
    now = (now or _clock(tz)).astimezone(tz)
    if now.weekday() not in config.SCHEDULE_DAYS:
        return None
    for slot in _slots(now.date(), tz):
        key = slot.isoformat()
        with _lock:
            if key in _fired or now < slot:
                continue
            late = (now - slot).total_seconds()
            if late > config.SCHEDULE_GRACE_MINUTES * 60:
                _fired[key] = "missed"
                log.warning("schedule: %s was missed (%.0f min late, limit %d); skipping",
                            slot.strftime("%a %H:%M"), late / 60, config.SCHEDULE_GRACE_MINUTES)
                _save()
                continue
        if start_job():
            with _lock:
                global _last
                _fired[key] = "ran"
                _last = {"slot": key, "at": time.time()}
                _save()
            log.info("schedule: started the %s run (%.0fs after its time)", slot.strftime("%a %H:%M"), late)
            return slot
        log.info("schedule: %s is due but a job is already running; will retry until %d min late",
                 slot.strftime("%H:%M"), config.SCHEDULE_GRACE_MINUTES)
    return None


def skip_past_slots(now=None):
    """After the schedule is edited, don't fire slots that already passed today."""
    tz = ZoneInfo(config.SCHEDULE_TIMEZONE)
    now = now or _clock(tz)
    with _lock:
        for slot in _slots(now.date(), tz):
            if slot <= now:
                _fired.setdefault(slot.isoformat(), "skipped")
        _save()


def next_runs(n=3, now=None):
    """The next n scheduled run times (aware datetimes)."""
    if not (config.SCHEDULE_TIMES and config.SCHEDULE_DAYS):
        return []
    tz = ZoneInfo(config.SCHEDULE_TIMEZONE)
    now = now or _clock(tz)
    out = []
    for offset in range(0, 9):
        day = (now + timedelta(days=offset)).date()
        if day.weekday() in config.SCHEDULE_DAYS:
            out += [s for s in _slots(day, tz) if s > now]
    return out[:n]


def _fmt(dt, now):
    mins = int((dt - now).total_seconds() // 60)
    h, m = divmod(mins, 60)
    d, h = divmod(h, 24)
    away = (f"{d}d " if d else "") + (f"{h}h " if h else "") + f"{m}m"
    return f"{dt.strftime('%a %d %b %H:%M')} (in {away})"


def describe():
    """For the settings page."""
    tz = ZoneInfo(config.SCHEDULE_TIMEZONE)
    now = _clock(tz)
    nxt = [_fmt(d, now) for d in next_runs(3, now)]
    last = None
    if _last:
        last = datetime.fromtimestamp(_last["at"], tz).strftime("%a %d %b %H:%M")
    return {"enabled": config.SCHEDULE_ENABLED, "timezone": config.SCHEDULE_TIMEZONE,
            "now": now.strftime("%a %d %b %H:%M"), "next": nxt, "last_started": last}


def _loop(start_job):
    while True:
        try:
            tick(start_job)
        except Exception:
            log.exception("schedule: tick failed")
        time.sleep(config.SCHEDULE_TICK_SECONDS)


def start(start_job):
    """Start the scheduler thread, in one worker process only (file lock)."""
    global _lock_file
    try:
        Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
        _lock_file = open(Path(config.DATA_DIR) / "scheduler.lock", "w")
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.info("schedule: another worker is running the scheduler")
        return
    except OSError as e:
        log.warning("schedule: could not take the scheduler lock (%s); running anyway", e)
    _load()
    threading.Thread(target=_loop, args=(start_job,), daemon=True, name="scheduler").start()
    log.info("schedule: scheduler running (%s)", "enabled" if config.SCHEDULE_ENABLED else "disabled; enable it in Settings")
