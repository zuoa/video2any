"""Credential rotation, persistence, recovery and shared consumer regressions."""
import asyncio
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from email.message import Message
from http.cookiejar import HTTPONLY_ATTR, MozillaCookieJar
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
from fastapi.testclient import TestClient

from backend.app import bili_auth, bili_subtitle, storage
from backend.app.config import Settings, settings


@pytest.fixture(autouse=True)
def credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "cookies_dir", tmp_path)
    monkeypatch.setattr(settings, "bilibili_cookies_file", None)
    monkeypatch.setattr(settings, "bilibili_cookies", None)
    monkeypatch.setattr(settings, "bilibili_cookie_header", "SESSDATA=old-session; bili_jct=old-csrf; DedeUserID=123; buvid3=device")
    monkeypatch.setattr(settings, "bilibili_refresh_token", "old-token")
    monkeypatch.setattr(settings, "bilibili_keepalive_enabled", True)
    monkeypatch.setattr(settings, "bilibili_check_interval", 86400)
    monkeypatch.setattr(settings, "bilibili_retry_interval", 300)
    monkeypatch.setattr(bili_auth, "_last_status", {"status": "unconfigured"})
    clock = [1800000000.0]
    monkeypatch.setattr(bili_auth.time, "time", lambda: clock[0])
    # Unexpected network calls must fail locally, including tests of consumers.
    monkeypatch.setattr(bili_auth.requests, "Session", Mock(side_effect=AssertionError("unexpected network call")))
    return clock


def response(data=None, *, text="", cookies=None, status=200):
    result = requests.Response()
    result.status_code = status
    result.url = "https://passport.bilibili.com/"
    result._content = (json.dumps(data) if data is not None else text).encode()
    result.cookies = requests.cookies.RequestsCookieJar()
    for name, value in (cookies or {}).items():
        result.cookies.set_cookie(bili_auth._cookie(name, value, expires=1893456000, http_only=name == "SESSDATA"))
    return result


def info(refresh=False, *, code=0):
    return response({"code": code, "data": {"refresh": refresh, "timestamp": 1800000000123}})


class Session:
    def __init__(self, steps):
        self.steps = list(steps)
        self.calls = []
        self.headers = {}
        self.cookies = requests.cookies.RequestsCookieJar()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs, self.cookies.get_dict()))
        expected_method, suffix, result = self.steps.pop(0)
        assert (method, url.endswith(suffix)) == (expected_method, True)
        if callable(result):
            result = result(kwargs, self.cookies)
        if isinstance(result, Exception):
            raise result
        self.cookies.update(result.cookies)  # requests applies Set-Cookie before returning
        return result

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url, **kwargs):
        return self.request("POST", url, **kwargs)


def install_session(monkeypatch, steps):
    session = Session(steps)
    factory = Mock(return_value=session)
    monkeypatch.setattr(bili_auth.requests, "Session", factory)
    return session, factory


def renewal_steps(confirm=None):
    return [
        ("GET", "/cookie/info", info(True)),
        ("GET", "/correspond/1/signed", response(text="<div class='x' id = '1-name'> live-csrf </div>")),
        ("POST", "/cookie/refresh", response({"code": 0, "data": {"refresh_token": "new-token"}}, cookies={"SESSDATA": "new-session", "bili_jct": "new-csrf"})),
        ("POST", "/confirm/refresh", confirm if confirm is not None else response({"code": 0})),
    ]


def test_renewal_commits_matching_credentials_before_confirm_and_survives_restart(monkeypatch):
    def confirm(kwargs, jar):
        state = json.loads(bili_auth._state_path().read_text())
        assert state["refresh_token"] == "new-token"
        assert state["pending_confirmation"] == "old-token"
        assert "new-session" in state["cookie_text"]
        assert "new-session" in bili_auth._cookie_path().read_text()
        assert kwargs["data"] == {"csrf": "new-csrf", "refresh_token": "old-token"}
        return response({"code": 0})

    monkeypatch.setattr(bili_auth, "_correspond_path", lambda ts: "signed" if ts == 1800000000123 else "wrong")
    session, factory = install_session(monkeypatch, renewal_steps(confirm))
    status = bili_auth.ensure_session()
    assert status["status"] == "healthy"
    assert session.calls[2][2]["data"] == {
        "csrf": "old-csrf", "refresh_csrf": "live-csrf", "source": "main_web", "refresh_token": "old-token",
    }
    state = json.loads(bili_auth._state_path().read_text())
    assert "pending_confirmation" not in state
    assert bili_auth._state_path().stat().st_mode & 0o777 == 0o600
    assert bili_auth._cookie_path().stat().st_mode & 0o777 == 0o600
    # A restart has the original environment header and refresh token; disk wins.
    monkeypatch.setattr(bili_auth, "_last_status", {})
    assert bili_auth.load_cookies()["SESSDATA"] == "new-session"
    assert bili_auth.load_cookies()["buvid3"] == "device"
    jar = bili_auth.get_cookie_jar()
    sess = next(c for c in jar if c.name == "SESSDATA")
    assert sess.expires == 1893456000 and sess.has_nonstandard_attr("HttpOnly")
    factory.assert_called_once()
    assert not any(secret in json.dumps(status) for secret in ["new-token", "old-token", "new-session", "new-csrf"])


def test_daily_check_is_throttled_across_restart_and_rechecks_when_due(credentials, monkeypatch):
    _, factory = install_session(monkeypatch, [("GET", "/cookie/info", info()), ("GET", "/cookie/info", info())])
    assert bili_auth.ensure_session()["status"] == "healthy"
    monkeypatch.setattr(bili_auth, "_last_status", {})
    assert bili_auth.ensure_session()["status"] == "healthy"
    assert factory.call_count == 1
    credentials[0] += 86401
    assert bili_auth.ensure_session()["status"] == "healthy"
    assert factory.call_count == 2


@pytest.mark.parametrize("needs_refresh, expected", [(False, "missing_refresh_token"), (True, "refresh_required")])
def test_cookie_only_login_reports_limited_renewal(monkeypatch, needs_refresh, expected):
    monkeypatch.setattr(settings, "bilibili_refresh_token", "")
    session, _ = install_session(monkeypatch, [("GET", "/cookie/info", info(needs_refresh))])
    assert bili_auth.ensure_session()["status"] == expected
    assert len(session.calls) == 1
    assert bili_auth.load_cookies()["SESSDATA"] == "old-session"


def test_expired_cookie_reports_relogin_without_attempting_refresh(monkeypatch):
    session, _ = install_session(monkeypatch, [("GET", "/cookie/info", info(code=-101))])
    assert bili_auth.ensure_session()["status"] == "expired"
    assert len(session.calls) == 1
    assert bili_auth.load_cookies()["SESSDATA"] == "old-session"


@pytest.mark.parametrize("failure", [requests.Timeout("private-value"), response(status=412), response({"code": -352})])
def test_transient_failures_preserve_credentials_and_back_off(credentials, monkeypatch, failure, caplog):
    session, factory = install_session(monkeypatch, [("GET", "/cookie/info", failure)])
    status = bili_auth.ensure_session()
    assert status["status"] == "retrying"
    assert status["next_check_at"] == credentials[0] + 300
    assert bili_auth.load_cookies()["SESSDATA"] == "old-session"
    factory.assert_called_once()
    assert len(session.calls) == 1
    assert "private-value" not in caplog.text


def test_confirmation_failure_recovers_with_new_cookie_and_old_token(credentials, monkeypatch):
    monkeypatch.setattr(bili_auth, "_correspond_path", lambda ts: "signed")
    install_session(monkeypatch, renewal_steps(requests.Timeout()))
    assert bili_auth.ensure_session()["status"] == "retrying"
    assert bili_auth.load_cookies()["SESSDATA"] == "new-session"
    assert json.loads(bili_auth._state_path().read_text())["pending_confirmation"] == "old-token"
    credentials[0] += 301
    session, _ = install_session(monkeypatch, [
        ("POST", "/confirm/refresh", response({"code": 0})),
        ("GET", "/cookie/info", info()),
    ])
    assert bili_auth.ensure_session()["status"] == "healthy"
    assert session.calls[0][2]["data"] == {"csrf": "new-csrf", "refresh_token": "old-token"}
    assert "pending_confirmation" not in json.loads(bili_auth._state_path().read_text())


def test_failed_cookie_file_write_recovers_from_atomic_session_state(credentials, monkeypatch):
    monkeypatch.setattr(bili_auth, "_correspond_path", lambda ts: "signed")
    write = bili_auth._atomic_write

    def fail_cookie_file(path, text):
        if path == bili_auth._cookie_path():
            raise PermissionError()
        write(path, text)

    monkeypatch.setattr(bili_auth, "_atomic_write", fail_cookie_file)
    install_session(monkeypatch, renewal_steps()[:-1])
    assert bili_auth.ensure_session()["status"] == "retrying"
    assert bili_auth.load_cookies()["SESSDATA"] == "new-session"
    monkeypatch.setattr(bili_auth, "_atomic_write", write)
    credentials[0] += 301
    install_session(monkeypatch, [
        ("POST", "/confirm/refresh", response({"code": 0})),
        ("GET", "/cookie/info", info()),
    ])
    assert bili_auth.ensure_session()["status"] == "healthy"
    assert "new-session" in bili_auth._cookie_path().read_text()


def test_unwritable_state_does_not_start_remote_mutations(monkeypatch):
    monkeypatch.setattr(bili_auth, "_atomic_write", Mock(side_effect=PermissionError()))
    factory = bili_auth.requests.Session
    assert bili_auth.ensure_session()["status"] == "configuration_error"
    factory.assert_not_called()


def test_second_rotation_recovers_when_main_file_still_has_previous_rotation(monkeypatch):
    monkeypatch.setattr(bili_auth, "_correspond_path", lambda ts: "signed")
    install_session(monkeypatch, renewal_steps())
    assert bili_auth.ensure_session()["status"] == "healthy"
    write = bili_auth._atomic_write

    def fail_main_file(path, text):
        if path == bili_auth._cookie_path():
            raise PermissionError()
        write(path, text)

    monkeypatch.setattr(bili_auth, "_atomic_write", fail_main_file)
    install_session(monkeypatch, [
        ("GET", "/cookie/info", info(True)),
        ("GET", "/correspond/1/signed", response(text='<div id="1-name">live-csrf</div>')),
        ("POST", "/cookie/refresh", response({"code": 0, "data": {"refresh_token": "second-token"}},
                                              cookies={"SESSDATA": "second-session", "bili_jct": "second-csrf"})),
    ])
    assert bili_auth.ensure_session(force=True)["status"] == "retrying"
    assert "new-session" in bili_auth._cookie_path().read_text()
    assert bili_auth.load_cookies()["SESSDATA"] == "second-session"
    state = json.loads(bili_auth._state_path().read_text())
    assert state["refresh_token"] == "second-token"
    assert state["pending_confirmation"] == "new-token"


def test_ambiguous_refresh_rejection_retries_nonce_and_preserves_working_cookie(credentials, monkeypatch):
    monkeypatch.setattr(bili_auth, "_correspond_path", lambda ts: "signed")
    steps = renewal_steps()[:3]
    steps[-1] = ("POST", "/cookie/refresh", response({"code": 86095}))
    steps += [
        ("GET", "/cookie/info", info(True)),
        ("GET", "/correspond/1/signed", response(text='<div id="1-name">fresh-csrf</div>')),
        ("POST", "/cookie/refresh", response({"code": 86095})),
    ]
    install_session(monkeypatch, steps)
    result = bili_auth.ensure_session()
    assert result["status"] == "retrying"
    assert result["next_check_at"] == credentials[0] + 300
    assert bili_auth.load_cookies()["SESSDATA"] == "old-session"


def test_refresh_csrf_rejection_recovers_with_fresh_nonce(monkeypatch):
    monkeypatch.setattr(bili_auth, "_correspond_path", lambda ts: str(ts))
    refreshed = renewal_steps()[2][2]
    session, _ = install_session(monkeypatch, [
        ("GET", "/cookie/info", info(True)),
        ("GET", "/correspond/1/1800000000123", response(text='<div id="1-name">stale-csrf</div>')),
        ("POST", "/cookie/refresh", response({"code": 86095})),
        ("GET", "/cookie/info", response({"code": 0, "data": {"refresh": True, "timestamp": 1800000000456}})),
        ("GET", "/correspond/1/1800000000456", response(text='<div id="1-name">fresh-csrf</div>')),
        ("POST", "/cookie/refresh", refreshed),
        ("POST", "/confirm/refresh", response({"code": 0})),
    ])
    assert bili_auth.ensure_session()["status"] == "healthy"
    assert session.calls[2][2]["data"]["refresh_csrf"] == "stale-csrf"
    assert session.calls[5][2]["data"]["refresh_csrf"] == "fresh-csrf"
    assert bili_auth.load_cookies()["SESSDATA"] == "new-session"
    assert json.loads(bili_auth._state_path().read_text())["refresh_token"] == "new-token"


def test_real_set_cookie_domain_serializes_as_valid_netscape_file(tmp_path):
    headers = Message()
    headers.add_header("Set-Cookie", "SESSDATA=rotated; Domain=bilibili.com; Path=/; Secure; HttpOnly")
    jar = requests.cookies.RequestsCookieJar()
    prepared = requests.Request("POST", f"{bili_auth.PASSPORT}/cookie/refresh").prepare()
    jar.extract_cookies(requests.cookies.MockResponse(headers), requests.cookies.MockRequest(prepared))
    cookie = next(iter(jar))
    assert cookie.domain == ".bilibili.com" and cookie.domain_initial_dot is False
    path = tmp_path / "persisted.txt"
    path.write_text(bili_auth._cookie_text(jar))
    loaded = MozillaCookieJar(str(path))
    loaded.load(ignore_discard=True, ignore_expires=True)
    assert next(iter(loaded)).value == "rotated"
    assert next(iter(loaded)).has_nonstandard_attr(HTTPONLY_ATTR)
    assert "#HttpOnly_.bilibili.com\tTRUE" in bili_auth._cookie_text(loaded)


@pytest.mark.parametrize("target", ["video", "subtitle"])
def test_concurrent_requests_wait_for_credentials_before_rate_limiting(monkeypatch, target):
    ready = threading.Barrier(4)
    sent = []

    class Outgoing:
        def get(self, *args, **kwargs):
            sent.append(time.monotonic())
            return response({"code": 0, "data": {"cid": 123, "subtitle": {"subtitles": []}}})

    def get_session():
        # Every request waits on the same slow authentication check. Placing
        # rate limiting before this barrier consumes all slots before release.
        ready.wait(timeout=5)
        return Outgoing()

    monkeypatch.setattr(bili_subtitle, "_get_http_session", get_session)
    monkeypatch.setattr(bili_subtitle, "_sign_params", lambda params: params)
    monkeypatch.setattr(bili_subtitle, "_last_request_time", 0)
    monkeypatch.setattr(settings, "bili_rate_limit_seconds", 0.08)
    call = (lambda _: bili_subtitle.get_video_info("BVtest")) if target == "video" else (lambda _: bili_subtitle.get_subtitle_urls("BVtest", 123))
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(call, range(4)))
    assert len(sent) == 4
    times = sorted(sent)
    assert all(later - earlier >= 0.06 for earlier, later in zip(times, times[1:]))


def test_manual_reimport_replaces_old_state_and_triggers_check(monkeypatch):
    monkeypatch.setattr(bili_auth, "_correspond_path", lambda ts: "signed")
    install_session(monkeypatch, renewal_steps())
    bili_auth.ensure_session()
    jar = bili_auth._jar_from_text(bili_auth._cookie_path().read_text())
    jar.set_cookie(bili_auth._cookie("SESSDATA", "reimported-session"))
    bili_auth._cookie_path().write_text(bili_auth._cookie_text(jar))
    monkeypatch.setattr(settings, "bilibili_refresh_token", "reimported-token")
    session, _ = install_session(monkeypatch, [("GET", "/cookie/info", info())])
    assert bili_auth.ensure_session()["status"] == "healthy"
    assert session.calls[0][3]["SESSDATA"] == "reimported-session"
    assert json.loads(bili_auth._state_path().read_text())["refresh_token"] == "reimported-token"


def test_adding_refresh_token_to_existing_cookie_only_login_enables_renewal(monkeypatch):
    monkeypatch.setattr(settings, "bilibili_refresh_token", "")
    install_session(monkeypatch, [("GET", "/cookie/info", info())])
    assert bili_auth.ensure_session()["status"] == "missing_refresh_token"
    monkeypatch.setattr(settings, "bilibili_refresh_token", "old-token")
    monkeypatch.setattr(bili_auth, "_correspond_path", lambda ts: "signed")
    install_session(monkeypatch, renewal_steps())
    assert bili_auth.ensure_session()["status"] == "healthy"
    assert bili_auth.load_cookies()["SESSDATA"] == "new-session"


def test_environment_cookie_seed_never_rolls_back_persisted_file(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("BILIBILI_COOKIES_FILE", str(tmp_path / "mounted" / "cookies.txt"))
    monkeypatch.setenv("BILIBILI_COOKIES", "# Netscape HTTP Cookie File\\n.bilibili.com\tTRUE\t/\tTRUE\t0\tSESSDATA\tseed")
    config = Settings()
    config.ensure_dirs()
    path = config.prepare_bilibili_cookies_file()
    assert path.read_text().endswith("SESSDATA\tseed\n")
    path.write_text("renewed-cookie")
    config.ensure_dirs()
    assert config.prepare_bilibili_cookies_file().read_text() == "renewed-cookie"
    restarted = Settings()
    restarted.ensure_dirs()
    assert restarted.prepare_bilibili_cookies_file().read_text() == "renewed-cookie"


def test_download_snapshot_cannot_roll_back_rotated_credentials(monkeypatch):
    monkeypatch.setattr(bili_auth, "_correspond_path", lambda ts: "signed")
    install_session(monkeypatch, [("GET", "/cookie/info", info())])
    with bili_auth.download_cookie_args() as args:
        assert args[0] == "--cookies" and len(args) == 2
        snapshot = Path(args[1])
        assert snapshot.stat().st_mode & 0o777 == 0o600
        old_text = snapshot.read_text()
        install_session(monkeypatch, renewal_steps())
        assert bili_auth.ensure_session(force=True)["status"] == "healthy"
        snapshot.write_text(old_text)  # yt-dlp writes its stale jar on completion
        assert bili_auth.load_cookies()["SESSDATA"] == "new-session"
    assert not snapshot.exists()
    assert "new-session" in bili_auth._cookie_path().read_text()


def test_subtitle_session_replaces_stale_cookie_and_keeps_domain_scope(monkeypatch):
    monkeypatch.setattr(settings, "bilibili_keepalive_enabled", False)
    session = Session([])
    monkeypatch.setattr(bili_auth.requests, "Session", lambda: session)
    monkeypatch.delattr(bili_subtitle._http_sessions, "session", raising=False)
    first = bili_subtitle._get_http_session()
    assert first.cookies.get_dict()["SESSDATA"] == "old-session"
    monkeypatch.setattr(settings, "bilibili_cookie_header", "SESSDATA=new-session; bili_jct=new-csrf")
    second = bili_subtitle._get_http_session()
    assert second is first
    assert second.cookies.get_dict() == {"SESSDATA": "new-session", "bili_jct": "new-csrf"}
    request = requests.Request("GET", "https://example.com/", cookies=second.cookies).prepare()
    assert "Cookie" not in request.headers


def test_unconfigured_and_disabled_sessions_make_no_network_requests(monkeypatch):
    monkeypatch.setattr(settings, "bilibili_cookie_header", None)
    assert bili_auth.ensure_session()["status"] == "unconfigured"
    monkeypatch.setattr(settings, "bilibili_keepalive_enabled", False)
    assert bili_auth.ensure_session()["status"] == "disabled"
    bili_auth.requests.Session.assert_not_called()


def test_status_api_requires_administrator_and_makes_no_network_requests(monkeypatch):
    from backend.app import main

    monkeypatch.setattr(settings, "bilibili_keepalive_enabled", False)
    result = TestClient(main.app).get("/api/bilibili/session")
    assert result.status_code == 401
    assert not any(secret in result.text for secret in ["old-session", "old-token", "old-csrf"])
    bili_auth.requests.Session.assert_not_called()


def test_startup_runs_keepalive_in_background_and_shutdown_cancels_it(monkeypatch):
    from backend.app import main

    called = threading.Event()
    monkeypatch.setattr(main, "ensure_session", called.set)

    async def lifecycle():
        await main.start_bilibili_keepalive()
        for _ in range(100):
            if called.is_set():
                break
            await asyncio.sleep(0.01)
        assert called.is_set()
        await main.stop_bilibili_keepalive()
        assert main.bilibili_keepalive_task.cancelled()

    asyncio.run(lifecycle())


def test_simultaneous_consumers_run_one_check_and_rotation(monkeypatch):
    monkeypatch.setattr(bili_auth, "_correspond_path", lambda ts: "signed")
    session, factory = install_session(monkeypatch, renewal_steps())
    barrier = threading.Barrier(8)

    def consume(_):
        barrier.wait(timeout=5)
        return bili_auth.load_cookies()["SESSDATA"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(consume, range(8))) == ["new-session"] * 8
    factory.assert_called_once()
    assert len(session.calls) == 4


def test_correspond_path_uses_rsa_oaep_sha256_and_server_timestamp(monkeypatch):
    from Crypto.Cipher import PKCS1_OAEP
    from Crypto.Hash import SHA256
    from Crypto.PublicKey import RSA

    private_key = RSA.generate(1024)
    monkeypatch.setattr(bili_auth, "PUBLIC_KEY", private_key.public_key().export_key().decode())
    encrypted = bytes.fromhex(bili_auth._correspond_path(1800000000123))
    assert PKCS1_OAEP.new(private_key, SHA256).decrypt(encrypted) == b"refresh_1800000000123"
