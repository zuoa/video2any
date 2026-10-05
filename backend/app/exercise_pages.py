"""Immutable printable question snapshots and readable knowledge-point URLs."""
import hashlib
import json
import re
import time

from pypinyin import lazy_pinyin

from .ffmpeg_tools import VideoProcessingError


def snapshot(lesson, batch, question_ids=None, title=None, created_at=None):
    questions = batch["questions"]
    if question_ids is not None:
        wanted = set(question_ids)
        if len(wanted) != len(question_ids):
            raise VideoProcessingError("所选题目不能重复")
        if not wanted or not wanted.issubset({q["id"] for q in questions}):
            raise VideoProcessingError("所选题目不存在，请刷新后重试。")
        # Keep worksheet and answer numbering in the original batch order.
        questions = [q for q in questions if q["id"] in wanted]
    used = {point_id for q in questions for point_id in q["knowledge_point_ids"]}
    points = [p for p in batch.get("knowledge_points", lesson["knowledge_points"]) if p["id"] in used]
    labels = list(dict.fromkeys(p["title"] for p in points))
    topic = "、".join(labels[:3]) or lesson["title"]
    title = title or f"{topic} 知识练习"[:120]
    pinyin = "-".join(lazy_pinyin(topic))
    slug = re.sub(r"[^a-z0-9]+", "-", pinyin.lower()).strip("-")[:100].rstrip("-") or "zhi-shi-lian-xi"
    value = {
        "lesson_id": lesson["id"], "batch_id": batch["id"], "version": batch["version"],
        "title": title, "lesson_title": lesson["title"], "knowledge_points": points,
        "questions": questions, "question_count": len(questions),
        "knowledge_point_titles": labels, "created_at": created_at if created_at is not None else time.time(),
    }
    identity = [lesson["id"], batch["id"], title, [q["id"] for q in questions]]
    fingerprint = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()
    return value, slug, fingerprint


def summary(value):
    return {key: value[key] for key in (
        "slug", "title", "lesson_title", "lesson_id", "batch_id", "version", "created_at",
        "question_count", "knowledge_point_titles",
    )}
