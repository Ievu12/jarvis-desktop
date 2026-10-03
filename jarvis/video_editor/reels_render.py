"""The one renderer of the Reels layers (jarvis.video_editor.reels):
draws them with Pillow onto a transparent RGBA layer at timeline time
`t`. The live preview calls it for every displayed frame and
jarvis.video_editor.reels_export calls it for every exported frame, so
both show the same pixels (only the size differs: `scale` is frame
width / 1080).

Drawing is split in two steps: plan() works out WHAT is on screen
(each word's text, color, place, size and opacity, each background
box) as a hashable tuple, and paint() draws a plan. Equal plans give
equal pixels, so the export renders a still stretch of video once and
reuses it."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter

from jarvis.video_editor import text_render
from jarvis.video_editor.reels import (
    REELS_CANVAS_WIDTH,
    Phrase,
    ReelsCaptionStyle,
    ReelsCaptions,
    ReelsLayers,
    ReelsWord,
    build_phrases,
    phrase_at,
)

_POP_SECONDS = 0.22
_WORD_POP_SECONDS = 0.18
_ACTIVE_EASE_SECONDS = 0.08


@dataclass(frozen=True)
class WordOp:
    text: str
    font_file: str
    size_tenths: int
    fill: tuple[int, int, int, int]
    weight_px: int
    outline_px: int
    outline_color: tuple[int, int, int, int]
    shadow_px: int
    shadow_color: tuple[int, int, int, int]
    blur_px: int
    center_x: float
    center_y: float
    scale: float
    alpha: float


@dataclass(frozen=True)
class RectOp:
    x0: float
    y0: float
    x1: float
    y1: float
    radius: float
    color: tuple[int, int, int, int]


@dataclass(frozen=True)
class CaptionBox:
    """The subtitle block's resting place in frame pixels (for selecting
    and dragging it in the preview)."""

    center_x: float
    center_y: float
    width: float
    height: float


def scale_for(frame_width: int, frame_height: int) -> float:
    """Pixels per Reels canvas pixel for a frame of this size (sizes are
    designed for the 1080 x 1920 canvas; other shapes scale by their
    shorter side)."""
    return min(frame_width, frame_height) / REELS_CANVAS_WIDTH


# --- easing ------------------------------------------------------------------------------------


def _clamp01(value: float) -> float:
    return 0.0 if value < 0 else 1.0 if value > 1 else value


def _ease_out_back(p: float) -> float:
    """0 -> 1 with a small overshoot (a "pop")."""
    p = _clamp01(p)
    c1 = 1.70158
    c3 = c1 + 1
    return 1 + c3 * (p - 1) ** 3 + c1 * (p - 1) ** 2


def _ease_out_cubic(p: float) -> float:
    p = _clamp01(p)
    return 1 - (1 - p) ** 3


def _r(value: float) -> float:
    return round(value, 2)


def _fade(rgba: tuple[int, int, int, int], alpha: float) -> tuple[int, int, int, int]:
    return rgba[0], rgba[1], rgba[2], round(rgba[3] * _clamp01(alpha))


# --- layout ------------------------------------------------------------------------------------


@lru_cache(maxsize=64)
def _phrases(words: tuple[ReelsWord, ...], style: ReelsCaptionStyle) -> tuple[Phrase, ...]:
    return tuple(build_phrases(words, style))


def _display_text(word: ReelsWord, style: ReelsCaptionStyle) -> str:
    return word.text.upper() if style.uppercase else word.text


def _word_size(word: ReelsWord, style: ReelsCaptionStyle, scale: float) -> float:
    return style.font_size * scale * (style.emphasis_scale if word.emphasized else 1.0)


@dataclass(frozen=True)
class _Slot:
    index: int
    """Index into ReelsCaptions.words."""
    center_x: float
    center_y: float
    width: float
    """Advance width."""
    height: float
    """Line height of this word's font (ascent + descent)."""
    size: float


@lru_cache(maxsize=256)
def _layout(
    words: tuple[ReelsWord, ...], first: int, last: int, style: ReelsCaptionStyle, frame_w: int, frame_h: int,
    scale: float,
) -> tuple[tuple[_Slot, ...], tuple[float, float, float, float]]:
    """Where every word of a phrase rests: wrapped into centered lines no
    wider than style.max_width_fraction of the frame, the block centered
    vertically on style.y_fraction. Returns (slots, block box x0,y0,x1,y1)."""
    base_font = text_render.load_font(style.font_file, style.font_size * scale)
    space = base_font.getlength(" ")
    max_width = frame_w * style.max_width_fraction

    measured = []
    for index in range(first, last + 1):
        word = words[index]
        size = _word_size(word, style, scale)
        font = text_render.load_font(style.font_file, size)
        ascent, descent = font.getmetrics()
        measured.append((index, font.getlength(_display_text(word, style)), ascent, descent, size))

    lines: list[list[tuple]] = [[]]
    width = 0.0
    for entry in measured:
        extra = entry[1] + (space if lines[-1] else 0)
        if lines[-1] and width + extra > max_width:
            lines.append([])
            width = 0.0
            extra = entry[1]
        lines[-1].append(entry)
        width += extra

    line_metrics = []
    for line in lines:
        ascent = max(e[2] for e in line)
        descent = max(e[3] for e in line)
        line_width = sum(e[1] for e in line) + space * (len(line) - 1)
        line_metrics.append((ascent, descent, line_width))
    line_gap = style.font_size * scale * 0.08
    block_h = sum(a + d for a, d, _ in line_metrics) + line_gap * (len(lines) - 1)
    block_w = max(w for _, _, w in line_metrics)
    top = frame_h * style.y_fraction - block_h / 2

    slots: list[_Slot] = []
    y = top
    for line, (ascent, descent, line_width) in zip(lines, line_metrics):
        baseline = y + ascent
        x = (frame_w - line_width) / 2
        for index, advance, word_ascent, word_descent, size in line:
            center_y = baseline - (word_ascent - word_descent) / 2
            slots.append(_Slot(index, x + advance / 2, center_y, advance, word_ascent + word_descent, size))
            x += advance + space
        y += ascent + descent + line_gap
    box = ((frame_w - block_w) / 2, top, (frame_w + block_w) / 2, top + block_h)
    return tuple(slots), box


# --- planning ----------------------------------------------------------------------------------


def _phrase_transform(style: ReelsCaptionStyle, local: float, frame_h: int) -> tuple[float, float, float]:
    """(scale, alpha, y offset) of the whole phrase `local` seconds after it appeared."""
    speed = style.animation_speed
    if style.animation == "pop":
        p = local / (_POP_SECONDS / speed)
        return 0.6 + 0.4 * _ease_out_back(p), _clamp01(local / (0.08 / speed)), 0.0
    if style.animation == "fade":
        return 1.0, _ease_out_cubic(local / (0.25 / speed)), 0.0
    if style.animation == "slide_up":
        p = _ease_out_cubic(local / (0.25 / speed))
        return 1.0, p, (1 - p) * frame_h * 0.04
    if style.animation == "zoom":
        p = _ease_out_cubic(local / (0.3 / speed))
        return 1.3 - 0.3 * p, p, 0.0
    return 1.0, 1.0, 0.0


def _caption_ops(captions: ReelsCaptions, t: float, frame_w: int, frame_h: int, scale: float) -> list:
    style = captions.style
    words = captions.words
    phrase = phrase_at(list(_phrases(words, style)), t)
    if phrase is None:
        return []
    slots, (bx0, by0, bx1, by1) = _layout(words, phrase.first, phrase.last, style, frame_w, frame_h, scale)
    local = t - phrase.start_seconds
    phrase_scale, phrase_alpha, phrase_dy = _phrase_transform(style, local, frame_h)
    if phrase_alpha <= 0:
        return []
    cx = frame_w / 2
    cy = frame_h * style.y_fraction

    def place(x: float, y: float) -> tuple[float, float]:
        return cx + (x - cx) * phrase_scale, cy + (y - cy) * phrase_scale + phrase_dy

    ops: list = []
    base = style.font_size * scale
    if style.background != "none":
        pad = base * 0.22
        x0, y0 = place(bx0 - pad, by0 - pad * 0.6)
        x1, y1 = place(bx1 + pad, by1 + pad * 0.6)
        radius = (y1 - y0) / 2 if style.background == "pill" else base * 0.12
        color = _fade(text_render.parse_color(style.background_color, default="black"), phrase_alpha)
        ops.append(RectOp(_r(x0), _r(y0), _r(x1), _r(y1), _r(min(radius, (y1 - y0) / 2)), color))

    text_color = text_render.parse_color(style.text_color)
    highlight = text_render.parse_color(style.highlight_color, default="yellow")
    active_color = text_render.parse_color(style.active_color, default="yellow")
    outline_color = text_render.parse_color(style.outline_color, default="black")
    shadow_color = text_render.parse_color(style.shadow_color, default="black")
    weight_px = round(style.weight * base / 40)
    outline_px = round(style.outline_width * scale)
    shadow_px = round(style.shadow_offset * scale)
    blur_px = round(style.shadow_blur * scale)
    speed = style.animation_speed

    for slot in slots:
        word = words[slot.index]
        if style.reveal != "phrase" and t < word.start_seconds:
            continue
        active = word.start_seconds <= t < word.end_seconds
        word_scale = 1.0
        dy = 0.0
        alpha = phrase_alpha
        if style.reveal == "word_pop":
            word_scale *= 0.4 + 0.6 * _ease_out_back((t - word.start_seconds) / (_WORD_POP_SECONDS / speed))
        fill = highlight if word.emphasized else text_color
        if active:
            ease = _ease_out_cubic((t - word.start_seconds) / (_ACTIVE_EASE_SECONDS / speed))
            effect = style.active_effect
            if effect == "color":
                fill = active_color
            elif effect == "scale":
                word_scale *= 1 + 0.18 * ease
            elif effect == "bounce":
                p = _clamp01((t - word.start_seconds) / (0.3 / speed))
                dy = -math.sin(math.pi * p) * slot.size * 0.18
            elif effect in ("box", "underline"):
                x, y = place(slot.center_x, slot.center_y)
                w = slot.width * phrase_scale
                h = slot.height * phrase_scale
                if effect == "box":
                    pad_x, pad_y = slot.size * 0.14 * phrase_scale, slot.size * 0.02 * phrase_scale
                    grow = 0.85 + 0.15 * ease
                    half_w, half_h = (w / 2 + pad_x) * grow, (h / 2 + pad_y) * grow
                    color = _fade(text_render.parse_color(style.box_color, default="purple"), alpha)
                    ops.append(RectOp(
                        _r(x - half_w), _r(y - half_h), _r(x + half_w), _r(y + half_h), _r(slot.size * 0.16), color,
                    ))
                else:
                    thickness = max(2.0, slot.size * 0.08) * phrase_scale
                    under_y = y + h * 0.42
                    half_w = w / 2 * ease
                    ops.append(RectOp(
                        _r(x - half_w), _r(under_y), _r(x + half_w), _r(under_y + thickness), _r(thickness / 2),
                        _fade(active_color, alpha),
                    ))
        x, y = place(slot.center_x, slot.center_y + dy)
        ops.append(WordOp(
            text=_display_text(word, style), font_file=style.font_file, size_tenths=max(10, round(slot.size * 10)),
            fill=fill, weight_px=weight_px, outline_px=outline_px, outline_color=outline_color, shadow_px=shadow_px,
            shadow_color=shadow_color, blur_px=blur_px, center_x=_r(x), center_y=_r(y),
            scale=_r(phrase_scale * word_scale), alpha=_r(alpha),
        ))
    return ops


def plan(layers: ReelsLayers | None, *, t: float, frame_width: int, frame_height: int, scale: float) -> tuple:
    """Everything drawn at time `t`, as a hashable tuple of ops."""
    if layers is None:
        return ()
    ops: list = []
    captions = layers.captions
    if captions is not None and captions.visible and captions.words:
        ops.extend(_caption_ops(captions, t, frame_width, frame_height, scale))
    return tuple(ops)


# --- painting ----------------------------------------------------------------------------------


@lru_cache(maxsize=512)
def _word_block(
    text: str, font_file: str, size_tenths: int, fill: tuple, weight_px: int, outline_px: int, outline_color: tuple,
    shadow_px: int, shadow_color: tuple, blur_px: int,
) -> Image.Image:
    """The word drawn alone on a transparent image whose CENTER is the
    center of the word's layout box (advance width x ascent+descent)."""
    font = text_render.load_font(font_file, size_tenths / 10)
    ascent, descent = font.getmetrics()
    advance = font.getlength(text)
    stroke = outline_px + weight_px
    pad = stroke + abs(shadow_px) + blur_px * 2 + round(size_tenths / 10 * 0.25) + 2
    width = max(1, math.ceil(advance) + pad * 2)
    height = max(1, ascent + descent + pad * 2)
    origin = (pad, pad + ascent)
    block = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    if shadow_color[3] > 0 and (shadow_px or blur_px):
        shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).text(
            (origin[0] + shadow_px, origin[1] + shadow_px), text, font=font, anchor="ls", fill=shadow_color,
            stroke_width=stroke, stroke_fill=shadow_color,
        )
        if blur_px:
            shadow = shadow.filter(ImageFilter.GaussianBlur(blur_px))
        block.alpha_composite(shadow)
    draw_layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(draw_layer)
    if outline_px:
        draw.text(origin, text, font=font, anchor="ls", fill=outline_color, stroke_width=stroke, stroke_fill=outline_color)
    draw.text(origin, text, font=font, anchor="ls", fill=fill, stroke_width=weight_px, stroke_fill=fill if weight_px else None)
    block.alpha_composite(draw_layer)
    return block


def _with_alpha(image: Image.Image, alpha: float) -> Image.Image:
    if alpha >= 1.0:
        return image
    faded = image.copy()
    faded.putalpha(image.getchannel("A").point(lambda a: round(a * alpha)))
    return faded


def _paste_center(layer: Image.Image, image: Image.Image, center_x: float, center_y: float) -> None:
    x = round(center_x - image.width / 2)
    y = round(center_y - image.height / 2)
    if x >= layer.width or y >= layer.height or x + image.width <= 0 or y + image.height <= 0:
        return
    left, top = max(0, -x), max(0, -y)
    right, bottom = min(image.width, layer.width - x), min(image.height, layer.height - y)
    layer.alpha_composite(image.crop((left, top, right, bottom)), (x + left, y + top))


def paint(layer: Image.Image, ops: tuple) -> None:
    """Draws a plan() onto the RGBA `layer`."""
    for op in ops:
        if isinstance(op, RectOp):
            if op.x1 <= op.x0 or op.y1 <= op.y0 or op.color[3] == 0:
                continue
            shape = Image.new("RGBA", layer.size, (0, 0, 0, 0))
            ImageDraw.Draw(shape).rounded_rectangle(
                [round(op.x0), round(op.y0), round(op.x1), round(op.y1)], radius=round(op.radius), fill=op.color,
            )
            layer.alpha_composite(shape)
        elif isinstance(op, WordOp):
            if op.alpha <= 0 or op.scale <= 0.01:
                continue
            block = _word_block(
                op.text, op.font_file, op.size_tenths, op.fill, op.weight_px, op.outline_px, op.outline_color,
                op.shadow_px, op.shadow_color, op.blur_px,
            )
            if abs(op.scale - 1.0) > 0.005:
                size = (max(1, round(block.width * op.scale)), max(1, round(block.height * op.scale)))
                block = block.resize(size, Image.Resampling.BICUBIC)
            _paste_center(layer, _with_alpha(block, op.alpha), op.center_x, op.center_y)


def draw(layer: Image.Image, layers: ReelsLayers | None, *, t: float, scale: float) -> None:
    """plan() + paint() onto `layer` (whose size is the frame size)."""
    paint(layer, plan(layers, t=t, frame_width=layer.width, frame_height=layer.height, scale=scale))


def render_frame(layers: ReelsLayers | None, *, t: float, width: int, height: int, scale: float) -> Image.Image:
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw(layer, layers, t=t, scale=scale)
    return layer


def caption_box(
    layers: ReelsLayers | None, *, t: float, frame_width: int, frame_height: int, scale: float,
) -> CaptionBox | None:
    """Where the subtitle phrase showing at `t` rests, or None."""
    if layers is None or layers.captions is None or not layers.captions.visible or not layers.captions.words:
        return None
    captions = layers.captions
    phrase = phrase_at(list(_phrases(captions.words, captions.style)), t)
    if phrase is None:
        return None
    _slots, (x0, y0, x1, y1) = _layout(
        captions.words, phrase.first, phrase.last, captions.style, frame_width, frame_height, scale,
    )
    return CaptionBox((x0 + x1) / 2, (y0 + y1) / 2, x1 - x0, y1 - y0)
