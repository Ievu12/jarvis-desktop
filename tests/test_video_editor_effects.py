"""Tests for jarvis.video_editor.effects: EffectSpec validation and
build_segment_effect_filter()'s own filter-string construction are pure,
I/O-free unit tests (no ffmpeg needed); a handful of ffmpeg-guarded
tests at the bottom run a REAL export through multisource_export and
extract real frames to prove the motion genuinely changes the picture
over time, not just that ffmpeg exits 0."""

from __future__ import annotations

import subprocess

import pytest
from PIL import Image

from jarvis.video_editor import effects, media_import, multisource_export as mse, storage
from jarvis.video_editor.timeline import Timeline, TimelineStill
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available


def test_default_effect_spec_has_no_effect():
    spec = effects.EffectSpec()
    assert spec.motion == "none"
    assert spec.fade == "none"
    assert spec.brightness == 0.0 and spec.contrast == 1.0 and spec.saturation == 1.0
    assert spec.validate() == []


def test_validate_rejects_out_of_range_intensity():
    spec = effects.EffectSpec(motion="zoom_in", motion_intensity=10.0)
    problems = spec.validate()
    assert any("intensity" in p for p in problems)


def test_validate_rejects_fade_without_duration():
    spec = effects.EffectSpec(fade="fade_in", fade_seconds=0.0)
    problems = spec.validate()
    assert any("duration" in p for p in problems)


def test_validate_rejects_out_of_range_color_values():
    spec = effects.EffectSpec(brightness=5.0, contrast=-1.0, saturation=100.0)
    problems = spec.validate()
    assert len(problems) == 3


def test_build_segment_effect_filter_with_no_effect_is_a_cheap_passthrough():
    clause = effects.build_segment_effect_filter(
        effects.EffectSpec(), width=1080, height=1920, duration_seconds=3.0, fps=30,
        video_label="[vraw0]", output_label="v0",
    )
    assert clause == "[vraw0]null[v0]"


def test_build_segment_effect_filter_rejects_invalid_spec():
    bad_spec = effects.EffectSpec(motion_intensity=99.0)
    with pytest.raises(effects.EffectError):
        effects.build_segment_effect_filter(
            bad_spec, width=1080, height=1920, duration_seconds=3.0, fps=30,
            video_label="[vraw0]", output_label="v0",
        )


@pytest.mark.parametrize("motion", ["zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down"])
def test_build_segment_effect_filter_contains_zoompan_for_every_motion_kind(motion):
    clause = effects.build_segment_effect_filter(
        effects.EffectSpec(motion=motion, motion_intensity=1.3), width=1080, height=1920,
        duration_seconds=3.0, fps=30, video_label="[vraw0]", output_label="v0",
    )
    assert "zoompan=" in clause
    assert clause.endswith("[v0]")
    # d=1 (not the segment's own frame count) - see _zoompan_clause()'s
    # own docstring for the real duration-multiplication bug this
    # guards against.
    assert ":d=1:" in clause


def test_build_segment_effect_filter_combines_motion_fade_and_color_in_order():
    spec = effects.EffectSpec(motion="zoom_in", fade="fade_both", fade_seconds=0.5, brightness=0.2)
    clause = effects.build_segment_effect_filter(
        spec, width=1080, height=1920, duration_seconds=3.0, fps=30,
        video_label="[vraw0]", output_label="v0",
    )
    stages = clause.split(";")
    assert len(stages) == 3
    assert "zoompan=" in stages[0]
    assert "fade=" in stages[1]
    assert "eq=" in stages[2]
    assert stages[2].endswith("[v0]")


def test_build_segment_effect_filter_fade_only_has_no_zoompan():
    clause = effects.build_segment_effect_filter(
        effects.EffectSpec(fade="fade_in", fade_seconds=1.0), width=1080, height=1920,
        duration_seconds=3.0, fps=30, video_label="[vraw0]", output_label="v0",
    )
    assert "zoompan" not in clause
    assert "fade=t=in" in clause


def test_build_segment_effect_filter_color_only_has_no_zoompan_or_fade():
    clause = effects.build_segment_effect_filter(
        effects.EffectSpec(brightness=0.1, contrast=1.5, saturation=0.5), width=1080, height=1920,
        duration_seconds=3.0, fps=30, video_label="[vraw0]", output_label="v0",
    )
    assert "zoompan" not in clause
    assert "fade=" not in clause
    assert "eq=brightness=0.1:contrast=1.5:saturation=0.5" in clause


# --- real, ffmpeg-dependent export tests -----------------------------------------------------

pytestmark_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


@pytestmark_ffmpeg
def test_real_export_with_zoom_in_produces_correct_duration_and_changing_frames(tmp_path):
    # Real regression test for a hand-hit bug: zoompan's own `d=`
    # parameter, if set to the segment's own total frame count instead
    # of 1, multiplied the segment's duration by itself (a 3s clip
    # became a measured 270s export). This test asserts the REAL,
    # ffprobe-measured duration matches the requested display duration,
    # and that frames extracted at different timestamps genuinely
    # differ (proving real zoom motion, not a frozen/static output).
    # A patterned (not flat-color) photo is required here - zooming
    # into a uniform color produces identical pixels at every frame
    # (a real, hand-hit false-negative in this test's own first draft,
    # not a backend bug), so a frame with real spatial detail is needed
    # to prove the crop window genuinely moves over time.
    project = storage.create_project()
    photo = tmp_path / "photo.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=800x600:duration=1", "-frames:v", "1", str(photo)],
        capture_output=True, timeout=15, check=True,
    )
    m1 = media_import.import_media(photo, project, media_item_id="m1")
    media_items = {"m1": m1}

    still = TimelineStill(
        clip_id="c1", media_item_id="m1", display_duration_seconds=3.0,
        effect=effects.EffectSpec(motion="zoom_in", motion_intensity=1.3),
    )
    timeline = Timeline(items=(still,), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "720p")
    output_path = project.exports_dir / "zoom.mp4"

    result = mse.export_timeline(timeline, media_items, export_format=fmt, output_path=output_path)
    assert abs(result.duration_seconds - 3.0) < 0.3

    frame_a = tmp_path / "a.png"
    frame_b = tmp_path / "b.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "0.1", "-i", str(output_path), "-frames:v", "1", str(frame_a)],
        capture_output=True, timeout=15, check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "2.5", "-i", str(output_path), "-frames:v", "1", str(frame_b)],
        capture_output=True, timeout=15, check=True,
    )
    assert Image.open(frame_a).tobytes() != Image.open(frame_b).tobytes()


@pytestmark_ffmpeg
def test_real_export_with_effect_stays_windows_media_player_compatible(tmp_path):
    project = storage.create_project()
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (800, 600), color=(50, 60, 70)).save(photo, "JPEG")
    m1 = media_import.import_media(photo, project, media_item_id="m1")
    media_items = {"m1": m1}

    still = TimelineStill(
        clip_id="c1", media_item_id="m1", display_duration_seconds=2.0,
        effect=effects.EffectSpec(motion="pan_left", fade="fade_both", fade_seconds=0.3, contrast=1.2),
    )
    timeline = Timeline(items=(still,), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "720p")
    output_path = project.exports_dir / "combined.mp4"

    mse.export_timeline(timeline, media_items, export_format=fmt, output_path=output_path)
    mse._verify_playable_encoding(output_path, ffmpeg_path="ffmpeg")  # must not raise


@pytestmark_ffmpeg
def test_invalid_effect_spec_on_a_timeline_item_raises_before_running_ffmpeg(tmp_path):
    project = storage.create_project()
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (800, 600), color=(1, 2, 3)).save(photo, "JPEG")
    m1 = media_import.import_media(photo, project, media_item_id="m1")
    media_items = {"m1": m1}

    still = TimelineStill(
        clip_id="c1", media_item_id="m1", display_duration_seconds=2.0,
        effect=effects.EffectSpec(motion="zoom_in", motion_intensity=99.0),
    )
    timeline = Timeline(items=(still,), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "720p")
    output_path = project.exports_dir / "invalid.mp4"

    # Timeline.validate() itself should already catch this before
    # export_timeline() is even called, in real GUI usage - this test
    # directly targets build_filtergraph()'s own defensive EffectError
    # handling since the GUI's own pre-export validate() call is a
    # separate, already-tested code path (test_video_editor_timeline.py).
    with pytest.raises(mse.MultiSourceExportError):
        mse.export_timeline(timeline, media_items, export_format=fmt, output_path=output_path)


# --- animation preview (render_effect_preview) -----------------------------------------------


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_render_effect_preview_produces_three_real_frames_showing_progress(tmp_path):
    photo = tmp_path / "photo.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=800x600:duration=1", "-frames:v", "1", str(photo)],
        capture_output=True, timeout=15, check=True,
    )
    spec = effects.EffectSpec(motion="zoom_in", motion_intensity=1.3)
    frames = effects.render_effect_preview(
        photo, spec=spec, width=360, height=640, duration_seconds=2.0, is_still=True, output_dir=tmp_path / "preview",
    )
    assert len(frames) == 3
    for frame in frames:
        assert frame.is_file()
    assert Image.open(frames[0]).tobytes() != Image.open(frames[2]).tobytes()


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_render_effect_preview_rejects_invalid_spec(tmp_path):
    photo = tmp_path / "photo.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=800x600:duration=1", "-frames:v", "1", str(photo)],
        capture_output=True, timeout=15, check=True,
    )
    bad_spec = effects.EffectSpec(motion_intensity=99.0)
    with pytest.raises(effects.EffectError):
        effects.render_effect_preview(
            photo, spec=bad_spec, width=360, height=640, duration_seconds=2.0, is_still=True,
            output_dir=tmp_path / "preview",
        )


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_render_effect_preview_with_no_motion_still_produces_identical_frames(tmp_path):
    photo = tmp_path / "photo.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=800x600:duration=1", "-frames:v", "1", str(photo)],
        capture_output=True, timeout=15, check=True,
    )
    frames = effects.render_effect_preview(
        photo, spec=effects.EffectSpec(), width=360, height=640, duration_seconds=2.0, is_still=True,
        output_dir=tmp_path / "preview",
    )
    # Near-identical, not necessarily byte-exact - H.264's own lossy
    # compression introduces tiny per-frame encoding noise even for a
    # genuinely unchanging source (a real, hand-hit false assumption in
    # this test's own first draft, not a backend bug): a static looped
    # image's start/end frames differ by a handful of bytes out of
    # ~200K, nowhere near the scale of a real zoom/pan's own visible
    # difference (see the sibling "shows_progress" test above).
    start_bytes = Image.open(frames[0]).tobytes()
    end_bytes = Image.open(frames[2]).tobytes()
    diff_count = sum(1 for a, b in zip(start_bytes, end_bytes) if a != b)
    assert diff_count < len(start_bytes) * 0.01
