"""Tests for jarvis.video_editor.live_preview.render_preview_frame: a
real, single-frame ffmpeg render of the Timeline's own already-correct
filtergraph (the same one export_timeline() uses) at a chosen
timestamp - every test here uses a real ffmpeg subprocess and a real
synthetic clip, never a mock, since the whole point of this module is
that the preview genuinely matches what export would produce."""

from __future__ import annotations

import subprocess

import pytest
from PIL import Image

from jarvis.video_editor import media_import, multisource_export as mse, storage
from jarvis.video_editor.live_preview import LivePreviewError, PreviewFilters, render_preview_frame
from jarvis.video_editor.text_overlay import TextOverlay, build_text_overlay_filter
from jarvis.video_editor.timeline import Timeline, TimelineClip
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available


def _make_clip(tmp_path, *, color="blue", duration=3, size="640x360"):
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}:d={duration}",
         "-c:v", "libx264", "-t", str(duration), str(clip)],
        capture_output=True, timeout=30, check=True,
    )
    return clip


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_render_preview_frame_produces_a_real_png(tmp_path):
    project = storage.create_project()
    clip = _make_clip(tmp_path)
    m1 = media_import.import_media(clip, project, media_item_id="m1")
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=3.0),
    ), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "720p")

    output_path = render_preview_frame(
        timeline, {"m1": m1}, export_format=fmt, timestamp_seconds=1.0,
        filters=PreviewFilters(), cwd=project.exports_dir,
    )
    assert output_path.is_file()
    image = Image.open(output_path)
    assert image.size == (fmt.width, fmt.height)


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_render_preview_frame_with_text_overlay_shows_the_real_text(tmp_path):
    project = storage.create_project()
    clip = _make_clip(tmp_path, color="black")
    m1 = media_import.import_media(clip, project, media_item_id="m1")
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=3.0),
    ), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "720p")

    plain = render_preview_frame(
        timeline, {"m1": m1}, export_format=fmt, timestamp_seconds=1.0,
        filters=PreviewFilters(), cwd=project.exports_dir,
    )
    overlay_filter = build_text_overlay_filter(
        [TextOverlay(text="HELLO", start_seconds=0.0, end_seconds=3.0)],
    )
    with_text = render_preview_frame(
        timeline, {"m1": m1}, export_format=fmt, timestamp_seconds=1.0,
        filters=PreviewFilters(text_overlay_filter=overlay_filter), cwd=project.exports_dir,
    )
    assert Image.open(plain).tobytes() != Image.open(with_text).tobytes()


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_render_preview_frame_at_different_timestamps_can_differ(tmp_path):
    # A moving/animated overlay should look different at different
    # timestamps - proves the preview genuinely seeks the assembled
    # OUTPUT timeline, not just always rendering frame 0.
    project = storage.create_project()
    clip = _make_clip(tmp_path, color="green", duration=4)
    m1 = media_import.import_media(clip, project, media_item_id="m1")
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=4.0),
    ), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "720p")
    overlay_filter = build_text_overlay_filter(
        [TextOverlay(text="POP", start_seconds=0.0, end_seconds=4.0, animation="pop_up")],
    )
    early = render_preview_frame(
        timeline, {"m1": m1}, export_format=fmt, timestamp_seconds=0.1,
        filters=PreviewFilters(text_overlay_filter=overlay_filter), cwd=project.exports_dir,
    )
    later = render_preview_frame(
        timeline, {"m1": m1}, export_format=fmt, timestamp_seconds=2.0,
        filters=PreviewFilters(text_overlay_filter=overlay_filter), cwd=project.exports_dir,
    )
    assert Image.open(early).tobytes() != Image.open(later).tobytes()


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_render_preview_frame_clamps_a_timestamp_past_the_end(tmp_path):
    project = storage.create_project()
    clip = _make_clip(tmp_path, duration=2)
    m1 = media_import.import_media(clip, project, media_item_id="m1")
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=2.0),
    ), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "720p")
    output_path = render_preview_frame(
        timeline, {"m1": m1}, export_format=fmt, timestamp_seconds=999.0,
        filters=PreviewFilters(), cwd=project.exports_dir,
    )
    assert output_path.is_file()


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_render_preview_frame_clamps_a_negative_timestamp(tmp_path):
    project = storage.create_project()
    clip = _make_clip(tmp_path)
    m1 = media_import.import_media(clip, project, media_item_id="m1")
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=3.0),
    ), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "720p")
    output_path = render_preview_frame(
        timeline, {"m1": m1}, export_format=fmt, timestamp_seconds=-5.0,
        filters=PreviewFilters(), cwd=project.exports_dir,
    )
    assert output_path.is_file()


def test_render_preview_frame_on_an_empty_timeline_raises_a_clear_error(tmp_path):
    output_dir = tmp_path / "out"
    with pytest.raises(LivePreviewError, match="no clips or photos|real duration"):
        render_preview_frame(
            Timeline(), {}, export_format=mse.resolve_export_format("9:16", "720p"),
            timestamp_seconds=0.0, filters=PreviewFilters(), cwd=output_dir,
        )


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_render_preview_frame_with_missing_media_raises_a_clear_error(tmp_path):
    from pathlib import Path

    from jarvis.video_editor.media_import import MediaItem

    missing_media = MediaItem(
        media_item_id="m1", original_filename="gone.mp4", stored_path=Path("does_not_exist.mp4"),
        kind="video", duration_seconds=3.0, width=640, height=360, fps=30.0,
    )
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=3.0),
    ), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "720p")
    with pytest.raises(LivePreviewError):
        render_preview_frame(
            timeline, {"m1": missing_media}, export_format=fmt, timestamp_seconds=0.0,
            filters=PreviewFilters(), cwd=tmp_path,
        )
