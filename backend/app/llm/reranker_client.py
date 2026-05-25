from __future__ import annotations

import re
from typing import Any

import httpx

from app.config import get_settings

settings = get_settings()

_WORD_RE = re.compile(r"[\wÄÖÜäöüß]+", re.UNICODE)


def _terms(text: str) -> set[str]:
    return {term.lower() for term in _WORD_RE.findall(text or "") if len(term) > 2}


def _score_from_item(item: dict[str, Any]) -> float:
    for key in ("score", "relevance_score", "relevance", "logit"):
        if key in item:
            return float(item[key])
    if "scores" in item and isinstance(item["scores"], list) and item["scores"]:
        return float(item["scores"][0])
    return 0.0


def _normalize_rerank_response(data: Any, documents: list[str]) -> list[dict[str, Any]]:
    """Normalize common rerank/score response shapes to one internal format."""
    if isinstance(data, dict):
        candidates = data.get("results") or data.get("data") or data.get("scores") or data.get("rankings")
    else:
        candidates = data

    normalized: list[dict[str, Any]] = []

    if isinstance(candidates, list):
        for position, item in enumerate(candidates):
            if isinstance(item, dict):
                index = int(item.get("index", item.get("document_index", position)))
                if 0 <= index < len(documents):
                    normalized.append(
                        {
                            "index": index,
                            "score": _score_from_item(item),
                            "document": documents[index],
                        }
                    )
                continue

            if isinstance(item, (int, float)):
                index = position
                if 0 <= index < len(documents):
                    normalized.append({"index": index, "score": float(item), "document": documents[index]})

    if not normalized:
        raise ValueError(f"Unsupported reranker response shape: {data!r}")

    return sorted(normalized, key=lambda item: item["score"], reverse=True)


class RerankerClient:
    async def rerank(self, query: str, documents: list[str]) -> list[dict[str, Any]]:
        if settings.llm_mock_enabled:
            query_terms = _terms(query)
            scored = []
            for index, document in enumerate(documents):
                doc_terms = _terms(document)
                overlap = len(query_terms & doc_terms)
                score = float(overlap) + (1.0 / (index + 1))
                scored.append({"index": index, "score": score, "document": document})
            return sorted(scored, key=lambda item: item["score"], reverse=True)

        base_url = settings.vllm_reranker_base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {settings.vllm_api_key}"}
        errors: list[str] = []

        async with httpx.AsyncClient(timeout=180.0) as client:
            # vLLM versions with reranker support commonly expose a /rerank-style
            # endpoint that accepts query + documents.
            try:
                response = await client.post(
                    f"{base_url}/rerank",
                    headers=headers,
                    json={
                        "model": settings.reranker_model,
                        "query": query,
                        "documents": documents,
                    },
                )
                if response.status_code < 400:
                    return _normalize_rerank_response(response.json(), documents)
                errors.append(f"/rerank {response.status_code}: {response.text[:500]}")
            except Exception as exc:  # pragma: no cover - exercised in integration
                errors.append(f"/rerank exception: {exc}")

            # Score/pooling endpoints are usually pairwise. Try the text_1/text_2
            # shape first because it preserves document order and returns scores.
            try:
                response = await client.post(
                    f"{base_url}/score",
                    headers=headers,
                    json={
                        "model": settings.reranker_model,
                        "text_1": query,
                        "text_2": documents,
                    },
                )
                if response.status_code < 400:
                    return _normalize_rerank_response(response.json(), documents)
                errors.append(f"/score text_1/text_2 {response.status_code}: {response.text[:500]}")
            except Exception as exc:  # pragma: no cover - exercised in integration
                errors.append(f"/score text_1/text_2 exception: {exc}")

            # Some servers use explicit pair lists.
            try:
                response = await client.post(
                    f"{base_url}/score",
                    headers=headers,
                    json={
                        "model": settings.reranker_model,
                        "pairs": [[query, document] for document in documents],
                    },
                )
                if response.status_code < 400:
                    return _normalize_rerank_response(response.json(), documents)
                errors.append(f"/score pairs {response.status_code}: {response.text[:500]}")
            except Exception as exc:  # pragma: no cover - exercised in integration
                errors.append(f"/score pairs exception: {exc}")

        raise RuntimeError("Reranker request failed: " + " | ".join(errors))
