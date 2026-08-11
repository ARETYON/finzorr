"""Provider registry: which vendors are configured, and their default models.

Ollama is always registered (local, keyless). Cloud vendors register only when
their API key is present — absence is graceful, never an error. The free-chain
order (groq -> gemini -> openrouter -> ollama) drives budget-exhaustion and
failure fallback in `completion.py`.
"""

from app.core.config import settings
from app.infrastructure.llm.openai_compatible import OpenAICompatibleProvider

FREE_CHAIN_ORDER = ["groq", "gemini", "openrouter", "ollama"]

_providers: dict[str, OpenAICompatibleProvider] | None = None
_default_models: dict[str, str] = {}


def _init() -> dict[str, OpenAICompatibleProvider]:
    global _providers  # noqa: PLW0603 — lazy module singleton
    if _providers is not None:
        return _providers
    providers: dict[str, OpenAICompatibleProvider] = {
        "ollama": OpenAICompatibleProvider(
            "ollama", f"{settings.OLLAMA_URL}/v1", api_key="ollama"
        )
    }
    _default_models["ollama"] = settings.LLM_MODEL
    if settings.GROQ_API_KEY:
        providers["groq"] = OpenAICompatibleProvider(
            "groq", "https://api.groq.com/openai/v1", settings.GROQ_API_KEY
        )
        _default_models["groq"] = settings.GROQ_MODEL
    if settings.GEMINI_API_KEY:
        providers["gemini"] = OpenAICompatibleProvider(
            "gemini",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            settings.GEMINI_API_KEY,
        )
        _default_models["gemini"] = settings.GEMINI_MODEL
    if settings.OPENROUTER_API_KEY:
        providers["openrouter"] = OpenAICompatibleProvider(
            "openrouter", "https://openrouter.ai/api/v1", settings.OPENROUTER_API_KEY
        )
        _default_models["openrouter"] = settings.OPENROUTER_MODEL
    if settings.HF_TOKEN:
        providers["huggingface"] = OpenAICompatibleProvider(
            "huggingface", "https://router.huggingface.co/v1", settings.HF_TOKEN
        )
        _default_models["huggingface"] = settings.HF_MODEL
    _providers = providers
    return providers


def get_provider(name: str | None = None) -> OpenAICompatibleProvider:
    """Resolve a provider by name, falling back to the configured default.

    If the requested provider was never registered (missing key), walk the
    free chain and return the first available — startup/config fallback.
    """
    providers = _init()
    target = name or settings.LLM_PROVIDER
    if target in providers:
        return providers[target]
    for candidate in FREE_CHAIN_ORDER:
        if candidate in providers:
            return providers[candidate]
    raise RuntimeError("no LLM provider configured")


def default_model(provider_name: str) -> str:
    """The configured default model for a provider (falls back to LLM_MODEL)."""
    _init()
    return _default_models.get(provider_name, settings.LLM_MODEL) or settings.LLM_MODEL


def available_providers() -> list[str]:
    """Names of every registered provider (for /health and diagnostics)."""
    return sorted(_init().keys())
