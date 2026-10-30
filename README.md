# Thermal News

One button on a web page: pull the latest RSS stories, summarise each with Ollama,
shorten the link with Sink, and print the lot (with a QR code per story) on a Star
80mm thermal printer over the network.

## Run

Edit `docker-compose.yml` (printer IP, Ollama URL, Sink URL and token, feeds), then:

    docker compose up -d --build

Open `http://<box>:8080`. **Preview only** renders the receipt in the browser without
printing; **Test print** sends a small page to the printer.

## Printer notes (important)

The app sends the receipt as a bitmap over raw TCP (port 9100). Star's TSP100III series
(including the TSP143IIIW) is documented around Star's own drivers/virtual port, and
raw 9100 support is not clearly documented, so verify first:

    python test_printer.py <printer-ip> starline
    python test_printer.py <printer-ip> escpos

Use whichever works as `PRINTER_PROTOCOL`. If neither prints, the printer is not
accepting raw sockets; the fallback is CloudPRNT (printer polls the server), which this
app does not implement yet. ESC/POS on TSP100 must be enabled in the printer's emulation
settings (see Star's "How to change the emulation on Star TSP100 series printers").

## Config (env vars)

| Var | Default | Meaning |
|---|---|---|
| `FEEDS` | BBC, HN | comma-separated RSS/Atom URLs |
| `ITEMS_PER_FEED` | 3 | newest N per feed |
| `MAX_AGE_HOURS` | 24 | ignore older stories |
| `OLLAMA_URL` / `OLLAMA_MODEL` | localhost:11434 / llama3.2 | summariser |
| `SINK_URL` / `SINK_TOKEN` | - | Sink base URL and `NUXT_SITE_TOKEN`; if unset the full URL is used |
| `PRINTER_HOST` / `PRINTER_PORT` | - / 9100 | printer |
| `PRINTER_PROTOCOL` | starline | `starline` or `escpos` |
| `PRINTER_WIDTH_DOTS` | 576 | 576 for 80mm at 203dpi |

Printed stories are remembered in `/data/printed.json`; tick "include stories already
printed" to override. If Ollama or Sink fail for a story it still prints (feed text and
full URL respectively). Summaries use the feed's own text, not the full article page.
