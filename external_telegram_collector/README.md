# External Telegram Collector

This component keeps Telegram credentials outside the backend. It uses Telethon
locally, pushes normalized messages and media to the backend HTTPS ingest API, and
stores its Telegram session in `TELEGRAM_SESSION_PATH`.

## Required Backend Setup

Create an ingest token with a normal backend user JWT:

```bash
curl -X POST "$BACKEND_URL/telegram/ingest/tokens" \
  -H "Authorization: Bearer $USER_ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"collector-1","expires_in_days":30}'
```

Use the returned one-time `token` as `TELEGRAM_INGEST_TOKEN`.

Create the token with the same backend user account that you use in the React
frontend. Collector chats are owned by that user; if the token comes from a
different user, the chats will register successfully but will not appear in the
frontend session you are using.

Ingest tokens expire after 30 days by default and may be created for 1 to 365
days. Rotate one without orphaning its registered chats in this order:

1. Create the replacement token and note its returned `id` and one-time `token`.
2. For each existing chat, assign the replacement with
   `PUT /telegram/ingest/chats/{chat_db_uuid}/token` and the JSON body
   `{"token_id":"<replacement-token-id>"}` using the owning user's JWT.
3. Put the replacement one-time token in `TELEGRAM_INGEST_TOKEN` and restart the
   collector.
4. Revoke the old token only after the collector is healthy.

Reassignment accepts only an active, unexpired token owned by the same user. It
also fails any old-token run still in progress and clears its lease before the
replacement collector can claim the chat.

## Environment

```env
BACKEND_URL=https://chat.example.com
# Development only, and only when BACKEND_URL uses localhost/127.0.0.1/::1.
TELEGRAM_ALLOW_INSECURE_LOOPBACK_BACKEND_HTTP=false
TELEGRAM_INGEST_TOKEN=tg_ingest_...
TELEGRAM_API_ID=123456
TELEGRAM_API_HASH=...
TELEGRAM_PHONE=+49123456789
# Must be absolute. The parent directory is forced to mode 0700.
TELEGRAM_SESSION_PATH=/data/telegram-external.session
# Canonical marked IDs only: -... for groups, -100... for channels.
TELEGRAM_CHAT_IDS=-1001234567890
TELEGRAM_ALL_CHATS=false
TELEGRAM_INCLUDE_RAW_METADATA=false
TELEGRAM_USE_TAKEOUT=false
TELEGRAM_TAKEOUT_WAIT_TIME=0
# Optional. Defaults to the current UTC time minus 30 days.
INITIAL_SYNC_FROM=
TELEGRAM_MAX_SYNC_RANGE_DAYS=31
SYNC_INTERVAL_MINUTES=60
POLL_SECONDS=15
MESSAGE_BATCH_SIZE=100
MESSAGE_PROGRESS_EVERY=250
TELEGRAM_MAX_MEDIA_FILE_BYTES=268435456
TELEGRAM_MAX_MEDIA_BYTES_PER_RUN=1073741824
TELEGRAM_MAX_MEDIA_FILES_PER_RUN=200
TELEGRAM_MEDIA_DOWNLOAD_TIMEOUT_SECONDS=300

# Local collector web interface. Enabled by default.
COLLECTOR_WEB_ENABLED=true
COLLECTOR_WEB_HOST=127.0.0.1
COLLECTOR_WEB_PORT=8787
# Optional locally. If omitted, a random password is printed once at startup.
COLLECTOR_WEB_AUTH_TOKEN=
COLLECTOR_WEB_ALLOWED_HOSTS=127.0.0.1,localhost,[::1]
COLLECTOR_WEB_ALLOWED_ORIGINS=http://127.0.0.1:8787,http://localhost:8787
COLLECTOR_WEB_MAX_BODY_BYTES=4096
COLLECTOR_WEB_API_REQUESTS_PER_MINUTE=120
COLLECTOR_WEB_LOGIN_ATTEMPTS_PER_MINUTE=6
```

For a collector running on a separate laptop, set `BACKEND_URL` to the same
TLS-protected public origin used by the browser. The collector rejects plain HTTP.
For a loopback-only development backend, set both
`BACKEND_URL=http://127.0.0.1:8000` and
`TELEGRAM_ALLOW_INSECURE_LOOPBACK_BACKEND_HTTP=true`. The override cannot enable
HTTP to a LAN, public, or non-loopback hostname. Backend requests ignore inherited
HTTP proxy environment variables so the ingest bearer and Telegram data do not
silently pass through a workstation proxy.

`TELEGRAM_CHAT_IDS` is an explicit allowlist of canonical marked peer IDs. Raw
positive Telethon IDs are rejected because Telegram's User, Chat, and Channel ID
namespaces can overlap. An empty allowlist fails closed;
set `TELEGRAM_ALL_CHATS=true` only after intentionally accepting collection of
every visible group/channel. The collector then polls
`/telegram/ingest/claims/next` and processes
due sync runs. After a completed sync, each claim includes the last stored
Telegram message ID and the collector requests only newer messages. Claims are
also checked against the local approved map, entity type, `INITIAL_SYNC_FROM`, and
`TELEGRAM_MAX_SYNC_RANGE_DAYS`; backend state alone cannot expand local access.

Set `TELEGRAM_USE_TAKEOUT=true` for large historical exports or media-heavy
syncs. In takeout mode, startup registration and claim polling still use the
normal Telegram session, but each claimed message scan and media download runs
through a Telegram takeout session. If Telegram requires a takeout warm-up delay,
the collector reports the retry delay back to the backend. Tune
`TELEGRAM_TAKEOUT_WAIT_TIME` if you still hit flood waits during takeout scans.

With an empty allowlist, the collector lists locally visible groups/channels and
their canonical IDs in its process log but registers none. Copy only the desired
negative ID (`-...` or `-100...`) into `TELEGRAM_CHAT_IDS` and restart. During
sync, `MESSAGE_PROGRESS_EVERY` controls periodic scan progress logs. Existing
backend registrations created with old raw positive IDs must be removed once;
the hardened collector deliberately rejects claims for them.

The collector omits the complete Telethon message object and peer access hash by
default. Set `TELEGRAM_INCLUDE_RAW_METADATA=true` only when downstream archival
needs justify the additional protocol metadata. Media is stopped during download
when its file or remaining per-run byte quota is crossed, and each download has a
deadline. Quota skips are reported as attachment failures rather than weakening
the local limits.

## Local Run

```bash
pip install -r requirements.txt
python collector.py
```

Open `http://127.0.0.1:8787`. The browser presents an HTTP Basic prompt: use
username `collector` and the generated password printed once in the collector
startup log, or the value configured in `COLLECTOR_WEB_AUTH_TOKEN`. On the first
run, the page then asks for the code sent by Telegram and, when enabled for the
account, the two-step verification password. Existing authorized sessions skip
the login form. The authenticated page exposes only redacted operational status;
account identity, chat titles, phone numbers, Telegram peer/run IDs, detailed
errors, local paths, and event logs remain in the local process log.

Persist `TELEGRAM_SESSION_PATH` so later restarts reuse the same Telegram
session. The collector rejects a relative path or symlink, requires state owned
by its process user, changes the directory to mode `0700`, and changes the SQLite
session and journal files to `0600`. A copied Telethon session is an account
credential that bypasses 2FA until revoked. Keep session and environment files
outside the repository and add their exact names to the repository's local Git
exclude; the component's `.dockerignore` prevents common session/env names from
entering its Docker build context.

To retain the original terminal login prompts instead of running the web server,
set:

```env
COLLECTOR_WEB_ENABLED=false
```

## Docker Run

Build the collector image. The image runs as UID/GID `10001` and stores its
session in a dedicated Docker volume. On Linux, host networking lets the process
retain its secure loopback bind without exposing a container-wide plaintext
listener:

```bash
docker build -t telegram-external-collector external_telegram_collector
chmod 600 external-telegram-collector.env
docker volume create telegram-collector-data
docker run --rm \
  --read-only \
  --cap-drop ALL \
  --security-opt no-new-privileges \
  --pids-limit 256 \
  --memory 2g \
  --cpus 2.0 \
  --tmpfs /tmp:rw,noexec,nosuid,size=1g,mode=1777 \
  --network host \
  --env-file external-telegram-collector.env \
  -v telegram-collector-data:/data \
  telegram-external-collector
```

The web server authenticates every page, asset, status request, and Telegram
login action. It also enforces host/origin allowlists, streaming request-body
limits, login/API rate limits, no-store responses, CSP, and frame denial. Ordinary
cross-origin form requests are additionally blocked by the JSON-only API.

A non-loopback bind, including `0.0.0.0` in bridged Docker networking, is rejected
unless all of the following are configured:

```env
COLLECTOR_WEB_ALLOW_REMOTE=true
COLLECTOR_WEB_AUTH_TOKEN=at-least-32-random-characters
COLLECTOR_WEB_ALLOWED_HOSTS=collector.example.com
COLLECTOR_WEB_ALLOWED_ORIGINS=https://collector.example.com
COLLECTOR_WEB_TLS_CERT_FILE=/tls/fullchain.pem
COLLECTOR_WEB_TLS_KEY_FILE=/tls/private-key.pem
```

Mount the certificate directory read-only and use a browser-trusted certificate.
Uvicorn serves TLS directly in this mode; an authentication token alone never
permits plaintext remote OTP or 2FA-password entry. `/health` remains
unauthenticated and returns only `ok` plus the coarse collector phase.
