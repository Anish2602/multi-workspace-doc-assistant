"""Application settings, loaded from environment variables (and `.env` locally).

Secrets live only here and are typed as SecretStr so they never show up in
reprs or logs by accident.
"""

from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    env: str = "development"
    database_url: SecretStr = SecretStr("postgresql://postgres:postgres@localhost:5432/mwda")

    # Auth. JWT_SECRET must be set in production (Render generates one).
    jwt_secret: SecretStr = SecretStr("dev-only-insecure-secret-change-me")
    jwt_ttl_hours: int = 24 * 7
    cookie_name: str = "mwda_session"

    # LLM / embeddings (free tiers). Keys are optional so tests/dev boot without them.
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    embedding_model: str = "gemini-embedding-001"
    # Tried in order on timeout / 429 / 5xx. Pinned versions, not -latest aliases.
    gemini_chat_models: list[str] = ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite"]
    groq_chat_model: str = "openai/gpt-oss-120b"
    llm_timeout_seconds: float = 30.0

    # Tool side effects
    discord_webhook_url: SecretStr | None = None

    # Ingestion
    max_upload_bytes: int = 10 * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @model_validator(mode="after")
    def _require_real_secret_in_production(self) -> "Settings":
        if self.is_production and self.jwt_secret.get_secret_value().startswith("dev-only"):
            raise ValueError("JWT_SECRET must be set in production")
        return self

    @field_validator(
        "database_url", "gemini_api_key", "groq_api_key", "discord_webhook_url", mode="before"
    )
    @classmethod
    def _strip_quotes(cls, value: object) -> object:
        # Dashboards (Neon's copy button, Render's env UI) often carry the value
        # wrapped in quotes; `.env` parsing strips them but Docker/Render don't.
        if isinstance(value, str):
            return value.strip().strip("'\"").strip() or None
        return value

    @property
    def async_database_url(self) -> str:
        return to_asyncpg_url(self.database_url.get_secret_value())


def to_asyncpg_url(url: str) -> str:
    """Convert a libpq-style URL (as Neon hands out) into an asyncpg SQLAlchemy URL.

    asyncpg rejects libpq-only query params like `sslmode` and `channel_binding`,
    so `sslmode=require` becomes asyncpg's `ssl=require` and the rest are dropped.
    """
    parts = urlsplit(url)
    scheme = "postgresql+asyncpg"
    query = dict(parse_qsl(parts.query))
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)
    if sslmode and sslmode != "disable":
        query["ssl"] = "require"
    return urlunsplit((scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


@lru_cache
def get_settings() -> Settings:
    return Settings()
