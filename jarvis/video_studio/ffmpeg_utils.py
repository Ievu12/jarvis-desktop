"""Thin subprocess wrappers around the system `ffmpeg`/`ffprobe`
binaries - probing a video file's metadata, detecting scene changes and
silence gaps. This is the ONE place jarvis.video_studio shells out to
FFmpeg for these read-only probes; jarvis.video_studio.transcribe.py
has its own, separate FFmpeg invocation (the `-af whisper` filter) kept
apart since it's a much longer-running call with different error modes
(missing model file) - see that module's docstring.

No Python video-processing library (moviepy/opencv/PIL) is used
anywhere in this module - every function here runs `ffmpeg`/`ffprobe`
as a subprocess and parses its stdout/stderr. This keeps AI Video
Studio's dependency footprint to "FFmpeg must be installed and on
PATH" (already true on this machine - see RELEASE.md) rather than
adding a compiled Python extension, several of which have been found
to be blocked outright by this machine's Windows Smart App Control
policy (matplotlib's ft2font, faster-whisper's PyAV dependency - see
jarvis.video_studio.transcribe's docstring for the second one). A
plain subprocess call to a native .exe already on PATH is unaffected
by that policy (python.exe itself is trusted/reputable, and it is
python.exe - not a blocked DLL - that is doing the invoking).

Every function here raises FFmpegError (never a bare CalledProcessError
or a silent empty result) on failure, with a message safe to show
directly in the GUI - per the module's brief ("Never silently fail",
"Display human-readable errors").
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

# ffmpeg/ffprobe calls here are all metadata probes over a single file,
# not encodes - generous but finite, so a hung/corrupted input can't
# block the calling background thread forever (see jarvis.gui.worker's
# docstring for why nothing may block the Tkinter main thread; these
# calls are always made from a background thread via
# run_generation_in_background(), never directly from a GUI callback).
_PROBE_TIMEOUT_SECONDS = 30

# Scene-change and silence detection scan the whole file once, so they
# get a longer budget than a simple metadata probe - still bounded, so
# a pathological input (e.g. corrupted causing FFmpeg to hang) can't
# wedge the analysis job indefinitely.
_SCAN_TIMEOUT_SECONDS = 300


class FFmpegError(Exception):
    """Raised by every function in this module on any failure (missing
    binary, unsupported/corrupted file, non-zero exit code, timeout) -
    always with a message written to be shown directly in the GUI, per
    the module's brief's "Display human-readable errors" / "Never
    silently fail" requirements."""


def _require_binary(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise FFmpegError(
            f"{name} was not found on PATH. AI Video Studio requires FFmpeg "
            f"(https://ffmpeg.org/download.html) to be installed and on PATH."
        )
    return path


def ffmpeg_available() -> bool:
    """True if both ffmpeg and ffprobe are on PATH - checked once by the
    Video Studio dashboard so it can show a clear "FFmpeg not found"
    state instead of every action failing individually."""
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


@dataclass(frozen=True)
class VideoProbe:
    """Raw technical metadata for one video file, straight from
    ffprobe - jarvis.video_studio.analysis.analyze_video() builds on
    this rather than re-probing itself."""

    duration_seconds: float
    width: int | None
    height: int | None
    fps: float | None
    has_audio: bool
    video_codec: str | None
    audio_codec: str | None
    file_size_bytes: int
    format_name: str | None


def probe_video(path: Path) -> VideoProbe:
    """Runs `ffprobe -show_format -show_streams` and parses its JSON
    output. Raises FFmpegError if the file can't be probed at all
    (unsupported/corrupted file, or ffprobe itself fails) - this is the
    first check any newly uploaded video goes through, so an
    unsupported/corrupted file is caught here with a clear message
    rather than failing confusingly deeper in analysis."""
    ffprobe = _require_binary("ffprobe")
    if not path.is_file():
        raise FFmpegError(f"File not found: {path}")

    try:
        result = subprocess.run(
            [
                ffprobe, "-v", "error", "-print_format", "json",
                "-show_format", "-show_streams", str(path),
            ],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired:
        raise FFmpegError(f"Timed out reading video metadata for {path.name}.") from None

    if result.returncode != 0:
        raise FFmpegError(
            f"Couldn't read {path.name} - it may be an unsupported or corrupted "
            f"video file. ffprobe said: {result.stderr.strip()[:300]}"
        )

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        raise FFmpegError(f"Couldn't parse video metadata for {path.name}.") from None

    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration_str = fmt.get("duration") or (video_stream or {}).get("duration")
    try:
        duration = float(duration_str) if duration_str is not None else 0.0
    except ValueError:
        duration = 0.0

    fps = None
    if video_stream is not None:
        fps = _parse_frame_rate(video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate"))

    try:
        file_size = int(fmt.get("size", 0))
    except (TypeError, ValueError):
        file_size = path.stat().st_size

    return VideoProbe(
        duration_seconds=duration,
        width=video_stream.get("width") if video_stream else None,
        height=video_stream.get("height") if video_stream else None,
        fps=fps,
        has_audio=audio_stream is not None,
        video_codec=video_stream.get("codec_name") if video_stream else None,
        audio_codec=audio_stream.get("codec_name") if audio_stream else None,
        file_size_bytes=file_size,
        format_name=fmt.get("format_name"),
    )


def _parse_frame_rate(raw: str | None) -> float | None:
    """ffprobe reports frame rate as a "num/den" fraction string (e.g.
    "30000/1001" for 29.97fps, or "25/1") - never a plain float."""
    if not raw or "/" not in raw:
        return None
    num_str, _, den_str = raw.partition("/")
    try:
        num, den = float(num_str), float(den_str)
    except ValueError:
        return None
    return None if den == 0 else round(num / den, 3)


@dataclass(frozen=True)
class SceneChange:
    timestamp_seconds: float
    score: float  # scdet's own 0-100 change-magnitude score, unmodified


def detect_scene_changes(path: Path, *, threshold: float = 10.0) -> list[SceneChange]:
    """Runs FFmpeg's `scdet` video filter over the whole file and parses
    the scene-change timestamps/scores it logs to stderr. `threshold`
    matches scdet's own 0-100 scale (its default) - not a claim this
    module can calibrate; the raw score is preserved in the returned
    SceneChange rather than translated into a made-up "0-10" custom
    scale, since scdet's score already is one."""
    ffmpeg = _require_binary("ffmpeg")
    if not path.is_file():
        raise FFmpegError(f"File not found: {path}")

    try:
        result = subprocess.run(
            [
                ffmpeg, "-i", str(path), "-filter:v", f"scdet=threshold={threshold}",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=_SCAN_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired:
        raise FFmpegError(f"Timed out detecting scene changes in {path.name}.") from None

    # scdet logs one "lavfi.scd.score: X, lavfi.scd.time: Y" line per
    # detected change to stderr (ffmpeg's normal, non-error logging
    # channel) - a non-zero exit code here still means genuine failure
    # (scdet itself never fails a run), so still surface it.
    if result.returncode != 0 and "lavfi.scd" not in result.stderr:
        raise FFmpegError(
            f"Couldn't analyze scenes in {path.name}. ffmpeg said: {result.stderr.strip()[:300]}"
        )

    changes: list[SceneChange] = []
    for line in result.stderr.splitlines():
        if "lavfi.scd.score:" not in line or "lavfi.scd.time:" not in line:
            continue
        try:
            score_part = line.split("lavfi.scd.score:", 1)[1].split(",", 1)[0].strip()
            time_part = line.split("lavfi.scd.time:", 1)[1].strip()
            changes.append(SceneChange(timestamp_seconds=float(time_part), score=float(score_part)))
        except (IndexError, ValueError):
            continue
    return changes


@dataclass(frozen=True)
class SilenceGap:
    start_seconds: float
    end_seconds: float

    @property
    def duration_seconds(self) -> float:
        return self.end_seconds - self.start_seconds


def detect_silence(
    path: Path, *, noise_db: float = -30.0, min_duration_seconds: float = 0.5
) -> list[SilenceGap]:
    """Runs FFmpeg's `silencedetect` audio filter and parses the
    silence_start/silence_end pairs it logs to stderr. A video with no
    audio stream returns an empty list (not an error) - see
    jarvis.video_studio.analysis.analyze_video(), which checks
    VideoProbe.has_audio before calling this."""
    ffmpeg = _require_binary("ffmpeg")
    if not path.is_file():
        raise FFmpegError(f"File not found: {path}")

    try:
        result = subprocess.run(
            [
                ffmpeg, "-i", str(path), "-af",
                f"silencedetect=noise={noise_db}dB:d={min_duration_seconds}",
                "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=_SCAN_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired:
        raise FFmpegError(f"Timed out detecting silence in {path.name}.") from None

    if result.returncode != 0 and "silence_start" not in result.stderr:
        # A file with no audio stream at all makes ffmpeg exit non-zero
        # here (no audio to filter) - not a real failure, just "no
        # silence gaps to report" (VideoProbe.has_audio already told
        # the caller this file has no audio).
        if "does not contain any stream" in result.stderr or "Stream map" in result.stderr:
            return []
        raise FFmpegError(
            f"Couldn't analyze audio in {path.name}. ffmpeg said: {result.stderr.strip()[:300]}"
        )

    gaps: list[SilenceGap] = []
    pending_start: float | None = None
    for line in result.stderr.splitlines():
        if "silence_start:" in line:
            try:
                pending_start = float(line.split("silence_start:", 1)[1].strip())
            except ValueError:
                pending_start = None
        elif "silence_end:" in line and pending_start is not None:
            try:
                end_part = line.split("silence_end:", 1)[1].split("|", 1)[0].strip()
                gaps.append(SilenceGap(start_seconds=pending_start, end_seconds=float(end_part)))
            except ValueError:
                pass
            pending_start = None
    return gaps


def extract_frame(path: Path, *, timestamp_seconds: float, output_path: Path) -> None:
    """Extracts a single frame at `timestamp_seconds` as a JPEG to
    `output_path` - used for cover-frame candidates
    (jarvis.video_studio.cover, a later stage) and thumbnail previews.
    Raises FFmpegError on failure."""
    ffmpeg = _require_binary("ffmpeg")
    if not path.is_file():
        raise FFmpegError(f"File not found: {path}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [
                ffmpeg, "-y", "-ss", str(max(0.0, timestamp_seconds)), "-i", str(path),
                "-frames:v", "1", "-q:v", "2", str(output_path),
            ],
            capture_output=True, text=True, timeout=_PROBE_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired:
        raise FFmpegError(f"Timed out extracting a frame from {path.name}.") from None

    if result.returncode != 0 or not output_path.exists():
        raise FFmpegError(
            f"Couldn't extract a frame from {path.name}. ffmpeg said: {result.stderr.strip()[:300]}"
        )
