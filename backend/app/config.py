"""Application settings, loaded from environment variables (and `.env` locally).

Secrets live only here and are typed as SecretStr so they never show up in
reprs or logs by accident.
"""

from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(".env", "../.env"), extra="ignore")

    env: str = "development"
    database_url: SecretStr = SecretStr("postgresql://postgres:postgres@localhost:5432/mwda")

    @field_validator("database_url", mode="before")
    @classmethod
    def _strip_quotes(cls, value: object) -> object:
        # Dashboards (Neon's copy button, Render's env UI) often carry the value
        # wrapped in quotes; `.env` parsing strips them but Docker/Render don't.
        if isinstance(value, str):
            return value.strip().strip("'\"").strip()
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
