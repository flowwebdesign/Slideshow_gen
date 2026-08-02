from __future__ import annotations

import os
import warnings
from io import BytesIO
from dataclasses import dataclass
from pathlib import Path

from PIL import (
    Image, ImageCms, ImageDraw, ImageFilter, ImageFont, ImageOps, UnidentifiedImageError,
)
from pillow_heif import libheif_info, register_heif_opener

from app.models import Background, FontPreset, SlideshowSettings, TextAlign, TextPosition


register_heif_opener(thumbnails=False)

ALLOWED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif",
    ".hif", ".avif", ".tif", ".tiff", ".dng", ".gif", ".bmp", ".mpo",
    ".j2k", ".jp2", ".jpf", ".jpx",
}
MAX_DECODED_PIXELS = 80_000_000
NORMALIZED_JPEG_QUALITY = 92

FONT_MAP: dict[FontPreset, tuple[str, int]] = {
    FontPreset.MODERN: ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 52),
    FontPreset.CLASSIC: ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", 50),
    FontPreset.FRIENDLY: ("/usr/share/fonts/truetype/lato/Lato-Regular.ttf", 54),
    FontPreset.ELEGANT: ("/usr/share/fonts/truetype/noto/NotoSerif-Regular.ttf", 52),
    FontPreset.CINEMATIC: ("/usr/share/fonts/truetype/lato/Lato-Heavy.ttf", 60),
    FontPreset.TYPEWRITER: ("/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf", 48),
    FontPreset.LARGE_TV: ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 68),
}


@dataclass(frozen=True)
class PreparedImage:
    path: Path
    width: int
    height: int


@dataclass(frozen=True)
class NormalizedImage:
    source_format: str
    width: int
    height: int
    frame_count: int


class ImageNormalizationError(ValueError):
    def __init__(self, message: str, *, code: str, detected_format: str = "UNKNOWN"):
        super().__init__(message)
        self.code = code
        self.detected_format = detected_format


def has_allowed_extension(filename: str | None) -> bool:
    return bool(filename and Path(filename).suffix.lower() in ALLOWED_EXTENSIONS)


def _validate_dimensions(image: Image.Image, max_pixels: int) -> None:
    width, height = image.size
    if width < 1 or height < 1 or width * height > max_pixels:
        raise ImageNormalizationError(
            f"decoded image exceeds the {max_pixels:,}-pixel safety limit",
            code="image_too_large", detected_format=(image.format or "UNKNOWN").upper(),
        )


def decoded_format(path: Path, *, max_pixels: int = MAX_DECODED_PIXELS) -> str:
    detected = "UNKNOWN"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(path) as image:
                detected = (image.format or "UNKNOWN").upper()
                _validate_dimensions(image, max_pixels)
                image.seek(0)
                image.load()
    except ImageNormalizationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageNormalizationError(
            f"decoded image exceeds the {max_pixels:,}-pixel safety limit",
            code="image_too_large", detected_format=detected,
        ) from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise ImageNormalizationError(
            "photo data could not be decoded as a supported still image",
            code="invalid_image", detected_format=detected,
        ) from exc
    return detected


def _to_srgb(image: Image.Image) -> Image.Image:
    has_alpha = image.mode in {"RGBA", "LA"} or (
        image.mode == "P" and "transparency" in image.info
    )
    alpha = image.convert("RGBA").getchannel("A") if has_alpha else None
    rgb = image.convert("RGB")
    profile_bytes = image.info.get("icc_profile")
    if isinstance(profile_bytes, bytes) and profile_bytes:
        try:
            rgb = ImageCms.profileToProfile(
                rgb, ImageCms.ImageCmsProfile(BytesIO(profile_bytes)),
                ImageCms.createProfile("sRGB"), outputMode="RGB",
            )
        except (ImageCms.PyCMSError, OSError, TypeError, ValueError):
            # A malformed optional colour profile must not make a decodable photo unusable.
            pass
    if alpha is not None:
        background = Image.new("RGB", rgb.size, "black")
        background.paste(rgb, (0, 0), alpha)
        return background
    return rgb


def normalize_image(
    source_path: Path, output_path: Path, *, max_pixels: int = MAX_DECODED_PIXELS,
) -> NormalizedImage:
    """Decode one untrusted still image and atomically store a canonical sRGB JPEG."""
    temporary = output_path.with_name(f".{output_path.name}.normalizing")
    temporary.unlink(missing_ok=True)
    detected = "UNKNOWN"
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source_path) as opened:
                detected = (opened.format or "UNKNOWN").upper()
                _validate_dimensions(opened, max_pixels)
                frame_count = int(getattr(opened, "n_frames", 1))
                # Pillow and pillow-heif expose the primary/main still as frame zero.
                opened.seek(0)
                opened.load()
                oriented = ImageOps.exif_transpose(opened)
                _validate_dimensions(oriented, max_pixels)
                normalized = _to_srgb(oriented)
                width, height = normalized.size
                normalized.save(
                    temporary, "JPEG", quality=NORMALIZED_JPEG_QUALITY,
                    subsampling=0, optimize=False,
                )
        with Image.open(temporary) as check:
            check.load()
            if check.format != "JPEG" or check.mode != "RGB":
                raise OSError("canonical image verification failed")
        os.replace(temporary, output_path)
        return NormalizedImage(detected, width, height, frame_count)
    except ImageNormalizationError:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ImageNormalizationError(
            f"decoded image exceeds the {max_pixels:,}-pixel safety limit",
            code="image_too_large", detected_format=detected,
        ) from exc
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError) as exc:
        raise ImageNormalizationError(
            "photo data could not be decoded as a supported still image",
            code="invalid_image", detected_format=detected,
        ) from exc
    finally:
        temporary.unlink(missing_ok=True)


def heif_decoder_available() -> bool:
    try:
        decoders = libheif_info().get("decoders", {})
    except Exception:
        return False
    return any(
        "hevc" in f"{name} {description}".lower() or "de265" in f"{name} {description}".lower()
        for name, description in decoders.items()
    )


def aspect_fit(source: tuple[int, int], target: tuple[int, int]) -> tuple[int, int]:
    source_width, source_height = source
    target_width, target_height = target
    scale = min(target_width / source_width, target_height / source_height)
    return max(1, round(source_width * scale)), max(1, round(source_height * scale))


def aspect_fill(source: tuple[int, int], target: tuple[int, int]) -> tuple[int, int]:
    source_width, source_height = source
    target_width, target_height = target
    scale = max(target_width / source_width, target_height / source_height)
    return max(1, round(source_width * scale)), max(1, round(source_height * scale))


def orient_and_rotate(image: Image.Image, rotation: int) -> Image.Image:
    oriented = ImageOps.exif_transpose(image)
    if rotation:
        oriented = oriented.rotate(-rotation, expand=True)
    return oriented.convert("RGB")


def _font(preset: FontPreset, size_multiplier: float = 1.0) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path, size = FONT_MAP[preset]
    try:
        return ImageFont.truetype(path, round(size * size_multiplier))
    except OSError:
        return ImageFont.load_default(size=round(size * size_multiplier))


def _draw_text_panel(
    canvas: Image.Image, text: str, position: TextPosition, font_preset: FontPreset,
    *, size_multiplier: float = 1.0, colour: str = "#ffffff", panel_opacity: int = 155,
    align: TextAlign = TextAlign.CENTRE, x_fraction: float = 0.5,
    y_fraction: float | None = None,
) -> tuple[int, int, int, int] | None:
    if not text:
        return None
    draw = ImageDraw.Draw(canvas, "RGBA")
    font = _font(font_preset, size_multiplier)
    safe_x = round(canvas.width * 0.07)
    max_width = canvas.width - safe_x * 2
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textbbox((0, 0), candidate, font=font, stroke_width=1)[2] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    rendered = "\n".join(lines[:4])
    pillow_align = "center" if align == TextAlign.CENTRE else align.value
    box = draw.multiline_textbbox(
        (0, 0), rendered, font=font, spacing=10, align=pillow_align, stroke_width=2,
    )
    text_width, text_height = box[2] - box[0], box[3] - box[1]
    margin_y = round(canvas.height * 0.08)
    safe_x = round(canvas.width * 0.05)
    x = round(canvas.width * x_fraction - text_width / 2)
    x = max(safe_x, min(canvas.width - safe_x - text_width, x))
    if y_fraction is not None:
        y = round(canvas.height * y_fraction - text_height / 2)
        y = max(margin_y, min(canvas.height - margin_y - text_height, y))
    elif position == TextPosition.TOP:
        y = margin_y
    elif position == TextPosition.CENTRE:
        y = (canvas.height - text_height) // 2
    else:
        y = canvas.height - margin_y - text_height
    padding = max(16, round(canvas.height * 0.018))
    if panel_opacity:
        draw.rounded_rectangle(
            (x - padding, y - padding, x + text_width + padding, y + text_height + padding),
            radius=padding, fill=(0, 0, 0, panel_opacity),
        )
    draw.multiline_text(
        (x, y), rendered, font=font, fill=colour, spacing=10, align=pillow_align,
        stroke_width=2, stroke_fill=(0, 0, 0, 210),
    )
    return x, y, text_width, text_height


def _draw_title_group(canvas: Image.Image, settings: SlideshowSettings) -> None:
    has_both = bool(settings.title and settings.subtitle)
    separation = min(0.13, 0.045 + 0.025 * settings.title_size)
    title_y = settings.text_y - separation / 2 if has_both else settings.text_y
    subtitle_y = settings.text_y + separation / 2 if has_both else settings.text_y
    _draw_text_panel(
        canvas, settings.title, TextPosition.CENTRE, settings.font,
        size_multiplier=settings.title_size, colour=settings.text_color,
        panel_opacity=settings.text_panel_opacity, align=settings.text_align,
        x_fraction=settings.text_x, y_fraction=title_y,
    )
    _draw_text_panel(
        canvas, settings.subtitle, TextPosition.CENTRE, settings.font,
        size_multiplier=settings.subtitle_size, colour=settings.text_color,
        panel_opacity=settings.text_panel_opacity, align=settings.text_align,
        x_fraction=settings.text_x, y_fraction=subtitle_y,
    )


def prepare_photo(
    source_path: Path, output_path: Path, frame_size: tuple[int, int], rotation: int,
    background: Background, caption: str, font: FontPreset, text_position: TextPosition,
    *, caption_size: float = 1, text_colour: str = "#ffffff", panel_opacity: int = 155,
    text_align: TextAlign = TextAlign.CENTRE,
) -> PreparedImage:
    with Image.open(source_path) as opened:
        photo = orient_and_rotate(opened, rotation)
    frame_width, frame_height = frame_size
    if background == Background.BLURRED:
        fill_size = aspect_fill(photo.size, frame_size)
        backdrop = photo.resize(fill_size, Image.Resampling.LANCZOS)
        left = (backdrop.width - frame_width) // 2
        top = (backdrop.height - frame_height) // 2
        canvas = backdrop.crop((left, top, left + frame_width, top + frame_height))
        canvas = canvas.filter(ImageFilter.GaussianBlur(radius=max(20, frame_width // 40)))
        canvas = Image.blend(canvas, Image.new("RGB", frame_size, "black"), 0.18)
    else:
        canvas = Image.new("RGB", frame_size, "black")
    fitted = photo.resize(aspect_fit(photo.size, frame_size), Image.Resampling.LANCZOS)
    fitted = fitted.filter(ImageFilter.UnsharpMask(radius=1.1, percent=65, threshold=3))
    canvas.paste(fitted, ((frame_width - fitted.width) // 2, (frame_height - fitted.height) // 2))
    _draw_text_panel(
        canvas, caption, text_position, font, size_multiplier=caption_size,
        colour=text_colour, panel_opacity=panel_opacity, align=text_align,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, "PNG", compress_level=2)
    return PreparedImage(output_path, frame_width, frame_height)


def create_title_card(output_path: Path, frame_size: tuple[int, int], settings: SlideshowSettings) -> PreparedImage:
    canvas = Image.new("RGB", frame_size, (20, 26, 35))
    draw = ImageDraw.Draw(canvas)
    width, height = frame_size
    for y in range(height):
        shade = round(20 + 28 * y / max(1, height - 1))
        draw.line((0, y, width, y), fill=(shade, shade + 5, shade + 12))
    _draw_title_group(canvas, settings)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, "PNG", compress_level=2)
    return PreparedImage(output_path, width, height)


def create_title_overlay(
    output_path: Path, frame_size: tuple[int, int], settings: SlideshowSettings,
) -> PreparedImage:
    canvas = Image.new("RGBA", frame_size, (0, 0, 0, 0))
    _draw_title_group(canvas, settings)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, "PNG", compress_level=2)
    return PreparedImage(output_path, frame_size[0], frame_size[1])
