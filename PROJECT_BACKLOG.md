# Chat Analyse: ideas and todos

Reviewed against the repository on 2026-09-17. This is a proposed backlog, not a release commitment. The correctness findings below come from code inspection and still need targeted reproduction. Check an item off when its completion criteria are met.

The project already supports ZIP and collected-chat analysis, saved question sets, scheduled reports, translation, media processing, authenticated downloads, retries, migrations, and operational metrics. The work below builds on those capabilities.

## P1 — Fix correctness gaps and make changes repeatable

- [ ] **T01 — Prevent duplicate analysis submissions.** Telegram submissions set `busy`, but the start button only checks `uploadInProgress`, which stays false for this source.
  **Done when:** both source modes reject repeat submission while pending; a rapid double-click creates one request; failure makes the form usable again.
  **Start in:** [App.jsx](frontend/src/App.jsx), [CreateJobPanel.jsx](frontend/src/components/CreateJobPanel.jsx).

- [ ] **T02 — Isolate monitoring state when switching jobs.** Status and event requests can finish after the user selects another job and still update shared state.
  **Done when:** responses from a previous job or login session are cancelled or ignored; delayed requests cannot mix job status, events, or event cursors after switching jobs or signing out.
  **Start in:** [App.jsx](frontend/src/App.jsx), [API client](frontend/src/api/client.js), [socket hook](frontend/src/hooks/useJobSocket.js).

- [ ] **T03 — Verify worker ownership during redelivery and long batches.** Workers fetch multiple tasks, process them sequentially, and heartbeat the currently executing message; inspect whether waiting messages can be redelivered and processed concurrently.
  **Done when:** a long first task, two workers, and a worker restart cannot cause concurrent execution of the same task or duplicate downstream work; abandoned claims recover automatically.
  **Start in:** [worker base](backend/app/workers/base.py), [worker control](backend/app/services/worker_control.py), [worker tests](backend/tests/test_worker_base.py).

- [ ] **T04 — Retry failed collected-media downloads independently.** A completed sync can advance its message cursor past messages whose attachments failed to download.
  **Done when:** a transient attachment failure is retried without requiring a new message or a full history rescan; permanent failures remain visible; equivalent behavior is checked for backend and external collection.
  **Start in:** [Telegram sync](backend/app/services/telegram_sync.py), [external collector](external_telegram_collector/collector.py).

- [ ] **T05 — Reconcile deployment documentation and configuration.** README and `.env.example` describe AIStor, `MINIO_IMAGE`, and a license mount, while Compose currently uses a fixed `minio/minio` image without that mount. Image override documentation also needs checking against actual service definitions.
  **Done when:** the intended storage deployment is consistent across Compose, environment examples, and setup instructions; a clean-start walkthrough works using those instructions.
  **Start in:** [README](README.md), [environment example](.env.example), [Compose](docker-compose.yml).

- [ ] **T06 — Make dependency installation reproducible.** Direct dependencies are pinned, but transitive locks are absent. The frontend explicitly documents an earlier lockfile problem involving an inaccessible registry.
  **Done when:** backend and collector installs use reviewed locks with hashes; the frontend has a lock using the intended public registry and installs with `npm ci`; release records include resolved container digests while preserving the documented image policy.
  **Start in:** [backend dependencies](backend/pyproject.toml), [collector dependencies](external_telegram_collector/requirements.txt), [frontend build](frontend/Dockerfile), [frontend README](frontend/README.md).

- [ ] **T07 — Automate the existing checks in CI.** There is a substantial Python test suite, but no CI workflow is present in this checkout.
  **Done when:** changes run backend and external-collector tests, a frontend production build, and migration integration checks using an isolated disposable database; documented commands use the correct working directories.
  **Start in:** [backend tests](backend/tests), [collector tests](external_telegram_collector/tests), [migration integration test](backend/tests/test_migrations_integration.py), [frontend scripts](frontend/package.json).

- [ ] **T08 — Add browser regression tests for complete user journeys.** Existing frontend checks include source-text assertions, and the frontend has no executable browser test suite.
  **Done when:** a repeatable mock-mode suite covers login expiry, both analysis sources, switching jobs, WebSocket fallback, retry/cancel, schedule editing, and both authenticated downloads. Include the regressions in T01 and T02.
  **Start in:** [frontend package](frontend/package.json), [existing frontend checks](backend/tests/test_telegram_frontend.py), [download checks](backend/tests/test_frontend_download_auth.py).

## P2 — Improve data reliability and daily use

- [ ] **T09 — Reconcile historical Telegram edits and deletions.** Forward sync intentionally reads newer message IDs; that alone cannot refresh older messages that change.
  **Done when:** a bounded reconciliation or update-event path handles edits and deletion markers in both collection modes, invalidates affected derived data, and states how already generated reports remain snapshots. Keep normal incremental sync efficient.
  **Start in:** [Telegram sync](backend/app/services/telegram_sync.py), [ingest service](backend/app/services/telegram_ingest.py), [external collector](external_telegram_collector/collector.py).

- [ ] **T10 — Show report completeness explicitly.** Partial collection is supported, but readers of the downloaded report need to understand the evidence available when it was generated.
  **Done when:** reports show requested and available time coverage, collection freshness, skipped or failed media processing, and a clear partial-data notice when applicable; the information remains available offline.
  **Start in:** [snapshot worker](backend/app/workers/telegram_snapshot_worker.py), [report worker](backend/app/workers/report_worker.py), [report templates](backend/app/templates/report).

- [ ] **T11 — Validate answer citations and link them to evidence.** Evidence metadata and message anchors already exist; extend them with a validated citation contract for generated answers.
  **Done when:** citations resolve only to supplied evidence, unknown references are flagged, and readers can jump from a claim to its supporting message. Cover direct and map/reduce answers.
  **Start in:** [answer generation](backend/app/services/answer_generation.py), [RAG worker](backend/app/workers/rag_worker.py), [question report](backend/app/templates/report/subreport.html.j2).

- [ ] **T12 — Establish a retrieval and answer-quality benchmark.** Structural tests exist; add a fixed corpus to measure whether pipeline changes improve actual answers.
  **Done when:** synthetic or explicitly approved fixtures cover multilingual messages, exact names, reply context, contradictory statements, and questions with no evidence; runs record retrieval recall, citation validity, abstention, and latency alongside model/configuration versions.
  **Start in:** [RAG worker](backend/app/workers/rag_worker.py), [answer tests](backend/tests/test_answer_generation.py), [map/reduce tests](backend/tests/test_answer_worker_map_reduce.py).

- [ ] **T13 — Add job deletion and configurable retention.** Job APIs expose cancellation and retry, but there is no user-facing lifecycle for removing completed jobs and their stored artifacts.
  **Done when:** owners can remove eligible jobs; repeatable cleanup removes job-owned database rows, objects, and vectors while preserving shared collected messages/media still in use. Include expired uploads, a retention preview, and interrupted-cleanup recovery.
  **Start in:** [job routes](backend/app/api/routes_jobs.py), [models](backend/app/models.py), [object storage](backend/app/services/minio_store.py), [vector storage](backend/app/services/qdrant_index.py).

- [ ] **T14 — Document and rehearse backup and restore.** Setup notes recommend backups without a complete recovery procedure.
  **Done when:** an isolated restore recovers users, collected messages, report downloads, and encrypted Telegram sessions; the procedure accounts for PostgreSQL, object storage, Qdrant, queue state, and encryption keys, with a documented consistency strategy and measured recovery time.
  **Start in:** [operational notes](README.md), [Compose volumes](docker-compose.yml), [migration instructions](backend/migrations/README.md).

- [ ] **T15 — Make older analyses discoverable.** The job list currently returns only the latest 50 jobs.
  **Done when:** stable pagination and source/status/date filters let a user find older analyses; the UI can load more results without duplicates, omissions, or cross-user data exposure.
  **Start in:** [job list route](backend/app/api/routes_jobs.py), [sidebar](frontend/src/components/AppSidebar.jsx), [App.jsx](frontend/src/App.jsx).

- [ ] **T16 — Add offline report search.** Static reports already contain answers, message evidence, and a media gallery, but no full search.
  **Done when:** extracted reports can search included answers and evidence, filter by sender/date/media, and jump to matching messages without a server; report-only downloads explain unavailable attachments.
  **Start in:** [report builder](backend/app/services/report_builder.py), [report templates and scripts](backend/app/templates/report).

- [ ] **T17 — Support cancellation and resumption of large uploads.** Upload progress is implemented, but each upload remains one authenticated streaming request.
  **Done when:** cancelling prevents job creation; an interrupted upload resumes from server-confirmed progress; ownership, total-size limits, expiry, and final ZIP validation remain enforced.
  **Start in:** [frontend API client](frontend/src/api/client.js), [upload routes](backend/app/api/routes_uploads.py), [ZIP validation](backend/app/services/zip_ingest.py).

- [ ] **T18 — Improve narrow-screen and keyboard workflows.** The current application shell is designed around a desktop minimum width.
  **Done when:** creating and monitoring an analysis works on a narrow viewport without page-wide horizontal scrolling; controls have useful focus states and labels; status and validation feedback are accessible from the keyboard and assistive technology.
  **Start in:** [styles](frontend/src/styles.css), [workspace chrome](frontend/src/components/WorkspaceChrome.jsx), [analysis form](frontend/src/components/CreateJobPanel.jsx).

- [ ] **T19 — Add operational dashboards and alert runbooks.** Metrics and alert rules already exist; make them easier to use during incidents.
  **Done when:** operators can see backlog, stage duration, retries, collection freshness, and model failures, and each alert explains how to diagnose and recover. Verify a test alert reaches the deployment's configured receiver.
  **Start in:** [observability configuration](observability), [application metrics](backend/app/observability), [operational notes](README.md).

## Ideas to explore after the foundations

These are optional product directions. Validate demand and scope before promoting one to an implementation todo.

- [ ] **I01 — Compare successive reports.** Show changed answers and new evidence for the same chat and question set. First version: select two reports, compare their windows and answers, and link each claimed change to evidence.
- [ ] **I02 — Ask follow-up questions against an existing analysis.** Reuse its indexed snapshot to avoid repeating upload and media processing. First version: one follow-up answer with citations, source-window context, and compatible model/index checks.
- [ ] **I03 — Compare several collected chats.** Answer one question across selected owned sources while retaining source attribution. First version: a comparison table with a separate evidence trail for each chat.
- [ ] **I04 — Explore topics, entities, and timelines.** Let readers trace recurring names, themes, and events back to messages. First version: explicit mentions and message counts over time, with inferred groupings clearly identified.
- [ ] **I05 — Annotate findings and export a briefing.** Save reviewer notes and selected evidence, then export a compact Markdown or print-friendly briefing that distinguishes human annotations from generated text.
- [ ] **I06 — Notify users about completed reports or notable changes.** Build on existing schedules with opt-in destinations, evidence links, and duplicate suppression. Start with completion/failure notifications before attempting topic-based alerts.

## Suggested starting order

1. Fix T01 and T02 and add focused interaction regressions.
2. Resolve T05, then establish reproducible installs and CI with T06–T08.
3. Reproduce and address T03 and T04 before increasing worker concurrency or collection volume.
4. Bring forward T13 and T14 before long-running deployments accumulate substantial data.
5. Improve report trust with T10–T12; deliver T15 and T16 for everyday usability.

Use the quality benchmark in T12 to evaluate later retrieval experiments, such as keyword/vector combination or reply-aware chunking, before changing defaults.
