"""Source video persistence, cleanup, and portable metadata paths."""
import asyncio
from io import BytesIO
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest
from fastapi import FastAPI, UploadFile
from fastapi.testclient import TestClient

from backend.app import exercise_store as store, storage
from backend.app.config import settings
from backend.app.models import SourceType, VideoInfo


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("uploads_dir", "downloads_dir", "exercises_dir"):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setattr(settings, name, directory)
    store.init_db()


def save_video(video_id, source_type):
    directory = storage._video_dir(video_id, source_type)
    directory.mkdir()
    source = directory / "source.mp4"
    source.write_bytes(b"video")
    info = VideoInfo(
        id=video_id, source_type=source_type, filename="lecture.mp4",
        duration=10, width=640, height=480,
        preview_url=f"/api/videos/{video_id}/file",
    )
    storage._write_metadata(info, source)
    return source


@pytest.mark.parametrize("source_type", [SourceType.upload, SourceType.bilibili])
def test_restart_cleanup_retains_lesson_video_and_removes_unused_video(source_type):
    video_id, unused_id = "a" * 32, "b" * 32
    source = save_video(video_id, source_type)
    unused = save_video(unused_id, source_type)
    store.save_lesson({"id": "lesson", "video_id": video_id, "batches": []})
    # Reopen the persistent store as on startup, after process-local pins end.
    store.init_db()
    assert video_id not in storage._source_pins
    assert storage.delete_old_videos(86400, now=time.time() + 2 * 86400) == 1
    assert source.is_file()
    assert not unused.exists()
    from backend.app.main import video_file
    app = FastAPI()
    app.add_api_route("/api/videos/{video_id}/file", video_file)
    response = TestClient(app).get(f"/api/videos/{video_id}/file")
    assert response.status_code == 200
    assert response.content == b"video"


def test_upload_can_be_read_by_fresh_process_after_data_directory_move(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ensure_dirs", lambda: None)
    monkeypatch.setattr(storage, "_ensure_faststart", lambda path: None)
    monkeypatch.setattr(storage, "probe_video", lambda path: (10, 640, 480))
    info = asyncio.run(storage.save_upload(UploadFile(filename="lecture.mp4", file=BytesIO(b"video"))))
    metadata_path = settings.uploads_dir / info.id / "meta.json"
    assert json.loads(metadata_path.read_text())["source_path"] == "source.mp4"
    relocated = tmp_path / "relocated"
    relocated.mkdir()
    shutil.move(str(settings.uploads_dir), relocated / "uploads")
    result = subprocess.run(
        [sys.executable, "-c", (
            "from backend.app.storage import get_video_file; "
            f"print(get_video_file('{info.id}').read_bytes().decode())"
        )],
        cwd=Path(__file__).resolve().parents[2],
        env={**os.environ, "DATA_DIR": str(relocated)},
        capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == "video"


@pytest.mark.parametrize("source_type", [SourceType.upload, SourceType.bilibili])
def test_legacy_absolute_path_uses_current_video_directory(source_type):
    video_id = "c" * 32
    source = save_video(video_id, source_type)
    metadata_path = source.parent / "meta.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["source_path"] = f"/old-server/data/{source_type.value}/{video_id}/source.mp4"
    if source_type == SourceType.bilibili:
        metadata.update(storage._bilibili_cache_metadata("BV1xx411c7mD", 1))
    metadata_path.write_text(json.dumps(metadata))
    assert storage.get_video_file(video_id) == source.resolve()
    assert storage.get_video_metadata(video_id)["source_path"] == str(source.resolve())
    if source_type == SourceType.bilibili:
        assert storage._cached_bilibili_info(video_id, "lecture.mp4", "BV1xx411c7mD", 1).id == video_id


def test_cleanup_works_before_exercise_database_exists():
    (settings.exercises_dir / "exercises.db").unlink()
    source = save_video("d" * 32, SourceType.upload)
    assert storage.delete_old_videos(86400, now=time.time() + 2 * 86400) == 1
    assert not source.exists()
