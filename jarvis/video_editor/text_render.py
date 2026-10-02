"""Pillow text rendering that matches ffmpeg `drawtext` placement - the
shared foundation for jarvis.video_editor.preview_compositor (the live,
per-frame preview) and the rotated-text export path in
jarvis.video_editor.text_overlay, so a text element is measured and
drawn by the SAME code in both places.

drawtext semantics this mirrors (measured against ffmpeg 6.1 with
Liberation Sans Bold, which is metrically identical to Arial Bold):
`text_w` is the advance width of the string, `text_h` is the height of
its ink bounding box, and the text is drawn so its ink top sits exactly
at `y` and its pen origin at `x`. Pillow's own getbbox() at anchor "la"
reports the same box, offset by the ascender gap, so drawing at
(x - bbox.x0, y - bbox.y0) lands on the same pixels (within ~1-2 px of
anti-aliasing difference).

Full Unicode (ąčęėįšųūž, emoji fallbacks aside) works because Pillow
renders straight from the TrueType file via FreeType, the same library
drawtext uses."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageColor, ImageDraw, ImageFont

_FALLBACK_FONT_FILES = (
    "C:/Windows/Fonts/arialbd.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)


@lru_cache(maxsize=256)
def _load_font(font_file: str, size_tenths: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    size = max(1.0, size_tenths / 10)
    candidates = (font_file, *_FALLBACK_FONT_FILES)
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default(size)


def load_font(font_file: str, size: float):
    """A cached FreeType font at `size` pixels (fractional sizes are
    kept to 0.1 px so a scaled-down preview font stays proportional).
    Falls back to a font that exists on this machine if `font_file`
    doesn't - the preview must never crash over a missing font; the
    exact export frame shows what ffmpeg itself will do."""
    return _load_font(font_file, max(10, round(size * 10)))


def parse_color(value: str, *, default: str = "white") -> tuple[int, int, int, int]:
    """Parses an ffmpeg color string ("white", "#FFD700", "0xFFD700",
    "black@0.6", "#FFFFFF80") into RGBA. Unknown values fall back to
    `default` rather than raising - a half-typed color in a text field
    must not break the preview."""
    text = (value or "").strip()
    alpha = 1.0
    if "@" in text:
        text, alpha_text = text.split("@", 1)
        try:
            alpha = int(alpha_text, 16) / 255 if alpha_text.lower().startswith("0x") else float(alpha_text)
        except ValueError:
            alpha = 1.0
    if text.lower().startswith("0x"):
        text = "#" + text[2:]
    try:
        rgba = ImageColor.getrgb(text)
    except ValueError:
        rgba = ImageColor.getrgb(default)
    if len(rgba) == 4:
        r, g, b, a = rgba
        alpha *= a / 255
    else:
        r, g, b = rgba
    return r, g, b, round(max(0.0, min(1.0, alpha)) * 255)


@dataclass(frozen=True)
class TextMetrics:
    width: float
    """drawtext's `text_w`."""
    height: float
    """drawtext's `text_h`."""
    offset_x: float
    offset_y: float
    """Where to draw with Pillow (anchor "la") relative to the drawtext
    (x, y) so the ink lands on the same pixels."""


def measure(text: str, font) -> TextMetrics:
    if not text:
        return TextMetrics(0.0, 0.0, 0.0, 0.0)
    x0, y0, x1, y1 = font.getbbox(text, anchor="la")
    return TextMetrics(width=float(x1 - x0), height=float(y1 - y0), offset_x=float(-x0), offset_y=float(-y0))


def draw_text(
    layer: Image.Image, text: str, *, font, x: float, y: float, fill: tuple[int, int, int, int],
    stroke_width: int = 0, stroke_fill: tuple[int, int, int, int] | None = None,
) -> None:
    """Draws `text` onto the RGBA `layer` with its drawtext-style
    top-left at (x, y). Semi-transparent fills are composited properly
    (drawn on their own layer first) instead of overwriting the pixels
    underneath the way ImageDraw does on an RGBA image."""
    if not text:
        return
    metrics = measure(text, font)
    position = (x + metrics.offset_x, y + metrics.offset_y)
    needs_own_layer = fill[3] < 255 or (stroke_fill is not None and stroke_fill[3] < 255)
    target = Image.new("RGBA", layer.size, (0, 0, 0, 0)) if needs_own_layer else layer
    draw = ImageDraw.Draw(target)
    draw.text(
        position, text, font=font, fill=fill, anchor="la",
        stroke_width=stroke_width, stroke_fill=stroke_fill if stroke_width else None,
    )
    if needs_own_layer:
        layer.alpha_composite(target)


def render_text_block(
    text: str, *, font, fill: tuple[int, int, int, int], padding: int = 4,
) -> tuple[Image.Image, TextMetrics]:
    """Renders `text` alone onto a tight transparent image (plus
    `padding` px on every side, so anti-aliased edges and rotation
    aren't clipped). The ink box occupies (padding, padding) to
    (padding + width, padding + height) of the returned image."""
    metrics = measure(text, font)
    width = max(1, round(metrics.width) + padding * 2)
    height = max(1, round(metrics.height) + padding * 2)
    block = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw_text(block, text, font=font, x=padding, y=padding, fill=fill)
    return block, metrics
