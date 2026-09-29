from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from app.main import app
from app.models import Chunk
from tests.conftest import first_workspace_id, signup, upload
from tests.docgen import make_docx, make_pdf

HANDBOOK = make_pdf(["Welcome to Acme.", "The office cat is named Pixel."])


async def chunk_count(session, workspace_id=None) -> int:
    q = select(func.count()).select_from(Chunk)
    if workspace_id:
        q = q.where(Chunk.workspace_id == workspace_id)
    return await session.scalar(q)


async def test_upload_pdf_creates_chunks_tagged_with_workspace(client, session):
    await signup(client, "a@example.com")
    ws = await first_workspace_id(client)
    r = await upload(client, ws, "handbook.pdf", HANDBOOK)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["duplicate"] is False and body["document"]["chunk_count"] >= 1

    rows = (await session.scalars(select(Chunk))).all()
    assert rows and all(str(c.workspace_id) == ws for c in rows)
    assert any(c.page == 2 and "Pixel" in c.content for c in rows)


async def test_reupload_same_file_is_idempotent(client, session, embedder):
    await signup(client, "b@example.com")
    ws = await first_workspace_id(client)
    first = await upload(client, ws, "handbook.pdf", HANDBOOK)
    before = await chunk_count(session)
    calls_before = embedder.calls

    second = await upload(client, ws, "renamed-copy.pdf", HANDBOOK)
    assert second.status_code == 200
    assert second.json()["duplicate"] is True
    assert second.json()["document"]["id"] == first.json()["document"]["id"]
    assert await chunk_count(session) == before
    assert embedder.calls == calls_before  # no wasted embedding quota either
    assert len((await client.get(f"/api/workspaces/{ws}/documents")).json()) == 1


async def test_same_file_in_two_workspaces_is_stored_separately(client, session):
    await signup(client, "c@example.com")
    ws_a = await first_workspace_id(client)
    ws_b = (await client.post("/api/workspaces", json={"name": "B"})).json()["id"]
    await upload(client, ws_a, "h.pdf", HANDBOOK)
    await upload(client, ws_b, "h.pdf", HANDBOOK)
    assert await chunk_count(session, ws_a) == await chunk_count(session, ws_b) > 0


async def test_two_documents_docx_and_markdown(client):
    await signup(client, "d@example.com")
    ws = await first_workspace_id(client)
    docx = make_docx({"Leave Policy": ["Employees get 24 days of annual leave."]})
    assert (await upload(client, ws, "policy.docx", docx)).status_code == 201
    assert (
        await upload(client, ws, "notes.md", b"# Notes\n\nFalcon launches in March.")
    ).status_code == 201
    names = {d["filename"] for d in (await client.get(f"/api/workspaces/{ws}/documents")).json()}
    assert names == {"policy.docx", "notes.md"}


async def test_bad_file_rejected_and_nothing_saved(client, session):
    await signup(client, "e@example.com")
    ws = await first_workspace_id(client)
    r = await upload(client, ws, "fake.pdf", b"definitely not a pdf")
    assert r.status_code == 422
    assert (await client.get(f"/api/workspaces/{ws}/documents")).json() == []
    assert await chunk_count(session) == 0


async def test_embedding_outage_returns_503_and_saves_nothing(client, session, embedder):
    await signup(client, "f@example.com")
    ws = await first_workspace_id(client)
    embedder.fail = True
    r = await upload(client, ws, "handbook.pdf", HANDBOOK)
    assert r.status_code == 503
    assert "retry" in r.json()["detail"].lower()
    assert (await client.get(f"/api/workspaces/{ws}/documents")).json() == []
    embedder.fail = False
    assert (await upload(client, ws, "handbook.pdf", HANDBOOK)).status_code == 201


async def test_oversized_upload_rejected(client, monkeypatch):
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "max_upload_bytes", 100)
    await signup(client, "g@example.com")
    ws = await first_workspace_id(client)
    assert (await upload(client, ws, "big.txt", b"x" * 500)).status_code == 413


async def test_cannot_upload_list_or_delete_in_someone_elses_workspace(client):
    await signup(client, "owner2@example.com")
    ws = await first_workspace_id(client)
    doc_id = (await upload(client, ws, "h.pdf", HANDBOOK)).json()["document"]["id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        await signup(other, "other2@example.com")
        assert (await upload(other, ws, "x.txt", b"hello there")).status_code == 404
        assert (await other.get(f"/api/workspaces/{ws}/documents")).status_code == 404
        own_ws = await first_workspace_id(other)
        # Even via their own workspace id, the other user's doc id doesn't resolve.
        r = await other.delete(f"/api/workspaces/{own_ws}/documents/{doc_id}")
        assert r.status_code == 404
    assert len((await client.get(f"/api/workspaces/{ws}/documents")).json()) == 1


async def test_delete_document_removes_its_chunks(client, session):
    await signup(client, "h@example.com")
    ws = await first_workspace_id(client)
    doc_id = (await upload(client, ws, "h.pdf", HANDBOOK)).json()["document"]["id"]
    r = await client.delete(f"/api/workspaces/{ws}/documents/{doc_id}")
    assert r.status_code == 204
    assert await chunk_count(session) == 0
