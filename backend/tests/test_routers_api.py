"""Router-level tests over a real Postgres: auth, CRUD, and — above all —
the ownership boundaries that ARE the product's multi-tenancy. A regression
that drops a `session.user_id != user.id` check must fail here."""

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------- auth

async def test_me_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/auth/me")).status_code == 401


async def test_dev_login_sets_cookie_and_me_works(user_client: AsyncClient) -> None:
    response = await user_client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.json()["email"] == "dev@localhost"


async def test_update_custom_instructions(user_client: AsyncClient) -> None:
    response = await user_client.patch(
        "/api/auth/me", json={"custom_instructions": "reply in bullet points"}
    )
    assert response.status_code == 200
    assert response.json()["custom_instructions"] == "reply in bullet points"


# ------------------------------------------------------- sessions CRUD

async def _create_session(client: AsyncClient) -> str:
    response = await client.post("/api/chat/sessions", json={})
    assert response.status_code in (200, 201), response.text
    return str(response.json()["id"])


async def test_create_and_list_sessions(user_client: AsyncClient) -> None:
    session_id = await _create_session(user_client)
    listed = await user_client.get("/api/chat/sessions")
    assert listed.status_code == 200
    assert session_id in {s["id"] for s in listed.json()}


async def test_sessions_require_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/chat/sessions")).status_code == 401
    assert (await client.post("/api/chat/sessions", json={})).status_code == 401


async def test_list_messages_of_own_empty_session(user_client: AsyncClient) -> None:
    session_id = await _create_session(user_client)
    response = await user_client.get(f"/api/chat/sessions/{session_id}/messages")
    assert response.status_code == 200
    assert response.json() == []


# ------------------------------------------------- ownership boundaries

async def test_other_user_cannot_read_my_messages(
    user_client: AsyncClient, other_client: AsyncClient
) -> None:
    session_id = await _create_session(user_client)
    response = await other_client.get(f"/api/chat/sessions/{session_id}/messages")
    assert response.status_code == 404


async def test_other_user_cannot_delete_my_session(
    user_client: AsyncClient, other_client: AsyncClient
) -> None:
    session_id = await _create_session(user_client)
    assert (await other_client.delete(f"/api/chat/sessions/{session_id}")).status_code == 404
    # still mine, still listed
    listed = await user_client.get("/api/chat/sessions")
    assert session_id in {s["id"] for s in listed.json()}


async def test_other_user_cannot_share_my_session(
    user_client: AsyncClient, other_client: AsyncClient
) -> None:
    session_id = await _create_session(user_client)
    response = await other_client.post(f"/api/chat/sessions/{session_id}/share")
    assert response.status_code == 404


async def test_delete_own_session(user_client: AsyncClient) -> None:
    session_id = await _create_session(user_client)
    assert (await user_client.delete(f"/api/chat/sessions/{session_id}")).status_code in (200, 204)
    listed = await user_client.get("/api/chat/sessions")
    assert session_id not in {s["id"] for s in listed.json()}


# ------------------------------------------------------------- sharing

async def test_share_link_is_public_and_readonly(
    user_client: AsyncClient, client: AsyncClient
) -> None:
    session_id = await _create_session(user_client)
    created = await user_client.post(f"/api/chat/sessions/{session_id}/share")
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    # unauthenticated read works…
    shared = await client.get(f"/api/share/{token}")
    assert shared.status_code == 200
    # …and an unknown token 404s
    assert (await client.get("/api/share/00000000-0000-0000-0000-000000000000")).status_code == 404


# ----------------------------------------------------------- watchlist

async def test_watchlist_add_list_delete_idempotent(user_client: AsyncClient) -> None:
    first = await user_client.post("/api/watchlist", json={"symbol": "TCS"})
    assert first.status_code in (200, 201), first.text
    dup = await user_client.post("/api/watchlist", json={"symbol": "TCS"})
    assert dup.status_code in (200, 201, 409)
    listed = await user_client.get("/api/watchlist")
    symbols = [w["symbol"] for w in listed.json()]
    assert symbols.count("TCS") == 1
    assert (await user_client.delete("/api/watchlist/TCS")).status_code in (200, 204)


async def test_watchlist_is_per_user(
    user_client: AsyncClient, other_client: AsyncClient
) -> None:
    await user_client.post("/api/watchlist", json={"symbol": "INFY"})
    listed = await other_client.get("/api/watchlist")
    assert listed.status_code == 200
    assert "INFY" not in [w["symbol"] for w in listed.json()]


# ------------------------------------------------------------ personas

async def test_persona_crud_and_isolation(
    user_client: AsyncClient, other_client: AsyncClient
) -> None:
    created = await user_client.post(
        "/api/personas", json={"name": "Tutor", "system_prompt": "Explain like a teacher."}
    )
    assert created.status_code in (200, 201), created.text
    persona_id = created.json()["id"]
    mine = await user_client.get("/api/personas")
    assert persona_id in {p["id"] for p in mine.json()}
    theirs = await other_client.get("/api/personas")
    assert persona_id not in {p["id"] for p in theirs.json()}
    assert (await other_client.delete(f"/api/personas/{persona_id}")).status_code == 404
    assert (await user_client.delete(f"/api/personas/{persona_id}")).status_code in (200, 204)


# ----------------------------------------------------------- documents

async def test_documents_list_empty_and_bad_upload_rejected(
    user_client: AsyncClient,
) -> None:
    listed = await user_client.get("/api/documents")
    assert listed.status_code == 200
    assert listed.json() == []
    # a .pdf name with non-PDF magic bytes must be rejected
    upload = await user_client.post(
        "/api/documents",
        files={"file": ("evil.pdf", b"#!/bin/sh\necho pwned", "application/pdf")},
    )
    assert upload.status_code == 400


# -------------------------------------------------------------- health

async def test_healthz_is_public(client: AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
