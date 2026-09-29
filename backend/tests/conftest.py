"""Test fixtures: a real Postgres + pgvector (docker compose `db`), fresh per test."""

import os
from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.config import to_asyncpg_url
from app.db import get_session
from app.llm.embeddings import get_embedder
from app.main import app
from app.models import Base
from tests.fakes import FakeEmbedder

TEST_DATABASE_URL = to_asyncpg_url(
    os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5433/mwda_test")
)


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
async def client(sessionmaker, embedder) -> AsyncIterator[AsyncClient]:
    async def override() -> AsyncIterator[AsyncSession]:
        async with sessionmaker() as s:
            yield s

    app.dependency_overrides[get_session] = override
    app.dependency_overrides[get_embedder] = lambda: embedder
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def signup(client: AsyncClient, email: str, password: str = "password123") -> dict:
    r = await client.post("/api/auth/signup", json={"email": email, "password": password})
    assert r.status_code == 201, r.text
    return r.json()


async def first_workspace_id(client: AsyncClient) -> str:
    return (await client.get("/api/workspaces")).json()[0]["id"]


async def upload(client: AsyncClient, workspace_id: str, name: str, data: bytes):
    return await client.post(
        f"/api/workspaces/{workspace_id}/documents", files={"file": (name, data)}
    )
