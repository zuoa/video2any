"""Durable lesson versions, question batches, and background task states."""
from contextlib import contextmanager
import json
import sqlite3
import time

from .config import settings
from .exercise_pages import snapshot, summary


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
        conn.execute("CREATE TABLE IF NOT EXISTS pages (slug TEXT PRIMARY KEY, lesson_id TEXT NOT NULL, batch_id TEXT NOT NULL, fingerprint TEXT UNIQUE NOT NULL, created_at REAL NOT NULL, summary TEXT NOT NULL, payload TEXT NOT NULL)")
        conn.execute("CREATE INDEX IF NOT EXISTS pages_recent ON pages(created_at DESC, slug)")
        # Backfill previous generations once, retaining existing URLs on restart.
        for row in conn.execute("SELECT payload FROM lessons").fetchall():
            value = json.loads(row["payload"])
            if _batch_pages(conn, value):
                conn.execute("UPDATE lessons SET payload=? WHERE id=?", (json.dumps(value, ensure_ascii=False), value["id"]))
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
        if _batch_pages(conn, value):
            conn.execute("UPDATE lessons SET payload=? WHERE id=?", (json.dumps(value, ensure_ascii=False), value["id"]))


def _save_page(conn, value, base_slug, fingerprint):
    existing = conn.execute("SELECT payload FROM pages WHERE fingerprint=?", (fingerprint,)).fetchone()
    if existing:
        return json.loads(existing["payload"])
    slug, number = base_slug, 2
    while conn.execute("SELECT 1 FROM pages WHERE slug=?", (slug,)).fetchone():
        slug = f"{base_slug}-{number}"
        number += 1
    value["slug"] = slug
    conn.execute("INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?, ?)", (
        slug, value["lesson_id"], value["batch_id"], fingerprint, value["created_at"],
        json.dumps(summary(value), ensure_ascii=False), json.dumps(value, ensure_ascii=False),
    ))
    return value


def _batch_pages(conn, lesson):
    changed = False
    for batch in lesson["batches"]:
        if batch.get("page_slug") or not batch["questions"]:
            continue
        page = _save_page(conn, *snapshot(lesson, batch, created_at=batch.get("created_at", 0)))
        batch["page_slug"] = page["slug"]
        changed = True
    return changed


def create_page(lesson_id, request):
    with connect() as conn:
        # Serialize slug allocation, including simultaneous saves from two tabs.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT payload FROM lessons WHERE id=?", (lesson_id,)).fetchone()
        if row is None:
            raise LookupError("课程记录不存在")
        value = json.loads(row["payload"])
        batch = next((b for b in value["batches"] if b["id"] == request.batch_id), None)
        if batch is None:
            raise LookupError("题目批次不存在")
        return _save_page(conn, *snapshot(value, batch, request.question_ids, request.title))


def page(slug):
    with connect() as conn:
        row = conn.execute("SELECT payload FROM pages WHERE slug=?", (slug,)).fetchone()
    if row is None:
        raise LookupError("试题页面不存在")
    return json.loads(row["payload"])


def recent_pages(limit=12, offset=0):
    with connect() as conn:
        rows = conn.execute("SELECT summary FROM pages ORDER BY created_at DESC, slug LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    return {"items": [json.loads(row["summary"]) for row in rows], "total": total}


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
