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

