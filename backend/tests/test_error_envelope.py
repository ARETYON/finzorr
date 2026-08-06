"""Router-level errors must carry the {detail, code, request_id} envelope.

Starlette's router raises the BASE HTTPException for unknown paths (404) and
wrong methods (405) — registering the handler on the FastAPI subclass alone
lets both escape (handler lookup walks the exception's MRO). These tests pin
the fix in core/errors.py; no DB, no fixtures — pure routing.
"""

import pytest
from starlette.testclient import TestClient

from app.main import app

pytestmark = pytest.mark.sanity


def test_unknown_path_404_carries_envelope() -> None:
    client = TestClient(app)
    response = client.get("/api/v1/nope")
    assert response.status_code == 404
    body = response.json()
    assert body["code"] == "not_found"
    assert body["detail"] == "Not Found"
    assert body["request_id"]  # middleware mints it before routing


def test_wrong_method_405_carries_envelope_and_allow_header() -> None:
    client = TestClient(app)
    response = client.put("/api/v1/chat/sessions")  # only GET/POST exist
    assert response.status_code == 405
    body = response.json()
    assert body["code"] == "method_not_allowed"
    assert body["request_id"]
    # the handler must pass Starlette's headers through, not swallow them.
    # (Starlette emits the FIRST path-matching route's methods, so Allow
    # holds one of GET/POST here — presence is what proves the passthrough.)
    allowed = {m.strip() for m in response.headers.get("allow", "").split(",") if m.strip()}
    assert allowed and allowed <= {"GET", "POST", "HEAD"}


def test_alias_mount_shares_the_envelope() -> None:
    client = TestClient(app)
    body = client.get("/api/definitely-not-a-route").json()
    assert body["code"] == "not_found"
    assert body["request_id"]


def test_bare_list_scope_documented_in_openapi() -> None:
    """The v1 list-shape split is a documented design decision, not an
    accident: the four bounded collections declare it in their OpenAPI
    descriptions (visible under BOTH the /api and /api/v1 mounts)."""
    spec = app.openapi()
    documented = [
        path
        for path, ops in spec["paths"].items()
        if "bare JSON array by design"
        in (ops.get("get", {}).get("description") or "")
    ]
    for path in (
        "/api/v1/watchlist",
        "/api/v1/documents",
        "/api/v1/auth/memories",
        "/api/v1/personas",
        "/api/watchlist",  # alias mount carries the same contract
    ):
        assert path in documented, f"{path} missing the bare-list-by-design note"
