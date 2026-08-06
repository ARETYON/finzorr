"""Image-generation slot (GATED-ON-KEY).

$0-quality generation is not viable locally (no GPU), so this registers only
when an OpenAI-images-compatible endpoint is configured. Generated images are
stored as the user's attachments and returned as a markdown image link.
"""

import base64
import uuid
from typing import Any

import httpx

from app.ai.base import ToolDefinition
from app.core.config import settings
from app.core.logging import log
from app.core.request_context import get_current_user_id
from app.documents.storage import get_storage
from app.tools_registry.dispatcher import register_tool

_TIMEOUT_S = 60.0


async def _generate(args: dict[str, Any]) -> str:
    prompt = str(args.get("prompt", "")).strip()
    if not prompt:
        return "Error: 'prompt' is required."
    user_id = get_current_user_id()
    if not user_id:
        return "Error: no user context."
    async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
        response = await client.post(
            settings.IMAGE_API_URL,
            headers={"Authorization": f"Bearer {settings.IMAGE_API_KEY}"},
            json={"model": settings.IMAGE_MODEL, "prompt": prompt, "response_format": "b64_json"},
        )
        response.raise_for_status()
        b64 = response.json()["data"][0]["b64_json"]
    token = f"gen-{uuid.uuid4().hex}.png"
    await get_storage().save(f"attachments/{user_id}/{token}", base64.b64decode(b64))
    log.info("image.generated", user_id=user_id)
    return (
        "Image generated. Embed it exactly as: "
        f"![generated image](/api/chat/attachments/{token})"
    )


def register_image_tools() -> int:
    if not (settings.IMAGE_API_URL and settings.IMAGE_API_KEY and settings.IMAGE_MODEL):
        return 0
    register_tool(
        ToolDefinition(
            name="generate_image",
            description="Generate an image from a text prompt.",
            input_schema={
                "type": "object",
                "properties": {"prompt": {"type": "string"}},
                "required": ["prompt"],
            },
        ),
        _generate,
        timeout_s=75.0,  # image APIs are slow; the handler's own cap is 60s
    )
    return 1
