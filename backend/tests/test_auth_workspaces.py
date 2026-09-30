import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.conftest import signup


async def test_signup_sets_httponly_cookie_and_default_workspace(client):
    r = await client.post(
        "/api/auth/signup",
        json={
            "email": "Alice@Example.com",
            "username": "Alice",
            "password": "password123",
            "confirm_password": "password123",
        },
    )
    assert r.status_code == 201
    assert r.json()["email"] == "alice@example.com"
    assert r.json()["username"] == "Alice"  # display casing preserved
    set_cookie = r.headers["set-cookie"].lower()
    assert "httponly" in set_cookie and "samesite=lax" in set_cookie

    workspaces = (await client.get("/api/workspaces")).json()
    assert [w["name"] for w in workspaces] == ["My Workspace"]


async def test_duplicate_signup_rejected(client):
    await signup(client, "bob@example.com")
    r = await client.post(
        "/api/auth/signup",
        json={
            "email": "bob@example.com",
            "username": "someone_else",
            "password": "x" * 8,
            "confirm_password": "x" * 8,
        },
    )
    assert r.status_code == 409
    assert "email" in r.json()["detail"]


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


# --- usernames ---------------------------------------------------------------------------


def _signup_body(**overrides) -> dict:
    body = {
        "email": "u@example.com",
        "username": "user_one",
        "password": "password123",
        "confirm_password": "password123",
    }
    return {**body, **overrides}


async def test_password_confirmation_must_match(client):
    r = await client.post("/api/auth/signup", json=_signup_body(confirm_password="password124"))
    assert r.status_code == 422
    assert "do not match" in r.text


@pytest.mark.parametrize(
    "bad", ["ab", "has space", "anish@home", "x" * 31, "semi;colon", "émoji😀"]
)
async def test_invalid_usernames_rejected(client, bad):
    r = await client.post("/api/auth/signup", json=_signup_body(username=bad))
    assert r.status_code == 422


async def test_username_taken_case_insensitively(client):
    await signup(client, "first@example.com", username="Anish")
    r = await client.post(
        "/api/auth/signup", json=_signup_body(email="second@example.com", username="aNiSh")
    )
    assert r.status_code == 409
    assert "username" in r.json()["detail"]


@pytest.mark.parametrize("identifier", ["Anish_K", "anish_k", "ANISH_K", "anish@example.com"])
async def test_login_with_username_or_email(client, identifier):
    await signup(client, "anish@example.com", username="Anish_K")
    client.cookies.clear()
    r = await client.post(
        "/api/auth/login", json={"identifier": identifier, "password": "password123"}
    )
    assert r.status_code == 200, r.text
    me = (await client.get("/api/auth/me")).json()
    assert me["username"] == "Anish_K" and me["email"] == "anish@example.com"


async def test_login_still_accepts_legacy_email_field(client):
    await signup(client, "legacy@example.com")
    client.cookies.clear()
    r = await client.post(
        "/api/auth/login", json={"email": "legacy@example.com", "password": "password123"}
    )
    assert r.status_code == 200


async def test_wrong_password_by_username_is_generic_401(client):
    await signup(client, "zed@example.com", username="zed")
    client.cookies.clear()
    bad_user = await client.post(
        "/api/auth/login", json={"identifier": "nobody", "password": "password123"}
    )
    bad_pass = await client.post(
        "/api/auth/login", json={"identifier": "zed", "password": "wrongpass1"}
    )
    assert bad_user.status_code == bad_pass.status_code == 401
    assert bad_user.json() == bad_pass.json()
