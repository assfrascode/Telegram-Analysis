import asyncio
from types import SimpleNamespace

from app.llm import reranker_client
from app.llm.reranker_client import RerankerClient, _normalize_rerank_response


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


def test_reranker_splits_segments_and_aggregates_scores(monkeypatch):
    calls: list[tuple[str, list[str]]] = []

    async def fake_budget(**kwargs):
        return SimpleNamespace(input_tokens=20)

    async def fake_rerank_once(self, query, documents):
        calls.append((query, documents))
        return [
            {"index": index, "score": float(len(document)), "document": document}
            for index, document in enumerate(documents)
        ]

    monkeypatch.setattr(reranker_client.settings, "llm_mock_enabled", False)
    monkeypatch.setattr(reranker_client.settings, "prompt_limit_rerank_pair_overhead_tokens", 1)
    monkeypatch.setattr(reranker_client, "resolve_prompt_budget", fake_budget)
    monkeypatch.setattr(reranker_client, "count_text_tokens", lambda text, model=None: len(text or ""))
    monkeypatch.setattr(
        reranker_client,
        "split_text_by_tokens",
        lambda text, max_tokens, model=None: [
            (text or "")[index : index + max_tokens]
            for index in range(0, len(text or ""), max_tokens)
        ]
        or [""],
    )
    monkeypatch.setattr(RerankerClient, "_rerank_once", fake_rerank_once)

    ranked = asyncio.run(RerankerClient().rerank("query", ["a" * 40, "b" * 5]))

    assert calls
    assert all(len(query) + len(document) + 1 <= 20 for query, docs in calls for document in docs)
    assert [item["index"] for item in ranked] == [0, 1]
    assert ranked[0]["score"] == 14.0
