import asyncio
import os

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret")

from app.llm import prompt_limits
from app.llm.prompt_limits import (
    PromptLimitError,
    clear_prompt_limit_cache,
    count_chat_messages_tokens,
    extract_max_model_len,
    resolve_prompt_budget,
    select_model_card,
    split_texts_by_tokens,
)


def test_select_model_card_and_extract_max_model_len() -> None:
    body = {
        "data": [
            {"id": "other", "max_model_len": 123},
            {"id": "served-name", "root": "base-model", "max_model_len": "4096"},
        ]
    }

    card = select_model_card(body, "base-model")

    assert card is not None
    assert card["id"] == "served-name"
    assert extract_max_model_len(card) == 4096


def test_split_texts_by_tokens_preserves_parent_indexes(monkeypatch) -> None:
    monkeypatch.setattr(prompt_limits, "_encoding", lambda: None)

    segments = split_texts_by_tokens(["abcdef", "xy"], max_tokens=2)

    assert [segment.parent_index for segment in segments] == [0, 0, 0, 1]
    assert [segment.part_index for segment in segments] == [0, 1, 2, 0]
    assert all(segment.token_count <= 2 for segment in segments)


def test_count_chat_messages_uses_text_parts_only() -> None:
    tokens = count_chat_messages_tokens(
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "abc"},
                    {"type": "image_url", "image_url": {"url": "data:..."}},
                ],
            }
        ]
    )

    assert tokens >= 3


def test_resolve_prompt_budget_uses_model_metadata(monkeypatch) -> None:
    async def fake_models_response(base_url: str) -> dict:
        return {"data": [{"id": "model-a", "max_model_len": 1000}]}

    monkeypatch.setattr(prompt_limits, "_models_response", fake_models_response)
    monkeypatch.setattr(prompt_limits.settings, "llm_mock_enabled", False)
    monkeypatch.setattr(prompt_limits.settings, "prompt_limit_safety_tokens", 100)
    monkeypatch.setattr(prompt_limits.settings, "prompt_limit_tiktoken_ratio", 0.5)

    budget = asyncio.run(
        resolve_prompt_budget(base_url="http://models/v1", model="model-a", output_reservation=100)
    )

    assert budget.max_model_len == 1000
    assert budget.input_tokens == 400


def test_resolve_prompt_budget_fails_without_limit_or_override(monkeypatch) -> None:
    async def fake_models_response(base_url: str) -> dict:
        return {"data": [{"id": "model-a"}]}

    clear_prompt_limit_cache()
    monkeypatch.setattr(prompt_limits, "_models_response", fake_models_response)
    monkeypatch.setattr(prompt_limits.settings, "llm_mock_enabled", False)
    monkeypatch.setattr(prompt_limits.settings, "prompt_limit_max_model_len_overrides", {})

    with pytest.raises(PromptLimitError, match="max_model_len"):
        asyncio.run(resolve_prompt_budget(base_url="http://models/v1", model="model-a"))


def test_models_response_is_cached(monkeypatch) -> None:
    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {"data": [{"id": "model-a", "max_model_len": 1000}]}

    class FakeClient:
        calls = 0

        def __init__(self, timeout: float) -> None:
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, url: str, headers: dict):
            FakeClient.calls += 1
            return FakeResponse()

    clear_prompt_limit_cache()
    monkeypatch.setattr(prompt_limits.settings, "prompt_limit_cache_ttl_seconds", 300)
    monkeypatch.setattr(prompt_limits.httpx, "AsyncClient", FakeClient)

    first = asyncio.run(prompt_limits._models_response("http://models/v1"))
    second = asyncio.run(prompt_limits._models_response("http://models/v1"))

    assert first == second
    assert FakeClient.calls == 1
