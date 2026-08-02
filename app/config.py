from __future__ import annotations

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SLIDESHOW_", env_file=".env", extra="ignore")

    data_dir: Path = Path("data")
    max_photos: int = Field(100, ge=1, le=100)
    max_photo_bytes: int = Field(20 * 1024 * 1024, ge=1024)
    max_total_bytes: int = Field(500 * 1024 * 1024, ge=1024)
    max_video_seconds: int = Field(20 * 60, ge=1)
    render_workers: int = Field(1, ge=1, le=4)
    ffmpeg_timeout_seconds: int = Field(1800, ge=5)
    cleanup_interval_seconds: int = Field(60, ge=1)
    ffmpeg_binary: str = "ffmpeg"

    @field_validator("data_dir")
    @classmethod
    def data_dir_must_not_be_root(cls, value: Path) -> Path:
        resolved = value.expanduser().resolve()
        if resolved == Path(resolved.anchor):
            raise ValueError("data_dir cannot be a filesystem root")
        return resolved

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "jobs.sqlite3"

    def ensure_directories(self) -> None:
        self.jobs_dir.mkdir(parents=True, exist_ok=True)


config = AppConfig()

