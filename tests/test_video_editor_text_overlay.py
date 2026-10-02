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
