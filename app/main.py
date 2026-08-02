from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import time
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
from app.image_processing import (
    FONT_MAP, ImageNormalizationError, heif_decoder_available, normalize_image,
)
from app.jobs import JobRepository
from app.models import FontPreset, JobRecord, JobState, SlideshowSettings
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


def safe_client_filename(filename: str | None, index: int) -> str:
    basename = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = "".join(character for character in basename if character.isprintable()).strip()
    return cleaned[:120] or f"photo-{index + 1}"


def safe_log_value(value: str | None, fallback: str = "none") -> str:
    cleaned = "".join(
        character for character in (value or "")
        if character.isascii() and (character.isalnum() or character in " .+-_/;:,()")
    )
    return cleaned[:120] or fallback


def file_signature(path: Path, length: int = 24) -> str:
    try:
        with path.open("rb") as stream:
            return stream.read(length).hex() or "empty"
    except OSError:
        return "unavailable"


def recover_interrupted_jobs(app_config: AppConfig, repository: JobRepository) -> int:
    recovered = 0
    interrupted_jobs = repository.list_states({JobState.PREPARING, JobState.RENDERING})
    for job in interrupted_jobs:
        job_dir = safe_job_path(app_config.jobs_dir, job.id)
        source_dir = job_dir / "source"
        source_count = sum(
            (source_dir / f"{index:03d}.upload").is_file()
            for index in range(job.photo_count)
        )
        if source_count == job.photo_count:
            shutil.rmtree(job_dir / "prepared", ignore_errors=True)
            shutil.rmtree(job_dir / "render", ignore_errors=True)
            (job_dir / "output.mp4").unlink(missing_ok=True)
            repository.requeue_interrupted(job.id)
            recovered += 1
            logger.warning("job_requeued_after_restart job_id=%s", job.id)
            continue
        repository.transition(
            job.id, JobState.FAILED, 100,
            "Rendering was interrupted and the source photos are no longer available. Please create the slideshow again.",
        )
        logger.error("job_failed_after_restart job_id=%s missing_sources=%s", job.id, job.photo_count - source_count)
    return recovered


def health_report(app_config: AppConfig) -> tuple[bool, dict[str, bool | int]]:
    try:
        free_bytes = shutil.disk_usage(app_config.data_dir).free
    except OSError:
        free_bytes = 0
    checks: dict[str, bool | int] = {
        "ffmpeg_available": shutil.which(app_config.ffmpeg_binary) is not None,
        "heif_decoder_available": heif_decoder_available(),
        "storage_writable": app_config.data_dir.is_dir() and os.access(app_config.data_dir, os.W_OK),
        "free_disk_bytes": free_bytes,
        "disk_space_available": free_bytes >= app_config.min_free_bytes,
    }
    healthy = bool(
        checks["ffmpeg_available"]
        and checks["heif_decoder_available"]
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
            recover_interrupted_jobs(settings_config, repository)
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

    @application.middleware("http")
    async def request_id_header(request: Request, call_next):
        request_id = secrets.token_hex(6)
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        if response.status_code >= 500:
            logger.error(
                "request_failed request_id=%s method=%s path=%s status=%s",
                request_id, request.method, request.url.path, response.status_code,
            )
        return response

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

    @application.get("/assets/fonts/{font_id}")
    async def font_asset(font_id: str) -> FileResponse:
        try:
            preset = FontPreset(font_id)
        except ValueError as exc:
            raise HTTPException(404, "Font not found") from exc
        font_path = Path(FONT_MAP[preset][0])
        if not font_path.is_file():
            raise HTTPException(404, "Font not available")
        return FileResponse(
            font_path, media_type="font/ttf", headers={"Cache-Control": "public, max-age=86400"},
        )

    @application.post("/api/upload-check")
    async def upload_check(request: Request) -> dict[str, object]:
        maximum = 1024 * 1024
        declared = request.headers.get("content-length")
        if declared and int(declared) > maximum:
            raise HTTPException(413, "Connection-check payload is too large")
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > maximum:
                raise HTTPException(413, "Connection-check payload is too large")
        logger.info(
            "upload_check_ok request_id=%s bytes=%s",
            request.state.request_id, received,
        )
        return {"status": "ok", "bytes_received": received, "request_id": request.state.request_id}

    @application.post("/api/uploads", status_code=201)
    async def start_upload(payload: dict[str, object], request: Request) -> JSONResponse:
        photo_count = payload.get("photo_count")
        raw_settings = payload.get("settings")
        if not isinstance(photo_count, int) or isinstance(photo_count, bool):
            raise HTTPException(422, "photo_count must be an integer")
        if photo_count < 1 or photo_count > settings_config.max_photos:
            raise HTTPException(413, f"A maximum of {settings_config.max_photos} photos is allowed")
        try:
            parsed = SlideshowSettings.model_validate(raw_settings)
            parsed.validate_for_count(photo_count, settings_config.max_video_seconds)
        except (ValidationError, ValueError) as exc:
            raise HTTPException(422, f"Invalid slideshow settings: {exc}") from exc
        job_id = generate_job_id()
        token = generate_access_token()
        job_dir = safe_job_path(settings_config.jobs_dir, job_id)
        try:
            (job_dir / "source").mkdir(parents=True, exist_ok=False)
            repository.create(
                job_id, token_digest(token), parsed, photo_count,
                initial_state=JobState.UPLOADING,
            )
        except Exception:
            shutil.rmtree(job_dir, ignore_errors=True)
            raise
        logger.info(
            "upload_started job_id=%s request_id=%s photos=%s",
            job_id, request.state.request_id, photo_count,
        )
        return JSONResponse(
            status_code=201,
            content={"job_id": job_id, "access_token": token, "state": JobState.UPLOADING},
        )

    @application.post("/api/uploads/{job_id}/files/{index}")
    async def upload_job_file(
        job_id: str, index: int, request: Request,
        file: Annotated[UploadFile, File()],
        x_job_token: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        started = time.monotonic()
        job = authorised_job(job_id, x_job_token, None)
        if job.state != JobState.UPLOADING:
            raise HTTPException(409, "This upload is no longer accepting photos")
        if index < 0 or index >= job.photo_count:
            raise HTTPException(422, "Photo number is outside this upload")
        source_dir = safe_job_path(settings_config.jobs_dir, job_id) / "source"
        target = source_dir / f"{index:03d}.upload"
        incoming = source_dir / f".{index:03d}.incoming"
        display_name = safe_client_filename(file.filename, index)
        extension = safe_log_value(Path(display_name).suffix.lower(), "none")
        content_type = safe_log_value(file.content_type, "blank")
        if target.is_file():
            await file.close()
            return JSONResponse(content={
                "status": "already_received", "index": index,
                "bytes": target.stat().st_size, "request_id": request.state.request_id,
            })
        existing_total = sum(path.stat().st_size for path in source_dir.glob("*.upload"))
        received = 0
        incoming.unlink(missing_ok=True)
        try:
            with incoming.open("xb") as destination:
                while chunk := await file.read(1024 * 1024):
                    received += len(chunk)
                    if received > settings_config.max_photo_bytes:
                        raise HTTPException(413, "This photo is larger than 20 MB")
                    if existing_total + received > settings_config.max_total_bytes:
                        raise HTTPException(413, "The selected photos are larger than 500 MB in total")
                    destination.write(chunk)
            normalized = normalize_image(
                incoming, target, max_pixels=settings_config.max_decoded_pixels,
            )
        except HTTPException:
            raise
        except ImageNormalizationError as exc:
            reason = (
                f"Photo {index + 1} ({display_name}) could not be read and was skipped. "
                "The remaining photos will continue uploading."
            )
            logger.warning(
                "upload_file_rejected job_id=%s request_id=%s photo=%s/%s bytes=%s "
                "extension=%s content_type=%s signature=%s detected_format=%s code=%s detail=%s",
                job_id, request.state.request_id, index + 1, job.photo_count, received,
                extension, content_type, file_signature(incoming),
                safe_log_value(exc.detected_format, "unknown"), exc.code,
                safe_log_value(str(exc)),
            )
            return JSONResponse(
                status_code=200,
                content={
                    "status": "skipped", "index": index, "reason": reason,
                    "filename": display_name,
                    "error": {
                        "code": exc.code, "detail": str(exc),
                        "detected_format": exc.detected_format,
                    },
                    "request_id": request.state.request_id,
                },
            )
        finally:
            incoming.unlink(missing_ok=True)
            await file.close()
        repository.set_progress(job_id, min(4, round((index + 1) / job.photo_count * 4)))
        logger.info(
            "upload_file_received job_id=%s request_id=%s photo=%s/%s bytes=%s "
            "extension=%s content_type=%s detected_format=%s normalized_bytes=%s "
            "dimensions=%sx%s frames=%s elapsed_ms=%s",
            job_id, request.state.request_id, index + 1, job.photo_count, received,
            extension, content_type, normalized.source_format, target.stat().st_size,
            normalized.width, normalized.height, normalized.frame_count,
            round((time.monotonic() - started) * 1000),
        )
        return JSONResponse(content={
            "status": "received", "index": index, "bytes": received,
            "detected_format": normalized.source_format,
            "request_id": request.state.request_id,
        })

    @application.post("/api/uploads/{job_id}/complete")
    async def complete_upload(
        job_id: str, request: Request,
        x_job_token: Annotated[str | None, Header()] = None,
    ) -> JSONResponse:
        job = authorised_job(job_id, x_job_token, None)
        if job.state != JobState.UPLOADING:
            raise HTTPException(409, "This upload has already been completed")
        source_dir = safe_job_path(settings_config.jobs_dir, job_id) / "source"
        received_indices = [
            index for index in range(job.photo_count)
            if (source_dir / f"{index:03d}.upload").is_file()
        ]
        if not received_indices:
            raise HTTPException(409, "None of the selected photos could be decoded")
        skipped_photos = job.photo_count - len(received_indices)
        if skipped_photos:
            temporary_paths: list[Path] = []
            for compact_index, original_index in enumerate(received_indices):
                temporary = source_dir / f".{compact_index:03d}.compact"
                os.replace(source_dir / f"{original_index:03d}.upload", temporary)
                temporary_paths.append(temporary)
            for compact_index, temporary in enumerate(temporary_paths):
                os.replace(temporary, source_dir / f"{compact_index:03d}.upload")
            index_map = {original: compact for compact, original in enumerate(received_indices)}
            title_original = job.settings.title_photo_index
            replacement_original = min(
                received_indices, key=lambda original: abs(original - title_original),
            )
            compact_settings = job.settings.model_copy(update={
                "rotations": [job.settings.rotations[index] for index in received_indices],
                "captions": [job.settings.captions[index] for index in received_indices],
                "title_photo_index": index_map[replacement_original],
            })
            compact_settings.validate_for_count(len(received_indices), settings_config.max_video_seconds)
            job = repository.update_upload_details(job_id, compact_settings, len(received_indices))
        repository.transition(job_id, JobState.QUEUED, 5)
        if start_services:
            worker.submit(job_id)
        logger.info(
            "upload_completed job_id=%s request_id=%s photos=%s skipped=%s",
            job_id, request.state.request_id, job.photo_count, skipped_photos,
        )
        response = JSONResponse(
            status_code=202,
            content={
                "job_id": job_id, "access_token": x_job_token,
                "state": JobState.QUEUED, "skipped_photos": skipped_photos,
                "accepted_photos": job.photo_count, "failed_photos": skipped_photos,
            },
        )
        response.set_cookie(
            key=f"slideshow_{job_id}", value=x_job_token or "", httponly=True,
            samesite="strict", secure=False, max_age=24 * 60 * 60,
            path=f"/api/jobs/{job_id}",
        )
        return response

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
                target = source_dir / f"{index:03d}.upload"
                incoming = source_dir / f".{index:03d}.incoming"
                file_bytes = 0
                with incoming.open("xb") as destination:
                    while chunk := await upload.read(1024 * 1024):
                        file_bytes += len(chunk)
                        total_bytes += len(chunk)
                        if file_bytes > settings_config.max_photo_bytes:
                            raise HTTPException(413, "One of the photos is larger than 20 MB")
                        if total_bytes > settings_config.max_total_bytes:
                            raise HTTPException(413, "The total upload is larger than 500 MB")
                        destination.write(chunk)
                try:
                    normalize_image(
                        incoming, target, max_pixels=settings_config.max_decoded_pixels,
                    )
                finally:
                    incoming.unlink(missing_ok=True)
            repository.create(job_id, token_digest(token), parsed, len(files))
        except HTTPException as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            logger.warning("job_upload_rejected job_id=%s status=%s", job_id, exc.status_code)
            raise
        except ImageNormalizationError as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            logger.warning("job_upload_rejected job_id=%s status=415", job_id)
            raise HTTPException(415, str(exc)) from exc
        except Exception as exc:
            shutil.rmtree(job_dir, ignore_errors=True)
            logger.exception("job_upload_failed job_id=%s", job_id)
            raise HTTPException(500, "The photos could not be saved") from exc
        finally:
            for upload in files:
                await upload.close()

        logger.info(
            "job_queued job_id=%s photos=%s aspect=%s quality=%s style=%s duration=%s",
            job_id, len(files), parsed.aspect_ratio, parsed.video_quality,
            parsed.style, parsed.duration,
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
        return {
            "job_id": job.id, "state": job.state, "progress": job.progress,
            "error": job.error, "updated_at": job.updated_at.isoformat(),
        }

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
