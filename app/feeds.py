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


def fetch_stories():
    """Newest stories per feed, newest first overall, filtered by age."""
    cutoff = time.time() - config.MAX_AGE_HOURS * 3600
    stories, errors = [], []
    for url in config.FEEDS:
        t0 = time.time()
        try:
            # feedparser has no timeout of its own, so fetch here
            r = requests.get(url, timeout=(5, 15), headers={"User-Agent": "thermalnews/1.0"})
            r.raise_for_status()
        except requests.RequestException as e:
            log.error("feed %s: fetch failed after %.1fs: %s", url, time.time() - t0, e)
            errors.append(f"{url}: {e}")
            continue
        log.info("feed %s: HTTP %s, %d bytes in %.1fs", url, r.status_code, len(r.content), time.time() - t0)
        parsed = feedparser.parse(r.content)
        if parsed.bozo and not parsed.entries:
            log.error("feed %s: could not parse: %s", url, parsed.get("bozo_exception"))
            errors.append(f"{url}: {parsed.get('bozo_exception')}")
            continue
        log.info("feed %s: %d entries (%s)", url, len(parsed.entries), parsed.feed.get("title", "untitled"))
        source = parsed.feed.get("title", url)
        for e in parsed.entries:
            ts = e.get("published_parsed") or e.get("updated_parsed")
            ts = calendar.timegm(ts) if ts else time.time()
            if ts < cutoff or not e.get("link"):
                log.debug("skip (too old or no link): %s", e.get("title", "")[:80])
                continue
            body = e.get("content", [{}])[0].get("value") or e.get("summary", "")
            stories.append({
                "id": e.get("id") or e["link"],
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
    log.info("selected %d of %d recent stories (%d per feed max, last %dh)",
             len(picked), len(stories), config.ITEMS_PER_FEED, config.MAX_AGE_HOURS)
    return picked, errors
