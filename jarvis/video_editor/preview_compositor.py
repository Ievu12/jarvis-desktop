"""Real-time overlay compositing for the Video Editor's live preview:
draws every text overlay, sticker/GIF and edited caption line on top of
one decoded video frame with Pillow, in milliseconds, so an edit shows
up on screen immediately - while playing, while dragging an element,
while moving a slider.

Every visual rule here is a port of the ffmpeg filter expression the
export builds for the same element (jarvis.video_editor.text_overlay,
.stickers, .captions), evaluated at one timestamp instead of handed to
ffmpeg. The export stays the source of truth: when playback is paused,
the dashboard also renders the exact export frame through ffmpeg
(jarvis.video_editor.live_preview) and swaps it in, and
tests/test_video_editor_preview_compositor.py compares the two
pixel-by-pixel for the common cases.

Coordinates: overlays store sizes in pixels of the export canvas
(`canvas_width` x `canvas_height`, the 1080p canvas of the timeline's
aspect ratio) and positions as fractions. The preview frame is smaller,
so every pixel quantity is multiplied by `scale = frame_width /
canvas_width` before drawing."""

from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from PIL import Image, ImageSequence

from jarvis.video_editor import reels_render
from jarvis.video_editor import text_overlay as text_overlay_module
from jarvis.video_editor import text_render
from jarvis.video_editor.captions import CaptionLine, CaptionStyle, caption_font_file
from jarvis.video_editor.reels import ReelsLayers
from jarvis.video_editor.stickers import (
    StickerInstance,
    blink_dim_factor,
    blink_period_seconds,
    builtin_sticker_image,
    is_animated_gif,
    sticker_fade_seconds,
)
from jarvis.video_editor.text_overlay import TextOverlay

ElementKind = Literal["text", "sticker", "reels_caption"]


@dataclass(frozen=True)
class Scene:
    """Everything drawn on top of the timeline's own video."""

    text_overlays: tuple[TextOverlay, ...] = ()
    stickers: tuple[StickerInstance, ...] = ()
    caption_style: CaptionStyle | None = None
    caption_lines: tuple[CaptionLine, ...] | None = None
    reels: ReelsLayers | None = None
    """Reels mode layers, drawn on top of everything else by
    jarvis.video_editor.reels_render (the same code the export uses)."""


@dataclass(frozen=True)
class ElementBox:
    """An element's resting geometry (animation offsets ignored, so
    handles don't jitter while an element bounces) in FRAME pixels -
    what the interactive preview hit-tests and draws handles around."""

    kind: ElementKind
    index: int
    """Index into Scene.text_overlays / Scene.stickers (0 for the Reels subtitles)."""
    center_x: float
    center_y: float
    width: float
    height: float
    rotation_degrees: float
    """Clockwise."""

    def contains(self, x: float, y: float, *, margin: float = 0.0) -> bool:
        local_x, local_y = self.to_local(x, y)
        return abs(local_x) <= self.width / 2 + margin and abs(local_y) <= self.height / 2 + margin

    def to_local(self, x: float, y: float) -> tuple[float, float]:
        """(x, y) relative to the center, un-rotated into the element's
        own axes."""
        angle = math.radians(self.rotation_degrees)
        dx, dy = x - self.center_x, y - self.center_y
        return dx * math.cos(angle) + dy * math.sin(angle), -dx * math.sin(angle) + dy * math.cos(angle)

    def corners(self) -> list[tuple[float, float]]:
        """Clockwise from top-left, in frame pixels."""
        angle = math.radians(self.rotation_degrees)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        half_w, half_h = self.width / 2, self.height / 2
        return [
            (self.center_x + lx * cos_a - ly * sin_a, self.center_y + lx * sin_a + ly * cos_a)
            for lx, ly in ((-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h))
        ]


def _visible(start: float, end: float, t: float) -> bool:
    # ffmpeg's between(t,start,end) is inclusive at both ends.
    return start <= t <= end


# --- stickers ------------------------------------------------------------------------------


@lru_cache(maxsize=128)
def _builtin_image(shape: str, tint: tuple[int, int, int] | None) -> Image.Image:
    return builtin_sticker_image(shape, tint=tint)


@lru_cache(maxsize=32)
def _custom_frames(path_text: str, mtime: float) -> tuple[tuple[Image.Image, ...], tuple[float, ...]]:
    """(frames, frame_durations_seconds) - one frame for a still image."""
    with Image.open(path_text) as image:
        if not is_animated_gif(Path(path_text)) or getattr(image, "n_frames", 1) <= 1:
            return (image.convert("RGBA"),), (1.0,)
        frames, durations = [], []
        for frame in ImageSequence.Iterator(image):
            frames.append(frame.convert("RGBA"))
            # GIF frame delays are in ms; browsers and ffmpeg's GIF
            # demuxer both treat a 0 delay as 0.1 s.
            durations.append((frame.info.get("duration") or 100) / 1000)
        return tuple(frames), tuple(durations)


def _sticker_source(sticker: StickerInstance, t: float) -> Image.Image | None:
    if sticker.custom_path is None:
        if sticker.shape is None:
            return None
        return _builtin_image(sticker.shape, sticker.tint)
    try:
        frames, durations = _custom_frames(str(sticker.custom_path), sticker.custom_path.stat().st_mtime)
    except OSError:
        return None
    if len(frames) == 1:
        return frames[0]
    # Loops from the sticker's own start, matching the export's
    # -ignore_loop 0 + setpts shift (see stickers.build_sticker_filter()).
    position = (t - sticker.start_seconds) % sum(durations)
    for frame, duration in zip(frames, durations):
        if position < duration:
            return frame
        position -= duration
    return frames[-1]


def _sticker_alpha(sticker: StickerInstance, t: float) -> float:
    alpha = sticker.opacity
    local = t - sticker.start_seconds
    if sticker.animation == "fade_in_out":
        fade = sticker_fade_seconds(sticker)
        alpha *= max(0.0, min(1.0, local / fade, (sticker.end_seconds - t) / fade))
    elif sticker.animation == "blink":
        period = blink_period_seconds(sticker)
        if (local % period) >= period / 2:
            alpha = sticker.opacity * blink_dim_factor(sticker)
    if sticker.fade_in_seconds > 0:
        alpha *= max(0.0, min(1.0, local / sticker.fade_in_seconds))
    if sticker.fade_out_seconds > 0:
        alpha *= max(0.0, min(1.0, (sticker.end_seconds - t) / sticker.fade_out_seconds))
    return alpha


def _sticker_rotation(sticker: StickerInstance, t: float) -> float:
    if sticker.animation == "spin":
        return (t - sticker.start_seconds) * 360.0 * sticker.animation_speed
    return sticker.rotation_degrees


def _sticker_y_offset(sticker: StickerInstance, t: float) -> float:
    """Canvas pixels, mirroring stickers._animation_overlay_expressions()."""
    local = t - sticker.start_seconds
    speed, strength = sticker.animation_speed, sticker.animation_intensity
    if sticker.animation == "pop_in":
        pop = 0.15 / speed
        return 20 * strength * (1 - local / pop) if local < pop else 0.0
    if sticker.animation == "float":
        return 8 * strength * math.sin(local * 2 * speed)
    if sticker.animation == "bounce":
        return -abs(15 * strength * math.sin(local * 4 * speed))
    return 0.0


def _sticker_side_px(sticker: StickerInstance, canvas_width: int) -> int:
    return int(sticker.size_fraction * canvas_width)


def _paste(layer: Image.Image, image: Image.Image, x: float, y: float) -> None:
    """alpha_composite() rejects negative destinations - crop instead,
    since elements partly off-screen are normal while dragging."""
    left, top = int(math.floor(x)), int(math.floor(y))
    crop_left, crop_top = max(0, -left), max(0, -top)
    if crop_left >= image.width or crop_top >= image.height:
        return
    if left >= layer.width or top >= layer.height:
        return
    if crop_left or crop_top:
        image = image.crop((crop_left, crop_top, image.width, image.height))
    layer.alpha_composite(image, dest=(max(0, left), max(0, top)))


# --- text overlays ---------------------------------------------------------------------------


def _text_font_size(overlay: TextOverlay, t: float, base_size: float) -> float:
    """Mirrors text_overlay._scaling_text_clause()'s fontsize expressions."""
    start, end = overlay.start_seconds, overlay.end_seconds
    if overlay.animation == "pop_up":
        ramp = max(0.05, 0.2 / overlay.speed)
        return base_size * (0.3 + 0.7 * (t - start) / ramp) if t < start + ramp else base_size
    if overlay.animation == "pop_out":
        ramp = max(0.05, min(0.2 / overlay.speed, (end - start) / 2))
        ramp_start = end - ramp
        return base_size * (1 - 0.9 * (t - ramp_start) / ramp) if t > ramp_start else base_size
    if overlay.animation == "zoom":
        duration = max(0.1, end - start)
        return base_size * (1 + 0.5 * overlay.intensity * (t - start) / duration)
    return base_size


def _typewriter_text(overlay: TextOverlay, t: float) -> str:
    char_duration = 0.08 / overlay.speed
    count = min(len(overlay.text), int((t - overlay.start_seconds) // char_duration) + 1)
    return overlay.text[:max(0, count)]


def _fade_alpha(overlay: TextOverlay, t: float) -> float:
    start, end, fade = overlay.start_seconds, overlay.end_seconds, overlay.fade_seconds
    fade_out_start = max(start, end - fade)
    if t < start + fade:
        return max(0.0, (t - start) / fade)
    if t < fade_out_start:
        return 1.0
    return max(0.0, min(1.0, (end - t) / fade))


def _with_alpha(rgba: tuple[int, int, int, int], alpha: float) -> tuple[int, int, int, int]:
    return rgba[0], rgba[1], rgba[2], round(rgba[3] * alpha)


def _text_position(overlay: TextOverlay, metrics: text_render.TextMetrics, frame_w: int, frame_h: int) -> tuple[float, float]:
    return (frame_w - metrics.width) * overlay.x_fraction, (frame_h - metrics.height) * overlay.y_fraction


def _draw_text_overlay(layer: Image.Image, overlay: TextOverlay, *, t: float, scale: float) -> None:
    frame_w, frame_h = layer.size
    fill = text_render.parse_color(overlay.color)
    base_size = overlay.font_size * scale

    if overlay.is_rotated:
        _draw_rotated_text(layer, overlay, t=t, scale=scale, fill=fill)
        return

    text = _typewriter_text(overlay, t) if overlay.animation == "typewriter" else overlay.text
    font = text_render.load_font(overlay.font_file, _text_font_size(overlay, t, base_size))
    look = text_overlay_module.text_look(overlay, scale)
    metrics = text_render.measure(text, font)
    x, y = _text_position(overlay, metrics, frame_w, frame_h)
    local = t - overlay.start_seconds
    animation = overlay.animation

    # drawtext's alpha (fade x opacity) scales text, outline, shadow and box alike.
    alpha = (_fade_alpha(overlay, t) if animation == "fade" else 1.0) * overlay.opacity
    if alpha < 1.0:
        fill = _with_alpha(fill, alpha)
        look = look.faded(alpha)
    if animation in ("slide_in", "slide_out"):
        offset = (-1 if overlay.direction == "left" else 1) * 600 * scale
        if animation == "slide_in":
            duration = max(0.05, 0.35 / overlay.speed)
            if local < duration:
                x += offset * (1 - local / duration)
        else:
            duration = max(0.05, min(0.35 / overlay.speed, (overlay.end_seconds - overlay.start_seconds) / 2))
            slide_start = overlay.end_seconds - duration
            if t > slide_start:
                x += offset * (t - slide_start) / duration
    elif animation == "bounce":
        y -= 20 * overlay.intensity * scale * math.exp(-3 * local) * abs(math.sin(6 * overlay.speed * local))
    elif animation == "shake":
        x += 6 * overlay.intensity * scale * math.sin(25 * overlay.speed * local)
    elif animation == "glitch":
        jitter = 4 * overlay.intensity * scale * math.sin(37 * local) * math.sin(11 * local)
        offset = 3 * overlay.intensity * scale
        for color, extra in (("red@0.6", -offset), ("cyan@0.6", offset)):
            ghost = _with_alpha(text_render.parse_color(color), overlay.opacity)
            text_render.draw_text(layer, text, font=font, x=x + jitter + extra, y=y, fill=ghost)
        x += jitter
    elif animation == "glow":
        # The halo is drawtext's border, so it takes the outline's place.
        halo = max(1, round(6 * overlay.intensity))
        look = dataclasses.replace(look, outline_width=max(1, round(halo * scale)), outline_color=_with_alpha(fill, 0.5))

    text_render.draw_styled_text(layer, text, font=font, x=x, y=y, fill=fill, look=look)


def _draw_rotated_text(layer: Image.Image, overlay: TextOverlay, *, t: float, scale: float, fill) -> None:
    """Mirrors text_overlay.build_rotated_text_filters()."""
    look = text_overlay_module.text_look(overlay, scale)
    if overlay.animation == "fade":
        alpha = _fade_alpha(overlay, t)
        fill = _with_alpha(fill, alpha)
        look = look.faded(alpha)
    font = text_render.load_font(overlay.font_file, overlay.font_size * scale)
    block, metrics = text_render.render_text_block(overlay.text, font=font, fill=fill, look=look)
    rotated = block.rotate(-overlay.rotation_degrees, expand=True, resample=Image.Resampling.BICUBIC)
    if overlay.opacity < 1.0:  # the export's colorchannelmixer=aa on the whole rendered block
        rotated.putalpha(rotated.getchannel("A").point(lambda a: round(a * overlay.opacity)))
    center_x = (layer.width - metrics.width) * overlay.x_fraction + metrics.width / 2
    center_y = (layer.height - metrics.height) * overlay.y_fraction + metrics.height / 2
    _paste(layer, rotated, center_x - rotated.width / 2, center_y - rotated.height / 2)


# --- captions ------------------------------------------------------------------------------


def _draw_caption_line(layer: Image.Image, line: CaptionLine, style: CaptionStyle, *, scale: float) -> None:
    """Mirrors captions.build_caption_filter_from_lines() + _style_suffix()."""
    frame_w, frame_h = layer.size
    font = text_render.load_font(caption_font_file(style), style.font_size * scale)
    metrics = text_render.measure(line.text, font)
    x = (frame_w - metrics.width) / 2
    if style.position == "top":
        y = frame_h * 0.1
    elif style.position == "center":
        y = (frame_h - metrics.height) / 2
    else:
        y = frame_h * 0.8

    stroke = round(style.outline_width * scale) if style.outline_width > 0 else 0
    look = text_render.TextLook(
        outline_width=stroke,
        outline_color=text_render.parse_color(style.outline_color, default="black"),
        shadow_offset=round(style.shadow_offset * scale),
        shadow_color=text_render.parse_color(style.shadow_color, default="black"),
        box_padding=round(10 * scale) if style.background else 0,
        box_color=(0, 0, 0, 128) if style.background else None,
    )
    text_render.draw_styled_text(
        layer, line.text, font=font, x=x, y=y, fill=text_render.parse_color(style.color), look=look,
    )


# --- public API ----------------------------------------------------------------------------


def compose(base: Image.Image, scene: Scene, *, t: float, canvas_width: int, canvas_height: int) -> Image.Image:
    """Returns a new RGB image: `base` (one decoded frame at timeline
    time `t`) with every overlay of `scene` that is visible at `t`
    drawn on top, in the export's own stacking order - captions, then
    plain text, then rotated text, then stickers, then the Reels layers."""
    scale = base.width / canvas_width
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))

    if scene.caption_style is not None and scene.caption_lines:
        for line in scene.caption_lines:
            if _visible(line.start_seconds, line.end_seconds, t):
                _draw_caption_line(layer, line, scene.caption_style, scale=scale)

    texts = [o for o in scene.text_overlays if _visible(o.start_seconds, o.end_seconds, t)]
    for overlay in [o for o in texts if not o.is_rotated] + [o for o in texts if o.is_rotated]:
        _draw_text_overlay(layer, overlay, t=t, scale=scale)

    for sticker in scene.stickers:
        if _visible(sticker.start_seconds, sticker.end_seconds, t):
            _draw_sticker_safe(layer, sticker, t=t, canvas_width=canvas_width, canvas_height=canvas_height, scale=scale)

    if scene.reels is not None:
        reels_render.draw(layer, scene.reels, t=t, scale=reels_render.scale_for(*base.size))

    result = base.convert("RGBA")
    result.alpha_composite(layer)
    return result.convert("RGB")


def _draw_sticker_safe(layer, sticker, **kwargs) -> None:
    source_layer = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    try:
        _draw_sticker_on(source_layer, sticker, **kwargs)
    except (OSError, ValueError):
        return  # an unreadable custom image is reported by the panel's own validation, never a preview crash
    layer.alpha_composite(source_layer)


def _draw_sticker_on(layer: Image.Image, sticker: StickerInstance, *, t: float, canvas_width: int, canvas_height: int, scale: float) -> None:
    source = _sticker_source(sticker, t)
    if source is None:
        return
    side = max(1, round(_sticker_side_px(sticker, canvas_width) * scale))
    image = source.resize((side, side), Image.Resampling.BICUBIC)
    alpha = _sticker_alpha(sticker, t)
    if alpha < 1.0:
        image.putalpha(image.getchannel("A").point(lambda a: round(a * alpha)))
    rotation = _sticker_rotation(sticker, t)
    if rotation % 360 != 0:
        image = image.rotate(-rotation, expand=True, resample=Image.Resampling.BICUBIC)
    center_x = sticker.x_fraction * canvas_width * scale
    center_y = (sticker.y_fraction * canvas_height + _sticker_y_offset(sticker, t)) * scale
    _paste(layer, image, center_x - image.width / 2, center_y - image.height / 2)


def element_boxes(scene: Scene, *, t: float, frame_width: int, frame_height: int, canvas_width: int) -> list[ElementBox]:
    """Resting boxes of every text overlay and sticker visible at `t`,
    BOTTOM-most first (iterate reversed() to hit-test top-most first)."""
    scale = frame_width / canvas_width
    canvas_height = round(frame_height / scale)
    boxes: list[ElementBox] = []

    texts = [(i, o) for i, o in enumerate(scene.text_overlays) if _visible(o.start_seconds, o.end_seconds, t)]
    for index, overlay in [p for p in texts if not p[1].is_rotated] + [p for p in texts if p[1].is_rotated]:
        font = text_render.load_font(overlay.font_file, overlay.font_size * scale)
        metrics = text_render.measure(overlay.text, font)
        x, y = _text_position(overlay, metrics, frame_width, frame_height)
        boxes.append(ElementBox(
            kind="text", index=index, center_x=x + metrics.width / 2, center_y=y + metrics.height / 2,
            width=max(metrics.width, 8.0), height=max(metrics.height, 8.0), rotation_degrees=overlay.rotation_degrees,
        ))

    for index, sticker in enumerate(scene.stickers):
        if not _visible(sticker.start_seconds, sticker.end_seconds, t):
            continue
        side = _sticker_side_px(sticker, canvas_width) * scale
        boxes.append(ElementBox(
            kind="sticker", index=index, center_x=sticker.x_fraction * canvas_width * scale,
            center_y=sticker.y_fraction * canvas_height * scale, width=side, height=side,
            rotation_degrees=sticker.rotation_degrees,
        ))

    caption = reels_render.caption_box(
        scene.reels, t=t, frame_width=frame_width, frame_height=frame_height,
        scale=reels_render.scale_for(frame_width, frame_height),
    )
    if caption is not None:
        boxes.append(ElementBox(
            kind="reels_caption", index=0, center_x=caption.center_x, center_y=caption.center_y,
            width=caption.width, height=caption.height, rotation_degrees=0.0,
        ))
    return boxes


def hit_test(boxes: list[ElementBox], x: float, y: float, *, margin: float = 4.0) -> ElementBox | None:
    for box in reversed(boxes):
        if box.contains(x, y, margin=margin):
            return box
    return None


def text_fractions_for_center(
    overlay: TextOverlay, *, center_x: float, center_y: float, frame_width: int, frame_height: int, scale: float,
) -> tuple[float, float]:
    """The (x_fraction, y_fraction) that puts `overlay`'s center at
    (center_x, center_y) frame pixels - the inverse of the drawtext
    position rule `x = (w - text_w) * x_fraction`, clamped to 0-1."""
    font = text_render.load_font(overlay.font_file, overlay.font_size * scale)
    metrics = text_render.measure(overlay.text, font)

    def solve(center: float, size: float, frame: float) -> float:
        free = frame - size
        if free <= 0:
            return 0.5
        return max(0.0, min(1.0, (center - size / 2) / free))

    return solve(center_x, metrics.width, frame_width), solve(center_y, metrics.height, frame_height)
