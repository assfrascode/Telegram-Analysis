
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from app.config import get_settings

settings = get_settings()

MediaKind = Literal["image", "video"]


DEFAULT_MEDIA_DESCRIPTION_PROMPT = (
    "Beschreibe den Medieninhalt neutral, präzise und ohne Interpretation. "
    "Nenne sichtbare Texte nur, wenn sie klar lesbar sind. "
    "Erfinde keine Identitäten, keine Absichten und keine nicht sichtbaren Kontexte. "
    "Wenn der Inhalt nicht eindeutig erkennbar ist, sage das explizit."
)


@dataclass(slots=True)
class MediaDescriptionResult:
    description: str
    raw_response: dict[str, Any]


def multimodal_content_type(media_type: str) -> str:
    """Return the OpenAI-compatible content part type for a media row."""
    normalized = (media_type or "").lower()
    if normalized == "video":
        return "video_url"
    return "image_url"


def build_multimodal_content(
    *,
    media_url: str,
    media_type: str,
    prompt: str = DEFAULT_MEDIA_DESCRIPTION_PROMPT,
) -> list[dict[str, Any]]:
    """Build OpenAI-compatible multimodal chat content."""
    content_type = multimodal_content_type(media_type)
    return [
        {"type": "text", "text": prompt},
        {"type": content_type, content_type: {"url": media_url}},
    ]


def extract_chat_completion_text(response_json: dict[str, Any]) -> str:
    try:
        content = response_json["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError("Unexpected vLLM chat completion response shape") from exc

    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        text = "\n".join(parts).strip()
        if text:
            return text

    raise ValueError("vLLM response did not contain textual content")


def _mock_chat_response(content: str, *, model: str) -> dict[str, Any]:
    return {
        "id": "mock-chat-completion",
        "object": "chat.completion",
        "model": model,
        "mock": True,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }
        ],
    }


class VLLMGateway:
    def __init__(self) -> None:
        self.headers = {"Authorization": f"Bearer {settings.vllm_api_key}"}

    async def chat_completion(
        self,
        *,
        base_url: str,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.0,
        max_tokens: int = 2048,
        timeout: float = 180.0,
    ) -> dict[str, Any]:
        if settings.llm_mock_enabled:
            return _mock_chat_response(
                "[MOCK_LLM] Antwort wurde ohne vLLM-Server erzeugt. "
                "Dieser Text dient nur zum schnellen Testen der Pipeline.",
                model=model,
            )

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers=self.headers,
                json={
                    "model": model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                },
            )
            response.raise_for_status()
            return response.json()

    async def describe_media_with_raw(
        self,
        *,
        media_url: str,
        media_type: str,
        prompt: str = DEFAULT_MEDIA_DESCRIPTION_PROMPT,
        max_tokens: int = 1024,
        timeout: float = 300.0,
    ) -> MediaDescriptionResult:
        if settings.llm_mock_enabled:
            normalized = (media_type or "media").lower()
            description = (
                f"[MOCK_{normalized.upper()}_DESCRIPTION] "
                f"Neutrale Platzhalterbeschreibung für ein {normalized}-Medium. "
                "Es wurde kein vLLM-Request ausgeführt."
            )
            return MediaDescriptionResult(
                description=description,
                raw_response={
                    "mock": True,
                    "model": settings.vision_model,
                    "media_type": normalized,
                    "media_url_preview": media_url[:128],
                    "prompt_version": settings.media_analysis_prompt_version,
                },
            )

        content = build_multimodal_content(
            media_url=media_url,
            media_type=media_type,
            prompt=prompt,
        )
        raw = await self.chat_completion(
            base_url=settings.vllm_vision_base_url,
            model=settings.vision_model,
            messages=[{"role": "user", "content": content}],
            max_tokens=max_tokens,
            timeout=timeout,
        )
        return MediaDescriptionResult(description=extract_chat_completion_text(raw), raw_response=raw)

    async def describe_media(self, media_url: str, media_type: str) -> str:
        result = await self.describe_media_with_raw(media_url=media_url, media_type=media_type)
        return result.description

    async def answer_prompt(self, prompt: str) -> str:
        """Generate an answer from a fully built prompt body."""
        if settings.llm_mock_enabled:
            prompt_preview = prompt.strip().replace("\n", " ")[:500]
            return (
                "[MOCK_ANSWER] Diese Antwort wurde ohne vLLM-Server erzeugt. "
                "Die Pipeline hat Evidenz-Chunks geladen und einen Antwortprompt gebaut.\n\n"
                "Kurzbewertung: Die eigentliche inhaltliche Antwort steht erst mit aktiviertem Textmodell zur Verfügung.\n\n"
                f"Promptvorschau: {prompt_preview}"
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "Du beantwortest Fragen ausschließlich auf Basis der bereitgestellten Chat-Evidenz. "
                    "Wenn die Evidenz nicht reicht, sage das explizit. "
                    "Erfinde keine Belege und nutze keine externen Informationen."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        result = await self.chat_completion(
            base_url=settings.vllm_text_base_url,
            model=settings.text_model,
            messages=messages,
            max_tokens=4096,
        )
        return extract_chat_completion_text(result)

    async def synthesize_bluf(self, prompt: str) -> str:
        """Generate the main report BLUF from per-question summaries."""
        if not prompt.strip():
            raise ValueError("BLUF synthesis prompt is empty")

        if settings.llm_mock_enabled:
            prompt_preview = prompt.strip().replace("\n", " ")[:500]
            return (
                "[MOCK_BLUF] Diese BLUF wurde ohne vLLM-Server erzeugt. "
                "Die Pipeline hat die Kurzantworten der beantworteten Fragen geladen "
                "und einen Synthese-Prompt gebaut.\n\n"
                f"Promptvorschau: {prompt_preview}"
            )

        messages = [
            {
                "role": "system",
                "content": (
                    "Du erstellst eine knappe deutsche BLUF für einen analytischen Report. "
                    "Nutze ausschließlich die bereitgestellten Fragen und Kurzantworten. "
                    "Erfinde keine neuen Fakten, Belege oder Schlussfolgerungen."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        result = await self.chat_completion(
            base_url=settings.vllm_text_base_url,
            model=settings.text_model,
            messages=messages,
            max_tokens=1024,
        )
        bluf = extract_chat_completion_text(result)
        if not bluf:
            raise ValueError("vLLM BLUF synthesis returned empty text")
        return bluf

    async def answer_question(self, question: str, context: str) -> str:
        """Backward-compatible helper for callers that pass question/context."""
        prompt = f"Frage:\n{question}\n\nEvidenz:\n{context}\n\nAntwort:"
        return await self.answer_prompt(prompt)
