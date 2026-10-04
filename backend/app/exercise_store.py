"""Durable lesson versions, question batches, and background task states."""
from contextlib import contextmanager
import json
import sqlite3
import time

from .config import settings


@contextmanager
def connect():
    settings.exercises_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.exercises_dir / "exercises.db", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db():
    with connect() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS lessons (id TEXT PRIMARY KEY, video_id TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, lesson_id TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL)")
        for row in conn.execute("SELECT payload FROM jobs WHERE status IN ('queued', 'running')").fetchall():
            job = json.loads(row["payload"])
            job.update(status="failed", stage="interrupted", error="服务重启，任务已中断，请重试。", updated_at=time.time())
            conn.execute("UPDATE jobs SET status=?, payload=? WHERE id=?", (job["status"], json.dumps(job, ensure_ascii=False), job["id"]))


def lesson(lesson_id):
    with connect() as conn:
        row = conn.execute("SELECT payload FROM lessons WHERE id=?", (lesson_id,)).fetchone()
    if row is None:
        raise LookupError("课程记录不存在")
    return json.loads(row["payload"])


def lesson_for_video(video_id):
    with connect() as conn:
        row = conn.execute("SELECT payload FROM lessons WHERE video_id=?", (video_id,)).fetchone()
    return json.loads(row["payload"]) if row else None


def save_lesson(value):
    with connect() as conn:
        conn.execute("INSERT INTO lessons VALUES (?, ?, ?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload", (value["id"], value["video_id"], json.dumps(value, ensure_ascii=False)))


def job(job_id):
    with connect() as conn:
        row = conn.execute("SELECT payload FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise LookupError("任务不存在")
    return json.loads(row["payload"])


def save_job(value):
    value["updated_at"] = time.time()
    with connect() as conn:
        conn.execute("INSERT INTO jobs VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET status=excluded.status, payload=excluded.payload", (value["id"], value["lesson_id"], value["status"], json.dumps(value, ensure_ascii=False)))


def active_job(lesson_id):
    with connect() as conn:
        row = conn.execute("SELECT payload FROM jobs WHERE lesson_id=? AND status IN ('queued', 'running') LIMIT 1", (lesson_id,)).fetchone()
    return json.loads(row["payload"]) if row else None
