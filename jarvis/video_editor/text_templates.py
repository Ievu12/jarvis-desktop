"""Curated text style presets - requirement 2's own "Pridėk teksto
šablonus, šriftus, spalvų paletes" (add text templates, fonts, color
palettes). A TextTemplate is a real, ready-to-use combination of
font_size/color/animation/speed/intensity (every field
jarvis.video_editor.text_overlay.TextOverlay already has, minus
text/timing/position, which are always the person's own choice) - NOT a
new mechanism, purely curated DATA built entirely on the existing
TextOverlay/build_text_overlay_filter() machinery, mirroring
jarvis.video_editor.timeline's own "pure dataclass, no new I/O" shape.

A ColorPalette is a small, named set of real hex colors a person can
pick from when styling a TextOverlay/sticker tint - again pure curated
data, no mechanism of its own."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from jarvis.video_editor.text_overlay import TextAnimation, TextOverlay, TextSlideDirection


@dataclass(frozen=True)
class TextTemplate:
    """One named, ready-to-use text style - applying a template means
    building a real TextOverlay from this template's own fields plus
    the person's own text/timing/position, exactly the same "preset
    carries style only, never timing/placement" relationship
    jarvis.video_editor.sticker_library.StickerPreset already
    establishes for stickers."""

    name: str
    font_size: int
    color: str
    animation: TextAnimation
    speed: float = 1.0
    intensity: float = 1.0
    direction: TextSlideDirection = "left"


TEXT_TEMPLATES: tuple[TextTemplate, ...] = (
    TextTemplate(name="Clean Title", font_size=72, color="white", animation="fade", speed=1.0, intensity=1.0),
    TextTemplate(name="Bold Reveal", font_size=80, color="white", animation="pop_up", speed=1.0, intensity=1.0),
    TextTemplate(name="Elegant Slide", font_size=64, color="white", animation="slide_in", speed=0.8, intensity=1.0, direction="left"),
    TextTemplate(name="Typewriter Note", font_size=52, color="white", animation="typewriter", speed=1.2, intensity=1.0),
    TextTemplate(name="Energetic Bounce", font_size=76, color="#FFD166", animation="bounce", speed=1.3, intensity=1.2),
    TextTemplate(name="Neon Glow", font_size=68, color="#6C8CFF", animation="glow", speed=1.0, intensity=1.5),
    TextTemplate(name="Glitch Alert", font_size=70, color="white", animation="glitch", speed=1.0, intensity=1.3),
    TextTemplate(name="Soft Zoom", font_size=60, color="white", animation="zoom", speed=0.7, intensity=0.6),
    TextTemplate(name="Playful Shake", font_size=66, color="#FF8FA3", animation="shake", speed=1.4, intensity=1.0),
    TextTemplate(name="Quick Exit", font_size=64, color="white", animation="pop_out", speed=1.0, intensity=1.0),
    TextTemplate(name="Smooth Exit", font_size=64, color="white", animation="slide_out", speed=1.0, intensity=1.0, direction="right"),
)
# 11 real, distinct presets - every one of jarvis.video_editor.text_overlay's
# own `animation` values (except the no-op "none") appears in at least
# one template (a real coverage guarantee checked by this module's own
# test suite, not just an aspiration) so "pick a template" genuinely
# surfaces the full range of kinetic typography this package supports,
# not just a few favorites.

TEXT_TEMPLATE_NAMES: tuple[str, ...] = tuple(t.name for t in TEXT_TEMPLATES)
_TEMPLATES_BY_NAME: dict[str, TextTemplate] = {t.name: t for t in TEXT_TEMPLATES}


def get_text_template(name: str) -> TextTemplate | None:
    return _TEMPLATES_BY_NAME.get(name)


@dataclass(frozen=True)
class ColorPalette:
    """One named set of real hex colors - requirement: "spalvų paletes"
    (color palettes), for quickly picking a coordinated text/sticker
    tint rather than typing a hex code from scratch."""

    name: str
    colors: tuple[str, ...]


COLOR_PALETTES: tuple[ColorPalette, ...] = (
    ColorPalette(name="Classic", colors=("#FFFFFF", "#000000", "#FF4136", "#2ECC40", "#0074D9", "#FFDC00")),
    ColorPalette(name="Pastel", colors=("#FFD6E8", "#D6EFFF", "#FFF3D6", "#E0D6FF", "#D6FFE4")),
    ColorPalette(name="Autumn", colors=("#D97642", "#B5651D", "#E8B84B", "#8B4513", "#5C3317")),
    ColorPalette(name="Ocean", colors=("#003F5C", "#2F6690", "#3A86FF", "#8ECAE6", "#CAF0F8")),
    ColorPalette(name="Sunset", colors=("#FF6B6B", "#FFA07A", "#FFD166", "#F77F00", "#D62828")),
    ColorPalette(name="Monochrome", colors=("#FFFFFF", "#D9D9D9", "#A6A6A6", "#595959", "#000000")),
    ColorPalette(name="Neon", colors=("#39FF14", "#FF073A", "#00F0FF", "#FF61F6", "#FFFB00")),
)
COLOR_PALETTE_NAMES: tuple[str, ...] = tuple(p.name for p in COLOR_PALETTES)
_PALETTES_BY_NAME: dict[str, ColorPalette] = {p.name: p for p in COLOR_PALETTES}


def get_color_palette(name: str) -> ColorPalette | None:
    return _PALETTES_BY_NAME.get(name)


@dataclass(frozen=True)
class TextStylePreset:
    """One named look for a text: font, color, outline, shadow and
    background together (requirement: "teksto kontūrai, šešėliai,
    šriftai"). Applying it replaces only those style fields - the text,
    timing, position, size and animation stay the person's own."""

    name: str
    font: str = "arial_bold"
    color: str = "white"
    outline_width: int = 0
    outline_color: str = "black"
    shadow_offset: int = 0
    shadow_color: str = "black"
    shadow_opacity: float = 0.6
    background_opacity: float = 0.0
    background_color: str = "black"


TEXT_STYLE_PRESETS: tuple[TextStylePreset, ...] = (
    TextStylePreset(name="Paprastas"),
    TextStylePreset(name="Kontūras", outline_width=5),
    TextStylePreset(name="Šešėlis", shadow_offset=6, shadow_opacity=0.7),
    TextStylePreset(name="Etiketė", background_opacity=0.6),
    TextStylePreset(name="Geltona juosta", color="black", background_color="#FFD700", background_opacity=1.0),
    TextStylePreset(name="TikTok", font="impact", outline_width=6),
    TextStylePreset(name="Neonas", color="#7FDBFF", outline_width=3, outline_color="#1B3CFF", shadow_offset=4,
                    shadow_color="#1B3CFF", shadow_opacity=0.5),
    TextStylePreset(name="Elegantiškas", font="georgia", shadow_offset=4, shadow_opacity=0.5),
    TextStylePreset(name="Mielas", font="comic", color="#FF6B9D", outline_width=4, outline_color="white"),
)
TEXT_STYLE_PRESET_NAMES: tuple[str, ...] = tuple(p.name for p in TEXT_STYLE_PRESETS)

_STYLE_FIELDS = (
    "font", "color", "outline_width", "outline_color", "shadow_offset", "shadow_color", "shadow_opacity",
    "background_opacity", "background_color",
)


def apply_text_style(overlay: TextOverlay, preset: TextStylePreset) -> TextOverlay:
    return dataclasses.replace(overlay, **{name: getattr(preset, name) for name in _STYLE_FIELDS})
