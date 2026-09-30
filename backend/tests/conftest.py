"""Test fixtures: a real Postgres + pgvector (docker compose `db`), fresh per test."""

import os
import re
from collections.abc import AsyncIterator

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.chat.router import _chat_model
from app.config import to_asyncpg_url
from app.db import get_session, get_sessionmaker
from app.llm.embeddings import get_embedder
from app.main import app
from app.models import Base
from tests.fakes import FakeChat, FakeEmbedder

TEST_DATABASE_URL = to_asyncpg_url(
    os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/mwda_test")
)


@pytest.fixture(autouse=True)
def _no_real_network(monkeypatch):
    """Tests must never reach real services (they once posted to our real Discord).

    Any outbound HTTP through httpx's network transports fails loudly. The in-process
    ASGI transport used to call the app is unaffected.
    """

    def blocked(self, request, *args, **kwargs):
        raise RuntimeError(f"Real network call attempted in tests: {request.url.host}")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", blocked)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)


class FakeDiscord:
    """Records what the send_discord_summary tool would have posted."""

    def __init__(self) -> None:
        self.posts: list[dict] = []
        self.status_code = 204

    async def post(self, url, json=None, **_kwargs):
        self.posts.append({"url": url, "json": json})
        return httpx.Response(self.status_code)


@pytest.fixture(autouse=True)
def discord(monkeypatch) -> FakeDiscord:
    from app.config import get_settings
    from app.tools import builtin

    fake = FakeDiscord()
    monkeypatch.setattr(
        get_settings(), "discord_webhook_url", SecretStr("https://discord.invalid/webhook")
    )
    monkeypatch.setattr(builtin, "get_http_client", lambda: fake)
    return fake


@pytest.fixture(scope="session")
async def engine():
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def sessionmaker(engine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    yield async_sessionmaker(engine, expire_on_commit=False)
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {tables} CASCADE"))


@pytest.fixture
async def session(sessionmaker) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as s:
        yield s


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def chat_model() -> FakeChat:
    return FakeChat()


@pytest.fixture
async def client(sessionmaker, embedder, chat_model) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_sessionmaker] = lambda: sessionmaker
    app.dependency_overrides[get_embedder] = lambda: embedder
    app.dependency_overrides[_chat_model] = lambda: chat_model
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def username_for(email: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "", email.split("@")[0])[:30].ljust(3, "0")


async def signup(
    client: AsyncClient, email: str, password: str = "password123", username: str | None = None
) -> dict:
    r = await client.post(
        "/api/auth/signup",
        json={
            "email": email,
            "username": username or username_for(email),
            "password": password,
            "confirm_password": password,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def first_workspace_id(client: AsyncClient) -> str:
    return (await client.get("/api/workspaces")).json()[0]["id"]


async def upload(client: AsyncClient, workspace_id: str, name: str, data: bytes):
    return await client.post(
        f"/api/workspaces/{workspace_id}/documents", files={"file": (name, data)}
    )
