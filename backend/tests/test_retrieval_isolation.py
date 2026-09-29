"""The tenancy boundary: a workspace can never retrieve another workspace's chunks."""

import uuid

from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.main import app
from app.models import Chunk, Document, User, Workspace, WorkspaceMember
from app.retrieval.service import search
from tests.conftest import first_workspace_id, signup, upload
from tests.fakes import FakeEmbedder

PIXEL_FACT = b"# Office\n\nThe office cat is named Pixel and sleeps on the printer."
FALCON_SPEC = b"# Falcon\n\nProject Falcon launches on 14 March. Build code FALCON-7731."


async def test_fact_in_workspace_a_is_invisible_from_workspace_b(client):
    await signup(client, "iso@example.com")
    ws_a = await first_workspace_id(client)
    ws_b = (await client.post("/api/workspaces", json={"name": "B"})).json()["id"]
    await upload(client, ws_a, "office.md", PIXEL_FACT)
    await upload(client, ws_b, "falcon.md", FALCON_SPEC)

    from_a = (
        await client.get(f"/api/workspaces/{ws_a}/search", params={"q": "office cat name"})
    ).json()
    assert any("Pixel" in c["content"] for c in from_a["chunks"])

    from_b = (
        await client.get(f"/api/workspaces/{ws_b}/search", params={"q": "office cat name"})
    ).json()
    assert from_b["workspace_id"] == ws_b
    assert all("Pixel" not in c["content"] for c in from_b["chunks"])
    assert all(c["filename"] == "falcon.md" for c in from_b["chunks"])


async def test_empty_workspace_returns_nothing_even_if_others_match(client):
    await signup(client, "iso2@example.com")
    ws_a = await first_workspace_id(client)
    ws_empty = (await client.post("/api/workspaces", json={"name": "Empty"})).json()["id"]
    await upload(client, ws_a, "office.md", PIXEL_FACT)

    r = (await client.get(f"/api/workspaces/{ws_empty}/search", params={"q": "Pixel cat"})).json()
    assert r["chunks"] == [] and r["hit"] is False


async def test_keyword_leg_finds_exact_codes(client):
    await signup(client, "kw@example.com")
    ws = await first_workspace_id(client)
    await upload(client, ws, "falcon.md", FALCON_SPEC)
    r = (await client.get(f"/api/workspaces/{ws}/search", params={"q": "FALCON-7731"})).json()
    assert r["chunks"] and r["chunks"][0]["keyword_rank"] == 1


async def test_search_in_foreign_workspace_is_404(client):
    await signup(client, "victim@example.com")
    ws = await first_workspace_id(client)
    await upload(client, ws, "office.md", PIXEL_FACT)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as attacker:
        await signup(attacker, "attacker@example.com")
        r = await attacker.get(f"/api/workspaces/{ws}/search", params={"q": "Pixel"})
        assert r.status_code == 404


# --- The HNSW + filter pitfall -------------------------------------------------


async def _seed_big_and_small_workspace(session, embedder: FakeEmbedder):
    """Workspace BIG: 300 chunks all about cats. Workspace SMALL: 1 chunk about budgets.

    SMALL's chunk shares one word ("cat") with the query: it ranks below all 300 BIG
    chunks (so a naive filtered HNSW scan misses it) but isn't orthogonal to every
    other vector. A fully orthogonal vector can end up unreachable in the HNSW
    graph, which made an earlier version of this test flaky.
    """
    user = User(email=f"{uuid.uuid4()}@example.com", password_hash="x")
    session.add(user)
    await session.flush()
    big, small = Workspace(name="big", owner_id=user.id), Workspace(name="small", owner_id=user.id)
    session.add_all([big, small])
    await session.flush()
    session.add_all([WorkspaceMember(workspace_id=w.id, user_id=user.id) for w in (big, small)])

    async def add(ws, texts):
        doc = Document(
            workspace_id=ws.id,
            filename=f"{ws.name}.txt",
            content_type="text/plain",
            content_hash=uuid.uuid4().hex,
            size_bytes=1,
            status="ready",
            chunk_count=len(texts),
        )
        session.add(doc)
        await session.flush()
        vectors = await embedder.embed_documents(texts)
        session.add_all(
            Chunk(
                id=uuid.uuid4().hex,
                workspace_id=ws.id,
                document_id=doc.id,
                chunk_index=i,
                content=t,
                embedding=v,
            )
            for i, (t, v) in enumerate(zip(texts, vectors, strict=True))
        )

    await add(big, [f"cats cat kitten feline whiskers note {i}" for i in range(300)])
    await add(small, ["cat food budget spreadsheet totals"])
    await session.commit()
    return big, small


async def _force_hnsw_plan(session) -> None:
    """Make the planner use the HNSW index for a filtered query.

    Normally, for a selective filter (a small workspace), Postgres uses the btree
    index on workspace_id and sorts exactly, which is always correct. The pitfall
    only appears when the planner picks HNSW (large / unselective workspaces), so
    we force that plan: drop the btree index inside this transaction (rolled back
    afterwards) and disable the alternatives.
    """
    await session.execute(text("DROP INDEX ix_chunks_workspace_id"))
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    await session.execute(text("SET LOCAL enable_bitmapscan = off"))


async def test_hnsw_filter_pitfall_is_real_and_iterative_scan_fixes_it(session, embedder):
    big, small = await _seed_big_and_small_workspace(session, embedder)
    small_id = small.id  # plain value: ORM objects are expired by the rollbacks below
    qvec = "[" + ",".join(map(str, await embedder.embed_query("cat kitten"))) + "]"

    await _force_hnsw_plan(session)
    await session.execute(text("SET LOCAL hnsw.ef_search = 40"))
    await session.execute(text("SET LOCAL hnsw.iterative_scan = off"))
    naive_sql = text(
        "SELECT id FROM chunks WHERE workspace_id = :ws "
        "ORDER BY embedding <=> CAST(:q AS vector) LIMIT 5"
    )
    plan = "\n".join(
        (await session.execute(text(f"EXPLAIN {naive_sql.text}"), {"ws": small_id, "q": qvec}))
        .scalars()
        .all()
    )
    assert "ix_chunks_embedding_hnsw" in plan
    naive = (await session.execute(naive_sql, {"ws": small_id, "q": qvec})).all()
    # HNSW returns the 40 globally-nearest rows (all BIG's cat chunks); the
    # workspace filter then removes every one, so SMALL gets nothing back.
    assert naive == []
    await session.rollback()

    # search() turns on iterative scan: same forced HNSW plan, correct result,
    # and never a BIG chunk.
    await _force_hnsw_plan(session)
    result = await search(session, embedder, small_id, "cat kitten", hybrid=False)
    assert [c.content for c in result.chunks] == ["cat food budget spreadsheet totals"]
    await session.rollback()
