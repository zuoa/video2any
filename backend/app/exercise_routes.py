from fastapi import APIRouter, HTTPException
from .exercise_models import PrepareRequest, LessonPatch, GenerateRequest, ExportExercisesRequest
from . import exercise_service as service, exercise_store as store, exercise_export
from .ffmpeg_tools import VideoProcessingError
from .models import ExportResponse

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


@router.get("/jobs/{job_id}")
def job(job_id: str):
    return call(store.job, job_id)


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
