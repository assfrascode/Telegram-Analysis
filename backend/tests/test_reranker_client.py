from app.llm.reranker_client import _normalize_rerank_response


def test_normalize_rerank_results_with_relevance_score():
    docs = ["a", "b"]
    data = {"results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": 0.1}]}

    ranked = _normalize_rerank_response(data, docs)

    assert ranked[0]["index"] == 1
    assert ranked[0]["score"] == 0.9
    assert ranked[0]["document"] == "b"


def test_normalize_score_list():
    docs = ["a", "b", "c"]
    ranked = _normalize_rerank_response({"scores": [0.2, 0.8, 0.4]}, docs)

    assert [item["index"] for item in ranked] == [1, 2, 0]
