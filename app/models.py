from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class JobState(StrEnum):
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
    LARGE_TV = "large-tv"


class TextPosition(StrEnum):
    TOP = "top"
    CENTRE = "centre"
    BOTTOM = "bottom"


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
    rotations: list[int] = Field(default_factory=list)
    captions: list[str] = Field(default_factory=list)

    @field_validator("title", "subtitle")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

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
        title_seconds = 3 if self.title or self.subtitle else 0
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
