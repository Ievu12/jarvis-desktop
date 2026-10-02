"""Tests for jarvis.video_studio.export.export_reel(): renders a
jarvis.video_studio.reel.ReelEditPlan into an actual video file. Uses
REAL ffmpeg subprocess calls against a small, generated source video
(see tests/test_video_studio_ffmpeg_utils.py's module docstring for
why real calls, not mocks, are used for this package's ffmpeg-wrapping
modules). Skipped entirely if ffmpeg isn't on PATH.

Confirms: every EXPORT_FORMATS preset produces exactly its documented
output dimensions, "original" keeps the source's own resolution,
multi-clip trim+concat produces the correct total duration, the
ORIGINAL SOURCE FILE IS NEVER MODIFIED (the module's brief's central
rule - checked explicitly, not assumed), and every error path (empty
plan, unknown format, missing source) raises ExportError with a clear
message rather than crashing or silently producing a broken file.
"""

from __future__ import annotations

import subprocess

import pytest

from jarvis.video_studio.export import EXPORT_FORMATS, ExportError, export_reel
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available, probe_video
from jarvis.video_studio.reel import PlannedClip, ReelEditPlan

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


@pytest.fixture(scope="module")
def source_video(tmp_path_factory):
    """An 8-second, 1280x720 source with two distinct halves (blue
    then red) and a continuous tone - real ffmpeg-generated, not a
    checked-in fixture."""
    out_dir = tmp_path_factory.mktemp("video_studio_export")
    video_path = out_dir / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=1280x720:d=4",
            "-f", "lavfi", "-i", "color=c=red:s=1280x720:d=4",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-c:a", "aac", "-t", "8",
            str(video_path),
        ],
        capture_output=True, timeout=60, check=True,
    )
    return video_path


def _plan(clips=None) -> ReelEditPlan:
    if clips is None:
        clips = [PlannedClip(0.0, 2.0, "first", "First caption")]
    return ReelEditPlan(
        clips=clips, hook="Test hook", cta="Test CTA", target_duration_seconds=15,
        style="Educational", pacing="Normal", total_duration_seconds=sum(c.duration_seconds for c in clips),
        silence_removal_suggested=False, zoom_crop_suggested=True, insufficient_data=False, message=None,
    )


# --- format presets produce correct dimensions ---------------------------------------------


@pytest.mark.parametrize(
    "format_name,expected_w,expected_h",
    [
        ("instagram_reel", 1080, 1920),
        ("tiktok", 1080, 1920),
        ("youtube_shorts", 1080, 1920),
        ("square", 1080, 1080),
        ("portrait_4_5", 1080, 1350),
        ("landscape_16_9", 1920, 1080),
    ],
)
def test_export_format_produces_correct_dimensions(source_video, tmp_path, format_name, expected_w, expected_h):
    output = tmp_path / f"{format_name}.mp4"
    result = export_reel(source_video, _plan(), export_format=format_name, output_path=output, burn_in_captions=False)
    assert result.width == expected_w
    assert result.height == expected_h
    assert output.is_file()


def test_original_format_keeps_source_resolution(source_video, tmp_path):
    output = tmp_path / "original.mp4"
    result = export_reel(source_video, _plan(), export_format="original", output_path=output, burn_in_captions=False)
    assert result.width == 1280
    assert result.height == 720


def test_all_export_formats_are_registered_and_exportable(source_video, tmp_path):
    for name in EXPORT_FORMATS:
        output = tmp_path / f"all_{name}.mp4"
        result = export_reel(source_video, _plan(), export_format=name, output_path=output, burn_in_captions=False)
        assert output.is_file()
        assert result.file_size_bytes > 0


# --- multi-clip trim + concat -------------------------------------------------------------


def test_multi_clip_plan_produces_correct_total_duration(source_video, tmp_path):
    clips = [PlannedClip(0.0, 2.0, "a", "cap a"), PlannedClip(5.0, 7.0, "b", "cap b")]
    output = tmp_path / "multi.mp4"
    result = export_reel(source_video, _plan(clips), export_format="original", output_path=output, burn_in_captions=False)
    assert result.duration_seconds == pytest.approx(4.0, abs=0.3)


# --- captions/subtitles ------------------------------------------------------------------


def test_burned_in_captions_do_not_leave_a_leftover_srt_file(source_video, tmp_path):
    output = tmp_path / "with_captions.mp4"
    export_reel(source_video, _plan(), export_format="original", output_path=output, burn_in_captions=True)
    assert output.is_file()
    assert not output.with_suffix(".captions.srt").exists()


def test_export_without_captions_still_succeeds(source_video, tmp_path):
    output = tmp_path / "no_captions.mp4"
    result = export_reel(source_video, _plan(), export_format="original", output_path=output, burn_in_captions=False)
    assert output.is_file()
    assert result.duration_seconds > 0


# --- original source file is never modified -------------------------------------------------


def test_original_source_file_is_never_modified(source_video, tmp_path):
    original_bytes = source_video.read_bytes()
    output = tmp_path / "export.mp4"
    export_reel(source_video, _plan(), export_format="instagram_reel", output_path=output)
    assert source_video.read_bytes() == original_bytes


# --- error handling -----------------------------------------------------------------------


def test_empty_plan_raises_export_error(source_video, tmp_path):
    empty_plan = ReelEditPlan(
        clips=[], hook="", cta="", target_duration_seconds=15, style="s", pacing="p",
        total_duration_seconds=0.0, silence_removal_suggested=False, zoom_crop_suggested=False,
        insufficient_data=True, message="none",
    )
    with pytest.raises(ExportError, match="no clips"):
        export_reel(source_video, empty_plan, export_format="instagram_reel", output_path=tmp_path / "x.mp4")


def test_unknown_format_raises_export_error(source_video, tmp_path):
    with pytest.raises(ExportError, match="Unknown export format"):
        export_reel(source_video, _plan(), export_format="not_a_real_format", output_path=tmp_path / "x.mp4")


def test_missing_source_raises_export_error(tmp_path):
    with pytest.raises(ExportError, match="not found"):
        export_reel(
            tmp_path / "does_not_exist.mp4", _plan(), export_format="instagram_reel",
            output_path=tmp_path / "x.mp4",
        )
