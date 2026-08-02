# Photo Slideshow MVP

A local, mobile-friendly FastAPI application that turns ordered JPEG, PNG, WebP, HEIC, or HEIF photos into a television-compatible MP4. It uses Pillow for safe image decoding and normalization, FFmpeg for H.264 rendering, SQLite for job state, and a bounded in-process worker.

The simple default preserves the complete still photograph without stretching or foreground cropping, uses lossless temporary PNG frames, and produces high-quality Full HD Rec.709 video. Photo movement is used only when explicitly chosen under Advanced custom settings.

## Run locally

The primary command is:

```bash
docker compose up --build
```

Open <http://localhost:8000>. The container includes FFmpeg, ffprobe, HEIF support, and approved DejaVu/Liberation fonts. Port `8000` can be changed with `SLIDESHOW_PORT` in a local `.env` copied from `.env.example`.

The Docker service runs as the unprivileged `slideshow` user and has CPU, memory, and PID constraints. One renderer runs at a time by default so several large 1080p jobs cannot exhaust the machine simultaneously.

## Commands

```bash
# Full automated suite
docker compose run --rm slideshow pytest -q

# Render/ffprobe tests only
docker compose run --rm slideshow pytest -q -m integration

# Logs (application access logging is disabled so tokens cannot enter normal logs)
docker compose logs --tail 120

# One independent cleanup pass
docker compose exec slideshow python -m app.cleanup --once
```

Each submitted slideshow displays a random job reference. Safe lifecycle events using that reference
(`job_queued`, `job_preparing`, `job_rendering`, `job_ready`, or `job_failed`) appear in the container
logs. Access tokens and user filenames are deliberately excluded. A lightweight health check is
available at `GET /health`.

An easy non-Docker option is Python 3.12 plus system FFmpeg:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
uvicorn app.main:app --reload --port 8000 --no-access-log
```

## File lifecycle and privacy

Temporary files are stored in the Docker volume at `/data/jobs/<random-job-id>/`; local non-Docker runs use `./data/jobs`. The directory is never statically served. Files are available only through token-protected endpoints.

- Invalid uploads are removed immediately.
- Original and prepared photos are removed immediately after success, and best-effort immediately after failure.
- A failed job is swept after 15 minutes.
- A downloaded MP4 has a 15-minute retry window.
- An undownloaded MP4 expires 60 minutes after rendering finishes.
- Queued work expires after 15 minutes and rendering work after 30 minutes without an update.
- Metadata is removed after 24 hours.

The application uses normal filesystem deletion; it does **not** claim secure disk overwriting. The idempotent cleanup thread starts with the application, runs immediately and then every minute, so it handles browser abandonment and application restarts. To verify cleanup, inspect only the target job directory in the volume before and after `python -m app.cleanup --once`; the automated cleanup integration tests also prove this lifecycle.

## Manual phone/browser smoke test

1. Open the site on a phone-sized browser (or responsive browser mode at 390 × 844).
2. Select three synthetic/non-personal JPEGs and confirm three thumbnails appear.
3. Move the third photo earlier with its left arrow, rotate one photo, and enter one caption.
4. Leave `16:9`, `5 seconds`, `Smooth`, and `Blurred background` selected.
5. Tap **Create slideshow**, watch progress reach ready, play the preview, and download the MP4.
6. Confirm the YouTube instructions appear after download.

A Playwright dependency is intentionally not installed in this focused MVP because its browser binaries add hundreds of megabytes to the image and are not needed to test the framework-free interactions. API, lifecycle, real FFmpeg, and HTTP preview/download paths are automated; this short browser check covers layout and native media playback.

## Architecture and extension points

The service consists of a FastAPI HTTP layer, SQLite repository, bounded `ThreadPoolExecutor`, Pillow image-preparation module, FFmpeg command builder, renderer, and cleanup service. FFmpeg currently emits video without an audio stream. Its renderer boundary accepts prepared visuals and can later gain an approved music input/mix without changing job creation or storage.

Before any public launch, add reverse-proxy/request rate limiting, CAPTCHA or equivalent abuse protection, upload-body limits at the proxy, disk monitoring/alerts, production TLS, and a public security review. Those controls are deliberately not represented as already active.
