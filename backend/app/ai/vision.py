"""Provider-gated image understanding.

Order: Gemini flash (free tier, multimodal) when GEMINI_API_KEY is set; else a
local Ollama vision model when VISION_MODEL is set (e.g. `llava`); else vision
is unavailable and callers show a friendly configuration hint.
"""

import base64

from openai import AsyncOpenAI

from app.core.config import settings
from app.core.logging import log

_MAX_ANSWER_TOKENS = 1024


def vision_available() -> bool:
    return bool(settings.GEMINI_API_KEY or settings.VISION_MODEL)


def _client_and_model() -> tuple[AsyncOpenAI, str]:
    if settings.GEMINI_API_KEY:
        return (
            AsyncOpenAI(
                base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                api_key=settings.GEMINI_API_KEY,
            ),
            settings.GEMINI_MODEL,
        )
    return (
        AsyncOpenAI(base_url=f"{settings.OLLAMA_URL}/v1", api_key="ollama"),
        settings.VISION_MODEL,
    )


async def describe_image(question: str, image: bytes, mime: str) -> str:
    """Answer a question about one image; raises on provider failure."""
    client, model = _client_and_model()
    data_uri = f"data:{mime};base64,{base64.b64encode(image).decode()}"
    response = await client.chat.completions.create(
        model=model,
        max_tokens=_MAX_ANSWER_TOKENS,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": question or "Describe this image."},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
    )
    answer = response.choices[0].message.content or ""
    log.info("vision.answered", model=model, chars=len(answer))
    return answer
