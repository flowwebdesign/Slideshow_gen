from __future__ import annotations

import queue
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable, Sequence

from app.models import Movement, SlideshowSettings, TextAnimation, Transition


XFADE_MAP = {
    Transition.FADE: "fade",
    Transition.WIPE_LEFT: "wipeleft",
    Transition.SLIDE_LEFT: "slideleft",
    Transition.DISSOLVE: "dissolve",
}


def escape_drawtext(value: str) -> str:
    """Escape text for FFmpeg drawtext when that rendering path is used later."""
    return (
        value.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace("\n", "\\n")
    )


def _movement_for(settings: SlideshowSettings, index: int) -> Movement:
    if settings.movement != Movement.AUTO:
        return settings.movement
    return Movement.ZOOM_IN if index % 2 == 0 else Movement.ZOOM_OUT


def _transition_for(settings: SlideshowSettings, index: int) -> Transition:
    if settings.transition != Transition.AUTO:
        return settings.transition
    choices = [Transition.FADE, Transition.WIPE_LEFT, Transition.SLIDE_LEFT, Transition.DISSOLVE]
    return choices[index % len(choices)]


def build_ffmpeg_args(
    ffmpeg_binary: str, image_paths: Sequence[Path], output_path: Path,
    settings: SlideshowSettings, frame_size: tuple[int, int], *, title_card: bool = False,
    title_overlay_path: Path | None = None,
) -> list[str]:
    if not image_paths:
        raise ValueError("at least one prepared image is required")
    width, height = frame_size
    durations = [
        settings.title_duration if title_card and index == 0 else settings.duration
        for index in range(len(image_paths))
    ]
    args = [
        ffmpeg_binary, "-hide_banner", "-loglevel", "error", "-y",
        "-filter_threads", "2", "-filter_complex_threads", "2",
    ]
    for image_path, duration in zip(image_paths, durations, strict=True):
        args.extend([
            "-framerate", "30", "-loop", "1", "-t", f"{duration:.3f}", "-i", str(image_path)
        ])
    overlay_input_index: int | None = None
    if title_overlay_path is not None:
        overlay_input_index = len(image_paths)
        args.extend([
            "-framerate", "30", "-loop", "1", "-t", f"{settings.duration:.3f}",
            "-i", str(title_overlay_path),
        ])

    filters: list[str] = []
    for index, duration in enumerate(durations):
        movement = Movement.STATIC if title_card and index == 0 else _movement_for(settings, index)
        if movement == Movement.ZOOM_IN:
            visual = (
                f"zoompan=z='min(max(zoom,pzoom)+0.00035,1.05)':"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps=30"
            )
        elif movement == Movement.ZOOM_OUT:
            visual = (
                f"zoompan=z='if(eq(on,0),1.05,max(1.0,pzoom-0.00035))':"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s={width}x{height}:fps=30"
            )
        else:
            visual = f"scale={width}:{height},fps=30"
        target_photo_index = settings.title_photo_index + (1 if title_card else 0)
        base_label = f"base{index}" if overlay_input_index is not None and index == target_photo_index else f"v{index}"
        filters.append(
            f"[{index}:v]{visual},fps=30,trim=duration={duration:.3f},setpts=PTS-STARTPTS,"
            f"setsar=1,format=yuv420p,fps=30[{base_label}]"
        )
        if overlay_input_index is not None and index == target_photo_index:
            start = settings.title_start
            end = settings.title_start + settings.title_duration
            overlay_filter = (
                f"[{overlay_input_index}:v]scale={width}:{height},fps=30,format=rgba,"
                "setpts=PTS-STARTPTS"
            )
            if settings.text_animation == TextAnimation.FADE:
                fade_duration = min(0.4, settings.title_duration / 3)
                overlay_filter += (
                    f",fade=t=in:st={start:.3f}:d={fade_duration:.3f}:alpha=1"
                    f",fade=t=out:st={end - fade_duration:.3f}:d={fade_duration:.3f}:alpha=1"
                )
            filters.append(f"{overlay_filter}[titleoverlay]")
            filters.append(
                f"[{base_label}][titleoverlay]overlay=0:0:"
                f"enable='between(t\\,{start:.3f}\\,{end:.3f})':shortest=1[v{index}]"
            )

    final_label = "v0"
    if len(image_paths) > 1:
        transition_duration = (
            0.55 if settings.style.value == "celebration"
            else 1.2 if settings.style.value == "classic"
            else 0.75
        )
        elapsed = durations[0]
        for index in range(1, len(image_paths)):
            transition = _transition_for(settings, index - 1)
            output_label = f"x{index}"
            if transition == Transition.NONE:
                filters.append(
                    f"[{final_label}][v{index}]concat=n=2:v=1:a=0,fps=30[{output_label}]"
                )
                elapsed += durations[index]
            else:
                overlap = min(transition_duration, durations[index - 1] / 2, durations[index] / 2)
                offset = elapsed - overlap
                filters.append(
                    f"[{final_label}][v{index}]xfade=transition={XFADE_MAP[transition]}:"
                    f"duration={overlap:.3f}:offset={offset:.3f},fps=30[{output_label}]"
                )
                elapsed += durations[index] - overlap
            final_label = output_label

    args.extend([
        "-filter_complex", ";".join(filters), "-map", f"[{final_label}]", "-an",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-threads", "2",
        "-profile:v", "high", "-level:v", "4.1", "-pix_fmt", "yuv420p",
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-color_range", "tv",
        "-r", "30", "-movflags", "+faststart", str(output_path),
    ])
    return args


def estimated_video_duration(
    settings: SlideshowSettings, image_count: int, *, title_card: bool = False,
) -> float:
    durations = [
        settings.title_duration if title_card and index == 0 else settings.duration
        for index in range(image_count)
    ]
    if not durations:
        return 0
    elapsed = durations[0]
    transition_duration = (
        0.55 if settings.style.value == "celebration"
        else 1.2 if settings.style.value == "classic"
        else 0.75
    )
    for index in range(1, image_count):
        transition = _transition_for(settings, index - 1)
        if transition == Transition.NONE:
            elapsed += durations[index]
        else:
            overlap = min(transition_duration, durations[index - 1] / 2, durations[index] / 2)
            elapsed += durations[index] - overlap
    return elapsed


def parse_ffmpeg_progress(line: str) -> float | None:
    key, separator, value = line.strip().partition("=")
    if not separator or key not in {"out_time_us", "out_time_ms"}:
        return None
    try:
        return max(0, int(value) / 1_000_000)
    except ValueError:
        return None


def _terminate_process(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def run_ffmpeg(
    args: list[str], timeout_seconds: int, *,
    progress_callback: Callable[[float], None] | None = None,
    stall_timeout_seconds: float | None = None,
) -> None:
    if progress_callback is None:
        try:
            subprocess.run(
                args, shell=False, check=True, timeout=timeout_seconds,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("video rendering timed out") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or "").strip().splitlines()
            summary = " | ".join(detail)[:900] if detail else "unknown FFmpeg error"
            raise RuntimeError(f"video rendering failed: {summary}") from exc
        return

    command = [args[0], "-progress", "pipe:1", "-nostats", *args[1:]]
    process = subprocess.Popen(
        command, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, bufsize=1,
    )
    assert process.stdout is not None
    assert process.stderr is not None
    messages: queue.Queue[str | None] = queue.Queue()

    def read_progress() -> None:
        try:
            for line in process.stdout:
                messages.put(line)
        finally:
            messages.put(None)

    reader = threading.Thread(target=read_progress, name="ffmpeg-progress", daemon=True)
    reader.start()
    started = last_activity = time.monotonic()
    last_progress_seconds = -1.0
    reader_finished = False
    try:
        while not reader_finished:
            now = time.monotonic()
            if now - started > timeout_seconds:
                _terminate_process(process)
                raise RuntimeError("video rendering timed out")
            if stall_timeout_seconds and now - last_activity > stall_timeout_seconds:
                _terminate_process(process)
                raise RuntimeError(
                    "video rendering stopped making progress; please try again"
                )
            try:
                message = messages.get(timeout=0.5)
            except queue.Empty:
                if process.poll() is not None:
                    reader_finished = True
                continue
            if message is None:
                reader_finished = True
                continue
            seconds = parse_ffmpeg_progress(message)
            if seconds is not None and seconds > last_progress_seconds:
                last_progress_seconds = seconds
                last_activity = time.monotonic()
                progress_callback(seconds)
        try:
            return_code = process.wait(timeout=5)
        except subprocess.TimeoutExpired as exc:
            _terminate_process(process)
            raise RuntimeError("video rendering did not finish cleanly") from exc
        error_output = process.stderr.read()
        if return_code != 0:
            detail = error_output.strip().splitlines()
            summary = " | ".join(detail)[:900] if detail else "unknown FFmpeg error"
            raise RuntimeError(f"video rendering failed: {summary}")
    finally:
        if process.poll() is None:
            _terminate_process(process)
        reader.join(timeout=2)
