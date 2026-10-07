"""Local administrator bootstrap and revocable, server-side login sessions."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request

from .admin_store import connect, database_path
from .config import settings

COOKIE_NAME = "vta_admin_session"
SESSION_SECONDS = 8 * 60 * 60
PASSWORD_ITERATIONS = 600_000


def password_hash(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PASSWORD_ITERATIONS).hex()
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, expected = encoded.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def validate_password(password: str) -> None:
    if not 12 <= len(password) <= 256:
        raise ValueError("密码需要 12 至 256 个字符")


def initialize_admin(username: str, password: str, *, reset=False) -> None:
    username = username.strip()
    if not username or len(username) > 64:
        raise ValueError("管理员用户名需要 1 至 64 个字符")
    validate_password(password)
    encoded = password_hash(password)
    with connect(settings.data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        if db.execute("SELECT 1 FROM administrator").fetchone() and not reset:
            raise ValueError("管理员已创建；如需重置密码，请使用 reset 命令")
        db.execute("INSERT INTO administrator VALUES (1, ?, ?) ON CONFLICT(id) DO UPDATE SET username=excluded.username, password_hash=excluded.password_hash",
                   (username, encoded))
        db.execute("DELETE FROM sessions")
        db.execute("DELETE FROM login_attempts")


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if (origin is not None and origin != str(request.base_url).rstrip("/")) or request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "请从本站管理页面执行此操作")


def login(username: str, password: str, client: str) -> tuple[str, dict]:
    if not database_path(settings.data_dir).exists():
        raise HTTPException(503, "管理员尚未初始化，请先在服务器创建管理员")
    now = time.time()
    # Register each attempt before hashing so parallel workers cannot bypass
    # the budget. Successful attempts remove their own rate-limit record.
    with connect(settings.data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM login_attempts WHERE attempted_at < ?", (now - 900,))
        local_count = db.execute("SELECT COUNT(*) FROM login_attempts WHERE client=?", (client,)).fetchone()[0]
        total_count = db.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0]
        if local_count >= 5 or total_count >= 50:
            raise HTTPException(429, "登录尝试过多，请 15 分钟后重试", headers={"Retry-After": "900"})
        row = db.execute("SELECT * FROM administrator WHERE id=1").fetchone()
        if row is None:
            raise HTTPException(503, "管理员尚未初始化，请先在服务器创建管理员")
        attempt_id = db.execute("INSERT INTO login_attempts VALUES (?, ?)", (client, now)).lastrowid
        encoded, expected_username = row["password_hash"], row["username"]
    verified = verify_password(password, encoded)
    if not verified or not hmac.compare_digest(username.encode(), expected_username.encode()):
        raise HTTPException(401, "用户名或密码错误")
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    expires_at = now + SESSION_SECONDS
    with connect(settings.data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        # A password reset concurrent with verification must invalidate login.
        current = db.execute("SELECT password_hash FROM administrator WHERE id=1").fetchone()
        if current is None or current[0] != encoded:
            raise HTTPException(401, "管理员凭据已更新，请重新登录")
        db.execute("DELETE FROM login_attempts WHERE rowid=?", (attempt_id,))
        db.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
        db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (hashlib.sha256(token.encode()).hexdigest(), csrf, expires_at))
    return token, {"username": expected_username, "csrf_token": csrf, "expires_at": expires_at}


def require_admin(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME, "")
    if not token or len(token) > 128 or not database_path(settings.data_dir).exists():
        raise HTTPException(401, "请先登录管理后台")
    token_digest = hashlib.sha256(token.encode()).hexdigest()
    with connect(settings.data_dir) as db:
        row = db.execute("SELECT sessions.*, administrator.username FROM sessions CROSS JOIN administrator WHERE token_hash=? AND expires_at>?",
                         (token_digest, time.time())).fetchone()
    if row is None:
        raise HTTPException(401, "登录已过期，请重新登录")
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        check_origin(request)
        if not hmac.compare_digest(request.headers.get("x-admin-csrf", "").encode(), row["csrf"].encode()):
            raise HTTPException(403, "操作校验已失效，请刷新管理页面")
    return {"username": row["username"], "csrf_token": row["csrf"], "expires_at": row["expires_at"], "token_hash": token_digest}


def logout(token_hash: str) -> None:
    with connect(settings.data_dir) as db:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))


def change_password(current: str, replacement: str) -> None:
    validate_password(replacement)
    with connect(settings.data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT password_hash FROM administrator WHERE id=1").fetchone()
        if row is None or not verify_password(current, row[0]):
            raise HTTPException(400, "当前密码不正确")
        db.execute("UPDATE administrator SET password_hash=? WHERE id=1", (password_hash(replacement),))
        db.execute("DELETE FROM sessions")


def main() -> None:
    parser = argparse.ArgumentParser(description="创建或重置隐藏管理后台的管理员")
    parser.add_argument("action", choices=["setup", "reset"])
    parser.add_argument("--username", default="admin")
    args = parser.parse_args()
    try:
        password = getpass.getpass("管理员密码（至少 12 个字符）：")
        if password != getpass.getpass("再次输入密码："):
            raise ValueError("两次输入的密码不一致")
        initialize_admin(args.username, password, reset=args.action == "reset")
    except ValueError as exc:
        parser.exit(1, f"{exc}\n")
    print("管理员已保存。访问 /#/_manage 登录管理后台。")


if __name__ == "__main__":
    main()
