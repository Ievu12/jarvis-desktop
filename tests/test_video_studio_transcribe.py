"""Tests for jarvis.video_studio.transcribe: speech-to-text via
FFmpeg's built-in whisper.cpp audio filter. Uses REAL ffmpeg subprocess
calls and the REAL downloaded whisper.cpp model file (see
jarvis.config.VIDEO_STUDIO_MODELS_DIR) - skipped entirely if ffmpeg
isn't on PATH or the model hasn't been downloaded yet (a ~148MB
one-time download; CI/a fresh checkout won't have it), so this suite
degrades gracefully rather than failing confusingly. Test audio is
synthesized with Windows' own SAPI text-to-speech (System.Speech, via
PowerShell) at fixture-build time rather than a checked-in binary
fixture - real, understandable English speech is what actually
exercises whisper.cpp's transcription, unlike a synthetic sine tone.
"""

from __future__ import annotations

import dataclasses
import shutil
import subprocess

import pytest

from jarvis.video_studio.transcribe import (
    LANGUAGE_AUTO,
    TranscriptionResult,
    model_is_downloaded,
    transcribe_video,
)
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available

_HAS_POWERSHELL = shutil.which("powershell") is not None

pytestmark = pytest.mark.skipif(
    not (ffmpeg_available() and model_is_downloaded()),
    reason="ffmpeg not on PATH or the whisper.cpp model hasn't been downloaded yet",
)


@pytest.fixture(scope="module")
def speech_video(tmp_path_factory):
    if not _HAS_POWERSHELL:
        pytest.skip("PowerShell (for SAPI text-to-speech) not available")

    out_dir = tmp_path_factory.mktemp("video_studio_transcribe")
    wav_path = out_dir / "speech.wav"
    video_path = out_dir / "speech.mp4"

    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$synth.SetOutputToWaveFile('{wav_path}'); "
        "$synth.Speak('This is a test sentence for transcription.'); "
        "$synth.Dispose()"
    )
    result = subprocess.run(
        ["powershell", "-NonInteractive", "-Command", script],
        capture_output=True, timeout=30,
    )
    if result.returncode != 0 or not wav_path.is_file():
        pytest.skip("SAPI text-to-speech unavailable on this machine")

    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240",
            "-i", str(wav_path), "-c:v", "libx264", "-c:a", "aac", "-shortest", str(video_path),
        ],
        capture_output=True, timeout=30, check=True,
    )
    return video_path


@pytest.fixture(scope="module")
def silent_video(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("video_studio_transcribe_silent")
    video_path = out_dir / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=green:s=320x240:d=2",
            "-an", "-c:v", "libx264", "-t", "2", str(video_path),
        ],
        capture_output=True, timeout=30, check=True,
    )
    return video_path


def test_transcribe_real_speech_produces_accurate_text_and_timestamps(speech_video):
    result = transcribe_video(speech_video, language=LANGUAGE_AUTO)
    assert result.error is None
    assert len(result.segments) >= 1
    assert "test" in result.full_text.lower()
    for segment in result.segments:
        assert segment.end_seconds >= segment.start_seconds


def test_transcribe_video_with_no_speech_reports_clear_error(silent_video):
    result = transcribe_video(silent_video)
    assert result.error is not None
    assert result.segments == []


def test_transcribe_missing_file_reports_error(tmp_path):
    result = transcribe_video(tmp_path / "does_not_exist.mp4")
    assert result.error is not None
    assert "not found" in result.error.lower()


def test_transcribe_leaves_no_temp_srt_file_behind(speech_video):
    transcribe_video(speech_video)
    leftover = speech_video.with_suffix(".transcript.srt")
    assert not leftover.exists()


def test_srt_parsing_handles_standard_format():
    from jarvis.video_studio.transcribe import _parse_srt

    srt = (
        "1\n00:00:00,000 --> 00:00:02,500\nHello world.\n\n"
        "2\n00:00:02,500 --> 00:00:05,000\nSecond line.\n"
    )
    segments = _parse_srt(srt)
    assert len(segments) == 2
    assert segments[0].text == "Hello world."
    assert segments[0].start_seconds == 0.0
    assert segments[0].end_seconds == 2.5
    assert segments[1].text == "Second line."


def test_srt_parsing_skips_unparseable_blocks():
    from jarvis.video_studio.transcribe import _parse_srt

    srt = "not a valid srt block\n\n1\n00:00:00,000 --> 00:00:01,000\nValid.\n"
    segments = _parse_srt(srt)
    assert len(segments) == 1
    assert segments[0].text == "Valid."


def test_from_dict_round_trips_through_json_including_nested_segments(speech_video):
    # Regression test for a real bug found by hand-testing: dataclasses
    # .asdict() recursively converts nested dataclasses (TranscriptSegment)
    # to plain dicts, so TranscriptionResult(**data) (bare unpacking)
    # left `segments` as a list of dicts instead of TranscriptSegment
    # instances - an AttributeError the first time .start_seconds was
    # accessed on one.
    original = transcribe_video(speech_video, language=LANGUAGE_AUTO)
    as_dict = dataclasses.asdict(original)
    reconstructed = TranscriptionResult.from_dict(as_dict)
    assert reconstructed == original
    for segment in reconstructed.segments:
        assert segment.start_seconds is not None  # would raise AttributeError if still a dict
