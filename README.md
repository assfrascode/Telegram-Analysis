# Chat Analyse

Chat Analyse is a Dockerized Python application for analysing Telegram conversations. It can ingest Telegram Desktop ZIP exports or continuously collected Telegram chats, turn messages and media metadata into retrieval-ready chunks, run RAG-based question answering, and package a static HTML report.

The project is still an MVP, but it includes a functional backend, worker pipeline, storage layer, React frontend, Telegram collection flows, and report scheduling.

## Current Capabilities

- Authenticated React web interface for creating and monitoring analysis jobs.
- Two analysis sources:
  - Telegram Desktop ZIP exports streamed through the authenticated backend.
  - Collected Telegram groups/channels synchronized by the backend or by an external collector.
- Secure ZIP validation, extraction, Telegram JSON/HTML export parsing, and media inventory creation.
- Telegram account connection with API ID/hash login, code verification, optional two-step password verification, encrypted sessions, and disconnect support.
- External Telegram collector mode for keeping Telegram credentials outside the backend.
- Saved question sets with default analysis options for reusable templates.
- Optional message translation through LibreTranslate.
- Optional media analysis through an OpenAI-compatible vLLM vision endpoint.
- Optional audio/video transcription through the OpenAI Audio Transcriptions API.
- Chunking, embedding, Qdrant indexing, retrieval, reranking, answer generation, BLUF synthesis, and static report rendering.
- Per-job retrieval and rerank limits, with prompt budgets derived from model context length.
- Scheduled Telegram reports from collected chats and saved question sets. A selected 1, 7, 14, or 30-day interval controls both the recurrence and rolling report window while preserving the configured local run time and timezone.
- Live job monitoring through WebSocket events with polling fallback.
- Job history, pipeline stage view, event log filtering, cancellation, capacity checks, retry tracking, and dead-letter visibility.
- Report-only ZIP downloads plus a combined download that adds the static `report/` folder to the original uploaded Telegram export.
- Mock LLM mode for local development without GPU-backed model services.

## Architecture

| Component | Purpose |
| --- | --- |
| FastAPI | API, authentication, upload preparation/verification, job control, Telegram setup, report download |
| PostgreSQL | Persistent state, ownership checks, users, jobs, Telegram connections, collected messages, reports, worker metadata |
| NATS JetStream | Worker task queue and live progress events |
| MinIO | Uploaded ZIPs, extracted exports, collected media, intermediate files, final report artifacts |
| Qdrant | Vector storage for message chunks |
| Workers | Validation, extraction, parsing, Telegram sync, translation, media analysis, transcription, chunking, embedding, retrieval, reranking, answering, reporting |
| Report scheduler | Creates recurring Telegram report jobs from saved schedules |
| React/Vite + Nginx | Browser frontend and same-origin proxy to the backend |
| Optional vLLM services | Embedding and reranking model containers via the `models` Docker Compose profile |
| External Telegram collector | Optional separate Telethon process that pushes normalized messages/media to the backend ingest API |

## Pipeline

```text
Upload source:
  ZIP upload
  -> ZIP validation
  -> extraction
  -> Telegram export parsing

Collected Telegram source:
  backend/external Telegram sync
  -> snapshot messages/media for the requested report window

Shared analysis:
  media inventory / optional English media description
  + optional audio/video transcription
  -> optional English translation of messages and transcripts
  -> chunking
  -> embedding
  -> Qdrant upsert
  -> retrieval
  -> reranking
  -> answer generation
  -> BLUF synthesis
  -> static report rendering
  -> report ZIP packaging
```

Each job is owned by a user. Uploads, jobs, events, reports, Telegram resources, dead letters, and saved question sets are checked through ownership-aware access control. Foreign resource IDs return `404 Resource not found` where practical to avoid cross-user enumeration.

## Local Setup

```bash
cp .env.example .env
chmod 600 .env
python -c "import secrets; print(secrets.token_urlsafe(48))"
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Fill every blank required value in `.env`. Use unique URL-safe random values for
`SECRET_KEY`, `POSTGRES_PASSWORD`, `NATS_PASSWORD`, `MINIO_ACCESS_KEY`,
`MINIO_SECRET_KEY`, `QDRANT_API_KEY`, and `VLLM_API_KEY`; use the Fernet command's
output only for `TELEGRAM_CREDENTIALS_ENCRYPTION_KEY`. Set a non-default
`POSTGRES_USER`, a unique initial account email/password, `APP_BASE_URL` to the
externally visible HTTPS origin, and `TRUSTED_HOSTS` to a JSON array containing
that origin's hostname (for example `["chat.example.com"]`). Set both
`SERVER_NAME` and `HEALTHCHECK_HOST` to that same hostname; the latter lets the
private health probe pass the API's host allowlist. Compose fails closed while
any required value is empty. Prefix NATS credentials with an ASCII letter because
the broker reads them through its configuration parser.

```bash
docker compose up --build
```

This Compose file is a clean-install layout. In particular, current PostgreSQL
images persist the versioned data directory below `/var/lib/postgresql`. Do not
attach an older PostgreSQL 17-or-earlier volume at the new mount point; perform a
documented `pg_upgrade`/backup-and-restore migration before changing major image
versions.

After startup, the only host-published service is the frontend and same-origin API
proxy at `http://127.0.0.1:3000`. PostgreSQL, NATS, MinIO, Qdrant, the backend, and
optional model servers remain on private container networks. Put a TLS reverse
proxy in front of the loopback listener for any non-local deployment; do not bind
it publicly as plain HTTP. The TLS terminator must preserve the public `Host` and
send `X-Forwarded-Proto: https` (and normally `X-Forwarded-Port: 443`); the
loopback-only Nginx hop preserves those trusted values when proxying to FastAPI.

The initial account is created only for a clean database from the required
`BOOTSTRAP_ADMIN_EMAIL` and `BOOTSTRAP_ADMIN_PASSWORD`. Treat it as an ordinary
data-owning user. Public registration is disabled by default. After the first
successful login, set `BOOTSTRAP_ADMIN_ENABLED=false`, clear the two bootstrap
values from `.env`, and recreate the backend container.

The default object-store image follows the supported current MinIO AIStor image,
which will not start without a free-tier or commercial license. Store that file
outside Git and set `MINIO_LICENSE_FILE` to its host path; Compose mounts it
read-only. Confirm the product's licensing and support requirements before use.

The named PostgreSQL, NATS, AIStor, and Qdrant volumes contain application data
in plaintext at the container-storage layer. Put Docker's data root and backups
on encrypted storage, restrict Docker-daemon and backup access, and test encrypted
restore procedures. The supplied request, extraction, media, connection, and
temporary-filesystem limits bound the main untrusted inputs; also set host-level
CPU, memory, PID, and persistent-volume/bucket quotas for the size of your
deployment.

Compose follows the deployment's latest-image policy for PostgreSQL, NATS,
MinIO AIStor, Qdrant, vLLM, Python, Node, and Nginx. The Python and Node `slim`
aliases likewise track the current language image while reducing the runtime
surface. Every image remains overrideable by environment variable. Resolve and
record the deployed image digests in each release manifest before promotion; a
floating current tag is not reproducible and can introduce breaking major
upgrades.

Compose assigns an explicit runtime role to each backend process. JWT/bootstrap
secrets remain API-only; the internal Telegram collector receives only its
database, object-store, and Telegram-encryption credentials; NATS, Qdrant, model,
and transcription credentials are supplied only to consumers that use them.

Direct Python dependencies are exact-pinned to the versions exercised by this
workspace. Before producing a release image, generate and review a transitive
lock with hashes (for example with `pip-compile --generate-hashes`), install it
with `pip --require-hashes`, scan the resulting image/SBOM, and commit the lock.
Exact direct pins alone do not make transitive resolution reproducible.

## Analysis Sources

### ZIP Exports

Use the main `New Analysis` screen to upload a Telegram Desktop ZIP export in JSON or HTML format. Uploads pass through the authenticated backend so request and extraction limits are enforced before objects become available to workers.

### Backend Telegram Collection

Use `Telegram Setup` in the frontend to connect a Telegram account with credentials from `my.telegram.org`, load available groups/channels, and select chats to collect. The backend stores Telegram API credentials and session data encrypted at rest, then syncs active chats at the selected interval.

Completed syncs continue from the highest stored Telegram message ID instead of
rescanning a time overlap. Long syncs use progress-based inactivity limits:
`TELEGRAM_SYNC_INACTIVITY_TIMEOUT_SECONDS` and
`TELEGRAM_EXTERNAL_INACTIVITY_TIMEOUT_SECONDS` both default to 900 seconds.
External collectors have 60 seconds to respond to a newly requested report sync,
configured with `TELEGRAM_EXTERNAL_INITIAL_RESPONSE_TIMEOUT_SECONDS`. Once the
collector responds, the longer progress-based timeout applies. Reports with
`allow_partial_telegram_sync` enabled skip this wait and immediately use stored
messages while collection catches up in the background.
The former `TELEGRAM_SYNC_TIMEOUT_SECONDS` and
`TELEGRAM_EXTERNAL_COVERAGE_WAIT_SECONDS` names remain accepted as deprecated
fallbacks.

Set the required dedicated Fernet key before first startup:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```env
TELEGRAM_CREDENTIALS_ENCRYPTION_KEY=replace-with-generated-key
```

Back up this key securely. Losing it makes stored Telegram credentials unreadable;
changing it requires an explicit credential-rotation procedure.

### External Telegram Collector

Use `external_telegram_collector/` when Telegram credentials should remain outside the backend. Create an ingest token from the backend, run the collector with its own Telethon session, and register chat IDs through `TELEGRAM_CHAT_IDS`. Its local web page at `http://127.0.0.1:8787` accepts the Telegram verification code and two-step password and shows live collector status; set `COLLECTOR_WEB_ENABLED=false` to retain terminal prompts.

For a collector on another machine, point it at the same TLS-protected public
origin used by the browser, for example `BACKEND_URL=https://chat.example.com`.
The direct backend port is intentionally not published. The ingest token must be
created by the same backend user account that signs in to the React frontend,
otherwise registered collector chats belong to a different user and will not
appear there.

See [external_telegram_collector/README.md](external_telegram_collector/README.md) for the collector environment and local run commands.

## Model Services

Mock mode is enabled in `.env.example`:

```env
LLM_MOCK_ENABLED=true
```

This avoids vLLM, reranker, and transcription model calls and is useful for local structural testing.

For real model calls, set `LLM_MOCK_ENABLED=false` and configure these endpoints:

```env
VLLM_TEXT_BASE_URL=http://vllm-text:8000/v1
VLLM_VISION_BASE_URL=http://vllm-vision:8000/v1
VLLM_EMBEDDING_BASE_URL=http://vllm-embedding:8000/v1
VLLM_RERANKER_BASE_URL=http://vllm-reranker:8000/v1
VLLM_API_KEY=<unique-random-value>

TEXT_MODEL=google/gemma-4-E2B-it
VISION_MODEL=google/gemma-4-E2B-it
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
RERANKER_MODEL=Qwen/Qwen3-Reranker-0.6B
```

Optional local embedding and reranking containers can be started with:

```bash
docker compose --profile models up --build
```

The model servers have no host ports, require the configured API key, do not share
the host IPC namespace, do not enable model-repository remote code or usage
tracking, and run on a separate internal network without Internet egress. Pin
`VLLM_IMAGE` by digest and pre-populate the `hf-cache` volume for controlled
deployments before starting the model profile.

The application asks `/v1/models` for `max_model_len` and uses `tiktoken` to split or reject prompts before sending model requests. If an endpoint does not expose context length, provide a JSON override:

```env
PROMPT_LIMIT_MAX_MODEL_LEN_OVERRIDES={"Qwen/Qwen3-Embedding-0.6B":32768}
```

## Optional Services

### Translation

English evidence translation runs only when the per-job `Translate` option is enabled. Media processing runs first, then LibreTranslate converts both message bodies and completed audio/video transcripts to English before chunking. Configure LibreTranslate before enabling it:

```env
LIBRETRANSLATE_BASE_URL=http://libretranslate:5000
LIBRETRANSLATE_API_KEY=
```

The translation target is fixed to English. Translated jobs use only English content for chunking, retrieval, and answer generation. The final report shows English message text by default and provides an in-place original/English toggle so readers can verify a questionable translation. New image/video descriptions are also requested directly in English and use the `neutral-en-v2` prompt cache version.

### Transcription

Audio/video transcription runs when media analysis is enabled and matching media are present. Configure an OpenAI-compatible transcription endpoint:

```env
OPENAI_API_KEY=...
OPENAI_TRANSCRIPTION_BASE_URL=https://api.openai.com/v1
OPENAI_TRANSCRIPTION_MODEL=whisper-1
```

## Development And Tests

```bash
cd backend
pip install -e ".[dev]"
PYTHONPATH=. pytest
```

The React frontend lives in `frontend/` and is built into an Nginx image by Docker Compose. For frontend-only development:

```bash
cd frontend
npm install
npm run dev
```

## Reports

Completed jobs produce a ZIP containing only the static report:

```text
report.zip
  report/
    index.html
    media_gallery.html
    assets/
    questions/
```

Answers and summaries render sanitized Markdown; model-authored HTML is displayed as text so it cannot break the surrounding report. Every page includes a persistent dark/light theme switch, with a Telegram-inspired pale chat canvas and white message bubbles in light mode. Question pages show media inline and expose generated descriptions/transcriptions from a compact info control. Repeated raw chunk text is not included in the rendered pages. The media gallery provides an inline image/video/audio library with fallback cards for files the browser cannot preview.

Original Telegram media is not duplicated in the report ZIP. Previews and links are relative, so the `report/` folder is intended to be extracted next to the original Telegram export files when media references should resolve locally.

For completed upload jobs, **Download all** creates a second archive named `<original-stem>-with-report.zip`. It preserves the original export contents and adds `report/` beside the selected `result.json` or `messages.html`, so the report and its relative media links work after one extraction. Direct Telegram jobs continue to offer the report-only download because they have no original uploaded ZIP.

## Operational Notes

- Capacity checks reject new jobs when required dependencies are unhealthy or configured queue/job thresholds are reached.
- Workers record task attempts and dead letters for permanent failures.
- Stale queued jobs are recovered on backend/worker startup when `RECOVER_STALE_QUEUED_JOBS=true`.
- Media row failures are not job-fatal by default; set `MEDIA_FAIL_JOB_ON_ERROR=true` when any permanent media failure should fail the job.
- Compose keeps vLLM credentials and network access on the worker role; API and scheduler capacity checks therefore cover the data plane but intentionally skip model endpoints.

## Current Limitations

- Resumable browser uploads are not implemented yet; uploads run as one size-capped, authenticated backend streaming request.
- The static report does not yet include full in-report search.
- The Compose `models` profile currently provides embedding and reranking containers only; text and vision generation endpoints must be supplied separately unless mock mode is used.
- GPU placement and memory limits for optional model containers should be configured explicitly before production use.
- The project remains an MVP and should be hardened further before handling sensitive production workloads.
