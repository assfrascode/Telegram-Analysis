
import math
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import get_settings

try:  # pragma: no cover - exercised when the dependency is installed.
    import tiktoken
except ModuleNotFoundError:  # pragma: no cover - keeps local tests useful before reinstall.
    tiktoken = None  # type: ignore[assignment]

settings = get_settings()


class PromptLimitError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PromptBudget:
    base_url: str
    model: str
    max_model_len: int
    output_reservation: int
    safety_tokens: int
    ratio: float
    input_tokens: int


@dataclass(frozen=True, slots=True)
class TextSegment:
    parent_index: int
    part_index: int
    part_count: int
    text: str
    token_count: int


_MODELS_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}


def clear_prompt_limit_cache() -> None:
    _MODELS_CACHE.clear()


def _cache_ttl_seconds() -> float:
    return max(0.0, float(settings.prompt_limit_cache_ttl_seconds))


def _encoding():
    if tiktoken is None:
        return None
    try:
        return tiktoken.get_encoding(settings.prompt_limit_tiktoken_encoding)
    except Exception as exc:  # pragma: no cover - configuration error.
        raise PromptLimitError(
            f"Unknown tiktoken encoding: {settings.prompt_limit_tiktoken_encoding}"
        ) from exc


def count_text_tokens(text: str, *, model: str | None = None) -> int:
    value = text or ""
    if not value:
        return 0
    encoder = _encoding()
    if encoder is None:
        # Conservative fallback for local environments before dependencies are
        # reinstalled. Production uses the declared tiktoken dependency.
        return len(value)
    return len(encoder.encode(value))


def split_text_by_tokens(text: str, max_tokens: int, *, model: str | None = None) -> list[str]:
    if max_tokens <= 0:
        raise PromptLimitError("Token split budget must be greater than zero")
    value = text or ""
    if count_text_tokens(value, model=model) <= max_tokens:
        return [value]

    encoder = _encoding()
    if encoder is None:
        return [value[index : index + max_tokens] for index in range(0, len(value), max_tokens)]

    tokens = encoder.encode(value)
    parts: list[str] = []
    for index in range(0, len(tokens), max_tokens):
        parts.append(encoder.decode(tokens[index : index + max_tokens]))
    return parts or [""]


def split_texts_by_tokens(
    texts: list[str],
    *,
    max_tokens: int,
    model: str | None = None,
) -> list[TextSegment]:
    segments: list[TextSegment] = []
    for parent_index, text in enumerate(texts):
        parts = split_text_by_tokens(text or "", max_tokens, model=model)
        part_count = len(parts)
        for part_index, part in enumerate(parts):
            segments.append(
                TextSegment(
                    parent_index=parent_index,
                    part_index=part_index,
                    part_count=part_count,
                    text=part,
                    token_count=count_text_tokens(part, model=model),
                )
            )
    return segments


def content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    return ""


def count_chat_messages_tokens(messages: list[dict[str, Any]], *, model: str | None = None) -> int:
    total = 0
    overhead = max(0, int(settings.prompt_limit_chat_message_overhead_tokens))
    for message in messages:
        total += overhead
        total += count_text_tokens(str(message.get("role") or ""), model=model)
        total += count_text_tokens(content_text(message.get("content")), model=model)
    return total + overhead


def _model_override(base_url: str, model: str) -> int | None:
    overrides = settings.prompt_limit_max_model_len_overrides or {}
    base = base_url.rstrip("/")
    keys = (
        f"{base}|{model}",
        f"{base}/{model}",
        model,
    )
    for key in keys:
        value = overrides.get(key)
        if value is None:
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


async def _models_response(base_url: str) -> dict[str, Any]:
    base = base_url.rstrip("/")
    ttl = _cache_ttl_seconds()
    cached = _MODELS_CACHE.get(base)
    now = time.monotonic()
    if cached is not None and ttl > 0 and now - cached[0] <= ttl:
        return cached[1]

    headers = {"Authorization": f"Bearer {settings.vllm_api_key}"}
    async with httpx.AsyncClient(timeout=settings.prompt_limit_models_timeout_seconds) as client:
        response = await client.get(f"{base}/models", headers=headers)
        response.raise_for_status()
        body = response.json()
    if not isinstance(body, dict):
        raise PromptLimitError(f"Unexpected /models response from {base_url!r}")
    if ttl > 0:
        _MODELS_CACHE[base] = (now, body)
    return body


def select_model_card(models_response: dict[str, Any], model: str) -> dict[str, Any] | None:
    data = models_response.get("data")
    if not isinstance(data, list):
        return None
    for item in data:
        if not isinstance(item, dict):
            continue
        if item.get("id") == model or item.get("root") == model:
            return item
    return None


def extract_max_model_len(model_card: dict[str, Any]) -> int | None:
    for key in (
        "max_model_len",
        "max_model_length",
        "max_context_len",
        "max_context_length",
        "context_length",
    ):
        value = model_card.get(key)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


async def resolve_max_model_len(*, base_url: str, model: str) -> int:
    if settings.llm_mock_enabled:
        return max(1, int(settings.prompt_limit_mock_max_model_len))

    body: dict[str, Any] | None = None
    try:
        body = await _models_response(base_url)
    except Exception:
        override = _model_override(base_url, model)
        if override is not None:
            return override
        raise

    card = select_model_card(body, model)
    if card is not None:
        max_model_len = extract_max_model_len(card)
        if max_model_len is not None:
            return max_model_len

    override = _model_override(base_url, model)
    if override is not None:
        return override

    if card is None:
        raise PromptLimitError(f"Model {model!r} was not found in {base_url.rstrip('/')}/models")
    raise PromptLimitError(
        f"Model {model!r} did not expose max_model_len in {base_url.rstrip('/')}/models"
    )


async def resolve_prompt_budget(
    *,
    base_url: str,
    model: str,
    output_reservation: int = 0,
) -> PromptBudget:
    max_model_len = await resolve_max_model_len(base_url=base_url, model=model)
    safety_tokens = max(0, int(settings.prompt_limit_safety_tokens))
    ratio = max(0.01, min(1.0, float(settings.prompt_limit_tiktoken_ratio)))
    reserved = max(0, int(output_reservation)) + safety_tokens
    available = max_model_len - reserved
    if available <= 0:
        raise PromptLimitError(
            f"Prompt budget for model {model!r} is exhausted: "
            f"max_model_len={max_model_len}, reserved={reserved}"
        )
    input_tokens = max(1, math.floor(available * ratio))
    return PromptBudget(
        base_url=base_url,
        model=model,
        max_model_len=max_model_len,
        output_reservation=max(0, int(output_reservation)),
        safety_tokens=safety_tokens,
        ratio=ratio,
        input_tokens=input_tokens,
    )
