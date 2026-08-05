"""Generic local-microservice tool connector (config-driven).

Point `MICROSERVICE_TOOLS_CONFIG` at a JSON file describing internal HTTP
APIs; each entry becomes an LLM tool with no code changes:

    [
      {
        "name": "crm_lookup",
        "description": "Look up a customer in the internal CRM by email.",
        "url": "http://localhost:9000/customers/search",
        "method": "GET",
        "input_schema": {
          "type": "object",
          "properties": {"email": {"type": "string"}},
          "required": ["email"]
        }
      }
    ]

GET sends arguments as query params; POST sends them as a JSON body.
A worked example config ships at `tools_registry/examples/microservices.json`.
"""

import json
from pathlib import Path
from typing import Any

import httpx

from app.ai.base import ToolDefinition
from app.core.config import settings
from app.core.logging import log
from app.tools_registry.dispatcher import register_tool

_TIMEOUT_S = 15.0
_MAX_RESPONSE_CHARS = 2000


def _make_handler(url: str, method: str):  # type: ignore[no-untyped-def]
    async def handler(arguments: dict[str, Any]) -> str:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            if method.upper() == "GET":
                response = await client.get(url, params=arguments)
            else:
                response = await client.post(url, json=arguments)
            response.raise_for_status()
            return response.text[:_MAX_RESPONSE_CHARS]

    return handler


def register_microservice_tools() -> int:
    """Load the config file (if set) and register each entry. Returns count."""
    if not settings.MICROSERVICE_TOOLS_CONFIG:
        return 0
    path = Path(settings.MICROSERVICE_TOOLS_CONFIG)
    if not path.exists():
        log.warning("microservice_tools.config_missing", path=str(path))
        return 0
    try:
        entries = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("microservice_tools.config_invalid", error=str(exc))
        return 0
    count = 0
    for entry in entries if isinstance(entries, list) else []:
        try:
            register_tool(
                ToolDefinition(
                    name=str(entry["name"]),
                    description=str(entry["description"]),
                    input_schema=entry.get(
                        "input_schema", {"type": "object", "properties": {}}
                    ),
                ),
                _make_handler(str(entry["url"]), str(entry.get("method", "POST"))),
            )
            count += 1
        except KeyError as exc:
            log.warning("microservice_tools.entry_invalid", missing=str(exc))
    log.info("microservice_tools.registered", tools=count)
    return count
