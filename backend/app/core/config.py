"""Application settings.

Every value here must also appear in `backend/.env.example` — that parity is a
house rule (checked in CI) because a missing example entry is how deploys break
silently. Settings are read once at import time from the environment / `.env`.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven configuration for all components."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Environment ---
    APP_ENV: str = "dev"  # dev | uat | prod
    LOG_LEVEL: str = "INFO"

    # --- Data stores ---
    DATABASE_URL: str = "postgresql+asyncpg://finzorr:finzorr@localhost:5433/finzorr"
    NL2SQL_RO_DATABASE_URL: str = (
        "postgresql+asyncpg://finzorr_nl2sql_ro:nl2sql_ro@localhost:5433/finzorr"
    )
    REDIS_URL: str = "redis://localhost:6380/0"
    QDRANT_URL: str = "http://localhost:6335"
    QDRANT_API_KEY: str = ""

    # --- LLM providers (free chain: groq -> gemini -> openrouter -> ollama) ---
    LLM_PROVIDER: str = "ollama"  # ollama | groq | gemini | openrouter | huggingface
    LLM_MODEL: str = "qwen2.5:14b-instruct"
    LLM_FALLBACK_PROVIDER: str = ""  # optional one-bounded-retry target
    SUPERVISOR_MODEL: str = ""  # small/fast model for routing; empty = LLM_MODEL
    OLLAMA_URL: str = "http://localhost:11434"
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "llama-3.3-70b-versatile"
    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.0-flash"
    OPENROUTER_API_KEY: str = ""
    OPENROUTER_MODEL: str = ""
    HF_TOKEN: str = ""
    HF_MODEL: str = "openai/gpt-oss-120b"

    # --- Embeddings (host Ollama in dev; ollama-embed container in uat/prod) ---
    EMBED_OLLAMA_URL: str = "http://localhost:11434"
    EMBED_MODEL: str = "nomic-embed-text:v1.5"
    EMBED_DIM: int = 768

    # --- Auth ---
    GOOGLE_CLIENT_ID: str = ""
    SESSION_SECRET: str = "dev-only-change-me"  # noqa: S105 — dev default, overridden per env
    SESSION_TTL_DAYS: int = 7
    DEV_FAKE_AUTH: bool = False  # only honored when APP_ENV == "dev"
    COOKIE_DOMAIN: str = ""  # ".finzorr.ai" in uat/prod; empty locally

    # --- Frontend / CORS / WS origin check ---
    FRONTEND_ORIGIN: str = "http://localhost:5173"

    # --- Rate limits & quotas ---
    RATE_LIMIT_MESSAGES: int = 20
    RATE_LIMIT_WINDOW_S: int = 300
    DAILY_TOKEN_BUDGET: int = 2_000_000  # per provider per day; 0 disables

    # --- Web search ---
    TAVILY_API_KEY: str = ""
    SEARXNG_URL: str = ""

    # --- Documents ---
    DOCUMENT_STORAGE_DIR: str = "storage/uploads"
    MAX_UPLOAD_MB: int = 10
    MAX_UPLOAD_PAGES: int = 100
    MAX_DOCS_PER_USER: int = 20

    # --- External tool integrations ---
    GITHUB_TOKEN: str = ""
    MICROSERVICE_TOOLS_CONFIG: str = ""  # path to a JSON config of local API tools

    @property
    def is_dev(self) -> bool:
        """True in local development only."""
        return self.APP_ENV == "dev"


@lru_cache
def get_settings() -> Settings:
    """Cached singleton accessor used across the app."""
    return Settings()


settings = get_settings()
