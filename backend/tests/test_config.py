from app.config import to_asyncpg_url


def test_neon_url_is_converted_for_asyncpg():
    url = "postgresql://u:p@ep-x.neon.tech/db?sslmode=require&channel_binding=require"
    assert to_asyncpg_url(url) == "postgresql+asyncpg://u:p@ep-x.neon.tech/db?ssl=require"


def test_local_url_without_ssl():
    assert (
        to_asyncpg_url("postgresql://postgres:postgres@localhost:5432/mwda")
        == "postgresql+asyncpg://postgres:postgres@localhost:5432/mwda"
    )
