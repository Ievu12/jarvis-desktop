"""Audio-driven effect timing suggestions - requirement 4 ("Garso
takelio analizė ir ritmo aptikimas" / audio track analysis and rhythm
detection). HONEST DISCLOSURE, stated here and echoed in every public
name/docstring/GUI label this module reaches: this is NOT real beat/BPM
detection. True rhythm detection (onset detection, tempo estimation) is
a genuinely different algorithm class that needs a dedicated DSP library
(librosa, aubio, essentia) - none of which could be installed/verified
on this machine (no network access in the tool sandbox used to build
this; separately, `librosa`'s own `llvmlite` JIT-compiled dependency is
a likely candidate for this machine's own Smart App Control blocking,
the same mechanism that already blocked `faster-whisper`/`matplotlib`
on this exact machine - see RELEASE.md's own documented findings). The
user explicitly approved this honest fallback instead: real, measured
AMPLITUDE PEAKS (loud moments - a chorus hit, a drop, a strong downbeat
accent) computed directly from the real audio waveform via ffmpeg's own
PCM extraction + windowed RMS in Python - zero new dependencies, zero
compiled extensions, nothing invented or faked. This finds "loud
moments," genuinely useful for timing an effect to a drop/accent, but
it is NOT the same as "the beats of the song" a real tempo-tracking
algorithm would find, and this module/its GUI never claims otherwise.

Every suggested timestamp here is still a SUGGESTION, never auto-
applied - the caller (the GUI) lets the person review/adjust/reject
each one before using it to time any sticker/text/transition, matching
requirement 4's own "galimybė rankiniu būdu koreguoti automatiškai
parinktus efektų pradžios momentus" / "galimybė išjungti automatinį
sinchronizavimą" (ability to manually adjust automatically-chosen
timings / ability to disable automatic sync entirely)."""

from __future__ import annotations

import array
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_ANALYSIS_SAMPLE_RATE = 8000
# A low sample rate is deliberate and sufficient here - this module
# only ever measures amplitude ENVELOPE (loudness over time), never
# frequency content, so the Nyquist-limited 4kHz bandwidth this rate
# gives is irrelevant; a low rate keeps the extracted PCM file small and
# the RMS computation fast even for a multi-minute track.

_WINDOW_SECONDS = 0.05
# 50ms windows - short enough to localize a loud moment to within a
# musically-reasonable precision, long enough to average out single-
# sample noise spikes that aren't real loudness changes.

_MIN_PEAK_SPACING_SECONDS = 0.25
# Two "peaks" closer together than this are almost certainly the same
# loud moment (e.g. the attack and sustain of one hit), not two
# genuinely distinct accents - a real, sensible lower bound for how
# close together two real musical accents can be and still be usefully
# different suggestion points (anything faster than this is beyond what
# a person would want as separate, individually-adjustable effect
# trigger points anyway).

_ANALYSIS_TIMEOUT_SECONDS = 120


class AudioSyncError(Exception):
    """Raised for a genuine analysis failure (ffmpeg missing, file
    unreadable, no audio track) - a LOCAL exception, never subclassing
    a sibling module's own error type, matching this package's
    established per-module isolation convention."""


@dataclass(frozen=True)
class AmplitudePeak:
    """One real, measured loud moment - `timestamp_seconds` is the
    window's own start time, `relative_strength` is this peak's own RMS
    value divided by the TRACK's own overall max RMS (0.0-1.0), so a
    caller can visually/numerically rank peaks by how strong they are
    relative to the rest of the same track, without needing to know raw
    RMS units."""

    timestamp_seconds: float
    relative_strength: float


def analyze_amplitude_peaks(
    source_path: Path, *, max_peaks: int = 20, min_strength: float = 0.5,
) -> list[AmplitudePeak]:
    """Extracts real PCM audio from `source_path` via ffmpeg and
    computes real windowed RMS loudness, returning up to `max_peaks`
    local maxima whose own relative_strength is at least `min_strength`
    - sorted by timestamp (not by strength), since a caller timing
    effects against a timeline wants them in playback order. Raises
    AudioSyncError if ffmpeg is missing, the file can't be read, or it
    has no audio track - never returns a silently-empty "no peaks found
    because something went wrong" result for an outright failure (a
    genuinely silent/peak-free track still returns [], which IS a valid,
    honest result - see the real empty-list test for this exact
    distinction)."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise AudioSyncError("FFmpeg was not found on PATH. The Video Editor requires FFmpeg to be installed.")
    if not source_path.is_file():
        raise AudioSyncError(f"File not found: {source_path}")

    from jarvis.video_studio.ffmpeg_utils import FFmpegError, probe_video

    try:
        if not probe_video(source_path).has_audio:
            raise AudioSyncError(f"{source_path.name} has no audio track to analyze.")
    except FFmpegError as e:
        raise AudioSyncError(f"Couldn't read {source_path.name}: {e}") from e

    with tempfile.TemporaryDirectory(prefix="jarvis_audio_sync_") as tmp_dir:
        raw_path = Path(tmp_dir) / "audio.raw"
        cmd = [
            ffmpeg, "-y", "-i", str(source_path), "-vn", "-ar", str(_ANALYSIS_SAMPLE_RATE), "-ac", "1",
            "-f", "s16le", str(raw_path),
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=_ANALYSIS_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            raise AudioSyncError(f"Audio analysis timed out for {source_path.name}.") from None
        if result.returncode != 0 or not raw_path.is_file():
            raise AudioSyncError(f"Couldn't extract audio from {source_path.name}. ffmpeg said: {result.stderr.strip()[-300:]}")

        pcm_bytes = raw_path.read_bytes()

    return _find_peaks_in_pcm(pcm_bytes, max_peaks=max_peaks, min_strength=min_strength)


def _find_peaks_in_pcm(pcm_bytes: bytes, *, max_peaks: int, min_strength: float) -> list[AmplitudePeak]:
    samples = array.array("h")
    samples.frombytes(pcm_bytes[: len(pcm_bytes) - (len(pcm_bytes) % 2)])
    window_size = int(_ANALYSIS_SAMPLE_RATE * _WINDOW_SECONDS)
    if window_size <= 0 or len(samples) < window_size:
        return []

    rms_values: list[float] = []
    for start in range(0, len(samples) - window_size, window_size):
        window = samples[start : start + window_size]
        rms = math.sqrt(sum(s * s for s in window) / len(window))
        rms_values.append(rms)

    if not rms_values:
        return []
    max_rms = max(rms_values)
    if max_rms <= 0.0:
        return []  # genuinely silent track - a real, honest empty result

    # Rising edges: a window markedly louder than the window right
    # before it - deliberately NOT "strictly louder than both
    # neighbors" (a true local-maximum rule), since a real sustained
    # loud passage (a held chord, a drum fill, a chorus) is usually a
    # multi-window PLATEAU at a near-constant RMS, which a strict local-
    # max test never fires on at all (a real, hand-hit bug found by
    # testing against a synthetic fixture with a genuine 0.2s sustained
    # tone - the strict-local-max version found zero peaks in it). The
    # moment loudness jumps is the musically meaningful "accent," so
    # this looks for the ONSET of a loud region, not its exact single
    # loudest instant.
    candidates: list[tuple[float, float]] = []  # (timestamp, relative_strength)
    for i in range(1, len(rms_values)):
        strength = rms_values[i] / max_rms
        previous_strength = rms_values[i - 1] / max_rms
        if strength >= min_strength and strength > previous_strength * 1.5:
            candidates.append((i * _WINDOW_SECONDS, strength))

    # Enforce _MIN_PEAK_SPACING_SECONDS by greedily keeping the
    # STRONGEST candidate within each too-close cluster, not simply the
    # first one encountered - a real, musically-sensible tie-break
    # (the loudest moment in a cluster is the one worth suggesting).
    candidates.sort(key=lambda c: -c[1])
    kept: list[tuple[float, float]] = []
    for timestamp, strength in candidates:
        if all(abs(timestamp - kept_ts) >= _MIN_PEAK_SPACING_SECONDS for kept_ts, _ in kept):
            kept.append((timestamp, strength))
        if len(kept) >= max_peaks:
            break

    kept.sort(key=lambda c: c[0])
    return [AmplitudePeak(timestamp_seconds=ts, relative_strength=strength) for ts, strength in kept]
