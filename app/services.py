import logging
import time

import requests

from . import config

log = logging.getLogger("thermalnews.services")


def summarise(story):
    """Ask Ollama for a short summary; falls back to the feed text on failure."""
    prompt = (
        "Summarise this news story in 2-3 plain sentences (max 60 words) for a "
        "small printed receipt. No preamble, no markdown.\n\n"
        f"Title: {story['title']}\n\n{story['text'] or '(no body provided)'}"
    )
    t0 = time.time()
    log.info("ollama: %s asking %s to summarise %r (%d chars of text)",
             config.OLLAMA_URL, config.OLLAMA_MODEL, story["title"][:60], len(story["text"]))
    r = requests.post(
        f"{config.OLLAMA_URL}/api/generate",
        json={"model": config.OLLAMA_MODEL, "prompt": prompt, "stream": False},
        timeout=config.OLLAMA_TIMEOUT,
    )
    if not r.ok:
        log.error("ollama: HTTP %s: %s", r.status_code, r.text[:300])
    r.raise_for_status()
    out = r.json()["response"].strip()
    log.info("ollama: HTTP %s, %d chars in %.1fs", r.status_code, len(out), time.time() - t0)
    log.debug("ollama summary: %s", out)
    return out


def shorten(url):
    """Create a Sink short link. Returns the original URL if Sink isn't configured."""
    if not (config.SINK_URL and config.SINK_TOKEN):
        log.warning("sink: SINK_URL/SINK_TOKEN not set, using the full URL")
        return url
    t0 = time.time()
    r = requests.post(
        f"{config.SINK_URL}/api/link/create",
        headers={"Authorization": f"Bearer {config.SINK_TOKEN}"},
        json={"url": url, "tags": ["thermalnews"]},
        timeout=15,
    )
    if not r.ok:
        log.error("sink: HTTP %s from %s: %s", r.status_code, config.SINK_URL, r.text[:300])
    r.raise_for_status()
    data = r.json()
    log.debug("sink raw response: %s", data)
    link = data.get("link", data)
    short = data.get("shortLink") or (f"{config.SINK_URL}/{link['slug']}" if link.get("slug") else None)
    if short:
        log.info("sink: HTTP %s %s -> %s in %.1fs", r.status_code, url[:70], short, time.time() - t0)
        return short
    raise ValueError(f"unexpected Sink response: {data}")
