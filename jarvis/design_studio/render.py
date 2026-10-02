"""Design rendering engine (module brief, sections 3, 8, 20): composes
a DesignBrief + DesignStyle into an actual PNG/JPG image via Pillow -
the only place in this package (or this codebase) doing
ImageDraw/ImageFont compositing (see this package's own __init__.py
docstring for why: no AI image-generation capability exists anywhere
in this codebase, confirmed before this module was written).

Layout algorithm (module brief, section 8 "Automatic Layout"):
  1. A linear gradient background fills the whole canvas (module
     brief's own two-color-per-style model - see
     jarvis.design_studio.styles.DesignStyle).
  2. Headline: word-wrapped (using Pillow's OWN pixel-accurate
     font.getbbox() measurement - unlike jarvis.video_studio.cover's
     _wrap_text(), which has to approximate character width because
     FFmpeg's drawtext exposes no text-measurement API; Pillow's
     ImageFont DOES, so this module wraps exactly, never
     under/over-estimating a line's width) and vertically centered in
     the canvas's upper-middle band.
  3. Supporting text: word-wrapped the same way, placed below the
     headline.
  4. CTA: rendered as a filled rounded-rectangle "pill" near the
     bottom of the canvas.
  5. An optional user-supplied image (logo/brand/product photo - module
     brief section 7) is composited in, resized to fit without
     cropping the SUBJECT unrecognizably (module brief: "If the user
     uploads a product image, preserve the actual product appearance
     and do not unnecessarily regenerate the product" - this module
     never regenerates or alters an uploaded image's content, only
     resizes/positions it).

Safe zones (module brief, section 8's "For Instagram Story keep
important text away from areas that may be covered by Instagram UI"):
the SAME _SAFE_AREA_TOP_FRACTION/_SAFE_AREA_BOTTOM_FRACTION/
_SAFE_AREA_HORIZONTAL_MARGIN_FRACTION values jarvis.video_studio.cover
already established and hand-tested (0.25/0.80/0.08) - reused verbatim
here rather than re-deriving a second set of numbers, since both are
answering the exact same "Instagram Story chrome" question.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from PIL import Image, ImageDraw, ImageFont

from jarvis.design_studio.brief import DesignBrief
from jarvis.design_studio.styles import DesignStyle

# Module brief, section 3's exact pixel dimensions.
FORMAT_DIMENSIONS: dict[str, tuple[int, int]] = {
    "story": (1080, 1920),
    "post": (1080, 1350),
    "square": (1080, 1080),
    "reel_cover": (1080, 1920),
    "carousel": (1080, 1350),
}

# Same values as jarvis.video_studio.cover's own safe-area constants -
# see this module's own docstring for why they're reused verbatim.
_SAFE_AREA_TOP_FRACTION = 0.25
_SAFE_AREA_BOTTOM_FRACTION = 0.80
_SAFE_AREA_HORIZONTAL_MARGIN_FRACTION = 0.08

_HEADLINE_FONT_SIZE_FRACTION = 0.062  # of canvas width
_BODY_FONT_SIZE_FRACTION = 0.032
_CTA_FONT_SIZE_FRACTION = 0.028
_LINE_SPACING_FRACTION = 0.012


class RenderError(Exception):
    """Raised for any rendering failure (unknown format, missing/
    unreadable uploaded image, Pillow failure) - always with a
    human-readable message, per this codebase's established "never
    silently fail" convention."""


def _linear_gradient(size: tuple[int, int], color1: str, color2: str, *, vertical: bool) -> Image.Image:
    """A fast linear gradient: draws a single-pixel-wide (or tall)
    strip with the gradient, then resizes it to fill the canvas -
    avoids a slow per-pixel Python loop across the full image (a naive
    per-pixel approach was hand-tested at ~0.17s for a 1080x1920
    canvas; this strip-and-resize approach is effectively instant) and
    needs no numpy (not a dependency of this project)."""
    w, h = size
    c1 = _hex_to_rgb(color1)
    c2 = _hex_to_rgb(color2)
    if vertical:
        strip = Image.new("RGB", (1, h))
        pixels = strip.load()
        assert pixels is not None
        for i in range(h):
            t = i / max(1, h - 1)
            pixels[0, i] = _lerp_color(c1, c2, t)
        return strip.resize((w, h))
    strip = Image.new("RGB", (w, 1))
    pixels = strip.load()
    assert pixels is not None
    for i in range(w):
        t = i / max(1, w - 1)
        pixels[i, 0] = _lerp_color(c1, c2, t)
    return strip.resize((w, h))


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    hex_color = hex_color.lstrip("#")
    return (int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16))


def _lerp_color(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))  # type: ignore[return-value]


def _wrap_text_pixel_accurate(
    text: str, font: ImageFont.FreeTypeFont, *, max_width: int,
) -> list[str]:
    """Greedy word-wrap using Pillow's own font.getbbox() pixel
    measurement - see this module's own docstring for why this is more
    accurate than jarvis.video_studio.cover's character-count
    approximation (that module's underlying renderer, FFmpeg's
    drawtext, has no text-measurement API to use instead; Pillow
    does)."""
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        width = font.getbbox(candidate)[2]
        if width > max_width and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def _draw_wrapped_text(
    draw: ImageDraw.ImageDraw, lines: list[str], font: ImageFont.FreeTypeFont, *,
    center_x: int, top_y: int, color: str, line_spacing: int,
) -> int:
    """Draws each line centered horizontally at `center_x`, stacked
    starting at `top_y`. Returns the y-coordinate just below the last
    drawn line, so a caller can position the next element below it."""
    y = top_y
    for line in lines:
        bbox = font.getbbox(line)
        line_width = bbox[2] - bbox[0]
        line_height = bbox[3] - bbox[1]
        draw.text((center_x - line_width / 2, y), line, font=font, fill=color)
        y = int(y + line_height + line_spacing)
    return y


def _rounded_rectangle_pill(
    draw: ImageDraw.ImageDraw, *, center_x: int, center_y: int, text: str,
    font: ImageFont.FreeTypeFont, text_color: str, background_color: str,
    padding_x: int, padding_y: int, max_text_width: int, line_spacing: int,
) -> None:
    """Draws a pill sized to fit `text`, wrapping it (pixel-accurate,
    same _wrap_text_pixel_accurate() helper the headline/body text use)
    to `max_text_width` and growing the pill taller for extra lines,
    rather than growing wider without bound. A short one/two-word CTA
    ("Save this") still renders as the original single-line pill; a
    long, sentence-length CTA (jarvis.reel_generator's own CTAs, drawn
    from full script/plan text rather than Design Studio's own short-
    CTA-generating brief - see this fix's own regression test for the
    bug this replaced: an unwrapped pill silently overflowing past the
    canvas edge) wraps onto multiple lines inside a taller pill instead
    of running off the canvas."""
    lines = _wrap_text_pixel_accurate(text, font, max_width=max_text_width - padding_x * 2)
    if not lines:
        lines = [text]

    line_metrics = [font.getbbox(line) for line in lines]
    line_widths = [b[2] - b[0] for b in line_metrics]
    line_heights = [b[3] - b[1] for b in line_metrics]
    text_width = max(line_widths)
    total_text_height = sum(line_heights) + line_spacing * (len(lines) - 1)

    pill_width = text_width + padding_x * 2
    pill_height = total_text_height + padding_y * 2
    left = center_x - pill_width / 2
    top = center_y - pill_height / 2
    draw.rounded_rectangle(
        (left, top, left + pill_width, top + pill_height), radius=min(pill_height, pill_width) / 2, fill=background_color,
    )

    line_y = top + padding_y
    for line, line_width, line_height in zip(lines, line_widths, line_heights):
        draw.text((center_x - line_width / 2, line_y), line, font=font, fill=text_color)
        line_y += line_height + line_spacing


@dataclass(frozen=True)
class RenderResult:
    output_path: Path
    width: int
    height: int
    file_size_bytes: int


def render_design(
    brief: DesignBrief, style: DesignStyle, *, output_path: Path,
    uploaded_image_path: Path | None = None,
    image_role: Literal["logo", "product", "background"] | None = None,
) -> RenderResult:
    """Renders `brief` with `style` into a single image file at
    `output_path` (format inferred from the extension - .png or .jpg/
    .jpeg). `uploaded_image_path`, if given, is composited in verbatim
    (never regenerated/altered - module brief section 7's "preserve
    the actual product appearance" rule) - `image_role` controls WHERE:
    "logo" (small, corner-placed), "product"/"background" (larger,
    placed in the canvas's middle band, headline/CTA laid out around
    it). Raises RenderError on any failure (unknown format, unreadable
    uploaded image, Pillow failure)."""
    if brief.format not in FORMAT_DIMENSIONS:
        raise RenderError(f"Unknown design format '{brief.format}'.")
    width, height = FORMAT_DIMENSIONS[brief.format]

    canvas = _linear_gradient((width, height), style.background_color_1, style.background_color_2, vertical=style.gradient_vertical)
    draw = ImageDraw.Draw(canvas)

    uploaded_image: Image.Image | None = None
    if uploaded_image_path is not None:
        if not uploaded_image_path.is_file():
            raise RenderError(f"Uploaded image not found: {uploaded_image_path}")
        try:
            uploaded_image = Image.open(uploaded_image_path).convert("RGBA")
        except Exception as e:
            raise RenderError(f"Couldn't read uploaded image: {e}") from e

    margin = int(width * _SAFE_AREA_HORIZONTAL_MARGIN_FRACTION)
    max_text_width = width - margin * 2
    center_x = width // 2

    safe_top = int(height * _SAFE_AREA_TOP_FRACTION)
    safe_bottom = int(height * _SAFE_AREA_BOTTOM_FRACTION)
    line_spacing = int(height * _LINE_SPACING_FRACTION)

    cursor_y = safe_top

    if uploaded_image is not None and image_role in ("product", "background"):
        cursor_y = _composite_product_image(canvas, uploaded_image, width=width, top_y=cursor_y, max_height=int(height * 0.30))
        cursor_y += line_spacing * 2

    headline_font = ImageFont.truetype(style.headline_font, int(width * _HEADLINE_FONT_SIZE_FRACTION))
    headline_lines = _wrap_text_pixel_accurate(brief.headline, headline_font, max_width=max_text_width)
    cursor_y = _draw_wrapped_text(
        draw, headline_lines, headline_font, center_x=center_x, top_y=cursor_y,
        color=style.headline_color, line_spacing=line_spacing,
    )
    cursor_y += line_spacing * 2

    body_font = ImageFont.truetype(style.body_font, int(width * _BODY_FONT_SIZE_FRACTION))
    body_lines = _wrap_text_pixel_accurate(brief.supporting_text, body_font, max_width=max_text_width)
    cursor_y = _draw_wrapped_text(
        draw, body_lines, body_font, center_x=center_x, top_y=cursor_y,
        color=style.body_color, line_spacing=line_spacing,
    )

    if uploaded_image is not None and image_role == "logo":
        _composite_logo(canvas, uploaded_image, width=width, height=height, margin=margin)

    if brief.cta.strip():
        # A caller building a DesignBrief directly (not through
        # generate_design_brief(), which always fills every field -
        # see that function's own docstring) may legitimately leave
        # cta blank, e.g. jarvis.reel_generator.cover/.scenes building
        # a headline-only text card. Drawing an empty rounded-rectangle
        # pill for a blank CTA produced a real, hand-tested visual bug
        # (a stray floating pill shape with no text in it) - skipped
        # entirely instead, rather than drawing a pill around nothing.
        cta_font = ImageFont.truetype(style.body_font, int(width * _CTA_FONT_SIZE_FRACTION))
        cta_y = min(safe_bottom, height - int(height * 0.06))
        _rounded_rectangle_pill(
            draw, center_x=center_x, center_y=cta_y, text=brief.cta, font=cta_font,
            text_color=style.cta_color, background_color=style.cta_background,
            padding_x=int(width * 0.04), padding_y=int(height * 0.012),
            max_text_width=max_text_width, line_spacing=line_spacing,
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        rgb_canvas = canvas.convert("RGB")
        if output_path.suffix.lower() in (".jpg", ".jpeg"):
            rgb_canvas.save(output_path, quality=92)
        else:
            rgb_canvas.save(output_path)
    except Exception as e:
        raise RenderError(f"Couldn't save the rendered design: {e}") from e

    return RenderResult(
        output_path=output_path, width=width, height=height,
        file_size_bytes=output_path.stat().st_size,
    )


def _composite_product_image(
    canvas: Image.Image, uploaded_image: Image.Image, *, width: int, top_y: int, max_height: int,
) -> int:
    """Resizes `uploaded_image` to fit within `max_height` (preserving
    its aspect ratio - never cropping/distorting the actual product,
    per the module brief's "preserve the actual product appearance"
    rule) and pastes it centered horizontally at `top_y`. Returns the
    y-coordinate just below the pasted image."""
    img_w, img_h = uploaded_image.size
    scale = min(max_height / img_h, (width * 0.8) / img_w)
    new_size = (max(1, int(img_w * scale)), max(1, int(img_h * scale)))
    resized = uploaded_image.resize(new_size)
    paste_x = (width - new_size[0]) // 2
    canvas.paste(resized, (paste_x, top_y), resized if resized.mode == "RGBA" else None)
    return top_y + new_size[1]


def _composite_logo(canvas: Image.Image, uploaded_image: Image.Image, *, width: int, height: int, margin: int) -> None:
    """Places a small logo in the canvas's top-right corner - module
    brief section 8's "logo placement" layout step."""
    logo_size = int(width * 0.12)
    img_w, img_h = uploaded_image.size
    scale = logo_size / max(img_w, img_h)
    new_size = (max(1, int(img_w * scale)), max(1, int(img_h * scale)))
    resized = uploaded_image.resize(new_size)
    paste_x = width - margin - new_size[0]
    paste_y = margin
    canvas.paste(resized, (paste_x, paste_y), resized if resized.mode == "RGBA" else None)
