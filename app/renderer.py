from __future__ import annotations

import random
import subprocess
from pathlib import Path
from typing import Sequence

from app.models import Movement, SlideshowSettings, Transition


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
) -> list[str]:
    if not image_paths:
        raise ValueError("at least one prepared image is required")
    width, height = frame_size
    durations = [3.0 if title_card and index == 0 else settings.duration for index in range(len(image_paths))]
    args = [ffmpeg_binary, "-hide_banner", "-loglevel", "error", "-y"]
    for image_path, duration in zip(image_paths, durations, strict=True):
        args.extend([
            "-framerate", "30", "-loop", "1", "-t", f"{duration:.3f}", "-i", str(image_path)
        ])

    filters: list[str] = []
    for index, duration in enumerate(durations):
        frames = max(1, round(duration * 30))
        movement = Movement.STATIC if title_card and index == 0 else _movement_for(settings, index)
        if movement == Movement.ZOOM_IN:
            visual = (
                f"zoompan=z='min(zoom+0.0007,1.05)':"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={width}x{height}:fps=30"
            )
        elif movement == Movement.ZOOM_OUT:
            visual = (
                f"zoompan=z='if(eq(on,1),1.05,max(1.0,zoom-0.0007))':"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={width}x{height}:fps=30"
            )
        else:
            visual = f"scale={width}:{height},fps=30"
        filters.append(
            f"[{index}:v]{visual},fps=30,trim=duration={duration:.3f},setpts=PTS-STARTPTS,"
            f"setsar=1,format=yuv420p,fps=30[v{index}]"
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
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-r", "30", "-movflags", "+faststart", str(output_path),
    ])
    return args


def run_ffmpeg(args: list[str], timeout_seconds: int) -> None:
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
