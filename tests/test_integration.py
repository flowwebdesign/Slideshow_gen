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
from tests.conftest import heif_bytes, image_bytes, valid_settings


pytestmark = pytest.mark.integration


def create_three(client: TestClient):
    files = [
        ("files", ("landscape.jpg", image_bytes((320, 180), (180, 65, 50)), "image/jpeg")),
        ("files", ("portrait.jpg", image_bytes((120, 240), (50, 150, 90)), "image/jpeg")),
        ("files", ("square.png", image_bytes((180, 180), (60, 85, 190), "PNG"), "image/png")),
    ]
    return client.post(
        "/api/jobs", files=files,
        data={"settings": valid_settings(
            3, title="Integration test", subtitle="Timed overlay", style="custom",
            title_mode="overlay", title_photo_index=1, title_start=0.25,
            title_duration=0.5, text_x=0.25, text_y=0.2, text_color="#ffd166",
            title_size=1.6, subtitle_size=0.8, text_panel_opacity=120,
            text_align="left", text_animation="fade",
        )},
    )


def probe(path) -> dict[str, object]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "stream=codec_name,width,height,pix_fmt,profile,color_space,color_primaries,color_transfer,color_range:format=duration", "-of", "json", str(path),
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
    assert video["profile"] == "High"
    assert video["color_space"] == "bt709"
    assert video["color_primaries"] == "bt709"
    assert video["color_transfer"] == "bt709"
    assert 1.8 <= float(metadata["format"]["duration"]) <= 2.2

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


def test_real_4k_render_uses_ultra_hd_frame(client: TestClient, application) -> None:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg and ffprobe are required")
    response = client.post(
        "/api/jobs",
        files=[("files", ("photo.jpg", image_bytes((640, 480)), "image/jpeg"))],
        data={"settings": valid_settings(
            1, title="", title_mode="hidden", duration=1, video_quality="4k",
            style="custom", transition="none", movement="static",
        )},
    )
    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    application.state.worker.render(job_id)
    output = safe_job_path(application.state.config.jobs_dir, job_id) / "output.mp4"
    job = application.state.repository.get(job_id)
    assert job.state == JobState.READY, job.error
    metadata = probe(output)
    video = metadata["streams"][0]
    assert video["codec_name"] == "h264"
    assert (video["width"], video["height"]) == (3840, 2160)
    assert video["pix_fmt"] == "yuv420p"


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


def test_real_render_continues_after_one_undecodable_photo(
    client: TestClient, application,
) -> None:
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required")
    started = client.post(
        "/api/uploads",
        json={
            "photo_count": 4,
            "settings": json.loads(valid_settings(
                4, title="", title_mode="hidden", duration=1,
                style="custom", transition="none",
            )),
        },
    )
    assert started.status_code == 201, started.text
    upload = started.json()
    headers = {"X-Job-Token": upload["access_token"]}
    files = [
        ("first.jpg", image_bytes((160, 100), (180, 60, 40)), "image/jpeg"),
        ("fixture.heic", heif_bytes(), "image/heic"),
        ("broken.heic", b"not a decodable image", "application/octet-stream"),
        ("after-corrupt.jpg", image_bytes((100, 160), (40, 130, 180)), "image/jpeg"),
    ]
    responses = [
        client.post(
            f"/api/uploads/{upload['job_id']}/files/{index}", headers=headers,
            files={"file": (filename, content, content_type)},
        )
        for index, (filename, content, content_type) in enumerate(files)
    ]
    assert [response.status_code for response in responses] == [200, 200, 200, 200]
    assert responses[0].json()["status"] == "received"
    assert responses[1].json()["detected_format"] in {"HEIF", "HEIC"}
    assert responses[2].json()["status"] == "skipped"
    assert responses[3].json()["status"] == "received"
    completed = client.post(
        f"/api/uploads/{upload['job_id']}/complete", headers=headers,
    )
    assert completed.status_code == 202
    assert completed.json()["skipped_photos"] == 1
    assert completed.json()["accepted_photos"] == 3
    assert completed.json()["failed_photos"] == 1

    application.state.worker.render(upload["job_id"])
    job = application.state.repository.get(upload["job_id"])
    output = safe_job_path(application.state.config.jobs_dir, upload["job_id"]) / "output.mp4"
    assert job.state == JobState.READY, job.error
    assert job.photo_count == 3
    assert output.is_file() and output.stat().st_size > 10_000


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
