"""Tests for jarvis.video_studio.ffmpeg_utils: subprocess wrappers
around the system ffmpeg/ffprobe binaries. These tests use REAL ffmpeg/
ffprobe subprocess calls against small, generated test video files
(built with ffmpeg's own lavfi test-source filters, not checked-in
binary fixtures) - this module's entire job is correctly invoking and
parsing a real external binary, so mocking subprocess.run would only
test that this module calls subprocess.run with some arguments, not
that those arguments actually work against real ffmpeg output. Skips
the whole file if ffmpeg/ffprobe aren't on PATH (matches
ffmpeg_available()'s own check), so this suite degrades gracefully on
a machine without FFmpeg installed rather than failing confusingly.
"""

from __future__ import annotations

import subprocess

import pytest

from jarvis.video_studio.ffmpeg_utils import (
    FFmpegError,
    detect_scene_changes,
    detect_silence,
    extract_frame,
    ffmpeg_available,
    probe_video,
)

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg/ffprobe not on PATH")


@pytest.fixture(scope="module")
def test_video(tmp_path_factory):
    """A 6-second synthetic video: a 3s blue segment then a 3s red
    segment (one scene change at t=3), with 2s of audible tone then 2s
    of true silence then 2s more tone (one silence gap at 2-4s) - built
    once per test module run with ffmpeg's lavfi sources, not a
    checked-in binary fixture."""
    out_dir = tmp_path_factory.mktemp("video_studio_ffmpeg")
    video_path = out_dir / "test.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=3,format=yuv420p",
            "-f", "lavfi", "-i", "color=c=red:s=320x240:d=3,format=yuv420p",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono:d=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-filter_complex",
            "[0:v][1:v]concat=n=2:v=1:a=0[v];[2:a][3:a][4:a]concat=n=3:v=0:a=1[a]",
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-c:a", "aac",
            "-t", "6", str(video_path),
        ],
        capture_output=True, timeout=60, check=True,
    )
    return video_path


@pytest.fixture(scope="module")
def silent_video(tmp_path_factory):
    """A 2-second video with no audio stream at all."""
    out_dir = tmp_path_factory.mktemp("video_studio_ffmpeg_silent")
    video_path = out_dir / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=green:s=320x240:d=2,format=yuv420p",
            "-an", "-c:v", "libx264", "-t", "2", str(video_path),
        ],
        capture_output=True, timeout=30, check=True,
    )
    return video_path


# --- probe_video -----------------------------------------------------------------------


def test_probe_video_reads_duration_resolution_fps(test_video):
    probe = probe_video(test_video)
    assert probe.duration_seconds == pytest.approx(6.0, abs=0.2)
    assert probe.width == 320
    assert probe.height == 240
    assert probe.fps is not None
    assert probe.has_audio is True
    assert probe.video_codec == "h264"
    assert probe.file_size_bytes > 0


def test_probe_video_no_audio_stream(silent_video):
    probe = probe_video(silent_video)
    assert probe.has_audio is False
    assert probe.audio_codec is None


def test_probe_video_missing_file_raises(tmp_path):
    with pytest.raises(FFmpegError, match="not found"):
        probe_video(tmp_path / "does_not_exist.mp4")


def test_probe_video_corrupted_file_raises(tmp_path):
    bad = tmp_path / "corrupted.mp4"
    bad.write_bytes(b"not a real video file")
    with pytest.raises(FFmpegError, match="unsupported or corrupted"):
        probe_video(bad)


# --- detect_scene_changes -----------------------------------------------------------------


def test_detect_scene_changes_finds_the_cut(test_video):
    changes = detect_scene_changes(test_video)
    assert len(changes) >= 1
    assert any(2.5 < c.timestamp_seconds < 3.5 for c in changes)


def test_detect_scene_changes_missing_file_raises(tmp_path):
    with pytest.raises(FFmpegError, match="not found"):
        detect_scene_changes(tmp_path / "does_not_exist.mp4")


# --- detect_silence --------------------------------------------------------------------


def test_detect_silence_finds_the_gap(test_video):
    gaps = detect_silence(test_video, min_duration_seconds=0.5)
    assert len(gaps) >= 1
    gap = gaps[0]
    assert gap.start_seconds == pytest.approx(2.0, abs=0.3)
    assert gap.duration_seconds == pytest.approx(2.0, abs=0.3)


def test_detect_silence_on_silent_video_video_returns_empty_not_error(silent_video):
    # silent_video has NO audio stream at all (not "audio that is
    # silent") - detect_silence must treat this as "nothing to report",
    # not raise, since jarvis.video_studio.analysis.analyze_video()
    # relies on that to skip silence detection gracefully.
    gaps = detect_silence(silent_video)
    assert gaps == []


# --- extract_frame ---------------------------------------------------------------------


def test_extract_frame_writes_a_jpeg(test_video, tmp_path):
    out = tmp_path / "frame.jpg"
    extract_frame(test_video, timestamp_seconds=1.0, output_path=out)
    assert out.is_file()
    assert out.stat().st_size > 0


def test_extract_frame_missing_source_raises(tmp_path):
    with pytest.raises(FFmpegError, match="not found"):
        extract_frame(tmp_path / "does_not_exist.mp4", timestamp_seconds=0.0, output_path=tmp_path / "out.jpg")
