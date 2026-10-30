# Thermal News

One button on a web page: pull the latest RSS stories, summarise each with Ollama,
shorten the link with [Sink](https://github.com/ccbikai/Sink), and print the lot (with a
QR code per story) on a Star 80mm thermal printer over the network.

```
RSS feeds -> Ollama (summary) -> Sink (short link + QR) -> bitmap receipt -> printer (TCP 9100)
```

## What you need

- A Linux box (or anything that runs Docker) on the same network as the printer.
- The printer's IP address (see step 1).
- An [Ollama](https://ollama.com) server reachable from that box, with a model pulled.
- A Sink instance and its site token (optional; without it the full URL is printed
  and put in the QR code).

## Setup

### 1. Get the printer on your network and find its IP

Join the printer to your Wi-Fi using Star's setup tools/manual for your model, then
note its IP address. Most Star printers print a self-test or interface-settings page
(see your model's manual for the button combination) that includes the IP. Give it a
fixed IP or a DHCP reservation on your router so the address does not change.

### 2. Check the printer accepts raw network printing (do this first)

The app sends the receipt as a bitmap over raw TCP (port 9100). Star's TSP100III series
(including the TSP143IIIW) is documented around Star's own drivers and virtual port, and
raw 9100 support is not clearly documented, so verify before anything else:

```
pip install -r requirements.txt            # or use a venv
python test_printer.py <printer-ip> starline
python test_printer.py <printer-ip> escpos
```

A test receipt with a QR code should print. Use the mode that works as
`PRINTER_PROTOCOL` in step 5. Notes:

- `starline` is the TSP100's default emulation. `escpos` only works if ESC/POS emulation
  is enabled on the printer (see Star's "How to change the emulation on Star TSP100
  series printers" article).
- If it connects but prints garbage, try the other mode.
- If neither mode prints (or the connection is refused), the printer is not accepting
  raw sockets. The alternative is CloudPRNT (the printer polls the server for jobs),
  which this app does not implement yet.

You can also check the port is open with `nc -zv <printer-ip> 9100`.

### 3. Pull an Ollama model

On the machine running Ollama:

```
ollama pull llama3.2
```

Any model works; set `OLLAMA_MODEL` to match. By default Ollama listens only on
localhost, so if it runs on another machine set `OLLAMA_HOST=0.0.0.0` for the Ollama
service and make sure the Docker box can reach port 11434.

### 4. Get your Sink token

The token is the `NUXT_SITE_TOKEN` value you set when deploying Sink. The app calls
`POST <SINK_URL>/api/link/create` with it as a bearer token. `SINK_URL` is the base
address of your instance, e.g. `https://s.example.com` (no trailing path).

### 5. Configure and run

Copy the example settings file and edit it (`.env` is git-ignored, so your Sink token
stays out of version control):

```
cp .env.example .env
```

Settings:

| Var | Default | Meaning |
|---|---|---|
| `PRINTER_HOST` | - | printer IP (required to print) |
| `PRINTER_PORT` | 9100 | printer port |
| `PRINTER_PROTOCOL` | starline | `starline` or `escpos` (from step 2) |
| `PRINTER_WIDTH_DOTS` | 576 | 576 for 80mm at 203dpi |
| `PRINTER_CHUNK_BYTES` / `PRINTER_CHUNK_DELAY_MS` | 512 / 15 | send in chunks of this size with this pause between them |
| `PRINTER_HOLD_SECONDS` | 5 | keep the connection open this long after sending |
| `OLLAMA_URL` | http://localhost:11434 | Ollama base URL (use the real IP from inside Docker) |
| `OLLAMA_MODEL` | llama3.2 | model to summarise with |
| `SINK_URL` / `SINK_TOKEN` | - | Sink base URL and site token; if unset the full URL is used |
| `FEEDS` | BBC, Hacker News | comma-separated RSS/Atom URLs |
| `SCHEDULE_ENABLED` | false | print automatically at the times below |
| `SCHEDULE_TIMES` | 07:30 | comma-separated 24-hour times, e.g. `07:30,18:00` |
| `SCHEDULE_DAYS` | 0,1,2,3,4,5,6 | days to run, 0 = Monday .. 6 = Sunday |
| `SCHEDULE_TIMEZONE` | `TZ` or UTC | timezone the times are in, e.g. `Europe/London` |
| `PRINT_MODE` | per_feed | `per_feed` = one printout (cut between) per feed; `combined` = everything in one printout |
| `ITEMS_PER_FEED` | 3 | newest N stories per feed |
| `LOG_LEVEL` | INFO | `DEBUG` also logs skipped stories, raw Sink responses and summaries |
| `MAX_AGE_HOURS` | 24 | ignore stories older than this |
| `MAX_STORIES` | 0 | cap on total stories per press, 0 = no cap |
| `SINK_EXPIRY_DAYS` | 7 | Sink links (including test links) expire after this many days, 0 = never |

Note `localhost` inside a container is the container itself, so point `OLLAMA_URL` at
the Ollama machine's LAN IP.

Then start it:

```
docker compose up -d --build
```

Open `http://<box-ip>:8185` (the host port is set in `docker-compose.yml`).

**Changed `.env` later?** (Settings saved in the web page take priority over `.env`; reset them there first.) `docker compose restart` does not re-read it. Recreate the
container instead:

```
docker compose up -d --force-recreate
```

The line under the page title shows the model and printer the running container is
using, and you can also check with `docker compose exec thermalnews env | grep -E 'PRINTER|OLLAMA'`.

### 6. Use it

**Scheduled printing** (Settings > Schedule): tick "print automatically", enter one or more
times (24-hour, e.g. `07:30, 18:00`), pick the days and your timezone, then press
**Save & check schedule**, which lists the next runs. The container usually runs in UTC, so
set the timezone or "07:30" will fire at the wrong hour. A scheduled run does exactly what
the button does (new stories only, your feed and layout settings) and shows up in the log and
in "last run" on the Settings page. Each time fires at most once, even across restarts. If
the box was off, or a job was already running, for more than 15 minutes past a time, that
run is skipped rather than printing late. Saving a schedule never fires a time that has
already passed today. The scheduler lives in the web process, so keep it to one worker as in
the Dockerfile.

**Settings** (the panel on the page) lets you change the feeds, how many stories to print
and the print layout, the Ollama URL and model, the Sink URL, token and link expiry, and
the printer IP, port, protocol and width, without editing files or restarting. Each section has a **Save & test** button: Feeds fetches every feed and shows how many recent
entries it has, Ollama checks the server is up and
the model is pulled, Sink creates one real test link, and the printer sends a test page.
Saved values go in `settings.json` inside the data volume (`./data`, file mode 600),
and **override `.env`**; "Reset to .env values" deletes them. The Sink token is never sent
back to the browser (leave the field blank to keep it).

Each press prints the newest **new** stories from every feed (stories printed before are
skipped, and skipped stories no longer count against the per-feed limit). By default each
feed gets its own printout with a cut in between; choose "Everything in one printout" for a
single combined one. Stories are only marked as printed once the whole job has been sent.
If a press prints fewer stories than expected, the log says how many were skipped as
already printed; tick "include stories already printed" to reprint them.

- **Test print** sends a small test page to the printer.
- **Preview only** runs the whole pipeline (feeds, Ollama, Sink) and shows the receipt
  in the browser without printing. Note this does create Sink links.
- **Print the news** does the real thing. Progress shows in the log panel; a run with
  several stories can take a minute or more depending on your model.

Printed stories are remembered in `./data/printed.json`, so pressing the button again
prints only new stories. Tick "include stories already printed" to override, or delete
that file.

If Ollama fails for a story it prints the feed's own text instead; if Sink fails it
prints with the full URL. Summaries are based on the feed's text, not the full article.

## Logs

Everything the app does is logged to the container output, with timings: each feed
fetch, each Ollama and Sink call (status, duration, result), the render, and the printer
connection (host, port, protocol, bytes sent), plus every web request and full
tracebacks on errors. The page's once-a-second `/status` polling is left out of the request
log. The Sink token is never logged.

```
docker compose logs -f thermalnews
```

Set `LOG_LEVEL=DEBUG` in `.env` (then `docker compose up -d --force-recreate`) for more.

## Running without Docker

```
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
sudo apt install fonts-dejavu-core        # receipt font
set -a; . ./.env; set +a; export DATA_DIR=./data
gunicorn -b 0.0.0.0:8080 -w 1 --threads 4 --timeout 300 app.main:app
```

Keep it to one worker; job state is held in memory. The variables are the same as in the table above.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| "Test print" fails with connection refused/timeout (see also `docker compose logs`) | wrong `PRINTER_HOST`, printer asleep/offline, or it does not accept raw 9100 (step 2) |
| "Test print" says it sent but nothing prints | the printer accepted the connection but did not understand it: pick the other protocol in the drop-down next to the button and try again |
| Prints part of the receipt, then stops (maybe with a few specks) | the printer is losing data. In Settings > Printer > Advanced, lower the chunk size (e.g. 256), raise the pause (e.g. 30 ms) and the hold time, then test again. If the log says `printer closed the connection after N of M bytes`, the printer hung up by itself |
| Prints garbage or a long strip of symbols | wrong `PRINTER_PROTOCOL`; try the other one |
| Blank paper or text too small/wide | check `PRINTER_WIDTH_DOTS` matches your paper/model |
| Log shows `ollama failed` | `OLLAMA_URL` unreachable from the container, or the model is not pulled |
| Log shows `sink failed` | wrong `SINK_URL`/`SINK_TOKEN`; the log prints the HTTP error |
| "nothing new to print" | everything was already printed; tick the reprint box, raise `MAX_AGE_HOURS`, or check `FEEDS` |
| Text missing accents/emoji | the font lacks the glyph; the app uses DejaVu Sans |

There is no login on the web page, and the Settings panel can repoint the app at any
Ollama, Sink or printer address, so keep it on your LAN and do not expose it to the
internet.
