from __future__ import annotations

import json
import logging
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.cleanup import CleanupService
from app.config import AppConfig, config
from app.image_processing import decoded_format, has_allowed_extension
from app.jobs import JobRepository
from app.models import JobRecord, JobState, SlideshowSettings
from app.security import generate_access_token, generate_job_id, safe_job_path, token_digest, token_matches
from app.worker import RenderWorker


BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger("slideshow")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
logger.propagate = False


def health_report(app_config: AppConfig) -> tuple[bool, dict[str, bool | int]]:
    try:
        free_bytes = shutil.disk_usage(app_config.data_dir).free
    except OSError:
        free_bytes = 0
    checks: dict[str, bool | int] = {
        "ffmpeg_available": shutil.which(app_config.ffmpeg_binary) is not None,
        "storage_writable": app_config.data_dir.is_dir() and os.access(app_config.data_dir, os.W_OK),
        "free_disk_bytes": free_bytes,
        "disk_space_available": free_bytes >= app_config.min_free_bytes,
    }
    healthy = bool(
        checks["ffmpeg_available"]
        and checks["storage_writable"]
        and checks["disk_space_available"]
    )
    return healthy, checks


def create_app(app_config: AppConfig | None = None, *, start_services: bool = True) -> FastAPI:
    settings_config = app_config or config
    settings_config.ensure_directories()
    repository = JobRepository(settings_config.database_path)
    repository.initialise()
    worker = RenderWorker(settings_config, repository)
    cleanup = CleanupService(settings_config, repository)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if start_services:
            cleanup.run_once()
            cleanup.start()
            for queued in repository.list_states({JobState.QUEUED}):
                worker.submit(queued.id)
            logger.info("application_ready queued_jobs_resubmitted")
        yield
        cleanup.stop()
        worker.shutdown()

    application = FastAPI(title="Photo Slideshow", lifespan=lifespan)
    application.state.config = settings_config
    application.state.repository = repository
    application.state.worker = worker
    application.state.cleanup = cleanup
    application.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")

    @application.exception_handler(RequestValidationError)
    async def request_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"error": "Invalid request", "details": exc.errors()})

    @application.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request=request, name="index.html")

    @application.get("/health")
    async def health() -> JSONResponse:
        healthy, checks = health_report(settings_config)
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={"status": "ok" if healthy else "degraded", "checks": checks},
        )

    @application.post("/api/jobs", status_code=202)
    async def create_job(
        request: Request,
        files: Annotated[list[UploadFile], File()],
        settings: Annotated[str, Form()],
    ) -> JSONResponse:
        if not files:
            raise HTTPException(400, "Choose at least one photo")
        if len(files) > settings_config.max_photos:
            raise HTTPException(413, f"A maximum of {settings_config.max_photos} photos is allowed")
        try:
            parsed = SlideshowSettings.model_validate_json(settings)
            parsed.validate_for_count(len(files), settings_config.max_video_seconds)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(422, f"Invalid slideshow settings: {exc}") from exc

        job_id = generate_job_id()
        token = generate_access_token()
        job_dir = safe_job_path(settings_config.jobs_dir, job_id)
        source_dir = job_dir / "source"
        source_dir.mkdir(parents=True, exist_ok=False)
        total_bytes = 0
        try:
            for index, upload in enumerate(files):
                if not has_allowed_extension(upload.filename):
                    raise HTTPException(415, "Only JPEG, PNG, WebP, HEIC, and HEIF photos are accepted")
                target = source_dir / f"{index:03d}.upload"
                file_bytes = 0
                with target.open("xb") as destination:
                    while chunk := await upload.read(1024 * 1024):
                        file_bytes += len(chunk)
                        total_bytes += len(chunk)
                        if file_bytes > settings_config.max_photo_bytes:
                            raise HTTPException(413, "One of the photos is larger than 20 MB")
                        if total_bytes > settings_config.max_total_bytes:
                            raise HTTPException(413, "The total upload is larger than 500 MB")
                        destination.write(chunk)
                decoded_format(target)
                await upload.close()
            repository.create(job_id, token_digest(token), parsed, len(files))
        except HTTPException as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            logger.warning("job_upload_rejected job_id=%s status=%s", job_id, exc.status_code)
            raise
        except ValueError as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            logger.warning("job_upload_rejected job_id=%s status=415", job_id)
            raise HTTPException(415, str(exc)) from exc
        except Exception as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            logger.exception("job_upload_failed job_id=%s", job_id)
            raise HTTPException(500, "The photos could not be saved") from exc

        logger.info(
            "job_queued job_id=%s photos=%s aspect=%s style=%s duration=%s",
            job_id, len(files), parsed.aspect_ratio, parsed.style, parsed.duration,
        )
        if start_services:
            worker.submit(job_id)
        response = JSONResponse(
            status_code=202,
            content={"job_id": job_id, "access_token": token, "state": JobState.QUEUED},
        )
        response.set_cookie(
            key=f"slideshow_{job_id}", value=token, httponly=True, samesite="strict",
            secure=False, max_age=24 * 60 * 60, path=f"/api/jobs/{job_id}",
        )
        return response

    def authorised_job(
        job_id: str, x_job_token: str | None, cookie_token: str | None,
    ) -> JobRecord:
        try:
            safe_job_path(settings_config.jobs_dir, job_id)
        except ValueError as exc:
            raise HTTPException(404, "Job not found") from exc
        job = repository.get(job_id)
        if job is None or job.state == JobState.EXPIRED:
            raise HTTPException(404, "Job not found or expired")
        supplied = x_job_token or cookie_token
        if not supplied:
            raise HTTPException(401, "Job access token is required")
        if not token_matches(supplied, job.token_hash):
            raise HTTPException(403, "Job access token is invalid")
        return job

    @application.get("/api/jobs/{job_id}/status")
    async def job_status(
        job_id: str,
        request: Request,
        x_job_token: Annotated[str | None, Header()] = None,
    ) -> dict[str, object]:
        cookie_token = request.cookies.get(f"slideshow_{job_id}")
        job = authorised_job(job_id, x_job_token, cookie_token)
        return {"job_id": job.id, "state": job.state, "progress": job.progress, "error": job.error}

    @application.get("/api/jobs/{job_id}/preview")
    async def preview(
        job_id: str, request: Request,
        x_job_token: Annotated[str | None, Header()] = None,
    ) -> FileResponse:
        job = authorised_job(job_id, x_job_token, request.cookies.get(f"slideshow_{job_id}"))
        if job.state not in {JobState.READY, JobState.DOWNLOADED}:
            raise HTTPException(409, "Video is not ready")
        output = safe_job_path(settings_config.jobs_dir, job_id) / "output.mp4"
        if not output.is_file():
            raise HTTPException(404, "Video has expired")
        return FileResponse(output, media_type="video/mp4", headers={"Cache-Control": "private, no-store"})

    @application.get("/api/jobs/{job_id}/download")
    async def download(
        job_id: str, request: Request,
        x_job_token: Annotated[str | None, Header()] = None,
    ) -> FileResponse:
        job = authorised_job(job_id, x_job_token, request.cookies.get(f"slideshow_{job_id}"))
        if job.state not in {JobState.READY, JobState.DOWNLOADED}:
            raise HTTPException(409, "Video is not ready")
        output = safe_job_path(settings_config.jobs_dir, job_id) / "output.mp4"
        if not output.is_file():
            raise HTTPException(404, "Video has expired")
        if job.state == JobState.READY:
            repository.transition(job_id, JobState.DOWNLOADED, 100)
        return FileResponse(
            output, media_type="video/mp4", filename="my-slideshow.mp4",
            headers={"Cache-Control": "private, no-store"},
        )

    return application


app = create_app()
