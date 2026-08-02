from __future__ import annotations

import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.config import AppConfig
from app.image_processing import create_title_card, prepare_photo
from app.jobs import JobRepository
from app.models import JobState, RESOLUTIONS
from app.renderer import build_ffmpeg_args, run_ffmpeg
from app.security import safe_job_path


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

    def render(self, job_id: str) -> None:
        job_dir = safe_job_path(self.config.jobs_dir, job_id)
        source_dir = job_dir / "source"
        prepared_dir = job_dir / "prepared"
        render_dir = job_dir / "render"
        try:
            job = self.repository.get(job_id)
            if job is None or job.state != JobState.QUEUED:
                return
            self.repository.transition(job_id, JobState.PREPARING, 15)
            frame_size = RESOLUTIONS[job.settings.aspect_ratio]
            prepared_paths: list[Path] = []
            has_title = bool(job.settings.title or job.settings.subtitle)
            if has_title:
                title_path = prepared_dir / "title.jpg"
                create_title_card(title_path, frame_size, job.settings)
                prepared_paths.append(title_path)
            for index in range(job.photo_count):
                source_path = source_dir / f"{index:03d}.upload"
                output_path = prepared_dir / f"{index:03d}.jpg"
                prepare_photo(
                    source_path, output_path, frame_size, job.settings.rotations[index],
                    job.settings.background, job.settings.captions[index], job.settings.font,
                    job.settings.text_position,
                )
                prepared_paths.append(output_path)
                self.repository.set_progress(job_id, 15 + round(30 * (index + 1) / job.photo_count))
            self.repository.transition(job_id, JobState.RENDERING, 50)
            render_dir.mkdir(parents=True, exist_ok=True)
            output_path = job_dir / "output.mp4"
            args = build_ffmpeg_args(
                self.config.ffmpeg_binary, prepared_paths, output_path, job.settings,
                frame_size, title_card=has_title,
            )
            run_ffmpeg(args, self.config.ffmpeg_timeout_seconds)
            if not output_path.is_file() or output_path.stat().st_size == 0:
                raise RuntimeError("video renderer did not create an output file")
            shutil.rmtree(source_dir, ignore_errors=True)
            shutil.rmtree(prepared_dir, ignore_errors=True)
            shutil.rmtree(render_dir, ignore_errors=True)
            self.repository.transition(job_id, JobState.READY, 100)
        except Exception as exc:
            shutil.rmtree(source_dir, ignore_errors=True)
            shutil.rmtree(prepared_dir, ignore_errors=True)
            shutil.rmtree(render_dir, ignore_errors=True)
            (job_dir / "output.mp4").unlink(missing_ok=True)
            current = self.repository.get(job_id)
            if current and current.state in {JobState.QUEUED, JobState.PREPARING, JobState.RENDERING}:
                try:
                    self.repository.transition(job_id, JobState.FAILED, 100, str(exc)[:900])
                except (ValueError, RuntimeError, KeyError):
                    pass

    def shutdown(self) -> None:
        self.executor.shutdown(wait=True, cancel_futures=False)
