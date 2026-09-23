# Database migrations

Alembic owns all production schema changes. From `backend/`, run:

```bash
alembic upgrade head
```

The first revision supports both an empty PostgreSQL database and an existing,
unversioned MVP database. It validates the MVP tables, applies the historical
compatibility columns idempotently, and records the revision only if validation
succeeds. Application processes only verify the recorded revision.

Create future revisions with `alembic revision --autogenerate -m "description"`
and inspect the generated operations before committing them.


Revision `20260923_0004` adds nullable per-owner retention periods and durable
cleanup markers for jobs and uploads. Existing owners retain completed jobs and
uploaded files until they choose retention periods. Apply `alembic upgrade head`
before starting the updated application. The cleanup loop retries pending markers
after restarts; its poll interval is `CLEANUP_INTERVAL_SECONDS` (default 60), and
abandoned created/rejected uploads expire after `UPLOAD_EXPIRY_HOURS` (default 24).
