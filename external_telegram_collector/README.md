# External Telegram Collector

This component keeps Telegram credentials outside the backend. It uses Telethon
locally, pushes normalized messages and media to the backend HTTP ingest API, and
stores its Telegram session in `TELEGRAM_SESSION_PATH`.

## Required Backend Setup

Create an ingest token with a normal backend user JWT:

```bash
curl -X POST "$BACKEND_URL/telegram/ingest/tokens" \
  -H "Authorization: Bearer $USER_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"collector-1"}'
```

Use the returned one-time `token` as `TELEGRAM_INGEST_TOKEN`.

Create the token with the same backend user account that you use in the React
frontend. Collector chats are owned by that user; if the token comes from a
different user, the chats will register successfully but will not appear in the
frontend session you are using.

## Environment

```env
BACKEND_URL=http://localhost:8000
TELEGRAM_INGEST_TOKEN=tg_ingest_...
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=...
TELEGRAM_PHONE=+49123456789
TELEGRAM_SESSION_PATH=/data/telegram-external.session
TELEGRAM_CHAT_IDS=
TELEGRAM_USE_TAKEOUT=false
TELEGRAM_TAKEOUT_WAIT_TIME=0
# Optional. Defaults to the current UTC time minus 30 days.
INITIAL_SYNC_FROM=
SYNC_INTERVAL_MINUTES=60
POLL_SECONDS=15
MESSAGE_BATCH_SIZE=100
MESSAGE_PROGRESS_EVERY=250

# Local collector web interface. Enabled by default.
COLLECTOR_WEB_ENABLED=true
COLLECTOR_WEB_HOST=127.0.0.1
COLLECTOR_WEB_PORT=8787
```

For a collector running on a separate laptop, set `BACKEND_URL` to the backend
API host, for example `http://192.168.0.151:8000` without an extra dot. Open the
browser frontend at `http://192.168.0.151:3000`; port `8000` is the backend API
and legacy static UI, not the React frontend.

By default, an empty `TELEGRAM_CHAT_IDS` registers every visible group/channel
for the Telegram account. Set `TELEGRAM_CHAT_IDS` only when you want an
allowlist. The collector then polls `/telegram/ingest/claims/next` and processes
due sync runs. After a completed sync, each claim includes the last stored
Telegram message ID and the collector requests only newer messages.

Set `TELEGRAM_USE_TAKEOUT=true` for large historical exports or media-heavy
syncs. In takeout mode, startup registration and claim polling still use the
normal Telegram session, but each claimed message scan and media download runs
through a Telegram takeout session. If Telegram requires a takeout warm-up delay,
the collector reports the retry delay back to the backend. Tune
`TELEGRAM_TAKEOUT_WAIT_TIME` if you still hit flood waits during takeout scans.

On startup the collector prints the visible group/channel IDs for its Telegram
session. Use either the raw Telethon ID or the marked Telegram peer ID such as
`-100...` in `TELEGRAM_CHAT_IDS`; both forms are accepted. During sync,
`MESSAGE_PROGRESS_EVERY` controls periodic scan progress logs.

## Local Run

```bash
pip install -r requirements.txt
python collector.py
```

Open `http://127.0.0.1:8787`. On the first run, the page asks for the code sent
by Telegram and, when enabled for the account, the two-step verification
password. The same page shows connection state, chat registration, current sync
counters, the last sync result, retries, and recent collector events. Existing
authorized sessions skip the login form.

Persist `TELEGRAM_SESSION_PATH` so later restarts reuse the same Telegram
session. Codes, passwords, API hashes, session data, and ingest tokens are never
included in the status response or event list.

To retain the original terminal login prompts instead of running the web server,
set:

```env
COLLECTOR_WEB_ENABLED=false
```

## Docker Run

Build the collector image and publish its web interface only on the host's
loopback address:

```bash
docker build -t telegram-external-collector external_telegram_collector
docker run --rm \
  --env-file external-telegram-collector.env \
  -e COLLECTOR_WEB_HOST=0.0.0.0 \
  -p 127.0.0.1:8787:8787 \
  -v "$(pwd)/collector-data:/data" \
  telegram-external-collector
```

The server has no application-level authentication. Keep it bound to
`127.0.0.1`; listening on `0.0.0.0` is intended only inside a container whose
published host port is still restricted to loopback. If configuration is
invalid, the page stays available with the error and asks for a process restart
after the environment has been corrected.
