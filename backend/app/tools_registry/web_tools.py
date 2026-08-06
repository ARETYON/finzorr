"""Web page reading tool: fetch a URL and return its main text content.

Fetched content is UNTRUSTED — it is delimiter-wrapped so downstream prompts
treat it as data, never as instructions (indirect prompt-injection guard).
"""

import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from app.ai.base import ToolDefinition
from app.core.untrusted import wrap_untrusted
from app.tools_registry.dispatcher import register_tool

_TIMEOUT_S = 15.0
_MAX_CHARS = 6000
_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_STRIP_TAGS = ("script", "style", "nav", "footer", "header", "aside", "form", "iframe")


def _is_private_host(host: str) -> bool:
    """SSRF guard: refuse to fetch private/loopback/link-local addresses."""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()
    main = soup.find("article") or soup.find("main") or soup.body or soup
    text = " ".join(main.get_text(separator=" ").split())
    return text[:_MAX_CHARS]


_MAX_REDIRECTS = 5


def _url_blocked(url: str) -> str | None:
    """Validate scheme + resolve the host; returns an error string or None."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return "Error: a valid http(s) URL is required."
    if _is_private_host(parsed.hostname):
        return "Error: that address is not reachable."
    return None


async def _read_url(args: dict[str, Any]) -> str:
    raw_url = str(args.get("url", "")).strip()
    if error := _url_blocked(raw_url):
        return error
    # Redirects are followed manually: with follow_redirects=True a public
    # page could 302 to 169.254.169.254 / localhost and the private-host
    # guard (which only checked the ORIGINAL hostname) would never see it.
    async with httpx.AsyncClient(
        timeout=_TIMEOUT_S, headers={"User-Agent": _UA}, follow_redirects=False
    ) as client:
        url = raw_url
        for _hop in range(_MAX_REDIRECTS + 1):
            response = await client.get(url)
            if not response.is_redirect:
                break
            url = str(response.next_request.url) if response.next_request else ""
            if not url:
                return "Error: the page redirected nowhere readable."
            if error := _url_blocked(url):
                return error
        else:
            return "Error: too many redirects."
        response.raise_for_status()
        if "text/html" not in response.headers.get("content-type", "text/html"):
            return "Error: only HTML pages can be read."
        # lxml over a full page is CPU-bound — off the event loop, or four
        # concurrent deep-research parses stall every user's stream.
        text = await asyncio.to_thread(_extract_text, response.text)
    if not text:
        return "Error: the page contained no readable text."
    return wrap_untrusted(text, "page", header_extra=f'url="{raw_url}"')


register_tool(
    ToolDefinition(
        name="read_url",
        description=(
            "Fetch a web page by URL and return its main text content for "
            "summarizing or answering questions about it."
        ),
        input_schema={
            "type": "object",
            "properties": {"url": {"type": "string", "description": "Full http(s) URL"}},
            "required": ["url"],
        },
    ),
    _read_url,
    # own HTTP budget is 15s per request and up to 6 validated hops
    timeout_s=100.0,
)
