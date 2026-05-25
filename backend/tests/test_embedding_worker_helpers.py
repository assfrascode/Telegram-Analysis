from app.workers.embedding_worker import _batched, _embedding_hash


def test_batched_splits_items() -> None:
    assert _batched([1, 2, 3, 4, 5], 2) == [[1, 2], [3, 4], [5]]


def test_embedding_hash_changes_with_model() -> None:
    first = _embedding_hash(model_name="a", chunk_hash="h", text="text")
    second = _embedding_hash(model_name="b", chunk_hash="h", text="text")
    assert first != second
    assert len(first) == 64
