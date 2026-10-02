"""Tests for jarvis.video_editor.text_overlay: TextOverlay validation
and build_text_overlay_filter()'s own filter-string construction are
pure, I/O-free unit tests; a real ffmpeg export test at the bottom
proves the overlay genuinely appears on screen, not just that ffmpeg
exits 0."""

from __future__ import annotations

import subprocess

import pytest
from PIL import Image

from jarvis.video_editor import media_import, multisource_export as mse, storage, text_overlay
from jarvis.video_editor.timeline import Timeline, TimelineClip
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available


def test_default_text_overlay_values():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0)
    assert overlay.x_fraction == 0.5
    assert overlay.y_fraction == 0.1
    assert overlay.font_size == text_overlay.DEFAULT_TEXT_FONT_SIZE
    assert overlay.color == text_overlay.DEFAULT_TEXT_COLOR
    assert overlay.animation == "none"
    assert overlay.validate() == []


def test_validate_rejects_empty_text():
    overlay = text_overlay.TextOverlay(text="   ", start_seconds=0.0, end_seconds=1.0)
    problems = overlay.validate()
    assert any("no text" in p for p in problems)


def test_validate_rejects_backwards_time_window():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=5.0, end_seconds=2.0)
    problems = overlay.validate()
    assert any("end time must be after" in p for p in problems)


def test_validate_rejects_out_of_range_position():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0, x_fraction=1.5, y_fraction=-0.2)
    problems = overlay.validate()
    assert len(problems) == 2


def test_validate_rejects_non_positive_font_size():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0, font_size=0)
    problems = overlay.validate()
    assert any("font size" in p for p in problems)


def test_validate_rejects_fade_without_duration():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0, animation="fade", fade_seconds=0.0)
    problems = overlay.validate()
    assert any("fade duration" in p for p in problems)


def test_build_text_overlay_filter_with_no_overlays_is_a_passthrough():
    clause = text_overlay.build_text_overlay_filter([])
    assert clause == "[outv]null[textv]"


def test_build_text_overlay_filter_rejects_invalid_overlay():
    bad_overlay = text_overlay.TextOverlay(text="", start_seconds=0.0, end_seconds=1.0)
    with pytest.raises(text_overlay.TextOverlayError):
        text_overlay.build_text_overlay_filter([bad_overlay])


def test_build_text_overlay_filter_produces_one_drawtext_clause_per_overlay():
    overlays = [
        text_overlay.TextOverlay(text="First", start_seconds=0.0, end_seconds=1.0),
        text_overlay.TextOverlay(text="Second", start_seconds=1.0, end_seconds=2.0),
    ]
    clause = text_overlay.build_text_overlay_filter(overlays)
    assert clause.count("drawtext=") == 2
    assert clause.startswith("[outv]")
    assert clause.endswith("[textv]")


def test_build_text_overlay_filter_uses_custom_labels():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0)
    clause = text_overlay.build_text_overlay_filter([overlay], video_label="capv", output_label="finalv")
    assert clause.startswith("[capv]")
    assert clause.endswith("[finalv]")


def test_build_text_overlay_filter_escapes_special_characters():
    overlay = text_overlay.TextOverlay(text="It's: cool", start_seconds=0.0, end_seconds=1.0)
    clause = text_overlay.build_text_overlay_filter([overlay])
    assert "It\\'s\\: cool" in clause


def test_build_text_overlay_filter_with_fade_adds_alpha_expression():
    overlay = text_overlay.TextOverlay(
        text="Hi", start_seconds=0.0, end_seconds=2.0, animation="fade", fade_seconds=0.5,
    )
    clause = text_overlay.build_text_overlay_filter([overlay])
    assert "alpha=" in clause


# --- real, ffmpeg-dependent export test ------------------------------------------------------


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_real_export_with_text_overlay_is_visually_correct(tmp_path):
    project = storage.create_project()
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=1080x1920:d=2", "-c:v", "libx264", "-t", "2", str(clip)],
        capture_output=True, timeout=30, check=True,
    )
    m1 = media_import.import_media(clip, project, media_item_id="m1")
    media_items = {"m1": m1}
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=2.0),
    ), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "720p")

    overlay = text_overlay.TextOverlay(text="TITLE", start_seconds=0.3, end_seconds=1.7)
    clause = text_overlay.build_text_overlay_filter([overlay])

    plain_path = project.exports_dir / "plain.mp4"
    text_path = project.exports_dir / "with_text.mp4"
    mse.export_timeline(timeline, media_items, export_format=fmt, output_path=plain_path)
    mse.export_timeline(timeline, media_items, export_format=fmt, output_path=text_path, text_overlay_filter=clause)

    frame_plain = tmp_path / "plain.png"
    frame_text = tmp_path / "text.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "1.0", "-i", str(plain_path), "-frames:v", "1", str(frame_plain)],
        capture_output=True, timeout=15, check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "1.0", "-i", str(text_path), "-frames:v", "1", str(frame_text)],
        capture_output=True, timeout=15, check=True,
    )
    assert Image.open(frame_plain).tobytes() != Image.open(frame_text).tobytes()
    mse._verify_playable_encoding(text_path, ffmpeg_path="ffmpeg")  # must not raise


# --- kinetic typography animations (typewriter/pop_up/zoom/bounce/glitch/glow) -----------------


def test_text_animation_choices_includes_all_kinetic_kinds():
    assert text_overlay.TEXT_ANIMATION_CHOICES == (
        "none", "fade", "typewriter", "pop_up", "pop_out", "slide_in", "slide_out",
        "bounce", "shake", "glitch", "glow", "zoom",
    )


def test_default_speed_and_intensity():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0)
    assert overlay.speed == 1.0
    assert overlay.intensity == 1.0


def test_validate_rejects_non_positive_speed_and_intensity():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0, speed=0.0, intensity=-1.0)
    problems = overlay.validate()
    assert len(problems) == 2


def test_typewriter_produces_one_clause_per_character():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=5.0, animation="typewriter")
    clause = text_overlay.build_text_overlay_filter([overlay])
    assert clause.count("drawtext=") == 2  # "H" then "Hi"


def test_typewriter_substrings_are_cumulative():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=5.0, animation="typewriter")
    clause = text_overlay.build_text_overlay_filter([overlay])
    assert "text='H'" in clause
    assert "text='Hi'" in clause


def test_typewriter_with_very_short_window_never_raises():
    # A window shorter than one character's own reveal duration must
    # still produce a valid (even if minimal) clause, never raise or
    # produce an empty/broken filtergraph.
    overlay = text_overlay.TextOverlay(text="Hello", start_seconds=0.0, end_seconds=0.01, animation="typewriter")
    clause = text_overlay.build_text_overlay_filter([overlay])
    assert "[outv]" in clause and "[textv]" in clause


def test_pop_up_has_a_time_varying_fontsize():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0, animation="pop_up")
    clause = text_overlay.build_text_overlay_filter([overlay])
    assert "fontsize='if(lt(t," in clause


def test_zoom_has_a_continuously_growing_fontsize_distinct_from_pop_up():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=2.0, animation="zoom")
    clause = text_overlay.build_text_overlay_filter([overlay])
    assert "fontsize='" in clause
    assert "if(lt(t," not in clause  # zoom ramps continuously, no one-time if() branch


def test_bounce_has_a_sine_based_y_expression():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0, animation="bounce")
    clause = text_overlay.build_text_overlay_filter([overlay])
    assert "sin(" in clause
    assert "exp(" in clause


def test_glitch_produces_three_color_offset_clauses():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0, animation="glitch")
    clause = text_overlay.build_text_overlay_filter([overlay])
    assert clause.count("drawtext=") == 3
    assert "red@0.6" in clause
    assert "cyan@0.6" in clause


def test_glow_adds_a_wide_soft_colored_border():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0, animation="glow", intensity=2.0)
    clause = text_overlay.build_text_overlay_filter([overlay])
    assert "borderw=12" in clause  # 6 * intensity(2.0)


def test_pop_out_has_a_time_varying_fontsize_shrinking_near_the_end():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=2.0, animation="pop_out")
    clause = text_overlay.build_text_overlay_filter([overlay])
    assert "fontsize='if(gt(t," in clause


def test_pop_out_and_pop_up_produce_different_clauses():
    pop_up = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=2.0, animation="pop_up")
    pop_out = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=2.0, animation="pop_out")
    assert text_overlay.build_text_overlay_filter([pop_up]) != text_overlay.build_text_overlay_filter([pop_out])


def test_default_direction_is_left():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0)
    assert overlay.direction == "left"


def test_slide_in_has_a_time_varying_x_expression():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0, animation="slide_in")
    clause = text_overlay.build_text_overlay_filter([overlay])
    assert "x='if(lt(t," in clause


def test_slide_out_has_a_time_varying_x_expression():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0, animation="slide_out")
    clause = text_overlay.build_text_overlay_filter([overlay])
    assert "x='if(gt(t," in clause


def test_slide_in_left_and_right_produce_different_clauses():
    left = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0, animation="slide_in", direction="left")
    right = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0, animation="slide_in", direction="right")
    assert text_overlay.build_text_overlay_filter([left]) != text_overlay.build_text_overlay_filter([right])


def test_shake_has_a_sine_based_x_expression_across_the_full_duration():
    overlay = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0, animation="shake")
    clause = text_overlay.build_text_overlay_filter([overlay])
    assert "sin(" in clause
    assert "x='" in clause


def test_shake_is_distinct_from_glitch_and_bounce():
    shake = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0, animation="shake")
    glitch = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0, animation="glitch")
    bounce = text_overlay.TextOverlay(text="Hi", start_seconds=0.0, end_seconds=1.0, animation="bounce")
    shake_clause = text_overlay.build_text_overlay_filter([shake])
    assert shake_clause != text_overlay.build_text_overlay_filter([glitch])
    assert shake_clause != text_overlay.build_text_overlay_filter([bounce])
    assert shake_clause.count("drawtext=") == 1  # unlike glitch's 3-layer chromatic split


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
@pytest.mark.parametrize(
    "animation",
    ["typewriter", "pop_up", "pop_out", "slide_in", "slide_out", "zoom", "bounce", "shake", "glitch", "glow"],
)
def test_real_export_with_kinetic_animation_succeeds_and_stays_compatible(tmp_path, animation):
    project = storage.create_project()
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=1080x1920:d=2", "-c:v", "libx264", "-t", "2", str(clip)],
        capture_output=True, timeout=30, check=True,
    )
    m1 = media_import.import_media(clip, project, media_item_id="m1")
    media_items = {"m1": m1}
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=2.0),
    ), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "720p")

    overlay = text_overlay.TextOverlay(text="HELLO", start_seconds=0.2, end_seconds=1.8, animation=animation)
    clause = text_overlay.build_text_overlay_filter([overlay])

    plain_path = project.exports_dir / "plain.mp4"
    out_path = project.exports_dir / f"{animation}.mp4"
    mse.export_timeline(timeline, media_items, export_format=fmt, output_path=plain_path)
    result = mse.export_timeline(timeline, media_items, export_format=fmt, output_path=out_path, text_overlay_filter=clause)
    assert result.output_path.is_file()
    mse._verify_playable_encoding(out_path, ffmpeg_path="ffmpeg")  # must not raise

    frame_plain = tmp_path / "plain.png"
    frame_anim = tmp_path / f"{animation}.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "1.0", "-i", str(plain_path), "-frames:v", "1", str(frame_plain)],
        capture_output=True, timeout=15, check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "1.0", "-i", str(out_path), "-frames:v", "1", str(frame_anim)],
        capture_output=True, timeout=15, check=True,
    )
    assert Image.open(frame_plain).tobytes() != Image.open(frame_anim).tobytes()


# --- rotation and resolution scaling -----------------------------------------------------------

from pathlib import Path  # noqa: E402

from jarvis.video_editor.text_overlay import (  # noqa: E402
    ROTATABLE_TEXT_ANIMATIONS,
    TextOverlay,
    build_rotated_text_filters,
    build_text_overlay_filter,
    text_scale_for,
)


def test_rotation_is_only_valid_with_plain_or_fade_animation():
    assert not TextOverlay(text="Hi", start_seconds=0, end_seconds=1, rotation_degrees=20).validate()
    assert not TextOverlay(text="Hi", start_seconds=0, end_seconds=1, rotation_degrees=20, animation="fade").validate()
    problems = TextOverlay(text="Hi", start_seconds=0, end_seconds=1, rotation_degrees=20, animation="bounce").validate()
    assert any("Rotated text" in p for p in problems)
    assert set(ROTATABLE_TEXT_ANIMATIONS) == {"none", "fade"}


def test_full_turn_counts_as_unrotated():
    assert not TextOverlay(text="Hi", start_seconds=0, end_seconds=1, rotation_degrees=360).is_rotated


def test_drawtext_filter_skips_rotated_overlays():
    plain = TextOverlay(text="Plain", start_seconds=0, end_seconds=1)
    turned = TextOverlay(text="Turned", start_seconds=0, end_seconds=1, rotation_degrees=15)
    clause = build_text_overlay_filter([plain, turned])
    assert "Plain" in clause and "Turned" not in clause


def test_rotated_text_becomes_an_image_overlay_input(tmp_path):
    turned = TextOverlay(text="Turned", start_seconds=1, end_seconds=2, rotation_degrees=15, animation="fade")
    filters, label = build_rotated_text_filters(
        [TextOverlay(text="Plain", start_seconds=0, end_seconds=1), turned],
        canvas_width=1080, canvas_height=1920, cwd=tmp_path, video_label="textv", first_input_index=3,
    )
    assert len(filters) == 1
    extra_args, clause = filters[0]
    assert extra_args[-2] == "-i" and Path(extra_args[-1]).is_file()
    assert clause.startswith("[3:v]") and "fade=t=in" in clause and "[textv]" in clause
    assert label == "rtextv0"


def test_text_scale_keeps_1080p_sizes_and_scales_other_tiers():
    assert text_scale_for(1080, 1920) == 1.0
    assert text_scale_for(720, 1280) == pytest.approx(720 / 1080)
    assert text_scale_for(3840, 2160) == 2.0
    clause = build_text_overlay_filter([TextOverlay(text="Hi", start_seconds=0, end_seconds=1, font_size=60)], scale=2.0)
    assert "fontsize=120" in clause


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_rotated_text_appears_in_a_real_render(tmp_path):
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=640x360:r=30:d=2", "-c:v", "libx264", str(clip)],
        capture_output=True, timeout=30, check=True,
    )
    turned = TextOverlay(text="TURNED", start_seconds=0.5, end_seconds=1.5, rotation_degrees=90, font_size=60)
    filters, label = build_rotated_text_filters(
        [turned], canvas_width=640, canvas_height=360, cwd=tmp_path, video_label="base", first_input_index=1,
    )
    (extra_args, clause), = filters
    frame = tmp_path / "frame.png"
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(clip), *extra_args, "-filter_complex", f"[0:v]null[base];{clause}",
         "-map", f"[{label}]", "-ss", "1.0", "-frames:v", "1", str(frame)],
        capture_output=True, timeout=30, check=True, cwd=str(tmp_path),
    )
    left, top, right, bottom = Image.open(frame).convert("L").point(lambda v: 255 if v > 128 else 0).getbbox()
    assert (bottom - top) > (right - left) * 2  # turned 90 degrees: taller than wide
