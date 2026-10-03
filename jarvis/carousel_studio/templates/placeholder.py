"""The soft placeholder picture every template photo slot shows until
the person picks a photo ("Pakeisti nuotrauką…"), in the style's colors."""

from __future__ import annotations

import tempfile
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

from jarvis.carousel_studio.templates.layouts import PHOTO
from jarvis.carousel_studio.templates.styles import DesignStyle
from jarvis.carousel_studio.themes import hex_to_rgb

SIZE = (900, 1100)


def placeholder_image(style: DesignStyle) -> Image.Image:
    w, h = SIZE
    top = hex_to_rgb(style.colors["surface"])
    bottom = hex_to_rgb(style.colors["primary"])
    strip = Image.new("RGB", (1, 256))
    for i in range(256):
        t = 0.15 + 0.5 * i / 255  # mostly the light color, a hint of the primary
        strip.putpixel((0, i), tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    image = strip.resize(SIZE, Image.Resampling.BILINEAR)
    draw = ImageDraw.Draw(image, "RGBA")
    accent = hex_to_rgb(style.colors["accent"])
    draw.ellipse((w * 0.62, h * 0.16, w * 0.8, h * 0.31), fill=(*accent, 150))
    draw.polygon([(0, h * 0.78), (w * 0.33, h * 0.5), (w * 0.62, h * 0.8)], fill=(*bottom, 110))
    draw.polygon([(w * 0.38, h * 0.82), (w * 0.7, h * 0.56), (w, h * 0.84)], fill=(*bottom, 80))
    draw.rectangle((0, h * 0.8, w, h), fill=(*bottom, 60))
    return image


def write_placeholder(folder: Path, style: DesignStyle) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / PHOTO
    placeholder_image(style).save(path)
    return path


@lru_cache(maxsize=64)
def _preview_dir(style_key: str, colors: tuple) -> Path:
    return Path(tempfile.mkdtemp(prefix=f"jarvis_tpl_{style_key}_"))


def preview_assets_dir(style: DesignStyle) -> Path:
    """A temporary assets folder holding this style's placeholder, for
    rendering library previews without a project."""
    folder = _preview_dir(style.key, tuple(sorted(style.colors.items())))
    if not (folder / PHOTO).is_file():
        write_placeholder(folder, style)
    return folder
