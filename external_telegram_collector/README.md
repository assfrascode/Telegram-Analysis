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

## Environment

```env
BACKEND_URL=http://localhost:8000
TELEGRAM_INGEST_TOKEN=tg_ingest_...
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=...
TELEGRAM_PHONE=+49123456789
TELEGRAM_SESSION_PATH=/data/telegram-external.session
TELEGRAM_CHAT_IDS=12345,67890
INITIAL_SYNC_FROM=2026-01-01T00:00:00+00:00
SYNC_INTERVAL_MINUTES=60
POLL_SECONDS=15
MESSAGE_BATCH_SIZE=100
```

`TELEGRAM_CHAT_IDS` controls which dialogs are registered as external backend
chats on startup. The collector then polls `/telegram/ingest/claims/next` and
processes due sync runs.

## Local Run

```bash
pip install -r requirements.txt
python collector.py
```

On the first run Telethon may ask for a login code. Persist
`TELEGRAM_SESSION_PATH` so later restarts reuse the same Telegram session.
