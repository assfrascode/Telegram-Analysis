import asyncio
import sys

from app.db import SessionLocal, init_db
from app.nats_client import nats_context
from app.services.job_recovery import recover_stale_queued_jobs
from app.workers.ingest_worker import ValidateWorker, ExtractWorker
from app.workers.parser_worker import ParserWorker
from app.workers.media_worker import MediaWorker
from app.workers.chunk_worker import ChunkWorker
from app.workers.embedding_worker import EmbeddingWorker
from app.workers.rag_worker import RetrieveWorker, RerankWorker, AnswerWorker
from app.workers.report_worker import ReportWorker
from app.workers.telegram_snapshot_worker import TelegramSnapshotWorker


WORKERS = {
    "validate": ValidateWorker,
    "extract": ExtractWorker,
    "parser": ParserWorker,
    "telegram-snapshot": TelegramSnapshotWorker,
    "media": MediaWorker,
    "chunk": ChunkWorker,
    "embedding": EmbeddingWorker,
    "retrieve": RetrieveWorker,
    "rerank": RerankWorker,
    "answer": AnswerWorker,
    "report": ReportWorker,
}


async def main() -> None:
    await init_db()
    async with nats_context() as (_, js):
        async with SessionLocal() as session:
            await recover_stale_queued_jobs(session, js)

    selected = sys.argv[1:] or ["all"]
    if selected == ["all"]:
        workers = [cls() for cls in WORKERS.values()]
    else:
        workers = [WORKERS[name]() for name in selected]

    await asyncio.gather(*(worker.run_forever() for worker in workers))


if __name__ == "__main__":
    asyncio.run(main())
