from app.workers.rag_worker import _sanitize_rerank_results


def test_sanitize_rerank_results_sorts_by_score_and_keeps_valid_indexes():
    ranked = _sanitize_rerank_results(
        [
            {"index": 0, "score": 0.2},
            {"index": 2, "score": 0.9},
            {"index": 1, "score": 0.5},
        ],
        document_count=3,
    )

    assert [item["index"] for item in ranked] == [2, 1, 0]
    assert [item["score"] for item in ranked] == [0.9, 0.5, 0.2]


def test_sanitize_rerank_results_drops_invalid_and_duplicate_indexes():
    ranked = _sanitize_rerank_results(
        [
            {"index": 1, "score": 0.3},
            {"index": 99, "score": 1.0},
            {"index": -1, "score": 1.0},
            {"index": 1, "score": 0.9},
            {"index": "bad", "score": 0.9},
            {"score": 0.9},
            {"index": 0, "score": "bad"},
        ],
        document_count=2,
    )

    assert [item["index"] for item in ranked] == [1, 0]
    assert ranked[0]["score"] == 0.3
    assert ranked[1]["score"] == 0.0


def test_sanitize_rerank_results_uses_response_order_as_tiebreaker():
    ranked = _sanitize_rerank_results(
        [
            {"index": 2, "score": 1.0},
            {"index": 0, "score": 1.0},
            {"index": 1, "score": 1.0},
        ],
        document_count=3,
    )

    assert [item["index"] for item in ranked] == [2, 0, 1]
