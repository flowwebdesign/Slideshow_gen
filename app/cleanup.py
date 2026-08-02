from __future__ import annotations

import argparse
import shutil
import threading
from datetime import timedelta
from pathlib import Path

from app.config import AppConfig, config
from app.jobs import JobRepository
from app.models import JobRecord, JobState, now_utc
from app.security import safe_job_path


def remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def cleanup_decision(job: JobRecord) -> tuple[bool, bool]:
    """Return (expire files/job, delete metadata)."""
    now = now_utc()
    if now - job.created_at >= timedelta(hours=24):
        return True, True
    if job.state in {JobState.UPLOADING, JobState.QUEUED} and now - job.updated_at >= timedelta(minutes=15):
        return True, False
    if job.state in {JobState.PREPARING, JobState.RENDERING} and now - job.updated_at >= timedelta(minutes=30):
        return True, False
    if job.state == JobState.FAILED and now - job.updated_at >= timedelta(minutes=15):
        return True, False
    if job.state == JobState.DOWNLOADED and job.downloaded_at and now - job.downloaded_at >= timedelta(minutes=15):
        return True, False
    if job.state == JobState.READY and job.completed_at and now - job.completed_at >= timedelta(minutes=60):
        return True, False
    if job.state == JobState.EXPIRED:
        return True, False
    return False, False


class CleanupService:
    def __init__(self, app_config: AppConfig, repository: JobRepository):
        self.config = app_config
        self.repository = repository
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def run_once(self) -> dict[str, int]:
        expired = deleted = 0
        jobs = self.repository.list_states(set(JobState))
        for job in jobs:
            expire, delete_metadata = cleanup_decision(job)
            if not expire:
                continue
            remove_path(safe_job_path(self.config.jobs_dir, job.id))
            if delete_metadata:
                self.repository.delete(job.id)
                deleted += 1
            elif job.state != JobState.EXPIRED:
                try:
                    self.repository.transition(job.id, JobState.EXPIRED, 100)
                except (ValueError, RuntimeError, KeyError):
                    continue
                expired += 1
        return {"expired": expired, "metadata_deleted": deleted}

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="cleanup", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        self.run_once()
        while not self._stop.wait(self.config.cleanup_interval_seconds):
            self.run_once()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove expired slideshow job files")
    parser.add_argument("--once", action="store_true", help="run one cleanup pass")
    parser.parse_args()
    config.ensure_directories()
    repository = JobRepository(config.database_path)
    repository.initialise()
    result = CleanupService(config, repository).run_once()
    print(f"Expired jobs: {result['expired']}; metadata removed: {result['metadata_deleted']}")


if __name__ == "__main__":
    main()
