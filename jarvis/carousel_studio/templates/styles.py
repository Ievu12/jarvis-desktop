"""Design styles for the template library (Minimal, Luxury, Soft, ...).

A style is DATA: five role colors, two font families, a decoration and
a few typographic switches, read from templates/data/styles.json and
from the person's own JSON files (see catalog.user_template_dir()). A
style becomes the carousel's Theme, so after "Naudoti šį šabloną" the
editor's palette switcher, color pickers and fonts keep working exactly
as for any other carousel."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jarvis.carousel_studio import fonts
from jarvis.carousel_studio.themes import COLOR_ROLES, Theme, normalize_hex

DECORATIONS = ("none", "frame", "double_frame", "dots", "blob", "corner", "lines", "arch", "grid", "sparkle")


@dataclass(frozen=True)
class DesignStyle:
    key: str
    label: str
    colors: dict[str, str]
    heading_font: str = "arial"
    body_font: str = "arial"
    decor: str = "none"
    background: str = "color"  # "color" or "gradient" (background -> surface)
    radius: int = 24  # corner radius for cards and photos, output px
    heading_uppercase: bool = False
    heading_scale: float = 1.0
    tags: tuple[str, ...] = field(default_factory=tuple)

    def theme(self, margin: int = 80) -> Theme:
        # palette_key "custom" = the editor's "Sava" palette: the
        # person's colors stay until they pick one of the 9 palettes.
        return Theme(palette_key="custom", colors=dict(self.colors), heading_font=self.heading_font,
                     body_font=self.body_font, heading_bold=True, margin=margin)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DesignStyle":
        key = str(data["key"]).strip()
        if not key:
            raise ValueError("stiliaus raktas tuščias")
        colors = {}
        for role in COLOR_ROLES:
            value = normalize_hex(str((data.get("colors") or {}).get(role, "")))
            if value is None:
                raise ValueError(f"stiliui „{key}“ trūksta spalvos „{role}“")
            colors[role] = value
        decor = data.get("decor", "none")
        heading_font = data.get("heading_font", "arial")
        body_font = data.get("body_font", "arial")
        return cls(
            key=key, label=str(data.get("label") or key), colors=colors,
            heading_font=heading_font if heading_font in fonts.family_keys() else fonts.DEFAULT_FAMILY,
            body_font=body_font if body_font in fonts.family_keys() else fonts.DEFAULT_FAMILY,
            decor=decor if decor in DECORATIONS else "none",
            background="gradient" if data.get("background") == "gradient" else "color",
            radius=max(0, int(data.get("radius", 24))),
            heading_uppercase=bool(data.get("heading_uppercase", False)),
            heading_scale=max(0.6, min(1.5, float(data.get("heading_scale", 1.0)))),
            tags=tuple(str(t) for t in data.get("tags", ())),
        )
