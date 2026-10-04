"""Exercise pipeline, persistence, concurrency, and native Word export checks."""
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.app.config import settings
from backend.app import exercise_service as service, exercise_store as store, exercise_export as exporter, storage
from backend.app.exercise_models import GenerateRequest, LessonPatch, Question, ExportExercisesRequest
from backend.app.exercise_routes import router
from backend.app.ffmpeg_tools import VideoProcessingError


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    for name in ("exercises_dir", "uploads_dir", "downloads_dir", "outputs_dir", "whisper_models_dir"):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setattr(settings, name, directory)
    monkeypatch.setattr(settings, "openai_api_key", "test-key")
    monkeypatch.setattr(service, "_model", None)
    service.start()
    yield
    service.stop()


def lesson(segments=True):
    value = {"id": "lesson", "video_id": "a" * 32, "title": "代数课", "duration": 100, "source": {"type": "upload", "bv": None, "page": None}, "version": 1, "segments": [{"start": 0, "end": 10, "text": "二次方程的求根公式"}] if segments else [], "knowledge_points": [{"id": "point", "title": "二次方程", "detail": "求根公式与判别式", "formulas": "$x^2$", "segment_ids": [0]}] if segments else [], "batches": [], "latest_job_id": None}
    store.save_lesson(value)
    return value


def question(**updates):
    value = {"id": "q", "type": "calculation", "difficulty": "practice", "stem": "求解 $x^2=4$。", "options": [], "answer": "$x=\\pm 2$", "explanation": "两边开平方，得到正负两个根。", "knowledge_point_ids": ["point"]}
    value.update(updates)
    return value


def wait_job(task):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        job = store.job(task["job_id"])
        if job["status"] in ("succeeded", "failed"):
            return job
        time.sleep(.01)
    raise AssertionError("job did not finish")


def test_restart_marks_only_unfinished_jobs():
    lesson()
    for status in ("queued", "running", "succeeded"):
        store.save_job({"id": status, "lesson_id": "lesson", "status": status})
    store.init_db()
    assert store.job("queued")["status"] == "failed"
    assert store.job("running")["stage"] == "interrupted"
    assert store.job("succeeded")["status"] == "succeeded"


def test_long_segments_chunk_without_losing_tail_or_evidence(monkeypatch):
    monkeypatch.setattr(settings, "exercise_max_input_chars", 1000)
    segments = [{"start": 0, "end": 10, "text": "甲" * 2500}, {"start": 10, "end": 20, "text": "最后知识点"}]
    chunks = service._source_chunks(segments)
    assert len(chunks) >= 3
    assert sum(text.count("甲") for text, ids in chunks) == 2500
    assert "最后知识点" in chunks[-1][0]
    assert 1 in chunks[-1][1]


def test_knowledge_extraction_merges_evidence_and_rejects_wrong_ids(monkeypatch):
    segments = [{"start": 0, "end": 10, "text": "甲" * 1000}, {"start": 10, "end": 20, "text": "乙" * 1000}]
    monkeypatch.setattr(settings, "exercise_max_input_chars", 1100)
    calls = iter([json.dumps({"knowledge_points": [{"id": "a", "title": "概念", "detail": "前半段", "segment_ids": [0]}]}, ensure_ascii=False), json.dumps({"knowledge_points": [{"id": "a", "title": "概念", "detail": "后半段", "segment_ids": [1]}]}, ensure_ascii=False)])
    with patch.object(service, "_chat", side_effect=lambda *args, **kwargs: next(calls)):
        points = service.extract_knowledge(segments, {"id": "j", "lesson_id": "lesson"})
    assert len(points) == 1
    assert points[0]["segment_ids"] == [0, 1]
    assert "后半段" in points[0]["detail"]


def test_structured_output_repairs_once_and_never_accepts_prose():
    with patch.object(service, "_chat", side_effect=["not json", '{"value": 42}']) as chat:
        assert service._structured("prompt", lambda p: p["value"]) == 42
        assert chat.call_count == 2
    with patch.object(service, "_chat", return_value="broken") as chat:
        with pytest.raises(VideoProcessingError):
            service._structured("prompt", lambda p: p)
        assert chat.call_count == 2


@pytest.mark.parametrize("updates", [{"type": "single_choice", "options": ["one"], "answer": "A"}, {"type": "single_choice", "options": ["one", "two", "three", "four"], "answer": "two"}, {"options": ["unexpected"]}, {"answer": ""}])
def test_question_shape_requires_valid_answers_and_options(updates):
    with pytest.raises(ValidationError):
        Question.model_validate(question(**updates))


def test_no_duplicate_or_unselected_questions():
    request = GenerateRequest(version=1, knowledge_point_ids=["point"], types=["calculation"], count=2)
    with pytest.raises(ValueError, match="重复"):
        service._validate_questions({"questions": [question(), question()]}, 2, {"point"}, request)
    with pytest.raises(ValueError, match="未选择"):
        service._validate_questions({"questions": [question(knowledge_point_ids=["else"])]}, 1, {"point"}, request)


def test_generation_review_keeps_requested_count_and_snapshot():
    lesson()
    request = GenerateRequest(version=1, knowledge_point_ids=["point"], types=["calculation"], count=2)
    qs = [question(id="a", stem="题一 $x^2=4$"), question(id="b", stem="题二 $x^2=9$")]
    with patch.object(service, "_chat", return_value=json.dumps({"questions": qs})) as chat:
        task = service.generate("lesson", request)
        assert wait_job(task)["status"] == "succeeded"
    saved = store.lesson("lesson")
    assert chat.call_count == 2  # Generation plus answer/solution review.
    batch = saved["batches"][0]
    assert len(batch["questions"]) == 2
    assert batch["knowledge_points"] == saved["knowledge_points"]
    assert len({q["id"] for q in batch["questions"]}) == 2


def test_duplicate_generation_requests_reuse_active_task_and_block_edit():
    lesson()
    entered, release = threading.Event(), threading.Event()
    def generate(*args):
        entered.set()
        assert release.wait(3)
        return [question()]
    request = GenerateRequest(version=1, knowledge_point_ids=["point"], count=1)
    with patch.object(service, "generate_questions", side_effect=generate):
        task = service.generate("lesson", request)
        assert entered.wait(1)
        try:
            assert service.generate("lesson", request) == task
            with pytest.raises(HTTPException) as conflict:
                service.patch_lesson("lesson", LessonPatch(version=1, knowledge_points=store.lesson("lesson")["knowledge_points"]))
            assert conflict.value.status_code == 409
        finally:
            release.set()
        assert wait_job(task)["status"] == "succeeded"


def test_transcript_changes_invalidate_points_and_reuse_asr_result():
    value = lesson()
    value["batches"] = [{"id": "old", "version": 1, "questions": [question()]}]
    store.save_lesson(value)
    revised = [{"start": 0, "end": 10, "text": "修正后的公式"}]
    with patch.object(service, "extract_knowledge", return_value=value["knowledge_points"]), patch.object(service, "transcribe") as asr:
        task = service.patch_lesson("lesson", LessonPatch(version=1, segments=revised))
        assert wait_job(task)["status"] == "succeeded"
        asr.assert_not_called()
    saved = store.lesson("lesson")
    assert saved["version"] == 2 and saved["segments"] == revised
    assert saved["batches"][0]["version"] == 1
    with pytest.raises(HTTPException) as stale:
        service.generate("lesson", GenerateRequest(version=1, knowledge_point_ids=["point"]))
    assert stale.value.status_code == 409


def test_prepare_retries_extraction_without_source_file_or_asr():
    value = lesson()
    value["knowledge_points"] = []
    store.save_lesson(value)
    with patch.object(service, "extract_knowledge", side_effect=VideoProcessingError("LLM down")), patch.object(service, "transcribe") as asr:
        failed = service.prepare(value["video_id"])
        assert wait_job(failed)["status"] == "failed"
        asr.assert_not_called()
    with patch.object(service, "extract_knowledge", return_value=[{"id": "point", "title": "公式", "detail": "讲解", "formulas": "", "segment_ids": [0]}]):
        assert wait_job(service.prepare(value["video_id"]))["status"] == "succeeded"


def test_pin_protects_old_source_until_unpinned():
    video_id = "a" * 32
    directory = settings.uploads_dir / video_id
    directory.mkdir()
    source = directory / "source.mp4"
    source.write_bytes(b"video")
    (directory / "meta.json").write_text(json.dumps({"source_path": str(source), "last_used_at": 0}))
    storage.pin_video(video_id)
    assert storage.delete_old_videos(0, now=time.time() + 100) == 0
    storage.unpin_video(video_id)
    assert storage.delete_old_videos(0, now=time.time() + 100) == 1


def test_asr_consumes_lazy_segments_on_cpu_and_reports_empty_speech():
    model = SimpleNamespace(transcribe=lambda *a, **k: (iter([SimpleNamespace(start=0, end=2, text="你好")]), SimpleNamespace(duration=3)))
    with patch("faster_whisper.WhisperModel", return_value=model) as constructor:
        result = service.transcribe(Path("video.mp4"), {"id": "asr", "lesson_id": "lesson"})
    assert result == [{"start": 0., "end": 2., "text": "你好"}]
    assert constructor.call_args.kwargs["device"] == "cpu"
    assert constructor.call_args.kwargs["compute_type"] == "int8"
    model.transcribe = lambda *a, **k: (iter([]), SimpleNamespace(duration=3))
    with pytest.raises(VideoProcessingError, match="有效语音"):
        service.transcribe(Path("video.mp4"), {"id": "asr", "lesson_id": "lesson"})


def test_http_contract_and_validation():
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    lesson()
    assert client.get("/api/exercises/lesson").status_code == 200
    assert client.get("/api/exercises/missing").status_code == 404
    assert client.post("/api/exercises/lesson/generate", json={"version": 1, "knowledge_point_ids": ["missing"]}).status_code == 400
    assert client.post("/api/exercises/lesson/generate", json={"version": 1, "knowledge_point_ids": ["point"], "count": 31}).status_code == 422
    with patch.object(settings, "openai_api_key", ""):
        assert client.post("/api/exercises/prepare", json={"video_id": "a" * 32}).status_code == 503


def require_pandoc(monkeypatch):
    import shutil
    if shutil.which("pandoc"):
        return
    try:
        import pypandoc
        import os
        monkeypatch.setenv("PATH", str(Path(pypandoc.get_pandoc_path()).parent) + os.pathsep + os.environ["PATH"])
    except ImportError:
        pytest.fail("Install Pandoc (or pypandoc_binary) to run the Word integration checks")


def test_two_word_documents_have_matching_numbers_and_native_math(monkeypatch):
    require_pandoc(monkeypatch)
    from docx import Document
    value = lesson()
    qs = [question(id="q1"), question(id="q2", stem=r"计算 $\frac{1}{2}+\sqrt{4}$ 和 $\sum_{i=1}^{3} i$。", explanation="1. 先计算根号\n2. 再计算求和", answer=r"$\frac{17}{2}$"), question(id="q3", stem="不应导出的题目")]
    value["batches"] = [{"id": "batch", "version": 1, "knowledge_points": value["knowledge_points"], "questions": qs}]
    store.save_lesson(value)
    paths = []
    for kind in ("worksheet", "answers"):
        response = exporter.export("lesson", ExportExercisesRequest(batch_id="batch", question_ids=["q2", "q1"], title="代数练习", kind=kind))
        paths.append(settings.outputs_dir / response["filename"])
    worksheet, answers = [Document(p) for p in paths]
    for document in (worksheet, answers):
        text = "\n".join(p.text for p in document.paragraphs)
        assert "第 1 题" in text and "第 2 题" in text and "第 3 题" not in text
        assert "不应导出的题目" not in text
        assert "求解" in text and "计算" in text
        assert document.sections[0].page_width.cm == pytest.approx(21, abs=.01)
    assert "参考答案" not in "\n".join(p.text for p in worksheet.paragraphs)
    assert "参考答案" in "\n".join(p.text for p in answers.paragraphs)
    for path in paths:
        with zipfile.ZipFile(path) as archive:
            xml = archive.read("word/document.xml").decode()
            assert "<m:oMath" in xml and "<m:f>" in xml and "<m:rad>" in xml
            assert "Noto Serif CJK SC" in archive.read("word/styles.xml").decode()


@pytest.mark.parametrize("formula", [r"$x_i^2$", r"$\int_0^1 x\,dx$", r"$$\begin{pmatrix}1&2\\3&4\end{pmatrix}$$", r"$$f(x)=\begin{cases}x&x>0\\0&x\leq0\end{cases}$$"])
def test_common_latex_exports_as_omml(monkeypatch, formula):
    require_pandoc(monkeypatch)
    assert exporter._content_document(formula).element.body.xpath('.//*[local-name()="oMath"]')


def test_export_rejects_unsupported_formula_and_external_images(monkeypatch):
    require_pandoc(monkeypatch)
    with pytest.raises(VideoProcessingError):
        exporter._content_document(r"$\notarealcommand{x}$")
    with pytest.raises(VideoProcessingError, match="图片"):
        exporter._content_document("![x](https://example.com/private.png)")


def test_new_upload_prepare_is_cached_and_releases_source_pin():
    video_id = "b" * 32
    directory = settings.uploads_dir / video_id
    directory.mkdir()
    source = directory / "source.mp4"
    source.write_bytes(b"fake video")
    (directory / "meta.json").write_text(json.dumps({"source_path": str(source), "filename": "lesson.mp4", "duration": 100, "source_type": "upload"}))
    entered, release = threading.Event(), threading.Event()
    def transcribe(*args):
        entered.set()
        assert release.wait(3)
        return [{"start": 0, "end": 10, "text": "课程原文"}]
    points = [{"id": "point", "title": "概念", "detail": "讲解", "formulas": "", "segment_ids": [0]}]
    with patch.object(service, "transcribe", side_effect=transcribe) as asr, patch.object(service, "extract_knowledge", return_value=points):
        task = service.prepare(video_id)
        assert entered.wait(1)
        try:
            assert service.prepare(video_id) == task
            assert video_id in storage._source_pins
        finally:
            release.set()
        assert wait_job(task)["status"] == "succeeded"
        assert service.prepare(video_id) == task
        assert asr.call_count == 1
    service.stop()
    assert video_id not in storage._source_pins
    assert store.lesson(task["lesson_id"])["segments"][0]["text"] == "课程原文"


def test_fill_blank_question_requires_blank_position():
    with pytest.raises(ValidationError, match="填空位置"):
        Question.model_validate(question(type="fill_blank", stem="解释这个定义"))
    assert Question.model_validate(question(type="fill_blank", stem="公式是（________）。"))
