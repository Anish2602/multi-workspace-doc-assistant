from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.conftest import signup


async def test_signup_sets_httponly_cookie_and_default_workspace(client):
    r = await client.post(
        "/api/auth/signup", json={"email": "Alice@Example.com", "password": "password123"}
    )
    assert r.status_code == 201
    assert r.json()["email"] == "alice@example.com"
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie

    workspaces = (await client.get("/api/workspaces")).json()
    assert [w["name"] for w in workspaces] == ["My Workspace"]


async def test_duplicate_signup_rejected(client):
    await signup(client, "bob@example.com")
    r = await client.post(
        "/api/auth/signup", json={"email": "bob@example.com", "password": "x" * 8}
    )
    assert r.status_code == 409


async def test_login_wrong_password_and_unknown_email_look_identical(client):
    await signup(client, "carol@example.com")
    client.cookies.clear()
    wrong = await client.post(
        "/api/auth/login", json={"email": "carol@example.com", "password": "wrongpass1"}
    )
    unknown = await client.post(
        "/api/auth/login", json={"email": "nobody@example.com", "password": "wrongpass1"}
    )
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


async def test_login_then_me_then_logout(client):
    await signup(client, "dave@example.com")
    client.cookies.clear()
    assert (await client.get("/api/auth/me")).status_code == 401
    r = await client.post(
        "/api/auth/login", json={"email": "dave@example.com", "password": "password123"}
    )
    assert r.status_code == 200
    assert (await client.get("/api/auth/me")).json()["email"] == "dave@example.com"
    await client.post("/api/auth/logout")
    client.cookies.clear()
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_tampered_token_rejected(client):
    await signup(client, "erin@example.com")
    client.cookies.set("mwda_session", "not-a-real-jwt")
    assert (await client.get("/api/workspaces")).status_code == 401


async def test_create_and_list_workspaces(client):
    await signup(client, "frank@example.com")
    r = await client.post("/api/workspaces", json={"name": "Project Falcon"})
    assert r.status_code == 201
    names = [w["name"] for w in (await client.get("/api/workspaces")).json()]
    assert names == ["My Workspace", "Project Falcon"]


async def test_other_users_workspace_is_404(client, sessionmaker):
    await signup(client, "owner@example.com")
    owner_ws = (await client.get("/api/workspaces")).json()[0]["id"]

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as intruder:
        await signup(intruder, "intruder@example.com")
        r = await intruder.get(f"/api/workspaces/{owner_ws}")
        assert r.status_code == 404
        listed = [w["id"] for w in (await intruder.get("/api/workspaces")).json()]
        assert owner_ws not in listed


async def test_unauthenticated_requests_rejected(client):
    assert (await client.get("/api/workspaces")).status_code == 401
    assert (await client.post("/api/workspaces", json={"name": "x"})).status_code == 401
