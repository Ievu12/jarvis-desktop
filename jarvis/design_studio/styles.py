"""Design Style system (module brief, section 5): a fixed table of
named DesignStyle presets - colors, fonts, spacing, and a background
gradient direction - each a reusable "design system" a design can be
rendered with. Mirrors jarvis.video_studio.cover.COVER_TEMPLATES'
shape and purpose exactly (a named preset over the same rendering
pipeline, not a different pipeline per style) - see that module's own
docstring for the precedent this follows.

Every color here is used as a background gradient (two colors) or flat
fill (repeat the same color twice) - jarvis.design_studio.render draws
the actual gradient; this module only holds the palette/typography
DATA per style, no drawing code.

Fonts are limited to what jarvis.video_studio.cover.py already
established works on this machine (C:/Windows/Fonts/arial.ttf and
arialbd.ttf - confirmed the full extent of font support anywhere in
this codebase by this module's own architecture inspection) - every
style here uses one of those two, varied only by weight/size/color, not
a distinct typeface per style (no other .ttf is bundled with or
referenced by this project).
"""

from __future__ import annotations

from dataclasses import dataclass

_FONT_BOLD = "C:/Windows/Fonts/arialbd.ttf"
_FONT_REGULAR = "C:/Windows/Fonts/arial.ttf"


@dataclass(frozen=True)
class DesignStyle:
    name: str
    label: str
    background_color_1: str  # hex, gradient start (top or left)
    background_color_2: str  # hex, gradient end (bottom or right)
    gradient_vertical: bool
    headline_color: str
    body_color: str
    cta_color: str
    cta_background: str  # CTA pill/button background color
    headline_font: str
    body_font: str
    accent_color: str  # used for small decorative elements (CTA pill, divider line)


# The module brief's own style list (section 5), plus AUTO (handled by
# jarvis.design_studio.brief.generate_design_brief() picking one of the
# named styles below based on the request's topic/tone rather than
# being a style of its own - see that module's docstring). Palette
# choices here are this module's own reasonable defaults per style
# NAME (no design-system source to pull exact brand values from,
# same caveat jarvis.video_studio.cover.COVER_TEMPLATES' own docstring
# states for its font/color choices) - not a claim of matching any
# specific brand's actual visual identity.
DESIGN_STYLES: dict[str, DesignStyle] = {
    "minimal": DesignStyle(
        "minimal", "Minimal", "#FFFFFF", "#F2F2F2", True,
        "#1A1A1A", "#4A4A4A", "#FFFFFF", "#1A1A1A", _FONT_BOLD, _FONT_REGULAR, "#1A1A1A",
    ),
    "elegant": DesignStyle(
        "elegant", "Elegant", "#2B2B33", "#1A1A20", True,
        "#F5EFE6", "#C9C2B8", "#2B2B33", "#F5EFE6", _FONT_BOLD, _FONT_REGULAR, "#C9A15A",
    ),
    "luxury": DesignStyle(
        "luxury", "Luxury", "#0D0D0D", "#1F1B12", True,
        "#D4AF37", "#E8E3D8", "#0D0D0D", "#D4AF37", _FONT_BOLD, _FONT_REGULAR, "#D4AF37",
    ),
    "soft": DesignStyle(
        "soft", "Soft", "#FCEEF3", "#F3E0EA", True,
        "#5C4249", "#8A6E75", "#FFFFFF", "#D98BA8", _FONT_BOLD, _FONT_REGULAR, "#D98BA8",
    ),
    "feminine": DesignStyle(
        "feminine", "Feminine", "#FFD6E8", "#C9A0DC", True,
        "#3A1F33", "#5C3A52", "#FFFFFF", "#9B5DE5", _FONT_BOLD, _FONT_REGULAR, "#9B5DE5",
    ),
    "wellness": DesignStyle(
        "wellness", "Wellness", "#E8F0E3", "#C8DFC0", True,
        "#2E4A2E", "#4F6B4F", "#FFFFFF", "#6B9C5E", _FONT_BOLD, _FONT_REGULAR, "#6B9C5E",
    ),
    "yoga": DesignStyle(
        "yoga", "Yoga", "#F0E6D8", "#D9C7A8", True,
        "#3E3226", "#6B5D4A", "#FFFFFF", "#A8895A", _FONT_BOLD, _FONT_REGULAR, "#A8895A",
    ),
    "beauty": DesignStyle(
        "beauty", "Beauty", "#FFE8E0", "#F5C6C0", True,
        "#5C2A26", "#8A4A44", "#FFFFFF", "#E8756B", _FONT_BOLD, _FONT_REGULAR, "#E8756B",
    ),
    "k_beauty": DesignStyle(
        "k_beauty", "K-Beauty", "#FFF5F5", "#FFE0EC", True,
        "#4A3A3E", "#7A6468", "#FFFFFF", "#FFB3C6", _FONT_BOLD, _FONT_REGULAR, "#FFB3C6",
    ),
    "lifestyle": DesignStyle(
        "lifestyle", "Lifestyle", "#F5F0E8", "#E0D5C0", True,
        "#3A3228", "#665C4A", "#FFFFFF", "#B8935F", _FONT_BOLD, _FONT_REGULAR, "#B8935F",
    ),
    "professional": DesignStyle(
        "professional", "Professional", "#1A2332", "#0D1420", True,
        "#FFFFFF", "#A8B4C4", "#0D1420", "#4A90D9", _FONT_BOLD, _FONT_REGULAR, "#4A90D9",
    ),
    "educational": DesignStyle(
        "educational", "Educational", "#E8F1FA", "#C8DCF0", True,
        "#12293D", "#3D5A73", "#FFFFFF", "#2E6BA8", _FONT_BOLD, _FONT_REGULAR, "#2E6BA8",
    ),
    "bold": DesignStyle(
        "bold", "Bold", "#FF3B30", "#CC1A12", True,
        "#FFFFFF", "#FFE5E3", "#FF3B30", "#FFFFFF", _FONT_BOLD, _FONT_BOLD, "#FFFFFF",
    ),
    "modern": DesignStyle(
        "modern", "Modern", "#0F0F0F", "#2A2A2A", False,
        "#FFFFFF", "#B0B0B0", "#0F0F0F", "#FFFFFF", _FONT_BOLD, _FONT_REGULAR, "#00D9C0",
    ),
    "clean": DesignStyle(
        "clean", "Clean", "#FAFAFA", "#EFEFEF", True,
        "#101010", "#5A5A5A", "#FAFAFA", "#101010", _FONT_BOLD, _FONT_REGULAR, "#101010",
    ),
}

# Special value for the module brief's "AUTO STYLE" dropdown option -
# never a key in DESIGN_STYLES itself; jarvis.design_studio.brief
# .generate_design_brief() resolves it to one of the named styles above
# based on the request, before jarvis.design_studio.render ever sees
# a style name.
AUTO_STYLE = "auto"

STYLE_CHOICES = (AUTO_STYLE,) + tuple(DESIGN_STYLES.keys())


def resolve_style(style_name: str) -> DesignStyle:
    """Returns the DesignStyle for `style_name`, falling back to
    "minimal" for AUTO_STYLE or any unrecognized name - AUTO_STYLE
    should normally already have been resolved to a concrete style
    name by jarvis.design_studio.brief before this is called; this
    fallback exists so a rendering call is never blocked by a missing/
    invalid style choice."""
    return DESIGN_STYLES.get(style_name, DESIGN_STYLES["minimal"])
