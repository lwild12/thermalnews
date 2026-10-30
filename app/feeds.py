import calendar
import logging
import re
import time
from html import unescape

import feedparser
import requests

from . import config

log = logging.getLogger("thermalnews.feeds")


def _clean(html):
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _get(url):
    """Download and parse one feed. Raises on network, HTTP or parse failure."""
    # feedparser has no timeout of its own, so fetch here
    r = requests.get(url, timeout=(5, 15), headers={"User-Agent": "thermalnews/1.0"})
    r.raise_for_status()
    parsed = feedparser.parse(r.content)
    if parsed.bozo and not parsed.entries:
        raise ValueError(f"not a valid feed: {parsed.get('bozo_exception')}")
    return parsed, len(r.content)


def _ts(entry):
    ts = entry.get("published_parsed") or entry.get("updated_parsed")
    return calendar.timegm(ts) if ts else time.time()


def check(url):
    """For the settings page: returns a one-line description of a feed, raises if it is unusable."""
    parsed, size = _get(url)
    ages = [time.time() - _ts(e) for e in parsed.entries]
    recent = sum(1 for a in ages if a <= config.MAX_AGE_HOURS * 3600)
    return (f"{parsed.feed.get('title', 'untitled')}: {len(parsed.entries)} entries, "
            f"{recent} in the last {config.MAX_AGE_HOURS}h")


def fetch_stories(skip_ids=()):
    """Newest unseen stories per feed (skip_ids are dropped before the per-feed cap).

    Returns (stories newest first, errors, number skipped as already printed).
    """
    cutoff = time.time() - config.MAX_AGE_HOURS * 3600
    stories, errors, skipped = [], [], 0
    for url in config.FEEDS:
        t0 = time.time()
        try:
            parsed, size = _get(url)
        except (requests.RequestException, ValueError) as e:
            log.error("feed %s: failed after %.1fs: %s", url, time.time() - t0, e)
            errors.append(f"{url}: {e}")
            continue
        log.info("feed %s: %d bytes in %.1fs, %d entries (%s)", url, size, time.time() - t0,
                 len(parsed.entries), parsed.feed.get("title", "untitled"))
        source = parsed.feed.get("title", url)
        for e in parsed.entries:
            ts = _ts(e)
            if ts < cutoff or not e.get("link"):
                log.debug("skip (too old or no link): %s", e.get("title", "")[:80])
                continue
            sid = e.get("id") or e["link"]
            if sid in skip_ids:
                skipped += 1
                continue
            body = e.get("content", [{}])[0].get("value") or e.get("summary", "")
            stories.append({
                "id": sid,
                "title": _clean(e.get("title", "")),
                "link": e["link"],
                "text": _clean(body)[:4000],
                "source": source,
                "ts": ts,
                "feed": url,
            })
    picked, per_feed = [], {}
    for s in sorted(stories, key=lambda s: s["ts"], reverse=True):
        if per_feed.get(s["feed"], 0) < config.ITEMS_PER_FEED:
            per_feed[s["feed"]] = per_feed.get(s["feed"], 0) + 1
            picked.append(s)
    if config.MAX_STORIES:
        picked = picked[:config.MAX_STORIES]
    log.info("selected %d new stories (%d unseen in the last %dh, %d already printed skipped; "
             "max %d per feed, total cap %s)", len(picked), len(stories), config.MAX_AGE_HOURS,
             skipped, config.ITEMS_PER_FEED, config.MAX_STORIES or "none")
    return picked, errors, skipped
