from __future__ import annotations

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
