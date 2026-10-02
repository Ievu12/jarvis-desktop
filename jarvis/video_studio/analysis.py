"""Video Analysis Engine (module brief, section 2): combines
jarvis.video_studio.ffmpeg_utils' raw probes into one structured
VideoAnalysis result - duration, resolution, aspect ratio, fps, audio
presence, scene changes, silence gaps. No LLM call here (this is pure
FFmpeg-derived measurement); jarvis.video_studio.highlights (a later
stage) is where scene/silence data plus the transcript get turned into
highlight candidates.

Per the brief: "Do not claim that a clip is 'viral'. Describe it as a
candidate based on measurable characteristics." This module only ever
reports measurable characteristics (counts, timestamps, durations) -
it never scores or labels a moment as good/bad/viral itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jarvis.video_studio.ffmpeg_utils import (
    FFmpegError,
    SceneChange,
    SilenceGap,
    detect_scene_changes,
    detect_silence,
    probe_video,
)


def _aspect_ratio_label(width: int | None, height: int | None) -> str | None:
    """A common-fraction label (e.g. "16:9", "9:16", "1:1") for the
    stream's pixel dimensions, via the greatest common divisor - not a
    guess, an exact reduction of the actual measured width/height."""
    if not width or not height:
        return None
    import math

    divisor = math.gcd(width, height)
    if divisor == 0:
        return None
    return f"{width // divisor}:{height // divisor}"


@dataclass(frozen=True)
class VideoAnalysis:
    duration_seconds: float
    width: int | None
    height: int | None
    aspect_ratio: str | None
    fps: float | None
    has_audio: bool
    file_size_bytes: int
    video_codec: str | None
    audio_codec: str | None
    scene_changes: list[SceneChange]
    silence_gaps: list[SilenceGap]
    error: str | None
    """Set (with every other field at its default/empty value) if
    analysis could not complete - e.g. an unsupported/corrupted file.
    Callers must check this before trusting any other field, exactly
    like jarvis.instagram_ai_manager.analytics_services' own
    insufficient_data pattern - never silently show zeroed-out fields
    as if they were a real empty video."""

    @property
    def scene_count(self) -> int:
        return len(self.scene_changes)

    @property
    def total_silence_seconds(self) -> float:
        return sum(gap.duration_seconds for gap in self.silence_gaps)

    @staticmethod
    def from_dict(data: dict) -> "VideoAnalysis":
        """Reconstructs a VideoAnalysis from the plain dict
        jarvis.video_studio.db stores (via dataclasses.asdict()) - NOT
        a bare VideoAnalysis(**data), which would leave `scene_changes`/
        `silence_gaps` as lists of plain dicts rather than SceneChange/
        SilenceGap instances (dataclasses.asdict() recursively converts
        NESTED dataclasses to dicts too, and unpacking doesn't reverse
        that - the same class of bug found, by hand-testing, in
        jarvis.video_studio.transcribe.TranscriptionResult and
        jarvis.video_studio.highlights.HighlightResult; see those
        classes' own from_dict() docstrings)."""
        return VideoAnalysis(
            duration_seconds=data["duration_seconds"], width=data["width"], height=data["height"],
            aspect_ratio=data["aspect_ratio"], fps=data["fps"], has_audio=data["has_audio"],
            file_size_bytes=data["file_size_bytes"], video_codec=data["video_codec"],
            audio_codec=data["audio_codec"],
            scene_changes=[SceneChange(**c) for c in data["scene_changes"]],
            silence_gaps=[SilenceGap(**g) for g in data["silence_gaps"]],
            error=data["error"],
        )


def _empty_analysis(error: str) -> VideoAnalysis:
    return VideoAnalysis(
        duration_seconds=0.0, width=None, height=None, aspect_ratio=None, fps=None,
        has_audio=False, file_size_bytes=0, video_codec=None, audio_codec=None,
        scene_changes=[], silence_gaps=[], error=error,
    )


def analyze_video(
    path: Path, *, scene_threshold: float = 10.0, silence_noise_db: float = -30.0,
    silence_min_duration_seconds: float = 0.5,
) -> VideoAnalysis:
    """Runs every FFmpeg probe (metadata, scene changes, silence gaps)
    over `path` and returns one structured VideoAnalysis. Never raises
    - any FFmpegError from an individual probe is caught and reported
    via VideoAnalysis.error (per the module's brief: "Never silently
    fail" means always surface a clear message, not "always raise" -
    a GUI panel calling this through run_generation_in_background()
    would otherwise have to distinguish a genuine crash from a normal
    "this file can't be analyzed" outcome; this way both arrive the
    same way every other insufficient/error result in this codebase
    does, via a field on the returned object)."""
    try:
        probe = probe_video(path)
    except FFmpegError as e:
        return _empty_analysis(str(e))

    try:
        scenes = detect_scene_changes(path, threshold=scene_threshold)
    except FFmpegError as e:
        # Metadata succeeded but scene detection failed (e.g. an
        # unusual codec scdet can't handle) - still return what we
        # DO know rather than discarding it, with the scene-detection
        # failure folded into `error` so the caller can show a partial
        # result plus a clear note, not a hard stop.
        return VideoAnalysis(
            duration_seconds=probe.duration_seconds, width=probe.width, height=probe.height,
            aspect_ratio=_aspect_ratio_label(probe.width, probe.height), fps=probe.fps,
            has_audio=probe.has_audio, file_size_bytes=probe.file_size_bytes,
            video_codec=probe.video_codec, audio_codec=probe.audio_codec,
            scene_changes=[], silence_gaps=[],
            error=f"Video metadata read OK, but scene detection failed: {e}",
        )

    silence_gaps: list[SilenceGap] = []
    if probe.has_audio:
        try:
            silence_gaps = detect_silence(
                path, noise_db=silence_noise_db, min_duration_seconds=silence_min_duration_seconds,
            )
        except FFmpegError:
            # Same partial-result reasoning as scene detection above,
            # but silence detection failing is common enough (unusual
            # audio codecs) that it's folded in silently rather than
            # surfaced as an `error` - scene_count/duration/etc. are
            # still fully valid, and "silence_gaps: []" degrades
            # gracefully (jarvis.video_studio.highlights, a later
            # stage, simply won't have silence data to weigh).
            silence_gaps = []

    return VideoAnalysis(
        duration_seconds=probe.duration_seconds, width=probe.width, height=probe.height,
        aspect_ratio=_aspect_ratio_label(probe.width, probe.height), fps=probe.fps,
        has_audio=probe.has_audio, file_size_bytes=probe.file_size_bytes,
        video_codec=probe.video_codec, audio_codec=probe.audio_codec,
        scene_changes=scenes, silence_gaps=silence_gaps, error=None,
    )
