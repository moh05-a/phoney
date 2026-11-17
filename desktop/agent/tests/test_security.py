"""Pairing, tokens, source checks, rate limits: the acceptance tests from docs/SECURITY.md."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.core.context import AgentContext
from app.database.models import Device, PairingCode, utcnow
from app.main import create_lan_app
from app.security.auth import create_pairing_code, hash_token, is_private_address
from app.security.ratelimit import Lockout, RateLimiter
from tests.conftest import pair_device

PROTECTED = [
    ("get", "/api/v1/agent"), ("get", "/api/v1/system/snapshot"), ("get", "/api/v1/actions"),
    ("post", "/api/v1/actions/system.lock/run"), ("get", "/api/v1/deck"), ("get", "/api/v1/layout"),
    ("get", "/api/v1/tasks"), ("get", "/api/v1/notes"), ("get", "/api/v1/ai/sessions"),
    ("get", "/api/v1/dev/ports"), ("delete", "/api/v1/devices/me"),
]  # fmt: skip


def test_health_needs_no_token(client: TestClient) -> None:
    body = client.get("/api/v1/health").json()
    assert body["status"] == "ok" and body["protocol_version"] == "1.0"


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_endpoints_reject_missing_and_wrong_tokens(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path).status_code == 401
    wrong = {"Authorization": "Bearer pbx_not-a-real-token"}
    response = client.request(method, path, headers=wrong)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_pairing_returns_token_once_and_stores_only_a_hash(client: TestClient, ctx: AgentContext) -> None:
    paired = pair_device(client, ctx)
    token = paired["token"]
    assert token.startswith("pbx_") and len(token) > 40
    with ctx.db() as db:
        device = db.get(Device, paired["device_id"])
    assert device.token_hash == hash_token(token)
    assert token not in device.token_hash
    assert client.get("/api/v1/agent", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_pairing_code_is_single_use(client: TestClient, ctx: AgentContext) -> None:
    with ctx.db() as db:
        code = create_pairing_code(db, 600)
    body = {"pairing_code": code, "device_name": "A"}
    assert client.post("/api/v1/pair", json=body).status_code == 201
    second = client.post("/api/v1/pair", json=body)
    assert second.status_code == 410
    assert second.json()["error"]["code"] == "pairing_code_expired"


def test_pairing_code_expires(client: TestClient, ctx: AgentContext) -> None:
    with ctx.db() as db:
        code = create_pairing_code(db, 600)
        db.get(PairingCode, code).expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert client.post("/api/v1/pair", json={"pairing_code": code, "device_name": "A"}).status_code == 410


def test_code_accepts_dash_and_lowercase(client: TestClient, ctx: AgentContext) -> None:
    with ctx.db() as db:
        code = create_pairing_code(db, 600)
    typed = f"{code[:4]}-{code[4:]}".lower()
    assert client.post("/api/v1/pair", json={"pairing_code": typed, "device_name": "A"}).status_code == 201


def test_wrong_codes_are_rate_limited_then_locked_out(client: TestClient, ctx: AgentContext) -> None:
    ctx.limits.pairing = RateLimiter(100, 60)  # isolate the lockout from the per-minute limit
    body = {"pairing_code": "WRONGCODE", "device_name": "A"}
    statuses = [client.post("/api/v1/pair", json=body).status_code for _ in range(11)]
    assert statuses[:10] == [410] * 10
    assert statuses[10] == 429
    with ctx.db() as db:  # even a correct code is refused while locked out
        code = create_pairing_code(db, 600)
    assert client.post("/api/v1/pair", json={"pairing_code": code, "device_name": "A"}).status_code == 429


def test_pairing_attempts_per_minute_are_limited(client: TestClient) -> None:
    body = {"pairing_code": "WRONGCODE", "device_name": "A"}
    statuses = [client.post("/api/v1/pair", json=body).status_code for _ in range(6)]
    assert statuses == [410] * 5 + [429]


def test_unknown_fields_are_rejected(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post("/api/v1/tasks", json={"title": "x", "run": "calc.exe"}, headers=auth)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_request"


def test_oversized_body_is_rejected(client: TestClient, auth: dict[str, str]) -> None:
    response = client.post("/api/v1/notes", json={"text": "x" * 70_000}, headers=auth)
    assert response.status_code == 413


def test_unpair_revokes_the_token(client: TestClient, auth: dict[str, str]) -> None:
    assert client.delete("/api/v1/devices/me", headers=auth).status_code == 204
    assert client.get("/api/v1/agent", headers=auth).status_code == 401


def test_public_source_is_rejected_for_http_static_and_websocket(ctx: AgentContext) -> None:
    ctx.config.security.enforce_private_sources = True
    app = create_lan_app(ctx)
    with TestClient(app, client=("8.8.8.8", 50000)) as public:
        assert public.get("/api/v1/health").status_code == 403
        assert public.get("/").status_code == 403
        with pytest.raises(WebSocketDisconnect), public.websocket_connect("/api/v1/ws"):
            pass
    with TestClient(app, client=("192.168.1.50", 50000)) as private:
        assert private.get("/api/v1/health").status_code == 200


def test_lan_app_has_no_local_routes(client: TestClient, auth: dict[str, str]) -> None:
    for path in ("/local/hooks/claude-code", "/local/devices/x/revoke", "/pair"):
        assert client.post(path, json={}, headers=auth).status_code in (404, 405)
    assert client.get("/pair", headers=auth).status_code == 404


@pytest.mark.parametrize(
    ("address", "expected"),
    [("192.168.1.20", True), ("10.0.0.5", True), ("172.16.4.4", True), ("127.0.0.1", True),
     ("169.254.1.1", True), ("::1", True), ("::ffff:192.168.1.2", True), ("fd00::1", True),
     ("8.8.8.8", False), ("2001:4860:4860::8888", False), ("testclient", False), (None, False)],
)  # fmt: skip
def test_private_address_check(address: str | None, expected: bool) -> None:
    assert is_private_address(address) is expected


def test_rate_limiter_refills_over_time() -> None:
    now = [0.0]
    limiter = RateLimiter(2, 10, clock=lambda: now[0])
    assert [limiter.allow("k"), limiter.allow("k"), limiter.allow("k")] == [True, True, False]
    assert limiter.allow("other") is True
    now[0] = 5.0
    assert limiter.allow("k") is True


def test_lockout_expires() -> None:
    now = [0.0]
    lockout = Lockout(2, 60, clock=lambda: now[0])
    lockout.failure("ip")
    assert not lockout.is_locked("ip")
    lockout.failure("ip")
    assert lockout.is_locked("ip")
    now[0] = 61.0
    assert not lockout.is_locked("ip")
