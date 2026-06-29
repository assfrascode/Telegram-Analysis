from app.workers.rag_worker import _bounded_int, _chunk_id_from_qdrant_hit, _merge_qdrant_hits_by_parent


def test_bounded_int_uses_default_for_invalid_value():
    assert _bounded_int("not-int", default=50, minimum=1, maximum=1000) == 50
    assert _bounded_int(None, default=50, minimum=1, maximum=1000) == 50


def test_bounded_int_clamps_to_range():
    assert _bounded_int(0, default=50, minimum=1, maximum=1000) == 1
    assert _bounded_int(5000, default=50, minimum=1, maximum=1000) == 1000
    assert _bounded_int("42", default=50, minimum=1, maximum=1000) == 42


def test_chunk_id_from_qdrant_hit_prefers_payload_chunk_id():
    chunk_id = "11111111-1111-4111-8111-111111111111"
    point_id = "22222222-2222-4222-8222-222222222222"
    assert str(_chunk_id_from_qdrant_hit({"id": point_id, "payload": {"chunk_id": chunk_id}})) == chunk_id


def test_chunk_id_from_qdrant_hit_prefers_parent_chunk_id():
    parent_id = "11111111-1111-4111-8111-111111111111"
    chunk_id = "22222222-2222-4222-8222-222222222222"
    assert (
        str(
            _chunk_id_from_qdrant_hit(
                {"id": chunk_id, "payload": {"chunk_id": chunk_id, "parent_chunk_id": parent_id}}
            )
        )
        == parent_id
    )


def test_chunk_id_from_qdrant_hit_falls_back_to_point_id():
    point_id = "22222222-2222-4222-8222-222222222222"
    assert str(_chunk_id_from_qdrant_hit({"id": point_id, "payload": {}})) == point_id


def test_chunk_id_from_qdrant_hit_returns_none_for_invalid_id():
    assert _chunk_id_from_qdrant_hit({"id": "not-a-uuid", "payload": {}}) is None


def test_merge_qdrant_hits_by_parent_keeps_best_subchunk_score():
    hits = [
        {
            "id": "point-a1",
            "score": 0.2,
            "payload": {"parent_chunk_id": "00000000-0000-0000-0000-000000000001"},
        },
        {
            "id": "point-b1",
            "score": 0.9,
            "payload": {"parent_chunk_id": "00000000-0000-0000-0000-000000000002"},
        },
        {
            "id": "point-a2",
            "score": 0.8,
            "payload": {"parent_chunk_id": "00000000-0000-0000-0000-000000000001"},
        },
    ]

    merged = _merge_qdrant_hits_by_parent(hits)

    assert [item["payload"]["parent_chunk_id"] for item in merged] == [
        "00000000-0000-0000-0000-000000000002",
        "00000000-0000-0000-0000-000000000001",
    ]
    assert merged[1]["score"] == 0.8
