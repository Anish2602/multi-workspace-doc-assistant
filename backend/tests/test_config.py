import pytest

from app.config import Settings, to_asyncpg_url


@pytest.mark.parametrize("wrapped", ['"{u}"', "'{u}'", "  {u}\n"])
def test_database_url_tolerates_quotes_and_whitespace(wrapped):
    url = "postgresql://u:p@ep-x.neon.tech/db?sslmode=require"
    settings = Settings(database_url=wrapped.format(u=url), _env_file=None)
    assert settings.async_database_url == "postgresql+asyncpg://u:p@ep-x.neon.tech/db?ssl=require"


def test_neon_url_is_converted_for_asyncpg():
    url = "postgresql://u:p@ep-x.neon.tech/db?sslmode=require&channel_binding=require"
    assert to_asyncpg_url(url) == "postgresql+asyncpg://u:p@ep-x.neon.tech/db?ssl=require"


def test_local_url_without_ssl():
    assert (
        to_asyncpg_url("postgresql://postgres:postgres@localhost:5432/mwda")
        == "postgresql+asyncpg://postgres:postgres@localhost:5432/mwda"
    )
