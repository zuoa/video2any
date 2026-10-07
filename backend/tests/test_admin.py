"""Authentication boundaries, managed secrets and persistent live configuration."""
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import admin_auth, admin_store, bili_auth, summary_service
from backend.app.config import Settings, settings
from backend.app.main import app

PASSWORD = "test-admin-password"


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "cookies_dir", tmp_path / "cookies")
    for key in admin_store.CONFIG_KEYS:
        monkeypatch.setattr(settings, key, getattr(settings, key))
    monkeypatch.setattr(settings, "bilibili_cookies_file", None)
    monkeypatch.setattr(settings, "bilibili_cookies", None)
    monkeypatch.setattr(settings, "bilibili_cookie_header", None)
    monkeypatch.setattr(settings, "bilibili_refresh_token", "")
    monkeypatch.setattr(settings, "bilibili_keepalive_enabled", False)
    admin_auth.initialize_admin("owner", PASSWORD)


@pytest.fixture
def client():
    return TestClient(app)


def authenticate(client):
    result = client.post("/api/_admin/login", json={"username": "owner", "password": PASSWORD})
    assert result.status_code == 200
    return {"X-Admin-CSRF": result.json()["csrf_token"]}


@pytest.mark.parametrize("method, path, body", [
    ("GET", "/api/_admin/settings", None), ("GET", "/api/_admin/bilibili", None),
    ("GET", "/api/bilibili/session", None), ("POST", "/api/_admin/bilibili/check", None),
    ("PUT", "/api/_admin/bilibili/credentials", {"cookie_text": "SESSDATA=secret"}),
    ("DELETE", "/api/_admin/bilibili/credentials", None),
    ("PUT", "/api/_admin/settings/site", {"site_url": "https://example.com"}),
])
def test_management_requires_authentication(client, method, path, body):
    response = client.request(method, path, json=body)
    assert response.status_code == 401
    assert response.headers["Cache-Control"] == "no-store"
    assert "noindex" in response.headers["X-Robots-Tag"]


def test_password_and_session_token_are_hashed_and_cookie_is_http_only(client):
    response = client.post("/api/_admin/login", json={"username": "owner", "password": PASSWORD})
    assert response.status_code == 200
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/api" in cookie
    token = client.cookies[admin_auth.COOKIE_NAME]
    with admin_store.connect(settings.data_dir) as db:
        password = db.execute("SELECT password_hash FROM administrator").fetchone()[0]
        digest = db.execute("SELECT token_hash FROM sessions").fetchone()[0]
    assert PASSWORD not in password and admin_auth.verify_password(PASSWORD, password)
    assert digest == hashlib.sha256(token.encode()).hexdigest() and token not in response.text
    assert admin_store.database_path(settings.data_dir).stat().st_mode & 0o777 == 0o600
    assert client.get("/api/_admin/me").json()["username"] == "owner"


def test_secure_cookie_is_used_over_https():
    client = TestClient(app, base_url="https://testserver")
    response = client.post("/api/_admin/login", json={"username": "owner", "password": PASSWORD})
    assert "Secure" in response.headers["set-cookie"]


def test_missing_or_wrong_csrf_and_cross_origin_writes_are_rejected(client):
    headers = authenticate(client)
    data = {"site_url": "https://example.com"}
    for invalid_headers in [{}, {"X-Admin-CSRF": "wrong"}, {**headers, "Origin": "https://attacker.example"}]:
        assert client.put("/api/_admin/settings/site", json=data, headers=invalid_headers).status_code == 403
    assert admin_store.read_config(settings.data_dir) == {}
    assert client.put("/api/_admin/settings/site", json=data, headers={**headers, "Origin": "http://testserver"}).status_code == 200


def test_cross_origin_login_is_rejected(client):
    response = client.post("/api/_admin/login", json={"username": "owner", "password": PASSWORD}, headers={"Origin": "https://attacker.example"})
    assert response.status_code == 403


def test_forged_and_expired_sessions_are_rejected(client):
    authenticate(client)
    original = client.cookies[admin_auth.COOKIE_NAME]
    client.cookies.clear()
    client.cookies.set(admin_auth.COOKIE_NAME, "forged-token")
    assert client.get("/api/_admin/settings").status_code == 401
    client.cookies.clear()
    client.cookies.set(admin_auth.COOKIE_NAME, original)
    with admin_store.connect(settings.data_dir) as db:
        db.execute("UPDATE sessions SET expires_at=0")
    assert client.get("/api/_admin/settings").status_code == 401


def test_logout_revokes_session_even_if_cookie_is_replayed(client):
    headers = authenticate(client)
    original = client.cookies[admin_auth.COOKIE_NAME]
    assert client.post("/api/_admin/logout", headers=headers).status_code == 200
    client.cookies.set(admin_auth.COOKIE_NAME, original)
    assert client.get("/api/_admin/settings").status_code == 401


def test_password_change_requires_current_password_and_revokes_all_sessions(client):
    headers = authenticate(client)
    other = TestClient(app)
    authenticate(other)
    response = client.post("/api/_admin/password", json={"current_password": "wrong", "new_password": "replacement-password"}, headers=headers)
    assert response.status_code == 400
    assert other.get("/api/_admin/settings").status_code == 200
    response = client.post("/api/_admin/password", json={"current_password": PASSWORD, "new_password": "replacement-password"}, headers=headers)
    assert response.status_code == 200
    assert other.get("/api/_admin/settings").status_code == 401
    assert client.get("/api/_admin/settings").status_code == 401
    assert client.post("/api/_admin/login", json={"username": "owner", "password": "replacement-password"}).status_code == 200


def test_server_password_reset_revokes_sessions_and_setup_cannot_overwrite(client):
    authenticate(client)
    with pytest.raises(ValueError, match="已创建"):
        admin_auth.initialize_admin("owner", "replacement-password")
    admin_auth.initialize_admin("owner", "replacement-password", reset=True)
    assert client.get("/api/_admin/settings").status_code == 401


def test_login_attempt_limit_is_persistent_and_ignores_spoofed_forwarded_ip(client):
    for _ in range(5):
        assert client.post("/api/_admin/login", json={"username": "owner", "password": "wrong"}).status_code == 401
    fresh_client = TestClient(app)
    result = fresh_client.post("/api/_admin/login", json={"username": "owner", "password": PASSWORD}, headers={"X-Forwarded-For": "another-ip"})
    assert result.status_code == 429 and result.headers["retry-after"] == "900"


def test_admin_routes_are_absent_from_public_schema_and_config(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert not any("_admin" in path or "bilibili/session" in path for path in paths)
    assert set(client.get("/api/config").json()) == {"site_url"}


def test_managed_credentials_are_not_echoed_and_clear_does_not_restore_environment(client, monkeypatch):
    headers = authenticate(client)
    monkeypatch.setattr(settings, "bilibili_cookie_header", "SESSDATA=environment-session; bili_jct=environment-csrf")
    monkeypatch.setattr(settings, "bilibili_refresh_token", "environment-token")
    response = client.put("/api/_admin/bilibili/credentials", json={
        "cookie_text": "SESSDATA=managed-session; bili_jct=managed-csrf; DedeUserID=123", "refresh_token": "managed-token",
    }, headers=headers)
    assert response.status_code == 200 and response.json()["has_cookie"] and response.json()["has_refresh_token"]
    assert not any(secret in response.text for secret in ["managed-session", "managed-csrf", "managed-token"])
    assert bili_auth.load_cookies()["SESSDATA"] == "managed-session"
    # Different startup environment cannot roll back administrator credentials.
    monkeypatch.setattr(settings, "bilibili_refresh_token", "another-environment-token")
    assert bili_auth._read_state(bili_auth._read_source())["refresh_token"] == "managed-token"
    assert client.delete("/api/_admin/bilibili/credentials", headers=headers).status_code == 200
    monkeypatch.setattr(bili_auth, "_last_status", {})
    assert bili_auth.load_cookies() == {}
    assert not client.get("/api/_admin/bilibili").json()["has_cookie"]


def test_invalid_import_keeps_existing_credentials_and_new_account_drops_old_token(client):
    headers = authenticate(client)
    endpoint = "/api/_admin/bilibili/credentials"
    assert client.put(endpoint, json={"cookie_text": "SESSDATA=one; bili_jct=csrf", "refresh_token": "one-token"}, headers=headers).status_code == 200
    assert client.put(endpoint, json={"cookie_text": "junk"}, headers=headers).status_code == 400
    assert bili_auth.load_cookies()["SESSDATA"] == "one"
    assert client.put(endpoint, json={"cookie_text": "SESSDATA=two; bili_jct=csrf-two"}, headers=headers).status_code == 200
    assert not client.get("/api/_admin/bilibili").json()["has_refresh_token"]


@pytest.mark.parametrize("body", [
    {"cookie_text": json.dumps({"SESSDATA": "bad\nvalue", "bili_jct": "csrf"})},
    {"cookie_text": "SESSDATA=one; bili_jct=csrf", "refresh_token": "bad\nvalue"},
])
def test_credentials_with_control_characters_are_rejected(client, body):
    headers = authenticate(client)
    assert client.put("/api/_admin/bilibili/credentials", json=body, headers=headers).status_code == 400
    assert not bili_auth._state_path().exists()


def test_complete_import_can_repair_corrupt_session_state(client):
    headers = authenticate(client)
    bili_auth._state_path().parent.mkdir(parents=True, exist_ok=True)
    bili_auth._state_path().write_text('{"managed":true}')
    assert client.put("/api/_admin/bilibili/credentials", json={"refresh_token": "token"}, headers=headers).status_code == 400
    assert client.put("/api/_admin/bilibili/credentials", json={"cookie_text": "SESSDATA=repaired; bili_jct=csrf"}, headers=headers).status_code == 200
    assert bili_auth.load_cookies()["SESSDATA"] == "repaired"


def test_persisted_settings_take_precedence_over_environment_after_restart(client, monkeypatch, tmp_path):
    headers = authenticate(client)
    assert client.put("/api/_admin/settings/site", json={"site_url": "https://managed.example/"}, headers=headers).status_code == 200
    assert client.get("/api/config").json() == {"site_url": "https://managed.example"}
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SITE_URL", "https://environment.example")
    assert Settings().site_url == "https://managed.example"
    payload = {"enabled": True, "check_interval_seconds": 7200, "retry_interval_seconds": 120, "rate_limit_seconds": 2}
    assert client.put("/api/_admin/settings/bilibili", json=payload, headers=headers).status_code == 200
    restarted = Settings()
    assert restarted.bilibili_keepalive_enabled and restarted.bilibili_check_interval == 7200
    assert restarted.bilibili_retry_interval == 120 and restarted.bili_rate_limit_seconds == 2


def test_llm_secret_preservation_clear_and_new_client_configuration(client, monkeypatch):
    headers = authenticate(client)
    llm = client.get("/api/_admin/settings").json()["llm"]
    llm.pop("api_key_configured")
    body = {**llm, "api_key": "saved-api-key", "base_url": "https://new.example/v1", "model": "model-two", "timeout": 123}
    response = client.put("/api/_admin/settings/llm", json=body, headers=headers)
    assert response.status_code == 200 and response.json()["api_key_configured"]
    assert "saved-api-key" not in response.text
    assert "saved-api-key" not in client.get("/api/_admin/settings").text
    assert settings.openai_api_key == "saved-api-key"
    created = []

    def factory(**kwargs):
        created.append(kwargs)
        return SimpleNamespace(**kwargs)

    monkeypatch.setattr(summary_service, "OpenAI", factory)
    monkeypatch.setattr(summary_service, "_client", None)
    monkeypatch.setattr(summary_service, "_client_configuration", None)
    first = summary_service._get_client()
    body.pop("api_key")
    body["base_url"] = "https://another.example/v1"
    assert client.put("/api/_admin/settings/llm", json=body, headers=headers).status_code == 200
    assert settings.openai_api_key == "saved-api-key"
    assert summary_service._get_client() is not first
    assert created[-1]["base_url"] == body["base_url"]
    assert client.put("/api/_admin/settings/llm", json={**body, "api_key": ""}, headers=headers).status_code == 200
    assert settings.openai_api_key == ""


def test_llm_client_never_pairs_old_key_with_new_provider_during_reload(monkeypatch):
    midway, resume, reading = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(settings, "openai_api_key", "old-provider-key")
    monkeypatch.setattr(settings, "openai_base_url", "https://old-provider.example")

    class PausedValues(dict):
        def items(self):
            yield "openai_base_url", "https://new-provider.example"
            midway.set()
            assert resume.wait(timeout=5)
            yield "openai_api_key", "new-provider-key"

    monkeypatch.setattr(admin_store, "read_config", lambda _: PausedValues())
    monkeypatch.setattr(summary_service, "OpenAI", lambda **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(summary_service, "_client", None)
    monkeypatch.setattr(summary_service, "_client_configuration", None)

    def get_client():
        reading.set()
        return summary_service._get_client()

    with ThreadPoolExecutor(max_workers=2) as pool:
        reloaded = pool.submit(settings.refresh_runtime)
        assert midway.wait(timeout=5)
        created = pool.submit(get_client)
        assert reading.wait(timeout=5)
        try:
            with pytest.raises(TimeoutError):
                created.result(timeout=0.05)
        finally:
            resume.set()
        reloaded.result(timeout=5)
        client = created.result(timeout=5)
    assert (client.api_key, client.base_url) == ("new-provider-key", "https://new-provider.example")


@pytest.mark.parametrize("body", [{"site_url": "javascript:alert(1)"}, {"site_url": "https://user:password@example.com"}, {"site_url": "https://example.com", "unknown": "value"}])
def test_invalid_configuration_is_rejected_without_persisting(client, body):
    headers = authenticate(client)
    assert client.put("/api/_admin/settings/site", json=body, headers=headers).status_code == 400
    assert admin_store.read_config(settings.data_dir) == {}
