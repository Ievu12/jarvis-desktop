"""Tests for jarvis.design_studio.styles: the fixed DesignStyle preset
table. Confirms every named style has a complete, valid definition
(hex colors, real font paths from the confirmed-working set), AUTO_STYLE
is never itself a key, and resolve_style() degrades gracefully for an
unrecognized name rather than raising."""

from __future__ import annotations

import re

from jarvis.design_studio.styles import AUTO_STYLE, DESIGN_STYLES, STYLE_CHOICES, resolve_style

_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


def test_module_brief_lists_fifteen_styles():
    # Module brief, section 5's own list: Minimal, Elegant, Luxury,
    # Soft, Feminine, Wellness, Yoga, Beauty, K-Beauty, Lifestyle,
    # Professional, Educational, Bold, Modern, Clean.
    assert len(DESIGN_STYLES) == 15


def test_every_style_has_valid_hex_colors():
    for name, style in DESIGN_STYLES.items():
        for field in (
            "background_color_1", "background_color_2", "headline_color", "body_color",
            "cta_color", "cta_background", "accent_color",
        ):
            value = getattr(style, field)
            assert _HEX_COLOR_RE.match(value), f"{name}.{field} = {value!r} is not a valid hex color"


def test_every_style_uses_a_confirmed_working_font():
    confirmed_fonts = {"C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"}
    for name, style in DESIGN_STYLES.items():
        assert style.headline_font in confirmed_fonts, name
        assert style.body_font in confirmed_fonts, name


def test_auto_style_is_not_a_key_in_design_styles():
    assert AUTO_STYLE not in DESIGN_STYLES


def test_style_choices_includes_auto_plus_every_named_style():
    assert STYLE_CHOICES[0] == AUTO_STYLE
    assert set(STYLE_CHOICES[1:]) == set(DESIGN_STYLES.keys())


def test_resolve_style_returns_the_named_style():
    style = resolve_style("bold")
    assert style.name == "bold"


def test_resolve_style_falls_back_to_minimal_for_unknown_name():
    style = resolve_style("not_a_real_style")
    assert style.name == "minimal"


def test_resolve_style_falls_back_to_minimal_for_auto():
    # AUTO_STYLE should already have been resolved to a concrete style
    # by jarvis.design_studio.brief before reaching this function - this
    # confirms the fallback still doesn't raise if it somehow isn't.
    style = resolve_style(AUTO_STYLE)
    assert style.name == "minimal"
