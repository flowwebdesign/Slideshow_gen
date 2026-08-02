from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.main import recover_interrupted_jobs
from app.models import JobState
from app.security import safe_job_path
from tests.conftest import heif_bytes, image_bytes, valid_settings


def create(client: TestClient, count: int = 1, settings: str | None = None):
    files = [("files", (f"photo-{index}.jpg", image_bytes(color=(index * 30, 80, 130)), "image/jpeg")) for index in range(count)]
    return client.post("/api/jobs", files=files, data={"settings": settings or valid_settings(count)})


def test_main_page_loads_and_is_responsive(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Create slideshow" in response.text
    assert 'name="viewport"' in response.text
    assert "automatically deleted within one hour" in response.text
    assert "ROB's Slideshow Generator" in response.text
    assert "/static/rob-reader.png" in response.text
    assert 'href="/static/styles.css"' in response.text
    assert 'src="/static/app.js"' in response.text
    assert 'id="video-estimate"' in response.text
    assert 'id="video-quality"' in response.text
    assert '<option value="4k" selected>' in response.text
    assert "Create another slideshow" in response.text
    assert 'id="menu-toggle"' in response.text
    assert 'id="settings-link"' in response.text
    assert 'id="design-preview"' in response.text
    assert 'id="title-mode"' in response.text
    assert 'id="font"' in response.text
    assert 'class="render-splash"' in response.text
    assert '/static/rob-reader.png' in response.text


def test_font_assets_are_allow_listed(client: TestClient) -> None:
    font = client.get("/assets/fonts/modern")
    assert font.status_code == 200
    assert font.headers["content-type"].startswith("font/ttf")
    assert client.get("/assets/fonts/../../etc/passwd").status_code == 404
    assert client.get("/assets/fonts/not-a-font").status_code == 404


def test_upload_connection_check_returns_request_id(client: TestClient) -> None:
    response = client.post(
        "/api/upload-check", content=b"x" * (512 * 1024),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert response.status_code == 200
    assert response.json()["bytes_received"] == 512 * 1024
    assert response.json()["request_id"] == response.headers["X-Request-ID"]


def test_resilient_upload_session_accepts_files_separately(client: TestClient, application) -> None:
    started = client.post(
        "/api/uploads",
        json={"photo_count": 2, "settings": json.loads(valid_settings(2, title_mode="hidden"))},
    )
    assert started.status_code == 201, started.text
    upload = started.json()
    token_header = {"X-Job-Token": upload["access_token"]}
    for index in range(2):
        received = client.post(
            f"/api/uploads/{upload['job_id']}/files/{index}", headers=token_header,
            files={"file": (f"photo-{index}.jpg", image_bytes(), "image/jpeg")},
        )
        assert received.status_code == 200, received.text
        assert received.json()["status"] == "received"
        assert received.json()["request_id"] == received.headers["X-Request-ID"]
    retry = client.post(
        f"/api/uploads/{upload['job_id']}/files/1", headers=token_header,
        files={"file": ("photo-1.jpg", image_bytes(), "image/jpeg")},
    )
    assert retry.status_code == 200
    assert retry.json()["status"] == "already_received"
    completed = client.post(
        f"/api/uploads/{upload['job_id']}/complete", headers=token_header,
    )
    assert completed.status_code == 202
    assert application.state.repository.get(upload["job_id"]).state == JobState.QUEUED


@pytest.mark.parametrize(
    ("image_format", "filename", "content_type"),
    [("JPEG", "ordinary.jpg", "image/jpeg"), ("PNG", "photo.png", "image/png"),
     ("WEBP", "photo.webp", "image/webp")],
)
def test_staged_upload_normalises_existing_formats(
    client: TestClient, application, image_format: str, filename: str, content_type: str,
) -> None:
    started = client.post(
        "/api/uploads",
        json={"photo_count": 1, "settings": json.loads(valid_settings(1, title_mode="hidden"))},
    ).json()
    response = client.post(
        f"/api/uploads/{started['job_id']}/files/0",
        headers={"X-Job-Token": started["access_token"]},
        files={"file": (filename, image_bytes(fmt=image_format), content_type)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "received"
    assert response.json()["detected_format"] == image_format
    stored = application.state.config.jobs_dir / started["job_id"] / "source" / "000.upload"
    with Image.open(stored) as normalized:
        assert normalized.format == "JPEG"
        assert normalized.mode == "RGB"


@pytest.mark.parametrize(
    "content_type",
    [
        "image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence",
        "application/octet-stream", "",
    ],
)
def test_real_heif_content_succeeds_independent_of_client_mime(
    client: TestClient, application, content_type: str,
) -> None:
    started = client.post(
        "/api/uploads",
        json={"photo_count": 1, "settings": json.loads(valid_settings(1, title_mode="hidden"))},
    ).json()
    response = client.post(
        f"/api/uploads/{started['job_id']}/files/0",
        headers={"X-Job-Token": started["access_token"]},
        files={"file": ("fixture.heic", heif_bytes(), content_type)},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "received"
    assert response.json()["detected_format"] in {"HEIF", "HEIC"}
    stored = application.state.config.jobs_dir / started["job_id"] / "source" / "000.upload"
    with Image.open(stored) as normalized:
        assert normalized.format == "JPEG"
        assert normalized.size == (29, 100)


def test_heif_content_named_jpg_is_detected_from_bytes(client: TestClient) -> None:
    started = client.post(
        "/api/uploads",
        json={"photo_count": 1, "settings": json.loads(valid_settings(1, title_mode="hidden"))},
    ).json()
    response = client.post(
        f"/api/uploads/{started['job_id']}/files/0",
        headers={"X-Job-Token": started["access_token"]},
        files={"file": ("mislabelled.jpg", heif_bytes(), "image/jpeg")},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "received"
    assert response.json()["detected_format"] in {"HEIF", "HEIC"}


def test_resilient_upload_reports_empty_and_unauthorised_uploads(client: TestClient) -> None:
    started = client.post(
        "/api/uploads",
        json={"photo_count": 1, "settings": json.loads(valid_settings(1, title_mode="hidden"))},
    ).json()
    url = f"/api/uploads/{started['job_id']}"
    assert client.post(
        f"{url}/files/0", headers={"X-Job-Token": "wrong"},
        files={"file": ("photo.jpg", image_bytes(), "image/jpeg")},
    ).status_code == 403
    incomplete = client.post(
        f"{url}/complete", headers={"X-Job-Token": started["access_token"]},
    )
    assert incomplete.status_code == 409
    assert "None of the selected photos" in incomplete.json()["detail"]


def test_restart_requeues_interrupted_render_and_removes_partial_outputs(
    client: TestClient, application,
) -> None:
    started = client.post(
        "/api/uploads",
        json={"photo_count": 1, "settings": json.loads(valid_settings(1, title_mode="hidden"))},
    ).json()
    headers = {"X-Job-Token": started["access_token"]}
    received = client.post(
        f"/api/uploads/{started['job_id']}/files/0", headers=headers,
        files={"file": ("photo.jpg", image_bytes(), "image/jpeg")},
    )
    assert received.status_code == 200
    assert client.post(f"/api/uploads/{started['job_id']}/complete", headers=headers).status_code == 202
    application.state.repository.transition(started["job_id"], JobState.PREPARING, 35)
    job_dir = application.state.config.jobs_dir / started["job_id"]
    (job_dir / "prepared").mkdir()
    (job_dir / "render").mkdir()
    (job_dir / "output.mp4").write_bytes(b"partial")

    assert recover_interrupted_jobs(application.state.config, application.state.repository) == 1
    recovered = application.state.repository.get(started["job_id"])
    assert recovered.state == JobState.QUEUED
    assert recovered.progress == 5
    assert (job_dir / "source" / "000.upload").is_file()
    assert not (job_dir / "prepared").exists()
    assert not (job_dir / "render").exists()
    assert not (job_dir / "output.mp4").exists()


def test_resilient_upload_skips_one_bad_photo_and_compacts_settings(
    client: TestClient, application,
) -> None:
    raw_settings = json.loads(valid_settings(
        3, title="iPhone memories", title_mode="overlay", title_photo_index=1,
        duration=5, title_duration=2, rotations=[0, 90, 180],
        captions=["first", "unsupported", "third"],
    ))
    started = client.post(
        "/api/uploads", json={"photo_count": 3, "settings": raw_settings},
    )
    assert started.status_code == 201, started.text
    upload = started.json()
    headers = {"X-Job-Token": upload["access_token"]}
    first = client.post(
        f"/api/uploads/{upload['job_id']}/files/0", headers=headers,
        files={"file": ("first.jpg", image_bytes(), "image/jpeg")},
    )
    rejected = client.post(
        f"/api/uploads/{upload['job_id']}/files/1", headers=headers,
        files={"file": ("IMG_0005.heic", b"not a decodable photo", "image/heic")},
    )
    third = client.post(
        f"/api/uploads/{upload['job_id']}/files/2", headers=headers,
        files={"file": ("third.jpg", image_bytes(color=(30, 90, 160)), "image/jpeg")},
    )
    assert first.status_code == third.status_code == 200
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "skipped"
    assert rejected.json()["error"]["code"] == "invalid_image"
    assert rejected.json()["error"]["detected_format"] == "UNKNOWN"
    assert rejected.json()["filename"] == "IMG_0005.heic"
    assert "remaining photos will continue uploading" in rejected.json()["reason"]

    completed = client.post(
        f"/api/uploads/{upload['job_id']}/complete", headers=headers,
    )
    assert completed.status_code == 202, completed.text
    assert completed.json()["skipped_photos"] == 1
    assert completed.json()["accepted_photos"] == 2
    assert completed.json()["failed_photos"] == 1
    job = application.state.repository.get(upload["job_id"])
    assert job.photo_count == 2
    assert job.settings.rotations == [0, 180]
    assert job.settings.captions == ["first", "third"]
    assert job.settings.title_photo_index == 0
    source = application.state.config.jobs_dir / upload["job_id"] / "source"
    assert sorted(path.name for path in source.iterdir()) == ["000.upload", "001.upload"]


def test_batch_with_no_decodable_photos_fails_clearly_and_cleans_incoming_file(
    client: TestClient, application,
) -> None:
    started = client.post(
        "/api/uploads",
        json={"photo_count": 1, "settings": json.loads(valid_settings(1, title_mode="hidden"))},
    ).json()
    headers = {"X-Job-Token": started["access_token"]}
    rejected = client.post(
        f"/api/uploads/{started['job_id']}/files/0", headers=headers,
        files={"file": ("broken.heic", b"invalid", "application/octet-stream")},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "skipped"
    source = application.state.config.jobs_dir / started["job_id"] / "source"
    assert list(source.iterdir()) == []
    completed = client.post(f"/api/uploads/{started['job_id']}/complete", headers=headers)
    assert completed.status_code == 409
    assert completed.json()["detail"] == "None of the selected photos could be decoded"


def test_resilient_upload_supports_100_photos_without_one_large_request(client: TestClient, application) -> None:
    count = 100
    started = client.post(
        "/api/uploads",
        json={
            "photo_count": count,
            "settings": json.loads(valid_settings(count, title_mode="hidden", duration=1)),
        },
    )
    assert started.status_code == 201, started.text
    upload = started.json()
    headers = {"X-Job-Token": upload["access_token"]}
    tiny_photo = image_bytes((2, 2))
    for index in range(count):
        response = client.post(
            f"/api/uploads/{upload['job_id']}/files/{index}", headers=headers,
            files={"file": (f"photo-{index}.jpg", tiny_photo, "image/jpeg")},
        )
        assert response.status_code == 200, f"photo {index + 1}: {response.text}"
    completed = client.post(
        f"/api/uploads/{upload['job_id']}/complete", headers=headers,
    )
    assert completed.status_code == 202
    assert application.state.repository.get(upload["job_id"]).state == JobState.QUEUED


def test_health_endpoint(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("app.main.shutil.which", lambda _: "/usr/bin/ffmpeg")
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["checks"]["ffmpeg_available"] is True
    assert data["checks"]["storage_writable"] is True
    assert data["checks"]["disk_space_available"] is True


def test_health_endpoint_reports_missing_ffmpeg(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("app.main.shutil.which", lambda _: None)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["checks"]["ffmpeg_available"] is False


def test_health_endpoint_reports_missing_heif_decoder(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("app.main.heif_decoder_available", lambda: False)
    response = client.get("/health")
    assert response.status_code == 503
    assert response.json()["checks"]["heif_decoder_available"] is False


def test_stylesheet_contains_phone_layout(client: TestClient) -> None:
    response = client.get("/static/styles.css")
    assert response.status_code == 200
    stylesheet = response.text
    assert "@media (max-width: 640px)" in stylesheet
    assert ".field-grid.two { grid-template-columns: 1fr; }" in stylesheet
    assert ".hero-art" in stylesheet
    assert "max-width: 82%" in stylesheet


def test_job_creation_and_token_authorisation(client: TestClient) -> None:
    created = create(client, 3)
    assert created.status_code == 202
    data = created.json()
    assert data["state"] == "queued"
    client.cookies.clear()
    missing = client.get(f"/api/jobs/{data['job_id']}/status")
    assert missing.status_code == 401
    invalid = client.get(f"/api/jobs/{data['job_id']}/status", headers={"X-Job-Token": "wrong"})
    assert invalid.status_code == 403
    status = client.get(f"/api/jobs/{data['job_id']}/status", headers={"X-Job-Token": data["access_token"]})
    assert status.status_code == 200
    assert status.json()["state"] == "queued"


def test_invalid_settings_rejected(client: TestClient) -> None:
    invalid = create(client, settings=valid_settings(1, transition="command"))
    assert invalid.status_code == 422
    mismatch = create(client, settings=valid_settings(2))
    assert mismatch.status_code == 422


def test_more_than_configured_maximum_is_rejected(client: TestClient, application) -> None:
    application.state.config.max_photos = 2
    response = create(client, 3)
    assert response.status_code == 413


def test_more_than_100_files_is_rejected(client: TestClient) -> None:
    tiny = image_bytes((2, 2))
    files = [("files", (f"p-{index}.jpg", tiny, "image/jpeg")) for index in range(101)]
    response = client.post("/api/jobs", files=files, data={"settings": valid_settings(101)})
    assert response.status_code == 413


def test_oversized_upload_rejected(client: TestClient, application) -> None:
    application.state.config.max_photo_bytes = 100
    response = create(client)
    assert response.status_code == 413
    assert list(application.state.config.jobs_dir.iterdir()) == []


def test_total_upload_limit_is_enforced(client: TestClient, application) -> None:
    one = image_bytes((50, 50))
    application.state.config.max_photo_bytes = len(one) + 100
    application.state.config.max_total_bytes = len(one) + 100
    response = client.post(
        "/api/jobs",
        files=[("files", ("one.jpg", one, "image/jpeg")), ("files", ("two.jpg", one, "image/jpeg"))],
        data={"settings": valid_settings(2)},
    )
    assert response.status_code == 413
    assert list(application.state.config.jobs_dir.iterdir()) == []


def test_invalid_image_rejected_and_removed(client: TestClient, application) -> None:
    response = client.post(
        "/api/jobs", files=[("files", ("fake.jpg", b"not an image", "image/jpeg"))],
        data={"settings": valid_settings(1)},
    )
    assert response.status_code == 415
    assert list(application.state.config.jobs_dir.iterdir()) == []


def test_download_unavailable_before_ready(client: TestClient) -> None:
    data = create(client).json()
    response = client.get(
        f"/api/jobs/{data['job_id']}/download", headers={"X-Job-Token": data["access_token"]}
    )
    assert response.status_code == 409


def test_download_and_expired_state(client: TestClient, application) -> None:
    data = create(client).json()
    repository = application.state.repository
    repository.transition(data["job_id"], JobState.PREPARING, 20)
    repository.transition(data["job_id"], JobState.RENDERING, 50)
    repository.transition(data["job_id"], JobState.READY, 100)
    output = safe_job_path(application.state.config.jobs_dir, data["job_id"]) / "output.mp4"
    output.write_bytes(b"small-test-video")
    response = client.get(
        f"/api/jobs/{data['job_id']}/download", headers={"X-Job-Token": data["access_token"]}
    )
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert repository.get(data["job_id"]).state == JobState.DOWNLOADED
    repository.transition(data["job_id"], JobState.EXPIRED, 100)
    unavailable = client.get(
        f"/api/jobs/{data['job_id']}/status", headers={"X-Job-Token": data["access_token"]}
    )
    assert unavailable.status_code == 404
