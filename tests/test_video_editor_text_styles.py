"""Stage 4: text and subtitle styles - fonts, outline, shadow and
background box, as the export writes them (drawtext options), as the
preview draws them (jarvis.video_editor.text_render.TextLook) and as
the one-click style presets set them. Pixel-level preview/export
parity lives in test_video_editor_preview_compositor.py."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from jarvis.video_editor import captions, text_overlay, text_render
from jarvis.video_editor.captions import CAPTION_STYLE_PRESETS, CaptionStyle, apply_caption_preset
from jarvis.video_editor.text_overlay import TextOverlay, build_text_overlay_filter, text_look
from jarvis.video_editor.text_templates import TEXT_STYLE_PRESETS, apply_text_style

_TEXT = TextOverlay(text="Labas", start_seconds=0, end_seconds=2)


def test_new_fields_default_to_no_style_so_old_projects_look_the_same():
    assert build_text_overlay_filter([_TEXT]).count("borderw") == 0
    assert "box=1" not in build_text_overlay_filter([_TEXT])
    assert "shadowx" not in build_text_overlay_filter([_TEXT])
    assert text_look(_TEXT) == text_render.TextLook(shadow_color=(0, 0, 0, 153))


def test_style_options_are_written_for_every_animation():
    styled = dataclasses.replace(
        _TEXT, outline_width=5, outline_color="#1B3CFF", shadow_offset=6, shadow_opacity=0.5,
        background_opacity=0.8, background_color="#FFD700",
    )
    for animation in text_overlay.TEXT_ANIMATION_CHOICES:
        graph = build_text_overlay_filter([dataclasses.replace(styled, animation=animation)])
        assert "shadowx=6:shadowy=6:shadowcolor=black@0.500" in graph, animation
        assert "box=1:boxcolor=#FFD700@0.800:boxborderw=11" in graph, animation
        if animation != "glow":  # glow's halo is the border
            assert "borderw=5:bordercolor=#1B3CFF" in graph, animation
    glitch = build_text_overlay_filter([dataclasses.replace(styled, animation="glitch")])
    assert glitch.count("box=1") == 1  # only on the real-color copy, not the red/cyan ghosts


def test_style_sizes_follow_the_export_resolution():
    styled = dataclasses.replace(_TEXT, font_size=60, outline_width=4, shadow_offset=1, background_opacity=0.5)
    graph = build_text_overlay_filter([styled], scale=2.0)
    assert "fontsize=120" in graph and "borderw=8" in graph and "boxborderw=24" in graph
    small = build_text_overlay_filter([styled], scale=0.4)
    assert "shadowx=1:" in small  # a thin shadow never rounds away to nothing
    look = text_look(styled, 0.5)
    assert (look.outline_width, look.shadow_offset, look.box_padding) == (2, 1, 6)


def test_validation_of_style_values():
    assert dataclasses.replace(_TEXT, font="nope").validate() == ["Unknown font: 'nope'."]
    assert dataclasses.replace(_TEXT, outline_width=99).validate()
    assert dataclasses.replace(_TEXT, background_opacity=1.5).validate()
    assert dataclasses.replace(_TEXT, shadow_offset=-5, shadow_opacity=0.2).validate() == []


def test_fonts_fall_back_to_the_default_file_when_not_installed(monkeypatch, tmp_path):
    assert text_render.resolve_font_file("arial_bold", "DEFAULT.ttf") == "DEFAULT.ttf"
    assert text_render.resolve_font_file("unknown", "DEFAULT.ttf") == "DEFAULT.ttf"
    fake = tmp_path / "impact.ttf"
    fake.write_bytes(b"")
    monkeypatch.setitem(text_render._FONT_FILES, "impact", ("C:/nope/impact.ttf", str(fake)))
    assert text_render.resolve_font_file("impact", "DEFAULT.ttf") == str(fake)
    assert text_render.font_is_available("impact")
    monkeypatch.setitem(text_render._FONT_FILES, "impact", ("C:/nope/impact.ttf",))
    assert not text_render.font_is_available("impact")
    graph = build_text_overlay_filter([dataclasses.replace(_TEXT, font="impact")])
    assert f"fontfile='{text_overlay._escape_drawtext_text(text_overlay._DEFAULT_FONT_FILE)}'" in graph


def test_every_font_choice_has_a_label_and_lithuanian_letters_render():
    assert set(text_render.FONT_LABELS) == set(text_render.FONT_CHOICES)
    for font in text_render.FONT_CHOICES:
        path = text_render.resolve_font_file(font, "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf")
        if not Path(path).is_file():
            pytest.skip("no TrueType font in this environment")
        loaded = text_render.load_font(path, 40)
        # every Lithuanian letter has its own glyph (not the "missing" box)
        missing = loaded.getmask("\uffff").getbbox()
        for letter in "ąčęėįšųūžĄČĘĖĮŠŲŪŽ":
            assert loaded.getmask(letter).getbbox() != missing, (font, letter)


def test_text_style_presets_replace_only_style_fields():
    placed = dataclasses.replace(
        _TEXT, x_fraction=0.2, font_size=90, animation="bounce", outline_width=9, background_opacity=0.3,
    )
    for preset in TEXT_STYLE_PRESETS:
        styled = apply_text_style(placed, preset)
        assert styled.validate() == [], preset.name
        assert (styled.text, styled.x_fraction, styled.font_size, styled.animation) == ("Labas", 0.2, 90, "bounce")
    plain = apply_text_style(placed, TEXT_STYLE_PRESETS[0])
    assert (plain.outline_width, plain.background_opacity) == (0, 0.0)  # leftovers of the old look are cleared
    tiktok = apply_text_style(placed, next(p for p in TEXT_STYLE_PRESETS if p.name == "TikTok"))
    assert (tiktok.font, tiktok.outline_width) == ("impact", 6)


def test_caption_presets_keep_size_position_and_animation():
    style = CaptionStyle(font_size=70, position="top", animation="karaoke", outline_width=7, background=True)
    for name in CAPTION_STYLE_PRESETS:
        styled = apply_caption_preset(style, name)
        assert (styled.font_size, styled.position, styled.animation) == (70, "top", "karaoke")
    assert apply_caption_preset(style, "Klasikinis").outline_width == CaptionStyle().outline_width
    tiktok = apply_caption_preset(style, "TikTok")
    assert (tiktok.font, tiktok.background) == ("impact", False)


def test_caption_font_is_used_by_the_export(monkeypatch):
    monkeypatch.setitem(text_render._FONT_FILES, "georgia", (__file__,))  # any existing file stands in
    lines = [captions.CaptionLine(text="Labas", start_seconds=0, end_seconds=1)]
    graph = captions.build_caption_filter_from_lines(lines, CaptionStyle(font="georgia", animation="none"))
    assert captions._escape_drawtext_text(__file__) in graph
