"""The one Pillow renderer for carousel slides. The editor preview,
thumbnails and every export call render_slide(), only with a different
`scale`, so an export always matches what the editor showed.

Text markup: words wrapped in *asterisks* are drawn in the element's
highlight color ("išskirti žodžiai"); the asterisks are not drawn."""

from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

from jarvis.carousel_studio import fonts
from jarvis.carousel_studio.model import Element, Project, Slide
from jarvis.carousel_studio.themes import Theme, hex_to_rgb

MIN_FONT_SIZE = 12
_SUPERSAMPLE = 2


# --- text layout ---------------------------------------------------------------------------

@dataclass
class TextLayout:
    lines: list[list[tuple[str, bool]]]  # each line: (word, highlighted)
    size: int  # font size actually used (after autofit), in output px
    line_height: float
    height: float  # total text height, output px
    overflow: bool  # text does not fit its box (only possible when autofit is off or at MIN_FONT_SIZE)


def parse_markup(text: str) -> list[list[tuple[str, bool]]]:
    """Paragraphs of (word, highlighted) - '*' toggles highlighting."""
    paragraphs: list[list[tuple[str, bool]]] = []
    highlighted = False
    for raw_paragraph in text.split("\n"):
        words: list[tuple[str, bool]] = []
        for raw_word in raw_paragraph.split(" "):
            if raw_word == "":
                continue
            # A word may open and close highlighting itself: "*žodis*".
            pieces = raw_word.split("*")
            out = ""
            word_highlight = highlighted
            for i, piece in enumerate(pieces):
                if i > 0:
                    highlighted = not highlighted
                    if not out:
                        word_highlight = highlighted
                out += piece
            if out:
                words.append((out, word_highlight))
        paragraphs.append(words)
    return paragraphs


def _font_for(element: Element, theme: Theme, size: int):
    props = element.props
    family = props.get("font") or (theme.heading_font if props.get("font_role") == "heading" else theme.body_font)
    return fonts.load_font(family, bool(props.get("bold")), size)


def _wrap(paragraphs, font, max_width: float, uppercase: bool) -> tuple[list[list[tuple[str, bool]]], bool]:
    space = font.getlength(" ")
    lines: list[list[tuple[str, bool]]] = []
    too_wide = False
    for words in paragraphs:
        line: list[tuple[str, bool]] = []
        width = 0.0
        for word, hl in words:
            shown = word.upper() if uppercase else word
            w = font.getlength(shown)
            if w > max_width:
                too_wide = True
            needed = w if not line else width + space + w
            if line and needed > max_width:
                lines.append(line)
                line, width = [(shown, hl)], w
            else:
                line.append((shown, hl))
                width = needed
        lines.append(line)
    return lines, too_wide


def layout_text(element: Element, theme: Theme) -> TextLayout:
    """Lays text out at OUTPUT resolution (scale 1) so wrapping is the
    same at every preview scale; render scales the result."""
    props = element.props
    paragraphs = parse_markup(str(props.get("text", "")))
    spacing = float(props.get("line_spacing", 1.25))
    size = int(props.get("size", 40))
    uppercase = bool(props.get("uppercase"))
    autofit = bool(props.get("autofit", True))
    while True:
        font = _font_for(element, theme, size)
        lines, too_wide = _wrap(paragraphs, font, element.w, uppercase)
        line_height = size * spacing
        height = size + line_height * (len(lines) - 1) if lines else 0
        overflow = too_wide or height > element.h + 0.5
        if not overflow or not autofit or size <= MIN_FONT_SIZE:
            return TextLayout(lines, size, line_height, height, overflow)
        size = max(MIN_FONT_SIZE, int(size * 0.94))


# --- element layers ------------------------------------------------------------------------

def _rgba(color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    return (*hex_to_rgb(color), alpha)


@lru_cache(maxsize=32)
def _load_image(path: str, mtime: float) -> Image.Image:
    with Image.open(path) as img:
        img.seek(0)
        return img.convert("RGBA")


def resolve_asset(path: str, assets_dir: Path | None) -> Path | None:
    if not path:
        return None
    p = Path(path)
    if not p.is_absolute() and assets_dir is not None:
        p = assets_dir / p
    return p if p.is_file() else None


def load_asset(path: str, assets_dir: Path | None) -> Image.Image | None:
    resolved = resolve_asset(path, assets_dir)
    if resolved is None:
        return None
    try:
        return _load_image(str(resolved), resolved.stat().st_mtime)
    except OSError:
        return None


def _fit_image(img: Image.Image, size: tuple[int, int], fit: str, zoom: float, ox: float, oy: float) -> Image.Image:
    bw, bh = size
    iw, ih = img.size
    if fit == "contain":
        ratio = min(bw / iw, bh / ih) * zoom
    else:
        ratio = max(bw / iw, bh / ih) * zoom
    nw, nh = max(1, round(iw * ratio)), max(1, round(ih * ratio))
    scaled = img.resize((nw, nh), Image.Resampling.LANCZOS)
    out = Image.new("RGBA", size, (0, 0, 0, 0))
    # offset -1..1 pans across the overflow; 0 = centered.
    left = (bw - nw) / 2 - ox * abs(bw - nw) / 2
    top = (bh - nh) / 2 - oy * abs(bh - nh) / 2
    _composite(out, scaled, (round(left), round(top)))
    return out


def _rounded_mask(size: tuple[int, int], radius: float) -> Image.Image:
    w, h = size
    ss = _SUPERSAMPLE
    mask = Image.new("L", (w * ss, h * ss), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, w * ss - 1, h * ss - 1), radius=radius * ss, fill=255)
    return mask.resize(size, Image.Resampling.LANCZOS)


def _draw_text(layer: Image.Image, element: Element, theme: Theme, scale: float) -> None:
    layout = layout_text(element, theme)
    props = element.props
    color = theme.resolve(props.get("color")) or "#000000"
    highlight = theme.resolve(props.get("highlight")) or color
    font = _font_for(element, theme, max(4, round(layout.size * scale)))
    draw = ImageDraw.Draw(layer)
    width = layer.width
    align = props.get("align", "left")
    valign = props.get("valign", "top")
    total = layout.height * scale
    y = 0.0
    if valign == "middle":
        y = (layer.height - total) / 2
    elif valign == "bottom":
        y = layer.height - total
    space = font.getlength(" ")
    for line in layout.lines:
        line_w = sum(font.getlength(w) for w, _ in line) + space * max(0, len(line) - 1)
        x = 0.0
        if align == "center":
            x = (width - line_w) / 2
        elif align == "right":
            x = width - line_w
        for word, hl in line:
            draw.text((x, y), word, font=font, fill=_rgba(highlight if hl else color))
            x += font.getlength(word) + space
        y += layout.line_height * scale


def _draw_shape(layer: Image.Image, element: Element, theme: Theme, scale: float) -> None:
    props = element.props
    ss = _SUPERSAMPLE
    w, h = layer.size
    big = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(big)
    fill = theme.resolve(props.get("fill"))
    stroke = theme.resolve(props.get("stroke"))
    stroke_w = round(float(props.get("stroke_width", 0)) * scale * ss)
    box = (0, 0, w * ss - 1, h * ss - 1)
    kwargs = {"fill": _rgba(fill) if fill else None, "outline": _rgba(stroke) if stroke and stroke_w else None, "width": stroke_w}
    shape = props.get("shape", "rect")
    if shape == "ellipse":
        draw.ellipse(box, **kwargs)
    elif shape == "rounded":
        draw.rounded_rectangle(box, radius=float(props.get("radius", 40)) * scale * ss, **kwargs)
    else:
        draw.rectangle(box, **kwargs)
    layer.alpha_composite(big.resize((w, h), Image.Resampling.LANCZOS))


def _draw_line(layer: Image.Image, element: Element, theme: Theme, scale: float) -> None:
    color = theme.resolve(element.props.get("color")) or "#000000"
    thickness = max(1, round(float(element.props.get("width", 4)) * scale))
    top = (layer.height - thickness) / 2
    ImageDraw.Draw(layer).rectangle((0, round(top), layer.width, round(top) + thickness - 1), fill=_rgba(color))


def _draw_image(layer: Image.Image, element: Element, assets_dir: Path | None) -> None:
    props = element.props
    img = load_asset(props.get("path", ""), assets_dir)
    if img is None:
        draw = ImageDraw.Draw(layer)
        draw.rectangle((0, 0, layer.width - 1, layer.height - 1), fill=(200, 200, 200, 255))
        draw.line((0, 0, layer.width, layer.height), fill=(150, 150, 150, 255), width=3)
        draw.line((0, layer.height, layer.width, 0), fill=(150, 150, 150, 255), width=3)
        return
    fitted = _fit_image(
        img, layer.size, props.get("fit", "cover"), float(props.get("zoom", 1.0)),
        float(props.get("offset_x", 0.0)), float(props.get("offset_y", 0.0)),
    )
    radius = float(props.get("radius", 0))
    if radius > 0:
        scale = layer.width / max(element.w, 1)
        alpha = fitted.getchannel("A")
        mask = _rounded_mask(layer.size, radius * scale)
        fitted.putalpha(Image.composite(alpha, Image.new("L", layer.size, 0), mask))
    layer.alpha_composite(fitted)


def render_element(element: Element, theme: Theme, *, scale: float = 1.0, assets_dir: Path | None = None) -> tuple[Image.Image, tuple[int, int]]:
    """The element as an RGBA layer (rotated, opacity applied) and the
    top-left position to composite it at, in scaled slide pixels."""
    w = max(1, round(element.w * scale))
    h = max(1, round(element.h * scale))
    layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if element.kind == "text":
        _draw_text(layer, element, theme, scale)
    elif element.kind == "shape":
        _draw_shape(layer, element, theme, scale)
    elif element.kind == "line":
        _draw_line(layer, element, theme, scale)
    elif element.kind == "image":
        _draw_image(layer, element, assets_dir)
    opacity = max(0.0, min(1.0, element.opacity))
    if opacity < 1.0:
        layer.putalpha(layer.getchannel("A").point([round(a * opacity) for a in range(256)]))
    if element.rotation % 360:
        layer = layer.rotate(-element.rotation, resample=Image.Resampling.BICUBIC, expand=True)
    cx, cy = element.center
    return layer, (round(cx * scale - layer.width / 2), round(cy * scale - layer.height / 2))


def _render_background(slide: Slide, theme: Theme, size: tuple[int, int], assets_dir: Path | None) -> Image.Image:
    bg = slide.background or {}
    kind = bg.get("type", "color")
    base_color = theme.resolve(bg.get("color") or "theme:background") or "#FFFFFF"
    canvas = Image.new("RGBA", size, _rgba(base_color))
    if kind == "gradient":
        c1 = hex_to_rgb(theme.resolve(bg.get("color1") or "theme:background") or "#FFFFFF")
        c2 = hex_to_rgb(theme.resolve(bg.get("color2") or "theme:primary") or "#000000")
        strip = Image.new("RGBA", (1, 256))
        for i in range(256):
            t = i / 255
            strip.putpixel((0, i), tuple(round(a + (b - a) * t) for a, b in zip(c1, c2)) + (255,))
        if bg.get("direction") == "diagonal":
            diag = round(math.hypot(*size))
            grad = strip.resize((diag, diag), Image.Resampling.BILINEAR).rotate(45, resample=Image.Resampling.BICUBIC)
            left, top = (diag - size[0]) // 2, (diag - size[1]) // 2
            canvas = grad.crop((left, top, left + size[0], top + size[1]))
        else:
            canvas = strip.resize(size, Image.Resampling.BILINEAR)
    elif kind == "image":
        img = load_asset(bg.get("path", ""), assets_dir)
        if img is not None:
            canvas.alpha_composite(_fit_image(img, size, "cover", float(bg.get("zoom", 1.0)), float(bg.get("offset_x", 0)), float(bg.get("offset_y", 0))))
        overlay = float(bg.get("overlay", 0.0))
        if overlay > 0:
            tint = theme.resolve(bg.get("overlay_color") or "#000000") or "#000000"
            canvas.alpha_composite(Image.new("RGBA", size, _rgba(tint, round(255 * min(overlay, 1.0)))))
    return canvas


def render_slide(project: Project, slide: Slide, *, scale: float = 1.0, assets_dir: Path | None = None) -> Image.Image:
    """The slide as an RGB image at project.size * scale."""
    width, height = project.size
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    canvas = _render_background(slide, project.theme, size, assets_dir)
    if canvas.size != size:
        canvas = canvas.resize(size)
    for element in slide.elements:
        if element.props.get("hidden"):
            continue
        layer, pos = render_element(element, project.theme, scale=scale, assets_dir=assets_dir)
        _composite(canvas, layer, pos)
    return canvas.convert("RGB")


def _composite(canvas: Image.Image, layer: Image.Image, pos: tuple[int, int]) -> None:
    """alpha_composite that tolerates layers partly outside the canvas."""
    x, y = pos
    left, top = max(0, -x), max(0, -y)
    right = min(layer.width, canvas.width - x)
    bottom = min(layer.height, canvas.height - y)
    if right <= left or bottom <= top:
        return
    canvas.alpha_composite(layer.crop((left, top, right, bottom)), (x + left, y + top))


def element_bounds(element: Element) -> tuple[float, float, float, float]:
    """Axis-aligned bounds of the (possibly rotated) element box, output px."""
    cx, cy = element.center
    a = math.radians(element.rotation)
    hw, hh = element.w / 2, element.h / 2
    xs, ys = [], []
    for sx, sy in ((-hw, -hh), (hw, -hh), (hw, hh), (-hw, hh)):
        xs.append(cx + sx * math.cos(a) - sy * math.sin(a))
        ys.append(cy + sx * math.sin(a) + sy * math.cos(a))
    return min(xs), min(ys), max(xs), max(ys)
