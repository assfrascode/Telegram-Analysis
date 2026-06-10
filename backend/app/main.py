from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes_auth import router as auth_router
from app.api.routes_capacity import router as capacity_router
from app.api.routes_jobs import router as jobs_router
from app.api.routes_question_sets import router as question_sets_router
from app.api.routes_uploads import router as uploads_router
from app.api.routes_telegram import router as telegram_router
from app.api.routes_ws import router as ws_router
from app.bootstrap import bootstrap_services
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.nats_client import nats_context
from app.services.job_recovery import recover_stale_queued_jobs

settings = get_settings()
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


app = FastAPI(title=settings.app_name, lifespan=lifespan)

app.include_router(auth_router)
app.include_router(capacity_router)
app.include_router(uploads_router)
app.include_router(jobs_router)
app.include_router(question_sets_router)
app.include_router(telegram_router)
app.include_router(ws_router)

app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def index():
    return FileResponse(static_dir / "index.html")


@app.get("/health")
async def health():
    return {"ok": True}
