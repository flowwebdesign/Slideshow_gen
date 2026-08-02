from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class JobState(StrEnum):
    UPLOADING = "uploading"
    QUEUED = "queued"
    PREPARING = "preparing"
    RENDERING = "rendering"
    READY = "ready"
    DOWNLOADED = "downloaded"
    FAILED = "failed"
    EXPIRED = "expired"


class AspectRatio(StrEnum):
    TV = "16:9"
    PORTRAIT = "9:16"
    SQUARE = "1:1"


class Background(StrEnum):
    BLURRED = "blurred"
    BLACK = "black"


class StylePreset(StrEnum):
    SIMPLE = "simple"
    SMOOTH = "smooth"
    CLASSIC = "classic"
    CELEBRATION = "celebration"
    CUSTOM = "custom"


class Transition(StrEnum):
    NONE = "none"
    FADE = "fade"
    WIPE_LEFT = "wipe-left"
    SLIDE_LEFT = "slide-left"
    DISSOLVE = "dissolve"
    AUTO = "auto"


class Movement(StrEnum):
    STATIC = "static"
    ZOOM_IN = "zoom-in"
    ZOOM_OUT = "zoom-out"
    AUTO = "auto"


class FontPreset(StrEnum):
    MODERN = "modern"
    CLASSIC = "classic"
    FRIENDLY = "friendly"
    ELEGANT = "elegant"
    CINEMATIC = "cinematic"
    TYPEWRITER = "typewriter"
    LARGE_TV = "large-tv"


class TextPosition(StrEnum):
    TOP = "top"
    CENTRE = "centre"
    BOTTOM = "bottom"


class TextAlign(StrEnum):
    LEFT = "left"
    CENTRE = "centre"
    RIGHT = "right"


class TitleMode(StrEnum):
    CARD = "card"
    OVERLAY = "overlay"
    HIDDEN = "hidden"


class TextAnimation(StrEnum):
    NONE = "none"
    FADE = "fade"


RESOLUTIONS = {
    AspectRatio.TV: (1920, 1080),
    AspectRatio.PORTRAIT: (1080, 1920),
    AspectRatio.SQUARE: (1080, 1080),
}


class SlideshowSettings(BaseModel):
    title: str = Field("", max_length=120)
    subtitle: str = Field("", max_length=200)
    duration: float = Field(5, ge=1, le=20)
    aspect_ratio: AspectRatio = AspectRatio.TV
    background: Background = Background.BLURRED
    style: StylePreset = StylePreset.SMOOTH
    transition: Transition = Transition.FADE
    movement: Movement = Movement.STATIC
    font: FontPreset = FontPreset.MODERN
    text_position: TextPosition = TextPosition.BOTTOM
    title_mode: TitleMode = TitleMode.CARD
    title_photo_index: int = Field(0, ge=0, le=99)
    title_start: float = Field(0, ge=0, le=20)
    title_duration: float = Field(3, ge=0.5, le=20)
    title_size: float = Field(1.45, ge=0.6, le=2.5)
    subtitle_size: float = Field(0.85, ge=0.5, le=1.8)
    caption_size: float = Field(1, ge=0.5, le=2)
    text_x: float = Field(0.5, ge=0.05, le=0.95)
    text_y: float = Field(0.5, ge=0.05, le=0.95)
    text_color: str = "#ffffff"
    text_panel_opacity: int = Field(155, ge=0, le=230)
    text_align: TextAlign = TextAlign.CENTRE
    text_animation: TextAnimation = TextAnimation.FADE
    rotations: list[int] = Field(default_factory=list)
    captions: list[str] = Field(default_factory=list)

    @field_validator("title", "subtitle")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("text_color")
    @classmethod
    def valid_text_colour(cls, value: str) -> str:
        normalised = value.lower()
        if len(normalised) != 7 or normalised[0] != "#" or any(
            character not in "0123456789abcdef" for character in normalised[1:]
        ):
            raise ValueError("text_color must be a six-digit hexadecimal colour")
        return normalised

    @field_validator("rotations")
    @classmethod
    def rotations_are_quarter_turns(cls, values: list[int]) -> list[int]:
        if any(value not in {0, 90, 180, 270} for value in values):
            raise ValueError("rotations must be 0, 90, 180, or 270")
        return values

    @field_validator("captions")
    @classmethod
    def caption_lengths(cls, values: list[str]) -> list[str]:
        if any(len(value) > 300 for value in values):
            raise ValueError("captions may contain at most 300 characters")
        return [value.strip() for value in values]

    @model_validator(mode="after")
    def apply_preset(self) -> "SlideshowSettings":
        if self.style == StylePreset.SIMPLE:
            self.movement = Movement.STATIC
            self.transition = Transition.FADE
        elif self.style == StylePreset.SMOOTH:
            self.movement = Movement.STATIC
            self.transition = Transition.FADE
        elif self.style == StylePreset.CLASSIC:
            self.background = Background.BLACK
            self.movement = Movement.STATIC
            self.transition = Transition.FADE
            self.font = FontPreset.CLASSIC
        elif self.style == StylePreset.CELEBRATION:
            self.movement = Movement.STATIC
            self.transition = Transition.AUTO
            self.font = FontPreset.LARGE_TV
        return self

    def validate_for_count(self, count: int, max_video_seconds: int) -> None:
        if len(self.rotations) != count or len(self.captions) != count:
            raise ValueError("rotation and caption counts must match the photos")
        has_title = bool(self.title or self.subtitle) and self.title_mode != TitleMode.HIDDEN
        if has_title and self.title_mode == TitleMode.OVERLAY:
            if self.title_photo_index >= count:
                raise ValueError("title overlay photo must exist")
            if self.title_start + self.title_duration > self.duration:
                raise ValueError("title overlay timing must fit within the selected photo")
        title_seconds = self.title_duration if has_title and self.title_mode == TitleMode.CARD else 0
        if count * self.duration + title_seconds > max_video_seconds:
            raise ValueError("slideshow would exceed the 20-minute limit")


class JobRecord(BaseModel):
    id: str
    token_hash: str
    state: JobState
    progress: int
    settings: SlideshowSettings
    photo_count: int
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    downloaded_at: datetime | None = None

    @classmethod
    def from_row(cls, row: Any) -> "JobRecord":
        import json

        return cls(
            id=row["id"], token_hash=row["token_hash"], state=row["state"],
            progress=row["progress"], settings=json.loads(row["settings"]),
            photo_count=row["photo_count"], error=row["error"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            completed_at=datetime.fromisoformat(row["completed_at"]) if row["completed_at"] else None,
            downloaded_at=datetime.fromisoformat(row["downloaded_at"]) if row["downloaded_at"] else None,
        )


def now_utc() -> datetime:
    return datetime.now(UTC)
