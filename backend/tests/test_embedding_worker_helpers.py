from uuid import uuid4

from app.models import MessageChunk
from app.workers.embedding_worker import _batched, _embedding_hash, _qdrant_point_id


def test_batched_splits_items() -> None:
    assert _batched([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_embedding_hash_changes_with_model() -> None:
    first = _embedding_hash(model_name="a", chunk_hash="h", text="text")
    second = _embedding_hash(model_name="b", chunk_hash="h", text="text")
    assert first != second
    assert len(first) == 64


def test_qdrant_point_id_is_stable_for_virtual_subchunks() -> None:
    chunk = MessageChunk(id=uuid4(), chunk_hash="abc")

    first = _qdrant_point_id(chunk, part_index=0, part_count=3)
    second = _qdrant_point_id(chunk, part_index=0, part_count=3)
    other = _qdrant_point_id(chunk, part_index=1, part_count=3)

    assert first == second
    assert first != other
    assert _qdrant_point_id(chunk, part_index=0, part_count=1) == str(chunk.id)
