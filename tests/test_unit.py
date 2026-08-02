from __future__ import annotations

import subprocess
from datetime import timedelta
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from app.cleanup import cleanup_decision
from app.config import AppConfig
from app.image_processing import (
    aspect_fill, aspect_fit, create_title_overlay, decoded_format, has_allowed_extension,
    orient_and_rotate, prepare_photo,
)
from app.jobs import JobRepository
from app.models import (
    Background, FontPreset, JobState, SlideshowSettings, TextPosition, TitleMode, now_utc,
)
from app.renderer import build_ffmpeg_args, escape_drawtext, run_ffmpeg
from app.security import (
    generate_access_token, generate_job_id, safe_job_path, token_digest, token_matches,
)


def settings(**updates: object) -> SlideshowSettings:
    base: dict[str, object] = {"rotations": [0], "captions": [""]}
    base.update(updates)
    return SlideshowSettings.model_validate(base)


def test_configuration_validation_rejects_root() -> None:
    with pytest.raises(ValidationError):
        AppConfig(data_dir=Path("/"))


@pytest.mark.parametrize("name", ["x.jpg", "x.JPEG", "x.png", "x.webp", "x.heic", "x.heif"])
def test_allowed_extensions(name: str) -> None:
    assert has_allowed_extension(name)
    assert not has_allowed_extension("photo.gif")
    assert not has_allowed_extension("../photo")


def test_decoded_formats_and_malformed_file(tmp_path: Path) -> None:
    png = tmp_path / "image.upload"
    Image.new("RGB", (10, 10), "red").save(png, "PNG")
    assert decoded_format(png) == "PNG"
    invalid = tmp_path / "invalid.upload"
    invalid.write_bytes(b"not an image")
    with pytest.raises(ValueError):
        decoded_format(invalid)


def test_random_ids_tokens_and_hash_comparison() -> None:
    job_ids = {generate_job_id() for _ in range(30)}
    tokens = {generate_access_token() for _ in range(30)}
    assert len(job_ids) == len(tokens) == 30
    assert all(len(value) == 22 for value in job_ids)
    token = generate_access_token()
    assert token_matches(token, token_digest(token))
    assert not token_matches(token + "x", token_digest(token))


def test_path_traversal_resistance(tmp_path: Path) -> None:
    valid = generate_job_id()
    assert safe_job_path(tmp_path, valid).parent == tmp_path.resolve()
    for malicious in ("../secret", "x/y", "/tmp/thing", "", "." * 22):
        with pytest.raises(ValueError):
            safe_job_path(tmp_path, malicious)


def test_state_transitions(tmp_path: Path) -> None:
    repository = JobRepository(tmp_path / "jobs.sqlite3")
    repository.initialise()
    job_id, token = generate_job_id(), generate_access_token()
    repository.create(job_id, token_digest(token), settings(), 1)
    assert repository.transition(job_id, JobState.PREPARING, 20).state == JobState.PREPARING
    assert repository.transition(job_id, JobState.RENDERING, 50).state == JobState.RENDERING
    assert repository.transition(job_id, JobState.READY, 100).completed_at is not None
    assert repository.transition(job_id, JobState.DOWNLOADED, 100).downloaded_at is not None
    with pytest.raises(ValueError):
        repository.transition(job_id, JobState.READY, 100)


def test_expiry_decisions() -> None:
    now = now_utc()
    base = {
        "id": generate_job_id(), "token_hash": "x", "progress": 0,
        "settings": settings(), "photo_count": 1, "error": None,
        "created_at": now, "updated_at": now, "completed_at": None, "downloaded_at": None,
    }
    queued = __import__("app.models", fromlist=["JobRecord"]).JobRecord(**base, state=JobState.QUEUED)
    assert cleanup_decision(queued) == (False, False)
    uploading = queued.model_copy(update={"state": JobState.UPLOADING})
    assert cleanup_decision(uploading.model_copy(update={"updated_at": now - timedelta(minutes=16)})) == (True, False)
    assert cleanup_decision(queued.model_copy(update={"updated_at": now - timedelta(minutes=16)})) == (True, False)
    rendering = queued.model_copy(update={"state": JobState.RENDERING, "updated_at": now - timedelta(minutes=31)})
    assert cleanup_decision(rendering) == (True, False)
    ready = queued.model_copy(update={"state": JobState.READY, "completed_at": now - timedelta(minutes=61)})
    assert cleanup_decision(ready) == (True, False)
    downloaded = queued.model_copy(update={"state": JobState.DOWNLOADED, "downloaded_at": now - timedelta(minutes=16)})
    assert cleanup_decision(downloaded) == (True, False)
    old = queued.model_copy(update={"created_at": now - timedelta(hours=25)})
    assert cleanup_decision(old) == (True, True)


def test_aspect_ratio_calculations_preserve_shape() -> None:
    assert aspect_fit((4000, 3000), (1920, 1080)) == (1440, 1080)
    assert aspect_fit((1000, 2000), (1920, 1080)) == (540, 1080)
    assert aspect_fill((1000, 2000), (1920, 1080)) == (1920, 3840)


def test_exif_orientation_and_rotation(tmp_path: Path) -> None:
    path = tmp_path / "oriented.jpg"
    photo = Image.new("RGB", (40, 20), "blue")
    exif = photo.getexif()
    exif[274] = 6
    photo.save(path, exif=exif)
    with Image.open(path) as opened:
        oriented = orient_and_rotate(opened, 0)
    assert oriented.size == (20, 40)
    rotated = orient_and_rotate(Image.new("RGB", (60, 30)), 90)
    assert rotated.size == (30, 60)
    assert rotated.mode == "RGB"


@pytest.mark.parametrize("duration", [0, 20.1, -1])
def test_duration_validation(duration: float) -> None:
    with pytest.raises(ValidationError):
        settings(duration=duration)


def test_duration_and_count_maximum() -> None:
    value = settings(duration=20, rotations=[0] * 61, captions=[""] * 61)
    with pytest.raises(ValueError, match="20-minute"):
        value.validate_for_count(61, 1200)


def test_title_overlay_must_target_an_existing_photo_and_fit_its_time() -> None:
    missing = settings(
        title="Overlay", duration=5, title_mode=TitleMode.OVERLAY,
        title_photo_index=1, title_start=0, title_duration=2,
    )
    with pytest.raises(ValueError, match="photo must exist"):
        missing.validate_for_count(1, 1200)
    too_late = settings(
        title="Overlay", duration=5, title_mode=TitleMode.OVERLAY,
        title_photo_index=0, title_start=4, title_duration=2,
    )
    with pytest.raises(ValueError, match="timing must fit"):
        too_late.validate_for_count(1, 1200)


@pytest.mark.parametrize("colour", ["white", "#fffff", "#fffffg", "123456"])
def test_text_colour_requires_hex(colour: str) -> None:
    with pytest.raises(ValidationError):
        settings(text_color=colour)


@pytest.mark.parametrize("style", ["simple", "smooth", "classic", "celebration"])
def test_presets_keep_photos_static(style: str) -> None:
    value = settings(style=style, movement="zoom-in")
    assert value.movement.value == "static"


@pytest.mark.parametrize("field,value", [("transition", "shell"), ("font", "uploaded.ttf")])
def test_transition_and_font_allow_lists(field: str, value: str) -> None:
    with pytest.raises(ValidationError):
        settings(**{field: value})


def test_text_escaping() -> None:
    escaped = escape_drawtext("Fred's: 100% [ok]\\next\nline")
    assert "\\'" in escaped and "\\:" in escaped and "\\%" in escaped
    assert "\\[" in escaped and "\\\\" in escaped and "\\n" in escaped


def test_ffmpeg_argument_construction_has_safe_output(tmp_path: Path) -> None:
    args = build_ffmpeg_args(
        "ffmpeg", [tmp_path / "one.jpg", tmp_path / "two.jpg"], tmp_path / "output.mp4",
        settings(style="custom", transition="wipe-left", movement="static"), (1920, 1080),
    )
    joined = " ".join(args)
    assert args[0] == "ffmpeg"
    assert "libx264" in args and "yuv420p" in args and "+faststart" in args
    assert args[args.index("-crf") + 1] == "18"
    assert args[args.index("-filter_complex_threads") + 1] == "2"
    assert args[args.index("-threads") + 1] == "2"
    assert args[args.index("-profile:v") + 1] == "high"
    assert args[args.index("-colorspace") + 1] == "bt709"
    assert args[args.index("-color_primaries") + 1] == "bt709"
    assert args[args.index("-color_trc") + 1] == "bt709"
    assert "wipeleft" in joined and "1920:1080" in joined
    assert str(tmp_path / "output.mp4") == args[-1]


def test_ffmpeg_title_overlay_is_timed_on_selected_photo(tmp_path: Path) -> None:
    overlay = tmp_path / "title-overlay.png"
    args = build_ffmpeg_args(
        "ffmpeg", [tmp_path / "one.png", tmp_path / "two.png"], tmp_path / "output.mp4",
        settings(
            style="custom", duration=2, title_mode="overlay", title_photo_index=1,
            title_start=0.25, title_duration=0.5, text_animation="fade",
        ),
        (1920, 1080), title_overlay_path=overlay,
    )
    filters = args[args.index("-filter_complex") + 1]
    assert str(overlay) in args
    assert "[1:v]scale=1920:1080" in filters
    assert "overlay=0:0" in filters
    assert "between(t\\,0.250\\,0.750)" in filters
    assert "fade=t=in" in filters and "alpha=1" in filters


def test_opening_card_uses_configured_duration(tmp_path: Path) -> None:
    args = build_ffmpeg_args(
        "ffmpeg", [tmp_path / "title.png", tmp_path / "photo.png"], tmp_path / "output.mp4",
        settings(title_duration=4), (1920, 1080), title_card=True,
    )
    first_duration = args[args.index("-t") + 1]
    assert first_duration == "4.000"


def test_prepared_image_uses_lossless_png(tmp_path: Path) -> None:
    source = tmp_path / "source.jpg"
    output = tmp_path / "prepared.png"
    Image.new("RGB", (80, 120), "purple").save(source, "JPEG")
    prepare_photo(
        source, output, (320, 180), 0, Background.BLURRED, "",
        FontPreset.MODERN, TextPosition.BOTTOM,
    )
    with Image.open(output) as prepared:
        assert prepared.format == "PNG"
        assert prepared.size == (320, 180)


def test_title_overlay_is_a_transparent_full_frame_png(tmp_path: Path) -> None:
    output = tmp_path / "title-overlay.png"
    create_title_overlay(
        output, (640, 360), settings(
            title="A title", subtitle="A subtitle", title_mode="overlay",
            font="modern", text_color="#ffd166", text_x=0.2, text_y=0.75,
        ),
    )
    with Image.open(output) as overlay:
        assert overlay.format == "PNG"
        assert overlay.mode == "RGBA"
        assert overlay.size == (640, 360)
        minimum, maximum = overlay.getchannel("A").getextrema()
        assert minimum == 0
        assert maximum > 0


def test_zoom_movement_emits_one_frame_per_input_frame(tmp_path: Path) -> None:
    args = build_ffmpeg_args(
        "ffmpeg", [tmp_path / "one.jpg"], tmp_path / "output.mp4",
        settings(style="custom", movement="zoom-in"), (1920, 1080),
    )
    filters = args[args.index("-filter_complex") + 1]
    assert ":d=1:" in filters
    assert ":d=150:" not in filters


def test_ffmpeg_invocation_uses_shell_false(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(args, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_ffmpeg(["ffmpeg", "-version"], 5)
    assert captured["shell"] is False
    assert captured["timeout"] == 5
