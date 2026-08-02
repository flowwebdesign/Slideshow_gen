from __future__ import annotations

import shutil
import threading
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.config import AppConfig
from app.image_processing import create_title_card, create_title_overlay, prepare_photo
from app.jobs import JobRepository
from app.models import JobState, RESOLUTIONS, TitleMode
from app.renderer import build_ffmpeg_args, run_ffmpeg
from app.security import safe_job_path


logger = logging.getLogger("slideshow")


class RenderWorker:
    def __init__(self, app_config: AppConfig, repository: JobRepository):
        self.config = app_config
        self.repository = repository
        self.executor = ThreadPoolExecutor(
            max_workers=app_config.render_workers, thread_name_prefix="renderer"
        )
        self._submitted: set[str] = set()
        self._lock = threading.Lock()

    def submit(self, job_id: str) -> None:
        with self._lock:
            if job_id in self._submitted:
                return
            self._submitted.add(job_id)
        self.executor.submit(self._render_and_release, job_id)

    def _render_and_release(self, job_id: str) -> None:
        try:
            self.render(job_id)
        finally:
            with self._lock:
                self._submitted.discard(job_id)

    def _estimate_render_progress(self, job_id: str, stop: threading.Event) -> None:
        progress = 55
        while not stop.wait(2):
            current = self.repository.get(job_id)
            if current is None or current.state != JobState.RENDERING:
                return
            self.repository.set_progress(job_id, progress)
            progress = min(90, progress + 5)

    def render(self, job_id: str) -> None:
        job_dir = safe_job_path(self.config.jobs_dir, job_id)
        source_dir = job_dir / "source"
        prepared_dir = job_dir / "prepared"
        render_dir = job_dir / "render"
        started = time.monotonic()
        try:
            job = self.repository.get(job_id)
            if job is None or job.state != JobState.QUEUED:
                return
            logger.info("job_preparing job_id=%s photos=%s", job_id, job.photo_count)
            self.repository.transition(job_id, JobState.PREPARING, 15)
            frame_size = RESOLUTIONS[job.settings.aspect_ratio]
            prepared_paths: list[Path] = []
            has_title = bool(job.settings.title or job.settings.subtitle)
            title_card = has_title and job.settings.title_mode == TitleMode.CARD
            title_overlay_path: Path | None = None
            if title_card:
                title_path = prepared_dir / "title.png"
                create_title_card(title_path, frame_size, job.settings)
                prepared_paths.append(title_path)
            elif has_title and job.settings.title_mode == TitleMode.OVERLAY:
                title_overlay_path = prepared_dir / "title-overlay.png"
                create_title_overlay(title_overlay_path, frame_size, job.settings)
            for index in range(job.photo_count):
                source_path = source_dir / f"{index:03d}.upload"
                output_path = prepared_dir / f"{index:03d}.png"
                prepare_photo(
                    source_path, output_path, frame_size, job.settings.rotations[index],
                    job.settings.background, job.settings.captions[index], job.settings.font,
                    job.settings.text_position, caption_size=job.settings.caption_size,
                    text_colour=job.settings.text_color,
                    panel_opacity=job.settings.text_panel_opacity,
                    text_align=job.settings.text_align,
                )
                prepared_paths.append(output_path)
                self.repository.set_progress(job_id, 15 + round(30 * (index + 1) / job.photo_count))
            self.repository.transition(job_id, JobState.RENDERING, 50)
            logger.info("job_rendering job_id=%s inputs=%s", job_id, len(prepared_paths))
            render_dir.mkdir(parents=True, exist_ok=True)
            output_path = job_dir / "output.mp4"
            args = build_ffmpeg_args(
                self.config.ffmpeg_binary, prepared_paths, output_path, job.settings,
                frame_size, title_card=title_card, title_overlay_path=title_overlay_path,
            )
            progress_stop = threading.Event()
            progress_thread = threading.Thread(
                target=self._estimate_render_progress, args=(job_id, progress_stop),
                name=f"render-progress-{job_id}", daemon=True,
            )
            progress_thread.start()
            try:
                run_ffmpeg(args, self.config.ffmpeg_timeout_seconds)
            finally:
                progress_stop.set()
                progress_thread.join(timeout=3)
            if not output_path.is_file() or output_path.stat().st_size == 0:
                raise RuntimeError("video renderer did not create an output file")
            shutil.rmtree(source_dir, ignore_errors=True)
            shutil.rmtree(prepared_dir, ignore_errors=True)
            shutil.rmtree(render_dir, ignore_errors=True)
            self.repository.transition(job_id, JobState.READY, 100)
            logger.info(
                "job_ready job_id=%s elapsed_seconds=%.1f output_bytes=%s",
                job_id, time.monotonic() - started, output_path.stat().st_size,
            )
        except Exception as exc:
            shutil.rmtree(source_dir, ignore_errors=True)
            shutil.rmtree(prepared_dir, ignore_errors=True)
            shutil.rmtree(render_dir, ignore_errors=True)
            (job_dir / "output.mp4").unlink(missing_ok=True)
            current = self.repository.get(job_id)
            safe_error = str(exc).replace(str(job_dir), "<job>")[:900]
            if current and current.state in {JobState.QUEUED, JobState.PREPARING, JobState.RENDERING}:
                try:
                    self.repository.transition(job_id, JobState.FAILED, 100, safe_error)
                except (ValueError, RuntimeError, KeyError):
                    pass
            logger.error(
                "job_failed job_id=%s elapsed_seconds=%.1f error=%s",
                job_id, time.monotonic() - started, safe_error,
            )

    def shutdown(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=False)
