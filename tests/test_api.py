from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.models import JobState
from app.security import safe_job_path
from tests.conftest import image_bytes, valid_settings


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
    assert "Create another slideshow" in response.text
    assert 'id="menu-toggle"' in response.text
    assert 'id="settings-link"' in response.text
    assert 'id="design-preview"' in response.text
    assert 'id="title-mode"' in response.text
    assert 'id="font"' in response.text


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


def test_resilient_upload_reports_missing_and_unauthorised_files(client: TestClient) -> None:
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
    assert "1 photos are missing" in incomplete.json()["detail"]


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
