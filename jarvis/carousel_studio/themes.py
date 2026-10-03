"""Carousel-wide design theme: a color palette, fonts and spacing that
every slide shares. Element colors are either a literal "#RRGGBB" or a
theme reference "theme:<role>", resolved at render time - so switching
the theme restyles the whole carousel while anything the person set by
hand (a literal hex) stays as they chose."""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass, field
from typing import Any

COLOR_ROLES: tuple[str, ...] = ("background", "surface", "text", "primary", "accent")
ROLE_LABELS = {
    "background": "Fonas", "surface": "Kortelė", "text": "Tekstas", "primary": "Pagrindinė", "accent": "Akcentas",
}

# 9 palettes from the brief (point 10): minimalist, luxury, pastel,
# natural, autumn, spring, modern, elegant, vivid.
PALETTES: dict[str, tuple[str, dict[str, str]]] = {
    "minimal": ("Minimalistinė", {"background": "#FFFFFF", "surface": "#F2F2F2", "text": "#111111", "primary": "#111111", "accent": "#E63946"}),
    "luxury": ("Prabangi", {"background": "#0E0E10", "surface": "#1C1A17", "text": "#F5EFE6", "primary": "#C9A45C", "accent": "#E8D3A2"}),
    "pastel": ("Pastelinė", {"background": "#FDF1F4", "surface": "#FFFFFF", "text": "#4A3F4F", "primary": "#E59CB3", "accent": "#9BC4E2"}),
    "natural": ("Natūrali", {"background": "#F3EEE4", "surface": "#E4DCCB", "text": "#3B3A30", "primary": "#6B7F4E", "accent": "#B5835A"}),
    "autumn": ("Rudens", {"background": "#FBF3E8", "surface": "#F1DFC6", "text": "#3E2A1E", "primary": "#B5502A", "accent": "#D9A441"}),
    "spring": ("Pavasario", {"background": "#F4FBF2", "surface": "#FFFFFF", "text": "#2D3B2D", "primary": "#5DAA68", "accent": "#F28FAD"}),
    "modern": ("Moderni", {"background": "#0F172A", "surface": "#1E293B", "text": "#F8FAFC", "primary": "#38BDF8", "accent": "#A78BFA"}),
    "elegant": ("Elegantiška", {"background": "#F7F4F0", "surface": "#FFFFFF", "text": "#2B2B2B", "primary": "#7A5C61", "accent": "#BFA8A0"}),
    "vivid": ("Ryški", {"background": "#FFE14D", "surface": "#FFFFFF", "text": "#111111", "primary": "#FF3D7F", "accent": "#3D5AFE"}),
}

_HEX_RE = re.compile(r"^#?([0-9a-fA-F]{6}|[0-9a-fA-F]{3})$")


def normalize_hex(value: str) -> str | None:
    """'#abc'/'abc'/'#AABBCC' -> '#AABBCC'; None if not a valid hex."""
    match = _HEX_RE.match(value.strip())
    if not match:
        return None
    digits = match.group(1)
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return "#" + digits.upper()


def hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = normalize_hex(value) or "#000000"
    return int(value[1:3], 16), int(value[3:5], 16), int(value[5:7], 16)


def relative_luminance(value: str) -> float:
    def channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4
    r, g, b = hex_to_rgb(value)
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def contrast_ratio(a: str, b: str) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def readable_on(background: str) -> str:
    return "#111111" if relative_luminance(background) > 0.4 else "#FFFFFF"


@dataclass
class Theme:
    palette_key: str = "minimal"
    colors: dict[str, str] = field(default_factory=lambda: dict(PALETTES["minimal"][1]))
    heading_font: str = "arial"
    body_font: str = "arial"
    heading_bold: bool = True
    margin: int = 80  # safe margin in px at 1080 width

    def resolve(self, value: str | None) -> str | None:
        """'theme:primary' -> that palette color; a hex -> normalized;
        None/'' -> None (transparent / no color)."""
        if not value:
            return None
        if value.startswith("theme:"):
            return self.colors.get(value[6:]) or "#000000"
        return normalize_hex(value) or "#000000"

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "Theme":
        if not data:
            return cls()
        base = cls()
        colors = dict(base.colors)
        colors.update({k: v for k, v in (data.get("colors") or {}).items() if k in COLOR_ROLES})
        return cls(
            palette_key=data.get("palette_key", base.palette_key),
            colors=colors,
            heading_font=data.get("heading_font", base.heading_font),
            body_font=data.get("body_font", base.body_font),
            heading_bold=bool(data.get("heading_bold", base.heading_bold)),
            margin=int(data.get("margin", base.margin)),
        )

    @classmethod
    def from_palette(cls, key: str, **overrides: Any) -> "Theme":
        theme = cls(palette_key=key, colors=dict(PALETTES[key][1]))
        return dataclasses.replace(theme, **overrides)
