"""Drawing of the Reels pop-up text cards and picture/video inserts, for
jarvis.video_editor.reels_render (which calls plan_*() while working out
a frame and paint_*() while drawing it). Enter/exit animations are
shared by both kinds of element."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

from jarvis.video_editor import reels_media, text_render
from jarvis.video_editor.reels import MediaInsert, TextCard

_SUPERSAMPLE = 2


def _clamp01(value: float) -> float:
    return 0.0 if value < 0 else 1.0 if value > 1 else value


def _ease_out_cubic(p: float) -> float:
    p = _clamp01(p)
    return 1 - (1 - p) ** 3


def _ease_in_cubic(p: float) -> float:
    p = _clamp01(p)
    return p ** 3


def _ease_out_back(p: float) -> float:
    p = _clamp01(p)
    c1 = 1.70158
    return 1 + (c1 + 1) * (p - 1) ** 3 + c1 * (p - 1) ** 2


def _ease_out_bounce(p: float) -> float:
    p = _clamp01(p)
    n1, d1 = 7.5625, 2.75
    if p < 1 / d1:
        return n1 * p * p
    if p < 2 / d1:
        p -= 1.5 / d1
        return n1 * p * p + 0.75
    if p < 2.5 / d1:
        p -= 2.25 / d1
        return n1 * p * p + 0.9375
    p -= 2.625 / d1
    return n1 * p * p + 0.984375


@dataclass(frozen=True)
class Motion:
    """How an element looks at one moment of its enter/exit animation."""

    dx: float = 0.0
    dy: float = 0.0
    """In frame pixels."""
    scale: float = 1.0
    alpha: float = 1.0
    reveal: float = 1.0
    """Share of the text shown (typewriter)."""


def motion(element, t: float, frame_w: int, frame_h: int) -> Motion | None:
    """The element's animation state at `t`, or None when it isn't on screen."""
    if not element.start_seconds <= t < element.end_seconds:
        return None
    duration = element.end_seconds - element.start_seconds
    enter_s = min(element.enter_seconds, duration / 2)
    exit_s = min(element.exit_seconds, duration / 2)
    local = t - element.start_seconds
    remaining = element.end_seconds - t
    dx = dy = 0.0
    scale = alpha = reveal = 1.0

    if enter_s > 0 and local < enter_s and element.enter_animation != "none":
        p = local / enter_s
        kind = element.enter_animation
        e = _ease_out_cubic(p)
        if kind == "pop":
            scale, alpha = 0.3 + 0.7 * _ease_out_back(p), _clamp01(p * 3)
        elif kind == "fade":
            alpha = e
        elif kind == "zoom":
            scale, alpha = 1.6 - 0.6 * e, e
        elif kind == "slide_left":
            dx, alpha = -(1 - e) * frame_w * 0.6, _clamp01(p * 2)
        elif kind == "slide_right":
            dx, alpha = (1 - e) * frame_w * 0.6, _clamp01(p * 2)
        elif kind == "slide_up":
            dy, alpha = (1 - e) * frame_h * 0.25, _clamp01(p * 2)
        elif kind == "slide_down":
            dy, alpha = -(1 - e) * frame_h * 0.25, _clamp01(p * 2)
        elif kind == "bounce":
            dy, alpha = -(1 - _ease_out_bounce(p)) * frame_h * 0.15, _clamp01(p * 4)
        elif kind == "typewriter":
            reveal = p

    if exit_s > 0 and remaining < exit_s and element.exit_animation != "none":
        q = 1 - remaining / exit_s
        kind = element.exit_animation
        e = _ease_in_cubic(q)
        if kind == "fade":
            alpha *= 1 - q
        elif kind == "pop":
            scale *= 1 - 0.7 * e
            alpha *= 1 - _clamp01((q - 0.6) / 0.4)
        elif kind == "zoom":
            scale *= 1 + 0.6 * e
            alpha *= 1 - q
        elif kind == "slide_left":
            dx -= e * frame_w * 0.6
        elif kind == "slide_right":
            dx += e * frame_w * 0.6
        elif kind == "slide_up":
            dy -= e * frame_h * 0.25
        elif kind == "slide_down":
            dy += e * frame_h * 0.25
        elif kind == "typewriter":
            reveal = min(reveal, 1 - q)
    return Motion(round(dx, 2), round(dy, 2), round(scale, 3), round(alpha * element.opacity, 3), reveal)


def _rgba(color: str, default: str = "white") -> tuple[int, int, int, int]:
    return text_render.parse_color(color, default=default)


# --- cards ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CardOp:
    card: TextCard
    text: str
    """The text shown right now (typewriter shows part of it)."""
    size_scale: float
    """Pixels per canvas pixel times the card's own size."""
    center_x: float
    center_y: float
    scale: float
    alpha: float
    rotation: float


@lru_cache(maxsize=128)
def _card_metrics(card_text: str, font_file: str, size_tenths: int, design: str) -> tuple[float, float, float, float]:
    """(card width, card height, text start x, baseline y) at this font size."""
    font = text_render.load_font(font_file, size_tenths / 10)
    size = size_tenths / 10
    ascent, descent = font.getmetrics()
    pad_x, pad_y = size * 0.6, size * 0.34
    left = pad_x + (size * 0.55 if design == "tag" else 0) + (size * 0.25 if design == "ribbon" else 0)
    right = pad_x + (size * 0.25 if design == "ribbon" else 0)
    width = left + font.getlength(card_text) + right
    height = ascent + descent + pad_y * 2
    return width, height, left, pad_y + ascent


def card_size(card: TextCard, size_scale: float) -> tuple[float, float]:
    """The card's resting size in frame pixels (full text)."""
    size_tenths = max(10, round(card.font_size * size_scale * 10))
    width, height, _x, _y = _card_metrics(card.shown_text or " ", card.font_file, size_tenths, card.design)
    return width, height


def _shape(draw: ImageDraw.ImageDraw, design: str, box, radius: float, **kwargs) -> None:
    x0, y0, x1, y1 = box
    if design == "ribbon":
        notch = (y1 - y0) * 0.28
        mid = (y0 + y1) / 2
        draw.polygon([(x0, y0), (x1, y0), (x1 - notch, mid), (x1, y1), (x0, y1), (x0 + notch, mid)],
                     fill=kwargs.get("fill"), outline=kwargs.get("outline"))
    else:
        draw.rounded_rectangle(box, radius=radius, **kwargs)


@lru_cache(maxsize=256)
def _card_image(card: TextCard, text: str, size_scale: float) -> Image.Image:
    """The card drawn alone (shadow/glow margin around it), centered on
    the card's center."""
    size = card.font_size * size_scale
    size_tenths = max(10, round(size * 10))
    font_file = card.font_file
    width, height, text_x, baseline = _card_metrics(card.shown_text or " ", font_file, size_tenths, card.design)
    margin = math.ceil(size * 0.6)
    s = _SUPERSAMPLE
    full = (round((width + margin * 2) * s), round((height + margin * 2) * s))
    box = (margin * s, margin * s, (margin + width) * s, (margin + height) * s)
    design = card.design
    radius = {"pill": height / 2, "tag": height * 0.18, "glass": height * 0.3, "neon": height * 0.3,
              "outline": height * 0.25}.get(design, 0) * s
    background = _rgba(card.background_color, "black")
    accent = _rgba(card.accent_color, "yellow")
    line = max(2.0, size * 0.07) * s

    image = Image.new("RGBA", full, (0, 0, 0, 0))
    if card.shadow and design in ("pill", "tag", "ribbon", "glass"):
        shadow = Image.new("RGBA", full, (0, 0, 0, 0))
        offset = size * 0.12 * s
        shifted = (box[0], box[1] + offset, box[2], box[3] + offset)
        _shape(ImageDraw.Draw(shadow), design, shifted, radius, fill=(0, 0, 0, 110))
        image.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(size * 0.18 * s)))
    if design == "neon":
        glow = Image.new("RGBA", full, (0, 0, 0, 0))
        ImageDraw.Draw(glow).rounded_rectangle(box, radius=radius, outline=accent, width=round(line * 2.2))
        image.alpha_composite(glow.filter(ImageFilter.GaussianBlur(size * 0.22 * s)))

    shape = Image.new("RGBA", full, (0, 0, 0, 0))
    draw = ImageDraw.Draw(shape)
    if design == "outline":
        _shape(draw, design, box, radius, outline=accent, width=round(line))
    elif design in ("glass", "neon"):
        _shape(draw, design, box, radius, fill=background, outline=accent, width=round(line * (0.6 if design == "glass" else 1)))
    else:
        _shape(draw, design, box, radius, fill=background)
    if design == "tag":
        dot = size * 0.32 * s
        cx = box[0] + (size * 0.6 + size * 0.2) * s
        cy = (box[1] + box[3]) / 2
        draw.ellipse((cx - dot / 2, cy - dot / 2, cx + dot / 2, cy + dot / 2), fill=accent)
    image.alpha_composite(shape)

    if text:
        font = text_render.load_font(font_file, size_tenths / 10 * s)
        text_layer = Image.new("RGBA", full, (0, 0, 0, 0))
        ImageDraw.Draw(text_layer).text(
            (box[0] + text_x * s, box[1] + baseline * s), text, font=font, anchor="ls", fill=_rgba(card.text_color),
        )
        image.alpha_composite(text_layer)
    return image.resize((max(1, round(full[0] / s)), max(1, round(full[1] / s))), Image.Resampling.LANCZOS)


def plan_card(card: TextCard, t: float, frame_w: int, frame_h: int, scale: float) -> CardOp | None:
    m = motion(card, t, frame_w, frame_h)
    if m is None or m.alpha <= 0:
        return None
    text = card.shown_text
    if m.reveal < 1.0:
        text = text[:round(len(text) * m.reveal)]
    return CardOp(
        card=card, text=text, size_scale=round(scale * card.scale, 4),
        center_x=round(card.x_fraction * frame_w + m.dx, 2), center_y=round(card.y_fraction * frame_h + m.dy, 2),
        scale=m.scale, alpha=m.alpha, rotation=card.rotation_degrees,
    )


def card_image(op: CardOp) -> Image.Image:
    return _card_image(op.card, op.text, op.size_scale)


# --- inserts -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class InsertOp:
    insert: MediaInsert
    width: int
    height: int
    """Picture size in frame pixels (before the animation scale)."""
    source_time: float
    """Video: time in the video file; photo: 0."""
    size_scale: float
    center_x: float
    center_y: float
    scale: float
    alpha: float
    rotation: float


def insert_size(insert: MediaInsert, frame_w: int) -> tuple[int, int]:
    width = max(2, round(insert.width_fraction * frame_w))
    return width, max(2, round(width / max(0.05, insert.aspect_ratio)))


def plan_insert(insert: MediaInsert, t: float, frame_w: int, frame_h: int, scale: float) -> InsertOp | None:
    m = motion(insert, t, frame_w, frame_h)
    if m is None or m.alpha <= 0:
        return None
    width, height = insert_size(insert, frame_w)
    source_time = 0.0
    if insert.kind == "video":
        source_time = round(insert.source_start_seconds + (t - insert.start_seconds), 3)
    return InsertOp(
        insert=insert, width=width, height=height, source_time=source_time, size_scale=round(scale, 4),
        center_x=round(insert.x_fraction * frame_w + m.dx, 2), center_y=round(insert.y_fraction * frame_h + m.dy, 2),
        scale=m.scale, alpha=m.alpha, rotation=insert.rotation_degrees,
    )


@lru_cache(maxsize=64)
def _mask(width: int, height: int, radius_fraction: float) -> Image.Image:
    s = _SUPERSAMPLE
    mask = Image.new("L", (width * s, height * s), 0)
    radius = radius_fraction * min(width, height) * s
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width * s - 1, height * s - 1), radius=radius, fill=255)
    return mask.resize((width, height), Image.Resampling.LANCZOS)


def _framed(picture: Image.Image, op: InsertOp) -> Image.Image:
    """`picture` (already op.width x op.height) with rounded corners,
    border and shadow, on a transparent image with room for the shadow."""
    insert = op.insert
    width, height = op.width, op.height
    margin = round(max(width, height) * 0.08) if insert.shadow > 0 else 0
    canvas = Image.new("RGBA", (width + margin * 2, height + margin * 2), (0, 0, 0, 0))
    mask = _mask(width, height, insert.corner_radius)
    if insert.shadow > 0:
        shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        dark = Image.new("RGBA", (width, height), (0, 0, 0, round(150 * min(1.0, insert.shadow))))
        shadow.paste(dark, (margin, margin + round(height * 0.03)), mask)
        canvas.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(max(1, margin * 0.45))))
    picture = picture.convert("RGBA")
    picture.putalpha(_multiply_alpha(picture, mask))
    canvas.alpha_composite(picture, (margin, margin))
    border = round(insert.border_width * op.size_scale)
    if border > 0:
        s = _SUPERSAMPLE
        ring = Image.new("RGBA", (width * s, height * s), (0, 0, 0, 0))
        radius = insert.corner_radius * min(width, height) * s
        ImageDraw.Draw(ring).rounded_rectangle(
            (0, 0, width * s - 1, height * s - 1), radius=radius, outline=_rgba(insert.border_color), width=border * s,
        )
        canvas.alpha_composite(ring.resize((width, height), Image.Resampling.LANCZOS), (margin, margin))
    return canvas


def _multiply_alpha(picture: Image.Image, mask: Image.Image) -> Image.Image:
    from PIL import ImageChops

    return ImageChops.multiply(picture.getchannel("A"), mask)


@lru_cache(maxsize=32)
def _framed_photo(insert: MediaInsert, width: int, height: int, size_scale: float, mtime: float) -> Image.Image | None:
    image = reels_media.load_image(insert.path)
    if image is None:
        return None
    picture = _cover(image, width, height)
    return _framed(picture, InsertOp(insert, width, height, 0.0, size_scale, 0, 0, 1, 1, 0))


def _cover(image: Image.Image, width: int, height: int) -> Image.Image:
    """`image` scaled to fill width x height, cropping what sticks out."""
    ratio = max(width / image.width, height / image.height)
    resized = image.resize((max(1, round(image.width * ratio)), max(1, round(image.height * ratio))),
                           Image.Resampling.LANCZOS)
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    return resized.crop((left, top, left + width, top + height))


def insert_image(op: InsertOp, frames: reels_media.VideoFrames) -> Image.Image | None:
    insert = op.insert
    if insert.kind == "image":
        try:
            from pathlib import Path

            mtime = Path(insert.path).stat().st_mtime
        except OSError:
            return None
        return _framed_photo(insert, op.width, op.height, op.size_scale, mtime)
    frame = frames.frame(insert.path, op.source_time, op.width, op.height)
    if frame is None:
        return None
    return _framed(frame, op)
