"""Shared Bilibili credentials and the web cookie renewal protocol.

Protocol references (no third-party service receives credentials):
https://github.com/pskdje/bilibili-API-collect/blob/main/docs/login/cookie_refresh.md
https://github.com/defy997/bilibili-api/blob/main/refresh_local.py
https://github.com/public-clis/bilibili-cli/blob/main/bili_cli/auth.py
"""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from http.cookiejar import HTTPONLY_ATTR, Cookie, LoadError, MozillaCookieJar
from pathlib import Path

import requests

from .config import settings

logger = logging.getLogger(__name__)
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
HEADERS = {"User-Agent": USER_AGENT, "Referer": "https://www.bilibili.com/", "Origin": "https://www.bilibili.com"}
PASSPORT = "https://passport.bilibili.com/x/passport-login/web"
PUBLIC_KEY = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDLgd2OAkcGVtoE3ThUREbio0Eg
Uc/prcajMKXvkCKFCWhJYJcLkcM2DKKcSeFpD/j6Boy538YXnR6VhcuUJOhH2x71
nzPjfdTcqMz7djHum0qSZA0AyCBDABUqCrfNgCiJ00Ra7GmRj+YCK1NJEuewlb40
JNrRuoEUXpabUzGB8QIDAQAB
-----END PUBLIC KEY-----"""
_guard = threading.RLock()
_last_status: dict = {"status": "unconfigured", "message": "未配置 Bilibili 登录态"}


def _is_bili_cookie_domain(domain: str) -> bool:
    domain = (domain or "").lstrip(".").lower()
    return domain == "bilibili.com" or domain.endswith(".bilibili.com")


def _cookie_dict_from_jar(jar) -> dict[str, str]:
    return {c.name: c.value for c in jar if c.name and c.value and _is_bili_cookie_domain(c.domain)}


def _parse_cookie_header(header: str) -> dict[str, str]:
    cookies = {}
    for pair in (header or "").split(";"):
        if "=" in pair:
            name, value = (part.strip() for part in pair.split("=", 1))
            if name and value:
                cookies[name] = value
    return cookies


def _cookie(name: str, value: str, domain=".bilibili.com", path="/", secure=True, expires=None, http_only=False) -> Cookie:
    return Cookie(
        version=0, name=name, value=value, port=None, port_specified=False,
        domain=domain, domain_specified=domain.startswith("."), domain_initial_dot=domain.startswith("."),
        path=path, path_specified=True, secure=secure, expires=expires,
        discard=expires is None, comment=None, comment_url=None,
        rest={"HttpOnly": None} if http_only else {},
    )


def _jar_from_text(text: str) -> MozillaCookieJar:
    jar = MozillaCookieJar()
    for line in text.splitlines():
        http_only = line.startswith("#HttpOnly_")
        if http_only:
            line = line[len("#HttpOnly_"):]
        elif not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t", 6)
        if len(fields) != 7:
            fields = line.split(None, 6)
        if len(fields) != 7:
            continue
        domain, _subdomains, path, secure, expires, name, value = fields
        if not name or not value or not _is_bili_cookie_domain(domain):
            continue
        try:
            expiry = int(expires) or None
        except ValueError:
            continue
        jar.set_cookie(_cookie(name, value, domain, path, secure == "TRUE", expiry, http_only))
    return jar


def _parse_netscape_cookie_text(text: str) -> dict[str, str]:
    return _cookie_dict_from_jar(_jar_from_text(text))


def _parse_json_cookie_export(parsed: object) -> dict[str, str]:
    if isinstance(parsed, list):
        cookies = {}
        for item in parsed:
            cookies.update(_parse_json_cookie_export(item))
        return cookies
    if not isinstance(parsed, dict):
        return {}
    if "name" in parsed and "value" in parsed:
        domain = str(parsed.get("domain", ""))
        if domain and not _is_bili_cookie_domain(domain):
            return {}
        name, value = str(parsed["name"]), str(parsed["value"])
        return {name: value} if name and value else {}
    for key in ("cookies", "cookie"):
        cookies = _parse_json_cookie_export(parsed.get(key))
        if cookies:
            return cookies
    keys = {"SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "buvid3", "buvid4", "sid"}
    return {str(k): str(v) for k, v in parsed.items() if k in keys and v}


def _cookie_path() -> Path:
    return Path(settings.bilibili_cookies_file).expanduser() if settings.bilibili_cookies_file else settings.cookies_dir / "bilibili.cookies.txt"


def _state_path() -> Path:
    return _cookie_path().with_suffix(".session.json")


def _read_source() -> MozillaCookieJar:
    try:
        managed = json.loads(_state_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        managed = {}
    if isinstance(managed, dict) and managed.get("managed"):
        # Once an administrator imports or clears credentials, environment
        # seeds must never restore another account or override the new token.
        if not isinstance(managed.get("cookie_text"), str):
            raise ValueError("Invalid managed cookie text")
        return _jar_from_text(managed["cookie_text"])
    jar = MozillaCookieJar()
    try:
        path = settings.prepare_bilibili_cookies_file()
    except FileNotFoundError:
        path = None
    if path:
        text = path.read_text(encoding="utf-8").lstrip()
        try:
            jar.load(str(path), ignore_discard=True, ignore_expires=True)
        except (LoadError, OSError):
            jar = _jar_from_text(text)
            if not jar and text.startswith(("{", "[")):
                for name, value in _parse_json_cookie_export(json.loads(text)).items():
                    jar.set_cookie(_cookie(name, value))
    # A persisted login takes precedence over an old environment header. Never
    # combine authentication fields from two different login sessions.
    if not _cookie_dict_from_jar(jar).get("SESSDATA"):
        for name, value in _parse_cookie_header(settings.bilibili_cookie_header or "").items():
            jar.set_cookie(_cookie(name, value))
    return jar


def _cookie_text(jar) -> str:
    lines = ["# Netscape HTTP Cookie File", "# Managed Bilibili credentials"]
    for c in jar:
        if not _is_bili_cookie_domain(c.domain):
            continue
        http_only = c.has_nonstandard_attr("HttpOnly") or c.has_nonstandard_attr(HTTPONLY_ATTR)
        domain = ("#HttpOnly_" if http_only else "") + c.domain
        lines.append("\t".join([domain, "TRUE" if c.domain.startswith(".") else "FALSE", c.path,
                                "TRUE" if c.secure else "FALSE", str(c.expires or 0), c.name, c.value or ""]))
    return "\n".join(lines) + "\n"


def _fingerprint(jar) -> str:
    cookies = _cookie_dict_from_jar(jar)
    identity = [cookies.get(key, "") for key in ("SESSDATA", "bili_jct", "DedeUserID")]
    return hashlib.sha256(json.dumps(identity).encode()).hexdigest()


def _read_state(source) -> dict:
    try:
        state = json.loads(_state_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        state = {}
    if not isinstance(state, dict):
        raise ValueError("Invalid Bilibili session file")
    if state and (
        not isinstance(state.get("cookie_text"), str)
        or not isinstance(state.get("refresh_token", ""), str)
        or not isinstance(state.get("next_check_at", 0), (int, float))
    ):
        raise ValueError("Invalid Bilibili session fields")
    seed_fingerprint = hashlib.sha256(settings.bilibili_refresh_token.encode()).hexdigest()
    if state.get("cookie_text"):
        fingerprint = _fingerprint(source)
        if fingerprint in {state.get("source_fingerprint"), _fingerprint(_jar_from_text(state["cookie_text"]))}:
            # Accept an explicitly added/corrected configuration token, while
            # keeping a rotated token when the startup environment is unchanged.
            if not state.get("managed") and settings.bilibili_refresh_token and seed_fingerprint != state.get("token_seed_fingerprint"):
                state.update(refresh_token=settings.bilibili_refresh_token, next_check_at=0,
                             token_seed_fingerprint=seed_fingerprint)
            return state
    return {
        "cookie_text": _cookie_text(source), "source_fingerprint": _fingerprint(source),
        "refresh_token": settings.bilibili_refresh_token, "next_check_at": 0,
        "token_seed_fingerprint": seed_fingerprint,
        "status": "unchecked" if _cookie_dict_from_jar(source).get("SESSDATA") else "unconfigured",
        "message": "尚未检查 Bilibili 登录态" if _cookie_dict_from_jar(source).get("SESSDATA") else "未配置 Bilibili 登录态",
    }


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _persist(state: dict, *, sync_cookies=False) -> None:
    # Commit cookies and their matching token together before invalidating the
    # old session. This file also recovers an interrupted cookie-file update.
    _atomic_write(_state_path(), json.dumps(state, ensure_ascii=False, indent=2) + "\n")
    if sync_cookies:
        _atomic_write(_cookie_path(), state["cookie_text"])


@contextmanager
def _file_guard():
    lock_path = _state_path().with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def import_credentials(cookie_text: str | None = None, refresh_token: str | None = None, *, clear=False) -> None:
    """Replace one managed account without exposing stored credential values."""
    global _last_status
    with _guard, _file_guard():
        try:
            source = _read_source()
            state = _read_state(source)
        except (OSError, ValueError, TypeError):
            # A complete import or removal can repair an unreadable state;
            # adding only a token must not discard an existing account.
            if not clear and cookie_text is None:
                raise ValueError("请重新导入完整登录凭据") from None
            source = MozillaCookieJar()
            state = {"cookie_text": ""}
        jar = _jar_from_text(state["cookie_text"])
        token = state.get("refresh_token", "")
        if clear:
            jar = MozillaCookieJar()
            token = ""
        elif cookie_text is not None:
            text = cookie_text.strip()
            if text.startswith(("{", "[")):
                values = _parse_json_cookie_export(json.loads(text))
                replacement = MozillaCookieJar()
                for name, value in values.items():
                    replacement.set_cookie(_cookie(name, value))
            elif "\t" in text or text.startswith("#"):
                replacement = _jar_from_text(text)
            else:
                replacement = MozillaCookieJar()
                for name, value in _parse_cookie_header(text).items():
                    replacement.set_cookie(_cookie(name, value))
            if not _cookie_dict_from_jar(replacement).get("SESSDATA"):
                raise ValueError("Cookie 中缺少 SESSDATA，请导入登录后的 Bilibili Cookie")
            if any(any(ord(char) < 32 or ord(char) == 127 for char in field)
                   for cookie in replacement for field in (cookie.domain, cookie.path, cookie.name, cookie.value or "")):
                raise ValueError("Cookie 字段包含无效的控制字符")
            if _fingerprint(jar) != _fingerprint(replacement):
                token = ""  # A new login must not inherit another session's token.
            jar = replacement
        if refresh_token is not None and not clear:
            token = refresh_token.strip()
            if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in token):
                raise ValueError("刷新令牌包含无效字符")
        if token and not all(_cookie_dict_from_jar(jar).get(k) for k in ("SESSDATA", "bili_jct")):
            raise ValueError("自动续期需要同一登录态的 SESSDATA、bili_jct 和刷新令牌")
        state = {
            "managed": True, "cookie_text": _cookie_text(jar), "refresh_token": token,
            "source_fingerprint": _fingerprint(source), "next_check_at": 0,
            "status": "unchecked" if jar else "unconfigured",
            "message": "凭据已保存，等待检查" if jar else "未配置 Bilibili 登录态",
        }
        _persist(state, sync_cookies=True)
        _last_status = state


def reset_check_schedule() -> None:
    with _guard, _file_guard():
        state = _read_state(_read_source())
        state["next_check_at"] = 0
        _persist(state)


class _APIError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(f"Bilibili API code={code}")


def _api_result(response) -> dict:
    response.raise_for_status()
    result = response.json()
    if result.get("code") != 0:
        raise _APIError(result.get("code"))
    return result.get("data") or {}


def _correspond_path(timestamp: int) -> str:
    from Crypto.Cipher import PKCS1_OAEP
    from Crypto.Hash import SHA256
    from Crypto.PublicKey import RSA

    return PKCS1_OAEP.new(RSA.import_key(PUBLIC_KEY), SHA256).encrypt(f"refresh_{timestamp}".encode()).hex()


def _confirm(session, state: dict) -> None:
    _api_result(session.post(f"{PASSPORT}/confirm/refresh", data={
        "csrf": _cookie_dict_from_jar(session.cookies)["bili_jct"],
        "refresh_token": state["pending_confirmation"],
    }, timeout=10))
    state.pop("pending_confirmation")
    _persist(state)


def _renew(session, state: dict, timestamp: int) -> None:
    cookies = _cookie_dict_from_jar(session.cookies)
    if not cookies.get("bili_jct"):
        raise ValueError("Missing bili_jct")
    old_token = state["refresh_token"]
    for attempt in range(2):
        if attempt:
            info = _api_result(session.get(f"{PASSPORT}/cookie/info", params={"csrf": cookies["bili_jct"]}, timeout=10))
            timestamp = int(info.get("timestamp") or time.time() * 1000)
        path = _correspond_path(timestamp)
        response = session.get(f"https://www.bilibili.com/correspond/1/{path}", timeout=10)
        response.raise_for_status()
        match = re.search(r'<div\b[^>]*\bid\s*=\s*[\"\']1-name[\"\'][^>]*>\s*([^<]+)', response.text)
        if not match:
            raise ValueError("Missing refresh_csrf")
        response = session.post(f"{PASSPORT}/cookie/refresh", data={
            "csrf": cookies["bili_jct"], "refresh_csrf": match.group(1).strip(),
            "source": "main_web", "refresh_token": old_token,
        }, timeout=10)
        try:
            result = _api_result(response)
            break
        except _APIError as exc:
            if exc.code != 86095 or attempt:
                raise
            # 86095 can mean an expired refresh-CSRF, not an invalid token.
            # Obtain a new server timestamp and nonce before retrying once.
    # Read Set-Cookie from the response explicitly. Normalize old same-name
    # cookies so request jars cannot send both an old and a new SESSDATA.
    replacements = _cookie_dict_from_jar(response.cookies)
    token = result.get("refresh_token")
    if not isinstance(token, str) or not token or not all(replacements.get(key) for key in ("SESSDATA", "bili_jct")):
        raise ValueError("Incomplete renewal response")
    for c in list(session.cookies):
        if c.name in replacements:
            session.cookies.clear(c.domain, c.path, c.name)
    for c in response.cookies:
        if _is_bili_cookie_domain(c.domain):
            session.cookies.set_cookie(c)
    state.update(cookie_text=_cookie_text(session.cookies), refresh_token=token,
                 pending_confirmation=old_token, last_refreshed_at=time.time())
    _persist(state, sync_cookies=True)
    _confirm(session, state)
    logger.info("Bilibili cookies renewed and persisted")


def _public_status(state: dict) -> dict:
    jar = _jar_from_text(state.get("cookie_text", ""))
    cookies = _cookie_dict_from_jar(jar)
    return {key: state.get(key) for key in ("status", "message", "last_checked_at", "last_refreshed_at", "next_check_at")} | {
        "keepalive_enabled": settings.bilibili_keepalive_enabled,
        "has_refresh_token": bool(state.get("refresh_token")),
        "has_cookie": bool(cookies.get("SESSDATA")),
        "account_id": cookies.get("DedeUserID") or None,
        "cookie_expires_at": next((c.expires for c in jar if c.name == "SESSDATA"), None),
    }


def ensure_session(*, force=False) -> dict:
    """Check at most daily, renew only when requested, and back off on failures."""
    global _last_status
    settings.refresh_runtime()
    if not settings.bilibili_keepalive_enabled and not force:
        return _public_status({"status": "disabled", "message": "自动续期已关闭"})
    with _guard:
        state = None
        try:
            source = _read_source()
            if not _cookie_dict_from_jar(source).get("SESSDATA"):
                _last_status = {"status": "unconfigured", "message": "未配置 Bilibili 登录态"}
                return _public_status(_last_status)
            with _file_guard():
                # Re-read after acquiring the lock: another worker may have
                # rotated the credentials while this worker was waiting.
                source = _read_source()
                state = _read_state(source)
                if not force and time.time() < state.get("next_check_at", 0):
                    _last_status = state
                    return _public_status(state)
                state["next_check_at"] = time.time() + settings.bilibili_retry_interval
                state["source_fingerprint"] = _fingerprint(source)
                # Validate persistence before a remote mutation takes place.
                _persist(state)
                try:
                    with requests.Session() as session:
                        session.headers.update(HEADERS)
                        session.cookies.update(_jar_from_text(state["cookie_text"]))
                        if state.get("pending_confirmation"):
                            _persist(state, sync_cookies=True)
                            _confirm(session, state)
                        info = _api_result(session.get(f"{PASSPORT}/cookie/info", params={
                            "csrf": _cookie_dict_from_jar(session.cookies).get("bili_jct", ""),
                        }, timeout=10))
                        if not isinstance(info.get("refresh"), bool):
                            raise ValueError("Invalid cookie info response")
                        if info.get("refresh"):
                            if state.get("refresh_token"):
                                _renew(session, state, int(info.get("timestamp") or time.time() * 1000))
                            else:
                                state.update(status="refresh_required", message="Cookie 需要续期，请在管理后台补充同一次登录的刷新令牌")
                        if not info.get("refresh") or state.get("refresh_token"):
                            if state.get("refresh_token"):
                                state.update(status="healthy", message="登录态有效，自动续期已就绪")
                            else:
                                state.update(status="missing_refresh_token", message="登录态有效；请在管理后台补充刷新令牌以启用自动续期")
                        state["next_check_at"] = time.time() + settings.bilibili_check_interval
                except _APIError as exc:
                    if exc.code == -101:
                        state.update(status="expired", message="Bilibili 登录态已失效，请重新导入 Cookie 和对应的刷新令牌")
                        state["next_check_at"] = time.time() + settings.bilibili_check_interval
                    elif exc.code == 86095:
                        state.update(status="retrying", message="刷新校验暂未通过，将重新获取口令后重试；持续失败时请更新同一登录态的凭据")
                    else:
                        state.update(status="retrying", message=f"Bilibili 暂时拒绝检查/续期（code={exc.code}），稍后重试")
                    logger.warning("Bilibili session check/renewal failed: code=%s", exc.code)
                except Exception as exc:
                    state.update(status="retrying", message="登录态检查/续期暂时失败，保留现有凭据并稍后重试")
                    # Never log Cookie, refresh tokens or upstream response bodies.
                    logger.warning("Bilibili session check/renewal failed: %s", type(exc).__name__)
                state["last_checked_at"] = time.time()
                if state.get("status") == "retrying":
                    state["next_check_at"] = time.time() + settings.bilibili_retry_interval
                _persist(state)
                _last_status = state
                if state["status"] != "healthy":
                    logger.warning("Bilibili session status=%s: %s", state["status"], state["message"])
                return _public_status(state)
        except Exception as exc:
            logger.warning("Bilibili credential persistence unavailable: %s", type(exc).__name__)
            _last_status = {"status": "configuration_error", "message": "无法读取或保存 Bilibili 凭据，请检查 Cookie 文件格式和目录写权限"}
            return _public_status(_last_status)


def get_cookie_jar() -> MozillaCookieJar:
    ensure_session()
    with _guard:
        source = _read_source()
        try:
            return _jar_from_text(_read_state(source)["cookie_text"])
        except (OSError, ValueError, TypeError):
            return source


def load_cookies() -> dict[str, str]:
    return _cookie_dict_from_jar(get_cookie_jar())


def session_status() -> dict:
    """Read cached status only; administrator APIs never return credentials."""
    with _guard:
        try:
            state = _read_state(_read_source())
        except (OSError, ValueError, TypeError):
            state = _last_status
        result = _public_status(state if state.get("status") else _last_status)
        if not settings.bilibili_keepalive_enabled:
            result.update(status="disabled", message="自动续期已关闭，可以手动检查登录态")
        return result


@contextmanager
def download_cookie_args():
    """Give each yt-dlp invocation a disposable jar to prevent stale writeback."""
    from .ffmpeg_tools import VideoProcessingError

    try:
        jar = get_cookie_jar()
    except (OSError, ValueError, TypeError) as exc:
        raise VideoProcessingError("无法读取 Bilibili Cookie，请检查文件格式和目录权限") from exc
    if not jar:
        yield []
        return
    with tempfile.TemporaryDirectory(prefix="bilibili-download-") as directory:
        path = Path(directory) / "cookies.txt"
        _atomic_write(path, _cookie_text(jar))
        yield ["--cookies", str(path)]


if __name__ == "__main__":
    print(json.dumps(ensure_session(force=True), ensure_ascii=False, indent=2))
