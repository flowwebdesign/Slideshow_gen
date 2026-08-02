from __future__ import annotations

import json
import shutil
import subprocess
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.cleanup import CleanupService
from app.jobs import JobRepository
from app.models import JobState, now_utc
from app.security import safe_job_path
from tests.conftest import image_bytes, valid_settings


pytestmark = pytest.mark.integration


def create_three(client: TestClient):
    files = [
        ("files", ("landscape.jpg", image_bytes((320, 180), (180, 65, 50)), "image/jpeg")),
        ("files", ("portrait.jpg", image_bytes((120, 240), (50, 150, 90)), "image/jpeg")),
        ("files", ("square.png", image_bytes((180, 180), (60, 85, 190), "PNG"), "image/png")),
    ]
    return client.post("/api/jobs", files=files, data={"settings": valid_settings(3, title="Integration test")})


def probe(path) -> dict[str, object]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "stream=codec_name,width,height,pix_fmt:format=duration", "-of", "json", str(path),
        ],
        check=True, shell=False, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


def test_real_render_http_download_and_expiry(client: TestClient, application) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe are required")
    response = create_three(client)
    assert response.status_code == 202, response.text
    data = response.json()
    job_id, token = data["job_id"], data["access_token"]
    application.state.worker.render(job_id)
    job_dir = safe_job_path(application.state.config.jobs_dir, job_id)
    output = job_dir / "output.mp4"
    job = application.state.repository.get(job_id)
    assert job.state == JobState.READY, job.error
    assert output.is_file() and output.stat().st_size > 10_000
    assert not (job_dir / "source").exists()
    assert not (job_dir / "prepared").exists()
    assert not (job_dir / "render").exists()

    metadata = probe(output)
    video = metadata["streams"][0]
    assert video["codec_name"] == "h264"
    assert (video["width"], video["height"]) == (1920, 1080)
    assert video["pix_fmt"] == "yuv420p"
    assert 3.0 <= float(metadata["format"]["duration"]) <= 5.0

    preview = client.get(f"/api/jobs/{job_id}/preview", headers={"X-Job-Token": token})
    assert preview.status_code == 200
    assert preview.headers["content-type"] == "video/mp4"
    assert len(preview.content) == output.stat().st_size
    downloaded = client.get(f"/api/jobs/{job_id}/download", headers={"X-Job-Token": token})
    assert downloaded.status_code == 200
    assert downloaded.content[:8].endswith(b"ftyp") or b"ftyp" in downloaded.content[:32]
    assert application.state.repository.get(job_id).state == JobState.DOWNLOADED

    application.state.repository.set_times_for_test(
        job_id, downloaded_at=now_utc() - timedelta(minutes=16)
    )
    restarted_repository = JobRepository(application.state.config.database_path)
    restarted_repository.initialise()
    result = CleanupService(application.state.config, restarted_repository).run_once()
    assert result["expired"] == 1
    assert not output.exists()
    assert restarted_repository.get(job_id).state == JobState.EXPIRED


def test_failed_ffmpeg_marks_failed_and_removes_sources(
    client: TestClient, application, monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = create_three(client)
    data = response.json()

    def fail(*_args, **_kwargs):
        raise RuntimeError("simulated renderer failure")

    monkeypatch.setattr("app.worker.run_ffmpeg", fail)
    application.state.worker.render(data["job_id"])
    job = application.state.repository.get(data["job_id"])
    job_dir = safe_job_path(application.state.config.jobs_dir, data["job_id"])
    assert job.state == JobState.FAILED
    assert "simulated renderer failure" in job.error
    assert not (job_dir / "source").exists()
    assert not (job_dir / "prepared").exists()
    assert not (job_dir / "output.mp4").exists()


def test_abandoned_job_cleanup_survives_repository_restart(client: TestClient, application) -> None:
    response = create_three(client)
    job_id = response.json()["job_id"]
    application.state.repository.set_times_for_test(
        job_id, updated_at=now_utc() - timedelta(minutes=16)
    )
    restarted_repository = JobRepository(application.state.config.database_path)
    restarted_repository.initialise()
    result = CleanupService(application.state.config, restarted_repository).run_once()
    assert result["expired"] == 1
    assert restarted_repository.get(job_id).state == JobState.EXPIRED
    assert not safe_job_path(application.state.config.jobs_dir, job_id).exists()


def test_heif_decoder_when_encoder_is_available(tmp_path) -> None:
    pillow_heif = pytest.importorskip("pillow_heif")
    path = tmp_path / "generated.heic"
    try:
        pillow_heif.from_pillow(Image.new("RGB", (24, 18), "purple")).save(path)
    except Exception as exc:
        pytest.skip(f"runtime has no HEIF encoder: {exc}")
    from app.image_processing import decoded_format

    assert decoded_format(path) in {"HEIF", "HEIC"}
