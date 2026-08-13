# Project Todo

This backlog is based on the current MVP, its documented limitations, and gaps
visible in the repository. Priorities describe the recommended implementation
order, not release commitments.

## P0 — Production readiness



- [ ] **Create and verify backup and recovery procedures.**
  - Document encrypted backup and restore for PostgreSQL, MinIO, NATS, Qdrant,
    and the Telegram credential-encryption key.
  - Run a restore drill and record recovery-time and recovery-point expectations.
  - Document PostgreSQL major-version upgrades before changing image versions.

- [ ] **Harden authentication controls for multi-instance deployment.**
  - Replace or supplement the process-local authentication rate limiter with a
    shared limiter or a documented trusted edge-proxy policy.
  - Add tests proving limits cannot be bypassed by switching API replicas.
  - Document session/token revocation and credential-rotation procedures.

- [ ] **Define data lifecycle and deletion behavior.**
  - Add configurable retention for uploads, extracted media, reports, vectors,
    events, dead letters, and collected Telegram messages.
  - Provide an owner-authorized deletion flow that removes related records and
    artifacts across PostgreSQL, MinIO, and Qdrant.
  - Test partial-failure recovery and audit the operation without logging content.

- [ ] **Make dependency and image builds reproducible.**
  - Generate a hash-locked transitive Python dependency file.
  - Commit a frontend lockfile produced against the public npm registry and use
    `npm ci` in builds.
  - Pin release container images by digest and record the digests in release
    metadata.
## P1 — MVP feature gaps

- [ ] **Support resumable browser uploads.**
  - Use chunked uploads with integrity checks, expiration, cancellation, and safe
    retry behavior.
  - Preserve the existing ownership, request-size, ZIP-validation, and capacity
    controls.

- [ ] **Add full-text search to static reports.**
  - Search questions, answers, summaries, messages, and media metadata offline.
  - Keep the report self-contained and sanitize all indexed and rendered content.
  - Add keyboard navigation and tests for large reports.

## Backlog maintenance

- Keep each item independently reviewable and add links to its issue or pull
  request when work begins.
- Split tasks that span more than one release before moving them into progress.
- Update `README.md` when a documented limitation is completed.
