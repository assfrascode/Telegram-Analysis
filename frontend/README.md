# Chat Analyse React Frontend

React/Vite frontend served by Nginx in production. The Dockerfile builds React inside Docker and then copies the generated `dist` output into a small Nginx runtime image.

## Docker

From the repository root:

```bash
docker compose build --no-cache chat-analyse-frontend
docker compose up
```

Frontend URL:

```text
http://localhost:3000
```

## Why there is no package-lock.json in this generated package

The earlier generated package-lock was produced inside a sandbox whose npm registry resolves to an internal mirror. That made Docker builds on normal machines wait on unreachable registry URLs. This package intentionally installs from `https://registry.npmjs.org/` inside Docker using exact top-level dependency versions.

After the first successful build, Docker/BuildKit caches `/root/.npm`, so subsequent builds should be much faster.

## Backend proxying

The Nginx runtime proxies same-origin browser calls to the backend service through `BACKEND_URL`, defaulting to:

```text
http://backend:8000
```

Proxied paths:

- `/auth`
- `/capacity`
- `/uploads`
- `/jobs`
- `/question-sets`
- `/ws`
