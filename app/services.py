import requests

from . import config


def summarise(story):
    """Ask Ollama for a short summary; falls back to the feed text on failure."""
    prompt = (
        "Summarise this news story in 2-3 plain sentences (max 60 words) for a "
        "small printed receipt. No preamble, no markdown.\n\n"
        f"Title: {story['title']}\n\n{story['text'] or '(no body provided)'}"
    )
    r = requests.post(
        f"{config.OLLAMA_URL}/api/generate",
        json={"model": config.OLLAMA_MODEL, "prompt": prompt, "stream": False},
        timeout=config.OLLAMA_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()["response"].strip()


def shorten(url):
    """Create a Sink short link. Returns the original URL if Sink isn't configured."""
    if not (config.SINK_URL and config.SINK_TOKEN):
        return url
    r = requests.post(
        f"{config.SINK_URL}/api/link/create",
        headers={"Authorization": f"Bearer {config.SINK_TOKEN}"},
        json={"url": url, "tags": ["thermalnews"]},
        timeout=15,
    )
    r.raise_for_status()
    data = r.json()
    link = data.get("link", data)
    if data.get("shortLink"):
        return data["shortLink"]
    if link.get("slug"):
        return f"{config.SINK_URL}/{link['slug']}"
    raise ValueError(f"unexpected Sink response: {data}")
