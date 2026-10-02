"""Tests for jarvis.video_editor.text_templates: pure curated-data
lookups (TextTemplate/ColorPalette), no I/O, no ffmpeg needed."""

from __future__ import annotations

from jarvis.video_editor import text_templates
from jarvis.video_editor.text_overlay import TEXT_ANIMATION_CHOICES


def test_every_template_name_is_unique():
    names = [t.name for t in text_templates.TEXT_TEMPLATES]
    assert len(names) == len(set(names))


def test_template_names_tuple_matches_templates():
    assert text_templates.TEXT_TEMPLATE_NAMES == tuple(t.name for t in text_templates.TEXT_TEMPLATES)


def test_get_text_template_returns_the_matching_template():
    template = text_templates.get_text_template("Bold Reveal")
    assert template is not None
    assert template.animation == "pop_up"


def test_get_text_template_returns_none_for_unknown_name():
    assert text_templates.get_text_template("Does Not Exist") is None


def test_every_real_animation_except_none_has_at_least_one_template():
    covered = {t.animation for t in text_templates.TEXT_TEMPLATES}
    missing = set(TEXT_ANIMATION_CHOICES) - covered - {"none"}
    assert missing == set()


def test_every_template_has_a_valid_animation_value():
    for template in text_templates.TEXT_TEMPLATES:
        assert template.animation in TEXT_ANIMATION_CHOICES


def test_every_template_has_positive_font_size_speed_and_intensity():
    for template in text_templates.TEXT_TEMPLATES:
        assert template.font_size > 0
        assert template.speed > 0
        assert template.intensity > 0


def test_every_palette_name_is_unique():
    names = [p.name for p in text_templates.COLOR_PALETTES]
    assert len(names) == len(set(names))


def test_palette_names_tuple_matches_palettes():
    assert text_templates.COLOR_PALETTE_NAMES == tuple(p.name for p in text_templates.COLOR_PALETTES)


def test_get_color_palette_returns_the_matching_palette():
    palette = text_templates.get_color_palette("Autumn")
    assert palette is not None
    assert "#D97642" in palette.colors


def test_get_color_palette_returns_none_for_unknown_name():
    assert text_templates.get_color_palette("Does Not Exist") is None


def test_every_palette_has_at_least_three_colors():
    for palette in text_templates.COLOR_PALETTES:
        assert len(palette.colors) >= 3


def test_every_palette_color_is_a_real_hex_string():
    for palette in text_templates.COLOR_PALETTES:
        for color in palette.colors:
            assert color.startswith("#")
            assert len(color) == 7
            int(color[1:], 16)  # must not raise
