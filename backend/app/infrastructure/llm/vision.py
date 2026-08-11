"""Provider-gated image understanding.

Order: Gemini flash (free tier, multimodal) when GEMINI_API_KEY is set; else a
local Ollama vision model when VISION_MODEL is set (e.g. `llava`); else vision
is unavailable and callers show a friendly configuration hint.
"""

import base64

from langsmith import get_current_run_tree, traceable
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


@traceable(
    run_type="llm",
    name="vision.describe_image",
    # the image payload is a multi-MB base64 blob — never serialize it into
    # run inputs
    process_inputs=lambda inputs: {
        "question": inputs.get("question", ""),
        "mime": inputs.get("mime", ""),
        "image_bytes": len(inputs.get("image", b"") or b""),
    },
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
    run_tree = get_current_run_tree()
    if run_tree is not None:
        usage = getattr(response, "usage", None)
        run_tree.set(
            usage_metadata={
                "input_tokens": getattr(usage, "prompt_tokens", 0) or 0,
                "output_tokens": getattr(usage, "completion_tokens", 0) or 0,
                "total_tokens": getattr(usage, "total_tokens", 0) or 0,
            },
            metadata={
                "ls_provider": "gemini" if settings.GEMINI_API_KEY else "ollama",
                "ls_model_name": model,
            },
        )
    log.info("vision.answered", model=model, chars=len(answer))
    return answer
