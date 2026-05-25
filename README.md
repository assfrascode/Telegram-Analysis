# Chat Analyse MVP

Startfähiges Fundament für eine mehrbenutzerfähige Python-Anwendung zur Analyse von Telegram-Chat-Exports.

## Enthalten

- FastAPI Backend
- PostgreSQL als Source of Truth
- NATS JetStream für Task-Queue und Progress-Events
- MinIO für Uploads, extrahierte Telegram-Exports und Report-Artefakte
- Qdrant als Vektordatenbank
- Worker für Ingest, Parsing, Media Analysis, Chunking, Embedding, RAG und Report Rendering
- Plain HTML/CSS/JS Frontend
- Report-only Download: Das finale Artefakt enthält nur den `report/`-Ordner, keine Originalmedien

## MVP-Prinzipien

- PostgreSQL hält persistenten Zustand, Recovery-Informationen und Audit-Trail.
- NATS JetStream verteilt Arbeit und sendet Live-Events.
- MinIO hält große Dateien, extrahierte Export-Dateien und finale Artefakte.
- Qdrant hält Chunk-Vektoren und Payload-Metadaten.
- Jeder Worker ist idempotent und prüft den Postgres-Zustand vor Arbeit.
- Jobs können abgebrochen und nach Fehlern wieder aufgenommen werden.

## Lokaler Start

```bash
cp .env.example .env
docker compose up --build
```

Danach:

- Frontend/Backend: http://localhost:8000
- API Docs: http://localhost:8000/docs
- MinIO Console: http://localhost:9001
- Qdrant: http://localhost:6333
- NATS: nats://localhost:4222

## Tests

```bash
cd backend
pip install -e ".[dev]"
PYTHONPATH=. pytest
```

## Login

Beim ersten Start wird ein Admin-User aus `.env` angelegt:

```text
BOOTSTRAP_ADMIN_EMAIL=admin@example.local
BOOTSTRAP_ADMIN_PASSWORD=change-me
```

## Upload-Fluss

Der Upload ist zweiphasig, damit große ZIPs nicht durch FastAPI gestreamt werden müssen:

1. `POST /uploads` erzeugt einen MinIO Presigned PUT.
2. Browser lädt ZIP direkt nach MinIO hoch.
3. `POST /uploads/{upload_id}/complete` markiert den Upload als vollständig.
4. `POST /jobs` startet den Analysejob für diesen Upload.

## Telegram Parser und Media Inventory

Der Ingest-Teil ist jetzt konkret umgesetzt:

1. ZIP wird aus MinIO in ein temporäres File gespult.
2. ZIP Central Directory wird geprüft:
   - keine absoluten Pfade
   - kein `../` / Zip Slip
   - keine Symlinks oder Spezialdateien
   - Limits für Dateianzahl, Einzelgröße und entpackte Gesamtgröße
   - einfache Kompressionsratio-Prüfung gegen Zip-Bomb-Muster
3. Dateien werden in den Job-Extract-Prefix in MinIO geschrieben.
4. `result.json` wird gesucht.
5. Telegram-Nachrichten werden geparst und gespeichert:
   - Message-ID
   - Timestamp
   - User/Sender
   - Reply-Bezug
   - Forwarded-from
   - Reactions
   - Edited timestamp
   - Roh-JSON
6. Medieninventar wird aus `photo` und `file`-Feldern gebaut:
   - Bilder
   - Videos
   - fehlende/nicht exportierte Medien
   - unsichere Medienpfade
   - nach Extraktion fehlende Dateien


## Report Rendering

Der `report-worker` hydratisiert die Evidenzliste jetzt vollständig:

1. Er lädt pro Frage den neuesten `question_run`.
2. Er lädt nur `retrieval_hits.used_in_answer=true`.
3. Er joint die zugehörigen `message_chunks`.
4. Er löst `message_chunks.message_ids` zurück auf die vollständigen `telegram_messages` auf.
5. Er lädt je Originalnachricht die zugehörigen `telegram_media` inklusive passender `media_analysis`.
6. Er rendert pro Frage einen Subreport mit Antwort, Evidenz-Chunks, vollständigen Originalnachrichten, Timestamps, msgIDs, Userdaten, Medienstatus, Medienbeschreibung und relativen Medienlinks.

Der ZIP-Download enthält weiterhin nur den Ordner `report/`, keine Originalmedien.

## Report-Download

Der Download liefert nur:

```text
report.zip
  report/
    index.html
    assets/
      report.css
      report.js
    questions/
      q_001.html
      q_002.html
```

Der Nutzer entpackt den Ordner `report/` manuell in den ursprünglichen Telegram-Export-Ordner. Medienlinks in Subreports sind relativ auf diesen Zielort ausgelegt.

## Medienanalyse mit vLLM

Der `media-worker` ist jetzt produktiv angebunden:

1. Er liest `TelegramMedia`-Rows mit Status `pending`, `running` oder `failed_retryable`.
2. Er lädt Medien aus MinIO.
3. Er sendet Bilder als `image_url` und Videos als `video_url` an den OpenAI-kompatiblen vLLM Vision-Endpunkt.
4. Er speichert neutrale Beschreibungen in `media_analysis`.
5. Er setzt `telegram_media.status` auf `completed`, `failed_retryable` oder `failed_permanent`.
6. Er sendet Fortschrittsevents pro Medium.
7. Er startet erst danach `jobs.chunk.create`.

Wichtige Konfiguration:

```text
MEDIA_ANALYSIS_CONCURRENCY=4
MEDIA_ANALYSIS_BATCH_SIZE=100
MEDIA_ANALYSIS_PROMPT_VERSION=neutral-v1
MEDIA_ANALYSIS_TRANSPORT=data_url
MAX_INLINE_MEDIA_ANALYSIS_BYTES=268435456
MAX_MEDIA_ANALYSIS_ATTEMPTS=3
VLLM_MEDIA_REQUEST_TIMEOUT_SECONDS=300
```

`MEDIA_ANALYSIS_TRANSPORT=data_url` ist für lokale Tests am einfachsten, kann aber große Videos nicht sinnvoll transportieren. Wenn dein vLLM-Server den MinIO-Host aus dem Backend-Netzwerk erreichen kann, setze stattdessen:

```text
MEDIA_ANALYSIS_TRANSPORT=internal_presigned_url
```

Dann übergibt der Worker eine interne signierte MinIO-URL an vLLM, statt die Datei base64-inline in den Request zu packen.

## Chunking

Der `chunk-worker` ist jetzt produktiv umgesetzt:

1. Er liest alle `telegram_messages` eines Jobs chronologisch.
2. Er lädt zugehörige `telegram_media`-Zeilen inklusive passender `media_analysis`.
3. Er rendert jede Nachricht als retrieval-fähigen Textblock mit:
   - Timestamp
   - Telegram-Message-ID
   - User/Sender
   - Reply-Bezug
   - Forwarded-from
   - Edited timestamp
   - Reactions
   - Originaltext
   - `IMAGE_DESCRIPTION:` / `VIDEO_DESCRIPTION:`
   - expliziten Missing-Media-Markierungen
4. Er erzeugt chronologische Chunks mit konfigurierbarer Zielgröße und Message-Overlap.
5. Er speichert `message_chunks` inklusive Hash, Message-IDs, Zeitraum und Payload-Metadaten.

Wichtige Konfiguration:

```text
CHUNK_TARGET_CHARS=8000
CHUNK_OVERLAP_MESSAGES=2
```

## LLM-Mock-Modus

Für schnelle lokale Pipeline-Tests ohne vLLM/GPU-Server gibt es jetzt einen Mock-Modus:

```text
LLM_MOCK_ENABLED=true
MOCK_EMBEDDING_DIMENSIONS=64
```

Wenn `LLM_MOCK_ENABLED=true` gesetzt ist:

- `media-worker` erzeugt Medienbeschreibungen ohne MinIO-Download und ohne vLLM-Request.
- `VLLMGateway.chat_completion()` liefert Mock-Antworten.
- `VLLMGateway.answer_question()` liefert Mock-Antworten.
- `EmbeddingClient` liefert deterministische Mock-Vektoren.
- `RerankerClient` liefert einfache lexikalische Mock-Scores.

Für echte Modellaufrufe:

```text
LLM_MOCK_ENABLED=false
```


## Embedding und Qdrant-Upsert

Der `embedding-worker` ist jetzt produktiv umgesetzt:

1. Er liest alle `message_chunks` eines Jobs chronologisch.
2. Er erzeugt Embeddings über den `EmbeddingClient`.
   - Im Mock-Modus deterministisch lokal.
   - Im Real-Modus über `/v1/embeddings` am vLLM-Embedding-Server.
3. Er erstellt die Qdrant-Collection bei Bedarf mit der erkannten Vektordimension.
4. Er löscht vor einem Job-Rebuild alte Qdrant-Punkte desselben Jobs.
5. Er upsertet pro Chunk einen Qdrant-Punkt mit Payload-Metadaten.
6. Er schreibt `embedding_model`, `embedding_hash`, `qdrant_point_id` und `embedded_at` zurück nach Postgres.
7. Er sendet `embedding.progress`-Events pro Batch.

Wichtige Konfiguration:

```text
QDRANT_URL=http://qdrant:6333
QDRANT_COLLECTION=chat_chunks
EMBEDDING_BATCH_SIZE=64
```

Wenn du zwischen Mock-Embeddings und echten Qwen-Embeddings wechselst, haben die Vektoren unterschiedliche Dimensionen. Nutze dafür entweder unterschiedliche Collections, z. B.:

```text
QDRANT_COLLECTION=chat_chunks_mock
QDRANT_COLLECTION=chat_chunks_qwen3
```

oder lösche die alte Collection in Qdrant.


## Retrieval

Der `retrieve-worker` ist jetzt produktiv umgesetzt:

1. Er liest die Fragen eines Jobs chronologisch aus Postgres.
2. Er verwendet den vom Nutzer gesetzten Wert `options.retrieval_k` aus dem Job-Payload. `DEFAULT_RETRIEVAL_K` ist nur Fallback für alte/fehlerhafte lokale Testjobs.
3. Er embedet jede Frage über den `EmbeddingClient`.
4. Er sucht pro Frage in Qdrant mit Filter `job_id = <job_id>`.
5. Er speichert pro Frage einen `question_runs`-Datensatz.
6. Er speichert die gefundenen Chunk-Treffer in `retrieval_hits` inklusive Rang und Qdrant-Score.
7. Er sendet `retrieval.progress`-Events pro Frage.

Wichtig: Das Retrieval nutzt nicht fest Top 50, sondern den Nutzerwert aus dem Frontend:

```text
options.retrieval_k
```

Der Default von 50 bleibt nur die Voreinstellung im UI/API-Schema.

## Nächste Implementierungsschritte

1. Authentifizierung und Job-Isolation härten.
2. Upload-Handling für 50-GB-Produktivbetrieb auf resumable/multipart Upload erweitern.
3. Optional: Report-Suche innerhalb des statischen HTML-Reports ergänzen.


### Hinweis zum lokalen Upload

Die erste Parser-Version gab dem Browser eine MinIO-Presigned-URL mit dem Docker-internen Host `minio:9000`. Dieser Host ist aus dem Browser nicht auflösbar. Diese Version nutzt im Frontend deshalb standardmäßig den Backend-Upload-Endpunkt `/uploads/{upload_id}/content`. Die Presigned-URL bleibt im API-Response vorhanden, ist aber für eine spätere direkte MinIO/CORS-Variante gedacht.

## Optionale vLLM-Container für Embedding und Reranking

Die `docker-compose.yml` enthält jetzt zwei optionale GPU-Services hinter dem Profil `models`:

```text
vllm-embedding -> Qwen/Qwen3-Embedding-0.6B -> http://vllm-embedding:8000/v1
vllm-reranker  -> Qwen/Qwen3-Reranker-0.6B  -> http://vllm-reranker:8000/v1
```

Für schnelle Pipeline-Tests bleibt der Mock-Modus aktiv:

```text
LLM_MOCK_ENABLED=true
```

Für echte Modellaufrufe:

```bash
# .env anpassen
LLM_MOCK_ENABLED=false
EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
RERANKER_MODEL=Qwen/Qwen3-Reranker-0.6B
VLLM_EMBEDDING_BASE_URL=http://vllm-embedding:8000/v1
VLLM_RERANKER_BASE_URL=http://vllm-reranker:8000/v1

# inklusive Modellcontainer starten
docker compose --profile models up --build
```

Die Modellserver werden extern zusätzlich auf Host-Ports veröffentlicht:

```text
Embedding: http://localhost:8011/v1
Reranker:  http://localhost:8012/v1
```

Beide Container teilen sich den Docker-Volume-Cache `hf-cache`, damit Hugging-Face-Downloads nicht bei jedem Rebuild neu geladen werden. Falls das Modell einen Token benötigt, setze `HF_TOKEN` in `.env`.

Hinweis: Die beiden Services nutzen `gpus: all`. Bei mehreren GPUs sollte die GPU-Zuweisung später explizit pro Service eingeschränkt werden, z. B. über Compose-Overrides oder `NVIDIA_VISIBLE_DEVICES`.


## Reranking worker update

The reranking worker now loads `retrieval_hits` per `question_run`, fetches the corresponding chunk texts, calls the configured reranker or mock reranker, writes `rerank_rank`/`rerank_score`, and marks only the user-configured `rerank_k` top hits as `used_in_answer=true`. The next stage can therefore answer strictly from the persisted evidence set.

<!-- reranking-worker now marks used_in_answer -->

## Answer worker update

The answer worker is now productive:

1. It loads only `retrieval_hits.used_in_answer=true` for each `question_run`.
2. It joins the corresponding `message_chunks`.
3. It renders a deterministic evidence context with chunk id, chunk index, message ids and time range.
4. It builds a question-specific answer prompt.
5. It calls the configured text model through `VLLMGateway.answer_prompt()` or returns deterministic mock answers when `LLM_MOCK_ENABLED=true`.
6. It stores `question_runs.answer` and `question_runs.short_answer`.
7. It stores audit metadata in `question_runs.raw_response["answer"]`, including evidence chunk ids and prompt/context lengths.
8. It emits `answer.progress` per question and enqueues `jobs.report.render` when done.

If no chunks are marked as `used_in_answer=true`, the worker does not call the model and stores an explicit no-evidence answer.

New setting:

```env
ANSWER_CONTEXT_MAX_CHARS=120000
```

<!-- answer-worker now stores answer and short_answer -->

## Cancellation, Retry, Capacity und Dead-Letter

Die Worker-Basisklasse vereinheitlicht jetzt das Fehlerverhalten aller Pipeline-Schritte:

- leere NATS-Pull-Fetches sind Idle-Zustand, kein Fehler,
- retryable Fehler werden bis `MAX_WORKER_TASK_ATTEMPTS` erneut zugestellt,
- permanente Fehler oder überschrittene Retries werden als Dead Letter persistiert,
- Dead-Letter-Events werden zusätzlich nach `dlq.>` in JetStream publiziert,
- Jobs werden bei permanenten Worker-Fehlern auf `failed` gesetzt,
- Cancel-Anfragen erzeugen `job.cancel.requested` und danach garantiert `job.cancelled`, damit WebSocket-Clients auch dann ein finales Event sehen, wenn gerade kein Worker aktiv ist.
- Worker laden Job-Status bei jedem Cancel-Checkpoint frisch aus PostgreSQL (`populate_existing=True`), damit API-seitige Cancels nicht durch SQLAlchemy-Identity-Map-Caching verborgen bleiben.
- Worker prüfen Cancellation vor Task-Start, zwischen Batches, nach langen Modell-/Qdrant-Aufrufen und direkt vor dem Publizieren des nächsten Pipeline-Subjects.
- `Worker.enqueue()` blockiert Pipeline-Fortsetzung für `cancelling`, `cancelled`, `failed` und `completed`.
- Pending Tasks bereits abgebrochener Jobs werden geacked und als `skipped` markiert, statt erneut zugestellt zu werden.

Neue Endpunkte:

```text
GET  /capacity
GET  /jobs/capacity
POST /jobs/{job_id}/cancel
GET  /jobs/{job_id}/dead-letters
```

`/capacity` und `/jobs/capacity` liefern denselben Snapshot. Der Top-Level-Endpunkt ist für das Frontend gedacht; `/jobs/capacity` bleibt als rückwärtskompatibler Debug-Endpunkt erhalten.

Neue relevante Settings:

```env
MAX_PENDING_WORKER_TASKS=50000
MAX_FAILED_RETRYABLE_TASKS=5000
MAX_WORKER_TASK_ATTEMPTS=3
WORKER_RETRY_BASE_DELAY_SECONDS=10
WORKER_RETRY_MAX_DELAY_SECONDS=300
```

`POST /jobs` blockiert neue Jobs, wenn das System nicht bereit ist. HTTP 429 wird für Kapazitätsgrenzen genutzt, HTTP 503 für nicht erreichbare Pflichtdienste. Geprüft werden:

- PostgreSQL: `SELECT 1` und danach Zählqueries für Jobs/Tasks.
- MinIO: Bucket-Erreichbarkeit über `bucket_exists`.
- NATS JetStream: Verbindung, Stream-Erreichbarkeit und Task-Stream-Message-Zahl.
- Qdrant: REST-Readiness beziehungsweise `/collections`-Fallback.
- vLLM: optional über `/v1/models`; in `LLM_MOCK_ENABLED=true` wird dieser Check bewusst übersprungen.

Neue Backpressure-Settings:

```env
MAX_NATS_TASK_STREAM_MESSAGES=100000
CAPACITY_HEALTH_TIMEOUT_SECONDS=2
CAPACITY_REQUIRE_POSTGRES=true
CAPACITY_REQUIRE_MINIO=true
CAPACITY_REQUIRE_NATS=true
CAPACITY_REQUIRE_QDRANT=true
CAPACITY_CHECK_VLLM=false
CAPACITY_REQUIRE_VLLM=false
```

Das Frontend prüft `/capacity` nach Login und direkt vor dem Upload. Dadurch wird ein großer ZIP-Upload vermieden, wenn das Backend ohnehin keine neuen Jobs akzeptiert.

## Job cancellation update

Cancellation is now enforced across the whole pipeline:

```text
POST /jobs/{job_id}/cancel
  -> job.cancel.requested event
  -> job.status=cancelled
  -> job.cancelled event
  -> running workers notice cancellation at fresh DB checkpoints
  -> current task becomes skipped
  -> next pipeline subject is not published
```

The frontend includes a basic “Job abbrechen” button. The WebSocket stream replays stored events and keeps listening after keepalive timeouts, so `job.cancelled` is visible even after reconnect.

## Retry/dead-letter hardening update

Retry handling is now task-type aware instead of one global limit only. The worker layer uses subject-specific defaults and optional JSON overrides from `WORKER_TASK_MAX_ATTEMPTS_BY_SUBJECT`.

Default max attempts:

```text
jobs.ingest.validate   1
jobs.ingest.extract    1
jobs.telegram.parse    1
jobs.media.describe    1
jobs.chunk.create      2
jobs.embedding.create  3
jobs.question.retrieve 3
jobs.question.rerank   3
jobs.question.answer   3
jobs.report.render     2
```

Every permanent worker failure now produces:

```text
worker_tasks.status = failed_permanent
worker_dead_letters row
worker.task.dead_letter job event
dlq.<original-subject> JetStream message
```

Retryable failures below the subject-specific max attempts produce:

```text
worker_tasks.status = failed_retryable
worker.task.retrying job event
NATS nak(delay)
```

The frontend now shows the current job status, job error message and latest dead-letter details through `/jobs/{job_id}` and `/jobs/{job_id}/dead-letters`.

Media analysis has separate row-level retry behavior. By default failed media do **not** fail the whole job; they remain visible in the report as failed/missing evidence. Set this to fail the job on any permanent media failure:

```env
MEDIA_FAIL_JOB_ON_ERROR=true
```

## Auth/access hardening update

All user-facing resources are now checked through owned-resource helpers rather than ad-hoc route queries:

```text
Upload access   -> uploads.owner_user_id == current_user.id
Job access      -> jobs.owner_user_id == current_user.id
Report access   -> reports.job_id -> jobs.owner_user_id == current_user.id
Event access    -> job owner check + job_events.owner_user_id filter
WebSocket       -> token auth + owned job check before replay/subscribe
Dead letters    -> owned job check before listing
```

Foreign resource IDs deliberately return `404 Resource not found` instead of `403`, so IDs cannot be enumerated across users.

Report downloads are now proxied through the backend and require the Bearer token. The frontend fetches the ZIP with the Authorization header and creates a local browser download; it no longer uses an unauthenticated `<a href>` to a protected route.

Direct/presigned MinIO uploads remain available for later deployment variants, but `/uploads/{id}/complete` now verifies that the object exists and that the stored size matches the declared upload size before marking it as uploaded. Backend-mediated uploads perform the same verification after writing to MinIO.

## Frontend-Ausbau

Das Plain-HTML/CSS/JS-Frontend wurde für den MVP-Betrieb ausgebaut:

- Session-Status mit Login/Logout.
- Kapazitätsanzeige über `GET /capacity` inklusive Ressourcenstatus.
- Job-Start mit JSON-Validierung für den Fragenkatalog.
- Browserseitiger Upload-Fortschritt für den bestehenden Backend-Upload-Endpunkt.
- Jobliste über `GET /jobs`.
- Aktiver Job mit Status, WebSocket-Verbindung, Cancel und Report-Download.
- Pipeline-Stufenansicht aus Live-Events und Polling-Fallback.
- Event-Log mit Level-Filter.
- Dead-Letter- und Fehleranzeige im Jobstatus.
- Reconnect-Logik für WebSockets.

Der große resumable/multipart Upload-Pfad ist bewusst noch nicht umgesetzt. Das Frontend nutzt weiterhin den bestehenden Backend-Upload-Endpunkt `/uploads/{upload_id}/content`, zeigt dafür aber Upload-Fortschritt über `XMLHttpRequest.upload.onprogress`.

## Frontend-Refresh

Die Plain-HTML/CSS/JS-Oberfläche wurde visuell überarbeitet, ohne Backend-API-Verträge zu ändern.

Ergänzt wurden:

- Hero-Bereich mit System-Chips.
- Glasmorphes Zwei-Spalten-Layout mit responsiver Stapelung.
- Drag-&-Drop-Zone für ZIP-Uploads.
- Anschaulichere Kapazitäts- und Ressourcenanzeige.
- Job-Dashboard mit Pipeline-Fortschritt, aktueller Phase, Event-Zähler und Dead-Letter-Zähler.
- Visuelle Pipeline-Stufen mit Nummerierung, Running-, Completed- und Failed-Zuständen.
- Modernisierte Jobliste, Event-Log, Buttons, Badges und Formularflächen.

Die Oberfläche bleibt weiterhin frameworkfrei und nutzt nur `/static/index.html`, `/static/style.css` und `/static/app.js`.

## Saved Question Sets

The frontend now supports reusable question catalogues. A user can save the current question fields as a named set, load an existing set into the form, update it, duplicate it, or archive it.

Backend endpoints:

```text
GET    /question-sets
POST   /question-sets
GET    /question-sets/{question_set_id}
PATCH  /question-sets/{question_set_id}
DELETE /question-sets/{question_set_id}
POST   /question-sets/{question_set_id}/duplicate
```

Question sets are user-owned. Foreign IDs return `404 Resource not found` like jobs/uploads. When a set is used for a job, the job still receives concrete question rows. This freezes the questions for that job even if the saved set is later changed.

`POST /jobs` remains backward compatible with explicit `questions`, and now also accepts optional `question_set_id`. If both are provided, the explicit questions are used while the set ID/name are stored as a snapshot in `job.options.question_set` for auditability.
