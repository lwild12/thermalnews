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

Edit `docker-compose.yml`:

| Var | Default | Meaning |
|---|---|---|
| `PRINTER_HOST` | - | printer IP (required to print) |
| `PRINTER_PORT` | 9100 | printer port |
| `PRINTER_PROTOCOL` | starline | `starline` or `escpos` (from step 2) |
| `PRINTER_WIDTH_DOTS` | 576 | 576 for 80mm at 203dpi |
| `OLLAMA_URL` | http://localhost:11434 | Ollama base URL (use the real IP from inside Docker) |
| `OLLAMA_MODEL` | llama3.2 | model to summarise with |
| `SINK_URL` / `SINK_TOKEN` | - | Sink base URL and site token; if unset the full URL is used |
| `FEEDS` | BBC, Hacker News | comma-separated RSS/Atom URLs |
| `ITEMS_PER_FEED` | 3 | newest N stories per feed |
| `MAX_AGE_HOURS` | 24 | ignore stories older than this |

Note `localhost` inside a container is the container itself, so point `OLLAMA_URL` at
the Ollama machine's LAN IP.

Then start it:

```
docker compose up -d --build
```

Open `http://<box-ip>:8080`.

### 6. Use it

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

## Running without Docker

```
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
sudo apt install fonts-dejavu-core        # receipt font
export PRINTER_HOST=192.168.1.50 OLLAMA_URL=http://192.168.1.10:11434 DATA_DIR=./data
gunicorn -b 0.0.0.0:8080 -w 1 --threads 4 --timeout 300 app.main:app
```

Keep it to one worker; job state is held in memory. Set the other variables from the
table above as needed.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| "Test print" fails with connection refused/timeout | wrong `PRINTER_HOST`, printer asleep/offline, or it does not accept raw 9100 (step 2) |
| Prints garbage or a long strip of symbols | wrong `PRINTER_PROTOCOL`; try the other one |
| Blank paper or text too small/wide | check `PRINTER_WIDTH_DOTS` matches your paper/model |
| Log shows `ollama failed` | `OLLAMA_URL` unreachable from the container, or the model is not pulled |
| Log shows `sink failed` | wrong `SINK_URL`/`SINK_TOKEN`; the log prints the HTTP error |
| "nothing new to print" | everything was already printed; tick the reprint box, raise `MAX_AGE_HOURS`, or check `FEEDS` |
| Text missing accents/emoji | the font lacks the glyph; the app uses DejaVu Sans |

There is no login on the web page, so keep it on your LAN and do not expose it to the
internet.
