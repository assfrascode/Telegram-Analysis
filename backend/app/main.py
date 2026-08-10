from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.api.routes_auth import router as auth_router
from app.api.routes_capacity import router as capacity_router
from app.api.routes_jobs import router as jobs_router
from app.api.routes_question_sets import router as question_sets_router
from app.api.routes_uploads import router as uploads_router
from app.api.routes_telegram import router as telegram_router
from app.api.routes_telegram_ingest import router as telegram_ingest_router
from app.api.routes_ws import router as ws_router
from app.bootstrap import bootstrap_services
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.nats_client import nats_context
from app.middleware import RequestBodyLimitMiddleware, SecurityHeadersMiddleware
from app.services.job_recovery import recover_stale_queued_jobs

settings = get_settings()
if settings.app_role not in {"api", "all"}:
    raise RuntimeError("The ASGI API requires APP_ROLE=api (or all)")
static_dir = Path(__file__).parent / "static" / "app"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with SessionLocal() as session:
        await bootstrap_services(session)
    async with nats_context() as (_, js):
        async with SessionLocal() as session:
            await recover_stale_queued_jobs(session, js)
    yield


app = FastAPI(
    title=settings.app_name,
    lifespan=lifespan,
    docs_url=None if settings.app_env == "production" else "/docs",
    redoc_url=None if settings.app_env == "production" else "/redoc",
    openapi_url=None if settings.app_env == "production" else "/openapi.json",
)
app.add_middleware(SecurityHeadersMiddleware, production=settings.app_env == "production")
app.add_middleware(
    RequestBodyLimitMiddleware,
    max_bytes=settings.max_request_body_bytes,
    path_limits=[
        (
            "/uploads/",
            "/content",
            settings.max_upload_bytes,
        ),
        (
            "/telegram/ingest/runs/",
            "/media",
            settings.max_ingest_media_bytes + 1024 * 1024,
        ),
        (
            "/telegram/ingest/runs/",
            "/messages",
            settings.max_ingest_messages_body_bytes,
        ),
    ],
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)

app.include_router(auth_router)
app.include_router(capacity_router)
app.include_router(uploads_router)
app.include_router(jobs_router)
app.include_router(question_sets_router)
app.include_router(telegram_router)
app.include_router(telegram_ingest_router)
app.include_router(ws_router)

app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index():
    return FileResponse(static_dir / "index.html")


@app.get("/health")
async def health():
    return {"ok": True}
