"""Private, persistent administrator sessions and runtime configuration."""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path


CONFIG_KEYS = frozenset({
    "bilibili_keepalive_enabled", "bilibili_check_interval", "bilibili_retry_interval", "bili_rate_limit_seconds",
    "openai_api_key", "openai_base_url", "openai_model", "openai_timeout", "openai_reasoning_effort",
    "openai_thinking_type", "openai_reasoning_max_tokens", "openai_json_mode", "summary_max_input_chars", "site_url",
})


def database_path(data_dir: Path) -> Path:
    return data_dir / "admin" / "admin.sqlite3"


@contextmanager
def connect(data_dir: Path):
    path = database_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    os.close(fd)
    path.chmod(0o600)
    db = sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS administrator (
                id INTEGER PRIMARY KEY CHECK (id = 1), username TEXT NOT NULL, password_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY, csrf TEXT NOT NULL, expires_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS login_attempts (client TEXT NOT NULL, attempted_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """)
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def read_config(data_dir: Path) -> dict:
    path = database_path(data_dir)
    if not path.is_file():
        return {}
    # A read does not initialize the schema or create configuration directories.
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)) as db:
        rows = db.execute("SELECT key, value FROM config").fetchall()
    return {key: json.loads(value) for key, value in rows if key in CONFIG_KEYS}


def save_config(data_dir: Path, values: dict) -> None:
    if set(values) - CONFIG_KEYS:
        raise ValueError("Unknown runtime configuration field")
    with connect(data_dir) as db:
        db.executemany("INSERT INTO config VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                       [(key, json.dumps(value, ensure_ascii=False)) for key, value in values.items()])
