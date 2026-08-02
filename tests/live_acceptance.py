"""Live HTTP acceptance helper; run against the Compose service, not under pytest."""
from __future__ import annotations

import io
import json
import os
import subprocess
import time
from datetime import timedelta
from pathlib import Path

import httpx
from PIL import Image

from app.cleanup import CleanupService
from app.config import config
from app.jobs import JobRepository
from app.models import now_utc
from app.security import safe_job_path


def photo(size: tuple[int, int], colour: tuple[int, int, int]) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", size, colour).save(output, "JPEG")
    return output.getvalue()


def main() -> None:
    base_url = os.environ.get("ACCEPTANCE_URL", "http://slideshow:8000")
    settings = {
        "title": "Live acceptance slideshow", "subtitle": "Landscape and portrait",
        "duration": 1, "aspect_ratio": "16:9", "background": "blurred", "style": "smooth",
        "transition": "fade", "movement": "zoom-in", "font": "modern", "text_position": "bottom",
        "rotations": [0, 90, 0], "captions": ["Landscape", "Portrait", "Square"],
    }
    files = [
        ("files", ("landscape.jpg", photo((480, 270), (190, 70, 50)), "image/jpeg")),
        ("files", ("portrait.jpg", photo((180, 360), (45, 145, 80)), "image/jpeg")),
        ("files", ("square.jpg", photo((280, 280), (65, 90, 190)), "image/jpeg")),
    ]
    with httpx.Client(base_url=base_url, timeout=30, trust_env=False) as client:
        homepage = client.get("/")
        homepage.raise_for_status()
        created = client.post("/api/jobs", files=files, data={"settings": json.dumps(settings)})
        created.raise_for_status()
        payload = created.json()
        job_id, token = payload["job_id"], payload["access_token"]
        headers = {"X-Job-Token": token}
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            status = client.get(f"/api/jobs/{job_id}/status", headers=headers)
            status.raise_for_status()
            state = status.json()["state"]
            if state in {"ready", "downloaded", "failed", "expired"}:
                break
            time.sleep(0.5)
        if state not in {"ready", "downloaded"}:
            raise RuntimeError(f"live job ended in {state}: {status.json().get('error')}")
        job_dir = safe_job_path(config.jobs_dir, job_id)
        output = job_dir / "output.mp4"
        proof = {
            "homepage_http": homepage.status_code,
            "job_id": job_id,
            "ready_state": state,
            "output_before_expiry": output.is_file(),
            "source_deleted": not (job_dir / "source").exists(),
            "prepared_deleted": not (job_dir / "prepared").exists(),
        }
        preview = client.get(f"/api/jobs/{job_id}/preview", headers=headers)
        preview.raise_for_status()
        downloaded = client.get(f"/api/jobs/{job_id}/download", headers=headers)
        downloaded.raise_for_status()
        local_video = Path("/tmp/slideshow-live-acceptance.mp4")
        local_video.write_bytes(downloaded.content)
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,width,height,pix_fmt,r_frame_rate:format=duration", "-of", "json", str(local_video)],
            shell=False, check=True, capture_output=True, text=True,
        )
        playback = subprocess.run(
            ["ffmpeg", "-v", "error", "-i", str(local_video), "-f", "null", "-"],
            shell=False, check=True, capture_output=True, text=True,
        )
        proof.update({
            "preview_http": preview.status_code,
            "download_http": downloaded.status_code,
            "download_bytes": len(downloaded.content),
            "ffprobe": json.loads(probe.stdout),
            "ffmpeg_decode": playback.returncode == 0,
        })

    repository = JobRepository(config.database_path)
    repository.initialise()
    proof["downloaded_state"] = repository.get(job_id).state.value
    repository.set_times_for_test(job_id, downloaded_at=now_utc() - timedelta(minutes=16))
    CleanupService(config, repository).run_once()
    proof["output_deleted_after_grace"] = not output.exists()
    proof["expired_state"] = repository.get(job_id).state.value
    local_video.unlink(missing_ok=True)
    print(json.dumps(proof, indent=2))


if __name__ == "__main__":
    main()
