"""Tests for jarvis.video_studio.analysis.analyze_video(): combines
jarvis.video_studio.ffmpeg_utils' probes into one VideoAnalysis result.
Uses real ffmpeg-generated test videos (see
tests/test_video_studio_ffmpeg_utils.py's module docstring for why real
subprocess calls, not mocks, are used here). Skips if ffmpeg/ffprobe
aren't on PATH.
"""

from __future__ import annotations

import subprocess

import pytest

import dataclasses

from jarvis.video_studio.analysis import VideoAnalysis, analyze_video
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg/ffprobe not on PATH")


@pytest.fixture(scope="module")
def test_video(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("video_studio_analysis")
    video_path = out_dir / "test.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=3,format=yuv420p",
            "-f", "lavfi", "-i", "color=c=red:s=640x360:d=3,format=yuv420p",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-c:a", "aac",
            "-t", "6", str(video_path),
        ],
        capture_output=True, timeout=60, check=True,
    )
    return video_path


def test_analyze_video_reports_measurable_fields(test_video):
    result = analyze_video(test_video)
    assert result.error is None
    assert result.duration_seconds == pytest.approx(6.0, abs=0.3)
    assert result.width == 640
    assert result.height == 360
    assert result.aspect_ratio == "16:9"
    assert result.has_audio is True
    assert result.scene_count >= 1


def test_analyze_video_never_labels_anything_viral(test_video):
    # Per the module's brief: "Do not claim that a clip is 'viral'.
    # Describe it as a candidate based on measurable characteristics."
    # This is a structural guarantee (no such field exists) rather than
    # a string-matching test of prose the module doesn't generate.
    result = analyze_video(test_video)
    field_names = {f for f in result.__dataclass_fields__}
    assert not any("viral" in name.lower() for name in field_names)


def test_analyze_video_missing_file_returns_error_not_raise(tmp_path):
    result = analyze_video(tmp_path / "does_not_exist.mp4")
    assert result.error is not None
    assert result.duration_seconds == 0.0


def test_analyze_video_corrupted_file_returns_error_not_raise(tmp_path):
    bad = tmp_path / "corrupted.mp4"
    bad.write_bytes(b"not a video")
    result = analyze_video(bad)
    assert result.error is not None


def test_aspect_ratio_reduces_correctly(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("video_studio_analysis_916")
    video_path = out_dir / "vertical.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=1080x1920:d=1,format=yuv420p",
            "-an", "-c:v", "libx264", "-t", "1", str(video_path),
        ],
        capture_output=True, timeout=30, check=True,
    )
    result = analyze_video(video_path)
    assert result.aspect_ratio == "9:16"


def test_total_silence_seconds_sums_all_gaps(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("video_studio_analysis_silence")
    video_path = out_dir / "silence_test.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=4,format=yuv420p",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=4",
            "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-c:a", "aac",
            "-t", "4", str(video_path),
        ],
        capture_output=True, timeout=30, check=True,
    )
    result = analyze_video(video_path)
    assert result.total_silence_seconds == pytest.approx(4.0, abs=0.3)


def test_from_dict_round_trips_through_json_including_nested_dataclasses(test_video):
    # Regression test for a real bug found by hand-testing: dataclasses
    # .asdict() recursively converts nested dataclasses (SceneChange,
    # SilenceGap) to plain dicts, so VideoAnalysis(**data) (bare
    # unpacking) left scene_changes/silence_gaps as dicts instead of
    # SceneChange/SilenceGap instances - a AttributeError the first
    # time .timestamp_seconds or .duration_seconds was accessed on one.
    original = analyze_video(test_video)
    as_dict = dataclasses.asdict(original)
    reconstructed = VideoAnalysis.from_dict(as_dict)
    assert reconstructed == original
    for scene_change in reconstructed.scene_changes:
        assert scene_change.timestamp_seconds is not None  # would raise AttributeError if still a dict
    for gap in reconstructed.silence_gaps:
        assert gap.duration_seconds is not None
