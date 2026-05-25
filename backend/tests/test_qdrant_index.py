from datetime import datetime, timezone
from uuid import uuid4

from app.models import MessageChunk
from app.services.qdrant_index import QdrantIndex, chunk_payload


def test_extract_vector_size_from_unnamed_collection() -> None:
    qdrant = QdrantIndex(base_url="http://example", collection="test")
    response = {"result": {"config": {"params": {"vectors": {"size": 64, "distance": "Cosine"}}}}}
    assert qdrant._extract_vector_size(response) == 64


def test_extract_vector_size_from_named_collection() -> None:
    qdrant = QdrantIndex(base_url="http://example", collection="test")
    response = {
        "result": {
            "config": {
                "params": {
                    "vectors": {"default": {"size": 128, "distance": "Cosine"}}
                }
            }
        }
    }
    assert qdrant._extract_vector_size(response) == 128


def test_chunk_payload_contains_retrieval_metadata() -> None:
    job_id = uuid4()
    chunk_id = uuid4()
    start = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)
    chunk = MessageChunk(
        id=chunk_id,
        job_id=job_id,
        chunk_index=3,
        chunk_hash="abc",
        text="hello world" * 100,
        message_ids=["m1", "m2"],
        start_timestamp=start,
        end_timestamp=start,
        has_media=True,
        payload={"telegram_message_ids": [10, 11], "media_types": ["image"]},
    )
    payload = chunk_payload(chunk, embedding_model="test-model")
    assert payload["job_id"] == str(job_id)
    assert payload["chunk_id"] == str(chunk_id)
    assert payload["chunk_index"] == 3
    assert payload["telegram_message_ids"] == [10, 11]
    assert payload["media_types"] == ["image"]
    assert payload["embedding_model"] == "test-model"
    assert payload["text_preview"].startswith("hello world")
