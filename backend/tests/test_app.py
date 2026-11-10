import json
import time
from datetime import date, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from app import main, mcp, theme


@pytest.fixture
def client(monkeypatch):
    for key in ("LOCAL_AUTH_MOCK_EMAIL", "LOCAL_AUTH_MOCK_NAME", "LOCAL_AUTH_MOCK_GROUPS", "COMMIT_SHA", "GIT_SHA",
                "IHC_THEME_URL", "APPROVER_GROUPS"):
        monkeypatch.delenv(key, raising=False)
    main.app.dependency_overrides[main.db.get_session] = lambda: None
    with TestClient(main.app) as client:
        yield client
    main.app.dependency_overrides.clear()


VALID = {"expense_date": date.today().isoformat(), "description": "  Uber cliente  ", "category": "Transporte",
         "amount_cents": 4590, "notes": "  "}


def test_health_public_commit(client, monkeypatch):
    assert client.get("/health").json() == {"ok": True, "commit": "unknown"}
    monkeypatch.setenv("GIT_SHA", "git-commit")
    assert client.get("/health").json()["commit"] == "git-commit"
    monkeypatch.setenv("COMMIT_SHA", "release-commit")
    assert client.get("/health").json()["commit"] == "release-commit"


def test_person_headers_override_mock(client, monkeypatch):
    monkeypatch.setenv("LOCAL_AUTH_MOCK_EMAIL", "local@example.com")
    monkeypatch.setenv("LOCAL_AUTH_MOCK_NAME", "Local")
    response = client.get("/api/me", headers={"X-authentik-email": "alice@example.com", "X-authentik-name": "Alice",
                                             "X-authentik-username": "alice", "X-authentik-groups": "users|team-a||"})
    body = response.json()
    assert {k: body[k] for k in ("email", "name", "username", "groups")} == {
        "email": "alice@example.com", "name": "Alice", "username": "alice", "groups": ["users", "team-a"]}
    assert body["is_approver"] is False
    assert "Transporte" in body["categories"]


def test_person_mock_is_opt_in(client, monkeypatch):
    assert client.get("/api/me").status_code == 401
    monkeypatch.setenv("LOCAL_AUTH_MOCK_NAME", "Local")
    assert client.get("/api/me").status_code == 401
    monkeypatch.setenv("LOCAL_AUTH_MOCK_EMAIL", "local@example.com")
    assert client.get("/api/me").json()["email"] == "local@example.com"


def test_approver_groups(client, monkeypatch):
    for groups, expected in (("ihc-admins", True), ("reembolso-gestores", True), ("ihc-users", False), ("", False)):
        assert client.get("/api/me", headers={"X-authentik-email": "a@example.com", "X-authentik-groups": groups}).json()["is_approver"] is expected
    monkeypatch.setenv("APPROVER_GROUPS", "financeiro")
    assert client.get("/api/me", headers={"X-authentik-email": "a@example.com", "X-authentik-groups": "ihc-admins"}).json()["is_approver"] is False
    assert client.get("/api/me", headers={"X-authentik-email": "a@example.com", "X-authentik-groups": "financeiro"}).json()["is_approver"] is True


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/expenses", None), ("POST", "/api/expenses", VALID),
    ("DELETE", "/api/expenses/00000000-0000-0000-0000-000000000001", None),
    ("GET", "/api/approvals", None),
    ("POST", "/api/approvals/00000000-0000-0000-0000-000000000001", {"decision": "aprovada"}),
])
def test_routes_require_person(client, monkeypatch, method, path, body):
    def unexpected(*args):
        pytest.fail("Unauthenticated requests must never touch the database")
    for name in ("list_my_expenses", "create_expense", "cancel_expense", "list_pending", "decide_expense"):
        monkeypatch.setattr(main.db, name, unexpected)
    assert client.request(method, path, json=body).status_code == 401


def test_approvals_require_approver_group(client, monkeypatch):
    monkeypatch.setattr(main.db, "list_pending", lambda *args: pytest.fail("Non-approvers must not read the queue"))
    monkeypatch.setattr(main.db, "decide_expense", lambda *args: pytest.fail("Non-approvers must not decide"))
    headers = {"X-authentik-email": "bob@example.com", "X-authentik-groups": "ihc-users"}
    assert client.get("/api/approvals", headers=headers).status_code == 403
    assert client.post("/api/approvals/00000000-0000-0000-0000-000000000001", headers=headers,
                       json={"decision": "aprovada"}).status_code == 403


def test_expense_validation(client, monkeypatch):
    monkeypatch.setattr(main.db, "create_expense", lambda *args: pytest.fail("Invalid input must not be saved"))
    headers = {"X-authentik-email": "alice@example.com"}
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    for change in ({"expense_date": tomorrow}, {"description": "   "}, {"category": "Viagem"}, {"amount_cents": 0},
                   {"amount_cents": -10}, {"amount_cents": main.MAX_AMOUNT_CENTS + 1}, {"notes": "x" * 1001},
                   {"amount_cents": "abc"}, {"expense_date": "07/11/2026"}):
        assert client.post("/api/expenses", headers=headers, json={**VALID, **change}).status_code == 422, change


def test_decision_requires_reason_to_decline(client, monkeypatch):
    monkeypatch.setattr(main.db, "decide_expense", lambda *args: pytest.fail("Invalid decision must not be saved"))
    headers = {"X-authentik-email": "boss@example.com", "X-authentik-groups": "ihc-admins"}
    url = "/api/approvals/00000000-0000-0000-0000-000000000001"
    assert client.post(url, headers=headers, json={"decision": "recusada"}).status_code == 422
    assert client.post(url, headers=headers, json={"decision": "recusada", "note": "  "}).status_code == 422
    assert client.post(url, headers=headers, json={"decision": "talvez"}).status_code == 422


def test_routes_pass_actor_to_database(client, monkeypatch):
    calls = []
    monkeypatch.setattr(main.db, "list_my_expenses", lambda session, email, status=None: calls.append(("list", email)) or [])
    monkeypatch.setattr(main.db, "create_expense", lambda session, email, name, data: calls.append(("create", email, name, data)) or object())
    monkeypatch.setattr(main.db, "serialize", lambda expense: {"ok": True})
    headers = {"X-authentik-email": "alice@example.com", "X-authentik-name": "Alice"}
    assert client.get("/api/expenses", headers=headers).json() == {"expenses": []}
    assert client.post("/api/expenses", headers=headers, json=VALID).status_code == 201
    assert calls[0] == ("list", "alice@example.com")
    assert calls[1][:3] == ("create", "alice@example.com", "Alice")
    assert calls[1][3]["description"] == "Uber cliente" and calls[1][3]["notes"] is None
    assert calls[1][3]["amount_cents"] == 4590 and calls[1][3]["expense_date"] == date.today()


@pytest.fixture
def signer(monkeypatch):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public.update(kid="test-key", alg="RS256", use="sig")
    monkeypatch.setenv("APP_SLUG", "sample-app")
    monkeypatch.setenv("IHC_MCP_ISSUER", "https://gateway.test")
    monkeypatch.setenv("IHC_MCP_AUDIENCE", "sample-app.mcp")
    monkeypatch.setenv("IHC_MCP_JWKS_URL", "https://gateway.test/jwks")
    monkeypatch.setattr(mcp, "_keys", {"url": None, "keys": [], "at": 0})
    calls = []
    def fetch(url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, json={"keys": [public]}, request=httpx.Request("GET", url))
    monkeypatch.setattr(mcp.httpx, "get", fetch)
    def sign(**overrides):
        claims = {"sub": "alice@example.com", "email": "alice@example.com", "groups": ["ihc-users"],
                  "iss": "https://gateway.test", "aud": "sample-app.mcp", "exp": int(time.time()) + 120, "scope": "sample-app.read"}
        claims.update(overrides)
        return {"Authorization": "Bearer " + jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})}
    return sign, calls


def test_actor_jwt_and_scopes(client, signer, monkeypatch):
    sign, calls = signer
    seen = []
    monkeypatch.setattr(main.db, "list_my_expenses", lambda session, email, status=None: seen.append((email, status)) or [])
    monkeypatch.setattr(main.db, "list_pending", lambda session: [])
    headers = sign()
    listing = client.get("/mcp/tools", headers=headers)
    assert listing.status_code == 200
    assert [tool["name"] for tool in listing.json()["tools"]] == ["list_my_expenses"]
    assert listing.json()["tools"][0]["access"] == "read"
    result = client.post("/mcp/tools/list_my_expenses/execute", headers=headers, json={"arguments": {}})
    assert result.json() == {"result": {"expenses": []}}
    assert client.post("/mcp/tools/list_my_expenses/execute", headers=headers, json={"arguments": {"status": "aprovada"}}).status_code == 200
    assert seen == [("alice@example.com", None), ("alice@example.com", "aprovada")]
    assert len(calls) == 1  # cached JWKS
    assert calls[0][1]["timeout"] == 5
    for path in ("/mcp/tools", "/mcp/tools/list_my_expenses/execute"):
        response = client.get(path, headers=sign(scope="other.read")) if path == "/mcp/tools" else client.post(path, headers=sign(scope="other.read"), json={})
        assert response.status_code == 403
    assert client.post("/mcp/tools/unknown/execute", headers=headers, json={}).status_code == 404
    for arguments in ({"email": "bob@example.com"}, {"status": "talvez"}, "x"):
        assert client.post("/mcp/tools/list_my_expenses/execute", headers=headers, json={"arguments": arguments}).status_code == 400
    # Pending queue: approvers only, in the listing and on execute.
    assert client.post("/mcp/tools/list_pending_expenses/execute", headers=headers, json={"arguments": {}}).status_code == 403
    boss = sign(email="boss@example.com", sub="boss@example.com", groups=["ihc-users", "ihc-admins"])
    assert [tool["name"] for tool in client.get("/mcp/tools", headers=boss).json()["tools"]] == ["list_my_expenses", "list_pending_expenses"]
    assert client.post("/mcp/tools/list_pending_expenses/execute", headers=boss, json={"arguments": {}}).json() == {"result": {"expenses": []}}


@pytest.mark.parametrize("claims", [{"iss": "wrong"}, {"aud": "wrong"}, {"exp": 1}, {"exp": None}, {"email": "", "sub": ""}])
def test_invalid_actor_claims(client, signer, claims):
    sign, _ = signer
    assert client.get("/mcp/tools", headers=sign(**claims)).status_code == 401


def test_actor_requires_signature_not_browser_headers(client, signer):
    sign, _ = signer
    denied = client.get("/mcp/tools")
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "invalid_actor_token"
    assert client.get("/mcp/tools", headers={"X-authentik-email": "alice@example.com"}).status_code == 401
    token = sign()["Authorization"].split()[1]
    head, payload, signature = token.split(".")
    forged = jwt.encode({"email": "alice@example.com", "exp": int(time.time()) + 120}, "test-signing-key-long-enough-for-hmac", algorithm="HS256", headers={"kid": "test-key"})
    for invalid in (f"{head}.{payload}.{signature[:-8]}AAAAAAAA", forged, "garbage"):
        assert client.get("/mcp/tools", headers={"Authorization": "Bearer " + invalid}).status_code == 401


def test_jwks_unknown_key_refresh_and_failure(client, signer, monkeypatch):
    sign, calls = signer
    headers = sign()
    assert client.get("/mcp/tools", headers=headers).status_code == 200
    mcp._keys.update(keys=[], at=time.monotonic() - 31)
    assert client.get("/mcp/tools", headers=headers).status_code == 200
    assert len(calls) == 2
    mcp._keys.update(keys=[], at=0)
    def unavailable(*args, **kwargs):
        raise httpx.ConnectError("offline")
    monkeypatch.setattr(mcp.httpx, "get", unavailable)
    assert client.get("/mcp/tools", headers=headers).status_code == 401


def test_theme_default_cache_timeout_and_failure(client, monkeypatch):
    monkeypatch.setattr(theme, "_cache", {"url": None, "css": "", "at": 0})
    default = client.get("/theme.css")
    assert default.headers["content-type"].startswith("text/css")
    assert ":root" in default.text and ".dark" in default.text
    monkeypatch.setenv("IHC_THEME_URL", "https://platform.test/theme.css")
    calls = []
    def fetch(url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(200, text=":root { --primary: red; }", request=httpx.Request("GET", url))
    monkeypatch.setattr(theme.httpx, "get", fetch)
    assert "--primary: red" in client.get("/theme.css").text
    client.get("/theme.css")
    assert calls == [{"timeout": 2, "follow_redirects": False}]
    theme._cache["at"] = time.monotonic() - 61
    def unavailable(*args, **kwargs):
        raise httpx.ReadTimeout("offline")
    monkeypatch.setattr(theme.httpx, "get", unavailable)
    assert client.get("/theme.css").text == theme.DEFAULT
    # The fallback is cached too; do not hit an unavailable upstream on every request.
    monkeypatch.setattr(theme.httpx, "get", lambda *args, **kwargs: pytest.fail("fallback not cached"))
    assert client.get("/theme.css").text == theme.DEFAULT


def test_spa_does_not_swallow_api_or_escape_static(client, monkeypatch, tmp_path):
    monkeypatch.setattr(main, "STATIC", tmp_path)
    (tmp_path / "index.html").write_text("<html>app</html>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets/app.js").write_text("// app")
    assert client.get("/nested/page").text == "<html>app</html>"
    assert client.get("/assets/app.js").text == "// app"
    for path in ("/api/unknown", "/mcp/unknown", "/assets/missing.js", "/missing.css", "/%2e%2e/requirements.txt"):
        assert client.get(path).status_code == 404
