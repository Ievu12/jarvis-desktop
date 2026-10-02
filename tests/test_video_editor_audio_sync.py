"""Tests for jarvis.video_editor.audio_sync: real, measured amplitude-
peak detection (explicitly NOT true beat/BPM detection - see that
module's own docstring for the full honest-disclosure reasoning and why
this fallback was chosen over librosa/aubio). All tests use real ffmpeg-
synthesized audio fixtures with KNOWN pulse timings, verifying detected
peaks land near those real, known timestamps - never mocked."""

from __future__ import annotations

import subprocess

import pytest

from jarvis.video_editor.audio_sync import AudioSyncError, analyze_amplitude_peaks
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


def _make_pulsed_audio(path, *, pulse_duration=0.2, gap_duration=0.8, pulse_count=4):
    inputs = []
    filter_inputs = []
    next_input_index = 0
    for i in range(pulse_count):
        inputs += ["-f", "lavfi", "-i", f"anullsrc=duration={gap_duration}:sample_rate=44100"]
        filter_inputs.append(f"[{next_input_index}]")
        next_input_index += 1
        inputs += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={pulse_duration}:sample_rate=44100"]
        filter_inputs.append(f"[{next_input_index}]")
        next_input_index += 1
    n = len(filter_inputs)
    filter_complex = "".join(filter_inputs) + f"concat=n={n}:v=0:a=1[out]"
    subprocess.run(
        ["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex, "-map", "[out]", str(path)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    # Pulses start at cumulative (gap_duration + pulse_duration) * i + gap_duration
    return [gap_duration + i * (gap_duration + pulse_duration) for i in range(pulse_count)]


def test_analyze_amplitude_peaks_rejects_a_missing_file(tmp_path):
    with pytest.raises(AudioSyncError, match="not found"):
        analyze_amplitude_peaks(tmp_path / "missing.wav")


def test_analyze_amplitude_peaks_rejects_a_video_with_no_audio(tmp_path):
    no_audio = tmp_path / "no_audio.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=1", "-c:v", "libx264", str(no_audio)],
        capture_output=True, timeout=30, check=True,
    )
    with pytest.raises(AudioSyncError, match="no audio track"):
        analyze_amplitude_peaks(no_audio)


def test_analyze_amplitude_peaks_on_a_silent_track_returns_empty_list(tmp_path):
    # A genuinely silent track is a real, honest "no peaks" result, NOT
    # an error - this is the real distinction this module's own
    # docstring draws between a legitimate empty result and a failure.
    silent = tmp_path / "silent.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=duration=2:sample_rate=44100", str(silent)],
        capture_output=True, timeout=30, check=True,
    )
    assert analyze_amplitude_peaks(silent) == []


def test_analyze_amplitude_peaks_detects_real_pulses_near_their_known_timestamps(tmp_path):
    audio_path = tmp_path / "pulsed.wav"
    expected_timestamps = _make_pulsed_audio(audio_path)

    peaks = analyze_amplitude_peaks(audio_path, max_peaks=10, min_strength=0.3)
    assert len(peaks) == len(expected_timestamps)
    for peak, expected in zip(peaks, expected_timestamps):
        assert abs(peak.timestamp_seconds - expected) < 0.15
        assert 0.0 < peak.relative_strength <= 1.0


def test_analyze_amplitude_peaks_respects_max_peaks(tmp_path):
    audio_path = tmp_path / "many_pulses.wav"
    _make_pulsed_audio(audio_path, pulse_count=8)

    peaks = analyze_amplitude_peaks(audio_path, max_peaks=3, min_strength=0.3)
    assert len(peaks) <= 3


def test_analyze_amplitude_peaks_returns_peaks_sorted_by_timestamp(tmp_path):
    audio_path = tmp_path / "pulsed.wav"
    _make_pulsed_audio(audio_path)

    peaks = analyze_amplitude_peaks(audio_path, max_peaks=10, min_strength=0.3)
    timestamps = [p.timestamp_seconds for p in peaks]
    assert timestamps == sorted(timestamps)


def test_analyze_amplitude_peaks_respects_min_strength_threshold(tmp_path):
    # A high min_strength should exclude weaker peaks - build audio with
    # one loud pulse and one quieter pulse, confirm only the loud one
    # survives a strict threshold.
    audio_path = tmp_path / "mixed_strength.wav"
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "anullsrc=duration=0.4:sample_rate=44100",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.2:sample_rate=44100,volume=1.0",
            "-f", "lavfi", "-i", "anullsrc=duration=0.8:sample_rate=44100",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.2:sample_rate=44100,volume=0.1",
            "-filter_complex", "[0][1][2][3]concat=n=4:v=0:a=1[out]",
            "-map", "[out]", str(audio_path),
        ],
        capture_output=True, text=True, timeout=30, check=True,
    )
    strict_peaks = analyze_amplitude_peaks(audio_path, max_peaks=10, min_strength=0.9)
    loose_peaks = analyze_amplitude_peaks(audio_path, max_peaks=10, min_strength=0.05)
    assert len(strict_peaks) < len(loose_peaks)
