from fastapi import APIRouter, HTTPException, Query
from .exercise_models import PrepareRequest, LessonPatch, GenerateRequest, ExportExercisesRequest, SaveExercisePageRequest
from . import exercise_service as service, exercise_store as store, exercise_export
from .ffmpeg_tools import VideoProcessingError
from .models import ExportResponse
from . import asr_service

router = APIRouter(prefix="/api/exercises", tags=["exercises"])


def call(operation, *args):
    try:
        return operation(*args)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except VideoProcessingError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/prepare")
def prepare(request: PrepareRequest):
    return call(service.prepare, request.video_id)


@router.get("/asr/options")
def asr_options():
    return asr_service.options()


@router.get("/jobs/{job_id}")
def job(job_id: str):
    return call(store.job, job_id)


@router.get("/pages")
def recent_pages(limit: int = Query(12, ge=1, le=100), offset: int = Query(0, ge=0)):
    return call(store.recent_pages, limit, offset)


@router.get("/pages/{slug}")
def page(slug: str):
    return call(store.page, slug)


@router.get("/{lesson_id}")
def lesson(lesson_id: str):
    return call(store.lesson, lesson_id)


@router.patch("/{lesson_id}")
def patch(lesson_id: str, request: LessonPatch):
    return call(service.patch_lesson, lesson_id, request)


@router.post("/{lesson_id}/generate")
def generate(lesson_id: str, request: GenerateRequest):
    return call(service.generate, lesson_id, request)


@router.post("/{lesson_id}/export", response_model=ExportResponse)
def export(lesson_id: str, request: ExportExercisesRequest):
    return call(exercise_export.export, lesson_id, request)


@router.post("/{lesson_id}/pages")
def save_page(lesson_id: str, request: SaveExercisePageRequest):
    return call(store.create_page, lesson_id, request)
