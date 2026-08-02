from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import AppConfig
from app.main import create_app


def image_bytes(size: tuple[int, int] = (160, 100), color: tuple[int, int, int] = (60, 120, 180), fmt: str = "JPEG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, fmt)
    return buffer.getvalue()


def valid_settings(count: int, **updates: object) -> str:
    data: dict[str, object] = {
        "title": "Test memories",
        "subtitle": "",
        "duration": 1,
        "aspect_ratio": "16:9",
        "background": "blurred",
        "style": "smooth",
        "transition": "fade",
        "movement": "zoom-in",
        "font": "modern",
        "text_position": "bottom",
        "rotations": [0] * count,
        "captions": [""] * count,
    }
    data.update(updates)
    return json.dumps(data)


@pytest.fixture
def app_config(tmp_path: Path) -> AppConfig:
    return AppConfig(
        data_dir=tmp_path / "data", max_photo_bytes=2 * 1024 * 1024,
        max_total_bytes=5 * 1024 * 1024, ffmpeg_timeout_seconds=120,
        cleanup_interval_seconds=3600,
    )


@pytest.fixture
def application(app_config: AppConfig):
    return create_app(app_config, start_services=False)


@pytest.fixture
def client(application):
    with TestClient(application) as test_client:
        yield test_client

