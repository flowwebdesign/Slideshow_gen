from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps, UnidentifiedImageError
from pillow_heif import register_heif_opener

from app.models import Background, FontPreset, SlideshowSettings, TextPosition


register_heif_opener()

ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
ALLOWED_DECODED_FORMATS = {"JPEG", "PNG", "WEBP", "HEIF", "HEIC"}

FONT_MAP: dict[FontPreset, tuple[str, int]] = {
    FontPreset.MODERN: ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 52),
    FontPreset.CLASSIC: ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf", 50),
    FontPreset.FRIENDLY: ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 52),
    FontPreset.LARGE_TV: ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 68),
}


@dataclass(frozen=True)
class PreparedImage:
    path: Path
    width: int
    height: int


def has_allowed_extension(filename: str | None) -> bool:
    return bool(filename and Path(filename).suffix.lower() in ALLOWED_EXTENSIONS)


def decoded_format(path: Path) -> str:
    try:
        with Image.open(path) as image:
            image.load()
            value = (image.format or "").upper()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ValueError("file is not a valid supported image") from exc
    if value not in ALLOWED_DECODED_FORMATS:
        raise ValueError("decoded image format is not supported")
    return value


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
    *, size_multiplier: float = 1.0,
) -> None:
    if not text:
        return
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
    box = draw.multiline_textbbox((0, 0), rendered, font=font, spacing=10, align="center", stroke_width=2)
    text_width, text_height = box[2] - box[0], box[3] - box[1]
    x = (canvas.width - text_width) // 2
    margin_y = round(canvas.height * 0.08)
    if position == TextPosition.TOP:
        y = margin_y
    elif position == TextPosition.CENTRE:
        y = (canvas.height - text_height) // 2
    else:
        y = canvas.height - margin_y - text_height
    padding = max(16, round(canvas.height * 0.018))
    draw.rounded_rectangle(
        (x - padding, y - padding, x + text_width + padding, y + text_height + padding),
        radius=padding, fill=(0, 0, 0, 155),
    )
    draw.multiline_text(
        (x, y), rendered, font=font, fill="white", spacing=10, align="center",
        stroke_width=2, stroke_fill=(0, 0, 0, 210),
    )


def prepare_photo(
    source_path: Path, output_path: Path, frame_size: tuple[int, int], rotation: int,
    background: Background, caption: str, font: FontPreset, text_position: TextPosition,
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
    canvas.paste(fitted, ((frame_width - fitted.width) // 2, (frame_height - fitted.height) // 2))
    _draw_text_panel(canvas, caption, text_position, font)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, "JPEG", quality=92, optimize=True)
    return PreparedImage(output_path, frame_width, frame_height)


def create_title_card(output_path: Path, frame_size: tuple[int, int], settings: SlideshowSettings) -> PreparedImage:
    canvas = Image.new("RGB", frame_size, (20, 26, 35))
    draw = ImageDraw.Draw(canvas)
    width, height = frame_size
    for y in range(height):
        shade = round(20 + 28 * y / max(1, height - 1))
        draw.line((0, y, width, y), fill=(shade, shade + 5, shade + 12))
    combined = settings.title
    if settings.subtitle:
        combined = f"{combined}\n{settings.subtitle}" if combined else settings.subtitle
    _draw_text_panel(canvas, combined, TextPosition.CENTRE, settings.font, size_multiplier=1.25)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output_path, "JPEG", quality=94)
    return PreparedImage(output_path, width, height)
