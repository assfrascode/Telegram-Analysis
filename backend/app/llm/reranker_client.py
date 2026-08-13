
import re
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings
from app.llm.prompt_limits import (
    PromptLimitError,
    count_text_tokens,
    resolve_prompt_budget,
    split_text_by_tokens,
)
from app.observability.metrics import observe_model_call

settings = get_settings()

_WORD_RE = re.compile(r"[\wÄÖÜäöüß]+", re.UNICODE)
RERANK_SEGMENT_BATCH_SIZE = 64


@dataclass(frozen=True, slots=True)
class _DocumentSegment:
    document_index: int
    text: str


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

        budget = await resolve_prompt_budget(
            base_url=settings.vllm_reranker_base_url,
            model=settings.reranker_model,
            output_reservation=0,
        )
        pair_overhead = max(0, int(settings.prompt_limit_rerank_pair_overhead_tokens))
        if budget.input_tokens <= pair_overhead + 1:
            raise PromptLimitError(
                f"Reranker prompt budget {budget.input_tokens} is too small for pair overhead {pair_overhead}"
            )

        query_cap = max(1, min(budget.input_tokens // 2, budget.input_tokens - pair_overhead - 1))
        query_segments = split_text_by_tokens(
            query,
            query_cap,
            model=settings.reranker_model,
        )
        scored: dict[int, float] = {}
        first_position: dict[int, int] = {}
        response_position = 0

        for query_segment in query_segments:
            query_tokens = count_text_tokens(query_segment, model=settings.reranker_model)
            document_cap = budget.input_tokens - query_tokens - pair_overhead
            if document_cap <= 0:
                raise PromptLimitError("Reranker query segment leaves no budget for documents")

            segments: list[_DocumentSegment] = []
            for document_index, document in enumerate(documents):
                for document_segment in split_text_by_tokens(
                    document,
                    document_cap,
                    model=settings.reranker_model,
                ):
                    segments.append(_DocumentSegment(document_index=document_index, text=document_segment))

            for start in range(0, len(segments), RERANK_SEGMENT_BATCH_SIZE):
                batch = segments[start : start + RERANK_SEGMENT_BATCH_SIZE]
                ranked = await self._rerank_once(query_segment, [segment.text for segment in batch])
                for item in ranked:
                    local_index = int(item["index"])
                    if local_index < 0 or local_index >= len(batch):
                        continue
                    document_index = batch[local_index].document_index
                    score = float(item.get("score", 0.0))
                    previous = scored.get(document_index)
                    if previous is None or score > previous:
                        scored[document_index] = score
                        first_position.setdefault(document_index, response_position)
                    response_position += 1

        return sorted(
            [
                {
                    "index": document_index,
                    "score": score,
                    "document": documents[document_index],
                }
                for document_index, score in scored.items()
            ],
            key=lambda item: (-item["score"], first_position.get(int(item["index"]), 0)),
        )

    async def _rerank_once(self, query: str, documents: list[str]) -> list[dict[str, Any]]:
        async with observe_model_call("vllm", "rerank", settings.reranker_model):
            return await self._rerank_once_unobserved(query, documents)

    async def _rerank_once_unobserved(self, query: str, documents: list[str]) -> list[dict[str, Any]]:
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
