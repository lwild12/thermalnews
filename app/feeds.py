import calendar
import re
import time
from html import unescape

import feedparser

from . import config


def _clean(html):
    text = re.sub(r"<[^>]+>", " ", html or "")
    return re.sub(r"\s+", " ", unescape(text)).strip()


def fetch_stories():
    """Newest stories per feed, newest first overall, filtered by age."""
    cutoff = time.time() - config.MAX_AGE_HOURS * 3600
    stories, errors = [], []
    for url in config.FEEDS:
        parsed = feedparser.parse(url, agent="thermalnews/1.0")
        if parsed.bozo and not parsed.entries:
            errors.append(f"{url}: {parsed.get('bozo_exception')}")
            continue
        source = parsed.feed.get("title", url)
        for e in parsed.entries:
            ts = e.get("published_parsed") or e.get("updated_parsed")
            ts = calendar.timegm(ts) if ts else time.time()
            if ts < cutoff or not e.get("link"):
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
    return picked, errors
