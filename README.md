# Chat Analyse

Chat Analyse is a Dockerized Python application for analysing Telegram conversations. It can ingest Telegram Desktop ZIP exports or continuously collected Telegram chats, turn messages and media metadata into retrieval-ready chunks, run RAG-based question answering, and package a static HTML report.

The project is still an MVP, but it includes a functional backend, worker pipeline, storage layer, React frontend, Telegram collection flows, and report scheduling.

## Current Capabilities

- Authenticated React web interface for creating and monitoring analysis jobs.
- Two analysis sources:
  - Telegram Desktop ZIP exports uploaded through MinIO presigned PUTs or backend streaming.
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
- Report-only ZIP download containing the static `report/` folder without original Telegram media.
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
  optional translation
  -> media inventory / optional media description
  -> optional audio/video transcription
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
docker compose up --build
```

After startup:

- Frontend: http://localhost:3000
- Backend API and legacy static UI: http://localhost:8000
- API documentation: http://localhost:8000/docs
- MinIO Console: http://localhost:9001
- Qdrant: http://localhost:6333
- NATS client URL: `nats://localhost:4222`
- NATS monitoring: http://localhost:8222

The initial admin user is created from `.env`:

```env
BOOTSTRAP_ADMIN_EMAIL=admin@example.local
BOOTSTRAP_ADMIN_PASSWORD=change-me
```

## Analysis Sources

### ZIP Exports

Use the main `New Analysis` screen to upload a Telegram Desktop ZIP export in JSON or HTML format. The browser uses a direct MinIO upload when possible and falls back to backend streaming for larger files.

### Backend Telegram Collection

Use `Telegram Setup` in the frontend to connect a Telegram account with credentials from `my.telegram.org`, load available groups/channels, and select chats to collect. The backend stores Telegram API credentials and session data encrypted at rest, then syncs active chats at the selected interval.

Completed syncs continue from the highest stored Telegram message ID instead of
rescanning a time overlap. Long syncs use progress-based inactivity limits:
`TELEGRAM_SYNC_INACTIVITY_TIMEOUT_SECONDS` and
`TELEGRAM_EXTERNAL_INACTIVITY_TIMEOUT_SECONDS` both default to 900 seconds.
The former `TELEGRAM_SYNC_TIMEOUT_SECONDS` and
`TELEGRAM_EXTERNAL_COVERAGE_WAIT_SECONDS` names remain accepted as deprecated
fallbacks.

Set a dedicated Fernet key in production:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```env
TELEGRAM_CREDENTIALS_ENCRYPTION_KEY=replace-with-generated-key
```

When omitted in local development, a stable key is derived from `SECRET_KEY`.

### External Telegram Collector

Use `external_telegram_collector/` when Telegram credentials should remain outside the backend. Create an ingest token from the backend, run the collector with its own Telethon session, and register chat IDs through `TELEGRAM_CHAT_IDS`.

For a collector on another machine, point it at the backend API, for example `BACKEND_URL=http://192.168.0.151:8000`. Open the React frontend in the browser at `http://192.168.0.151:3000`; `http://192.168.0.151:8000` is the backend API and legacy static UI. The ingest token must be created by the same backend user account that signs in to the React frontend, otherwise registered collector chats belong to a different user and will not appear there.

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
VLLM_API_KEY=local-key

TEXT_MODEL=google/gemma-4-E2B-it
VISION_MODEL=google/gemma-4-E2B-it
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
RERANKER_MODEL=Qwen/Qwen3-Reranker-0.6B
```

Optional local embedding and reranking containers can be started with:

```bash
docker compose --profile models up --build
```

The application asks `/v1/models` for `max_model_len` and uses `tiktoken` to split or reject prompts before sending model requests. If an endpoint does not expose context length, provide a JSON override:

```env
PROMPT_LIMIT_MAX_MODEL_LEN_OVERRIDES={"Qwen/Qwen3-Embedding-0.6B":32768}
```

## Optional Services

### Translation

Message translation runs only when the per-job `Translate` option is enabled. Configure LibreTranslate before enabling it:

```env
LIBRETRANSLATE_BASE_URL=http://libretranslate:5000
LIBRETRANSLATE_API_KEY=
LIBRETRANSLATE_TARGET_LANGUAGE=en
```

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
    assets/
    questions/
```

Original Telegram media is not included in the report ZIP. Media links are relative, so the `report/` folder is intended to be extracted next to the original Telegram export files when media references should resolve locally.

## Operational Notes

- Capacity checks reject new jobs when required dependencies are unhealthy or configured queue/job thresholds are reached.
- Workers record task attempts and dead letters for permanent failures.
- Stale queued jobs are recovered on backend/worker startup when `RECOVER_STALE_QUEUED_JOBS=true`.
- Media row failures are not job-fatal by default; set `MEDIA_FAIL_JOB_ON_ERROR=true` when any permanent media failure should fail the job.
- vLLM health checks are optional; enable them with `CAPACITY_CHECK_VLLM=true` and require them with `CAPACITY_REQUIRE_VLLM=true`.

## Current Limitations

- Resumable browser uploads are not implemented yet; uploads still run as one browser request, using backend streaming above the direct PUT size range.
- The static report does not yet include full in-report search.
- The Compose `models` profile currently provides embedding and reranking containers only; text and vision generation endpoints must be supplied separately unless mock mode is used.
- GPU placement and memory limits for optional model containers should be configured explicitly before production use.
- The project remains an MVP and should be hardened further before handling sensitive production workloads.
