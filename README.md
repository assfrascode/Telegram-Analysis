# Chat Analyse

Chat Analyse is a Dockerized Python application for analysing Telegram chat exports. It provides an end-to-end pipeline for ingesting Telegram ZIP exports, parsing messages and media metadata, creating retrieval-ready chunks, running RAG-based question answering, and generating a static HTML report.

The project is currently an MVP with a functional backend, worker pipeline, storage layer, and web frontend.

## Current capabilities

- Authenticated web interface for creating and monitoring analysis jobs.
- Telegram ZIP upload with browser-side progress, using direct object-store upload when possible and backend streaming for larger files.
- Secure ZIP validation, extraction, Telegram `result.json` parsing, and media inventory creation.
- Optional media analysis through an OpenAI-compatible vLLM vision endpoint.
- Chunking, embedding, Qdrant retrieval, reranking, answer generation, and report rendering.
- Saved question sets for reusable analysis templates.
- Live job monitoring through WebSocket events with polling fallback.
- Job history, pipeline stage view, event log filtering, cancellation, capacity checks, and dead-letter visibility.
- Report-only ZIP download containing the static `report/` folder without original Telegram media.
- Mock LLM mode for local development without GPU-backed model services.

## Architecture

| Component | Purpose |
| --- | --- |
| FastAPI | API, authentication, upload preparation/verification, backend upload fallback, job control, report download |
| PostgreSQL | Persistent state, ownership checks, audit trail, jobs, events, worker metadata |
| NATS JetStream | Worker task queue and live progress events |
| MinIO | Uploaded ZIPs, extracted exports, intermediate files, final report artefacts |
| Qdrant | Vector storage for message chunks |
| Workers | Ingest, parsing, media analysis, chunking, embedding, retrieval, reranking, answering, reporting |
| React/Vite | Browser frontend served by Nginx |

## Pipeline

```text
Upload
  -> ZIP validation
  -> extraction
  -> Telegram parsing
  -> media inventory / optional media analysis
  -> chunking
  -> embedding
  -> retrieval
  -> reranking
  -> answer generation
  -> static report rendering
```

Each job is owned by a user. Uploads, jobs, events, reports, dead letters, and saved question sets are checked through ownership-aware access control. Foreign resource IDs return `404 Resource not found` to avoid cross-user enumeration.

## Local setup

```bash
cp .env.example .env
docker compose up --build
```

After startup:

- Frontend and API: http://localhost:8000
- API documentation: http://localhost:8000/docs
- MinIO Console: http://localhost:9001
- Qdrant: http://localhost:6333
- NATS: nats://localhost:4222

The initial admin user is created from `.env`:

```env
BOOTSTRAP_ADMIN_EMAIL=admin@example.local
BOOTSTRAP_ADMIN_PASSWORD=change-me
```

## Development and tests

```bash
cd backend
pip install -e ".[dev]"
PYTHONPATH=. pytest
```

For local pipeline testing without external model services, enable mock mode:

```env
LLM_MOCK_ENABLED=true
```

For real model calls, disable mock mode and point the application to compatible vLLM endpoints for vision, embeddings, reranking, and text generation.

Telegram account sessions and user-provided API hashes are encrypted at rest. Set
a dedicated Fernet key in production:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

```env
TELEGRAM_CREDENTIALS_ENCRYPTION_KEY=replace-with-generated-key
```

When omitted in local development, a stable key is derived from `SECRET_KEY`.

Optional local embedding and reranking containers can be started with:

```bash
docker compose --profile models up --build
```

## Frontend

The frontend is implemented with React/Vite in `frontend/`, with a legacy static version in `backend/app/static/app`. It supports login/logout, ZIP upload to MinIO or through backend streaming, saved question sets, question editing, job creation, live monitoring, capacity checks, cancellation, dead-letter inspection, and authenticated report downloads.

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

## Current limitations

- Resumable browser uploads are not implemented yet; uploads still run as one browser request, using backend streaming above the direct PUT size range.
- The static report does not yet include full in-report search.
- GPU placement for optional model containers should be configured explicitly before production use.
- The project remains an MVP and should be hardened further before handling sensitive production workloads.
