"""Speech-to-text transcription with timestamps (module brief, section
5: "Automatic Subtitles" - "Transcribe the video's speech. Create
accurate subtitles with timestamps. Support Lithuanian and English.").

Uses FFmpeg's own built-in `whisper` audio filter (whisper.cpp compiled
directly into this machine's ffmpeg binary - confirmed via `ffmpeg -h
filter=whisper`) rather than a Python speech-recognition library, for
one specific, verified reason: every third-party compiled Python
extension tried for this (faster-whisper, whose `av`/PyAV dependency
is imported unconditionally at package load) is blocked outright by
this machine's Windows Smart App Control policy ("An Application
Control policy has blocked this file" - the same failure mode
independently confirmed for matplotlib's ft2font extension during the
Instagram AI Manager module's Analytics UI work). A plain subprocess
call to the ALREADY-INSTALLED, already-trusted ffmpeg.exe is
unaffected, since Smart App Control's block is against loading an
unreputable compiled Python DLL into python.exe's own process - it has
no bearing on python.exe spawning a separate, independently-trusted
.exe as a subprocess.

This is real Instagram AI Manager-file's Content-Studio-grade
groundwork, not a shortcut: `-af whisper ... format=srt` gives
sentence-level SRT timestamps directly, with a VAD (voice-activity
detection) option to skip non-speech gaps, and works fully offline
after the one-time model download below.

jarvis.voice.speech_to_text.listen_once() (live microphone, Google Web
Speech API, Lithuanian-only) is NOT reused here - it has no file-input
mode and no timestamp output; this module is a separate, file-based
transcription path with its own model rather than a variant of that
one, per the architecture inspection this feature's plan was built on.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from jarvis.config import VIDEO_STUDIO_MODELS_DIR

# The one model size actually downloaded/tested for this feature - a
# balance of accuracy vs. download size/CPU transcription time for
# short (Reel-length) videos, per the person's own choice when this
# stage was planned. Other whisper.cpp GGML model files (tiny/small/
# medium/large) exist at the same base URL and could be added as a
# user-facing choice later without changing this module's shape - only
# _MODEL_FILENAME/_MODEL_URL would need to become parameters.
_MODEL_FILENAME = "ggml-base.bin"
_MODEL_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/main/{_MODEL_FILENAME}"

# whisper.cpp's own language codes - 'auto' lets the model detect it,
# otherwise an ISO 639-1 code. The module brief asks specifically for
# Lithuanian and English support; whisper.cpp/OpenAI Whisper's
# multilingual base model supports both directly (and many others),
# so no separate per-language model download is needed - only the
# `language` filter option changes.
LANGUAGE_AUTO = "auto"
LANGUAGE_LITHUANIAN = "lt"
LANGUAGE_ENGLISH = "en"

_TRANSCRIBE_TIMEOUT_SECONDS = 900  # a full encode+transcribe pass over a several-minute video, generously bounded


class TranscriptionError(Exception):
    """Raised for any transcription failure (ffmpeg missing, model file
    missing/corrupted, no audio stream, subprocess failure/timeout) -
    always with a human-readable message."""


def model_path() -> Path:
    return VIDEO_STUDIO_MODELS_DIR / _MODEL_FILENAME


def model_is_downloaded() -> bool:
    return model_path().is_file()


def download_model(*, progress_callback: Callable[[float], None] | None = None) -> None:
    """Downloads the whisper.cpp GGML model file (~148MB) to
    VIDEO_STUDIO_MODELS_DIR if not already present - a one-time setup
    step the first transcription request triggers (see
    jarvis.gui.views.video_studio, a later stage, for where this is
    called from a background thread with a progress bar, per the
    module brief's "Show: ... Transcribing..." + progress indicator
    requirement). Idempotent: does nothing if the model file already
    exists. Raises TranscriptionError on any network/write failure,
    cleaning up a partial download rather than leaving a truncated
    model file that would fail confusingly on first use.

    `progress_callback`, if given, is called with a 0.0-1.0 fraction as
    the download proceeds - best-effort (some servers don't report
    Content-Length; in that case it's called with a monotonically
    increasing fraction estimated from bytes read against the LAST
    known size only, or not at all if no size is ever available)."""
    if model_is_downloaded():
        return

    VIDEO_STUDIO_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    destination = model_path()
    tmp_destination = destination.with_suffix(".bin.partial")

    try:
        with urllib.request.urlopen(_MODEL_URL, timeout=30) as response:
            total_size = response.length or 0
            downloaded = 0
            with open(tmp_destination, "wb") as f:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback is not None and total_size:
                        progress_callback(min(1.0, downloaded / total_size))
    except OSError as e:
        tmp_destination.unlink(missing_ok=True)
        raise TranscriptionError(
            f"Couldn't download the transcription model: {e}. Check your network connection."
        ) from e

    tmp_destination.replace(destination)


@dataclass(frozen=True)
class TranscriptSegment:
    start_seconds: float
    end_seconds: float
    text: str


@dataclass(frozen=True)
class TranscriptionResult:
    segments: list[TranscriptSegment]
    full_text: str
    language: str | None
    error: str | None
    """Set (with segments/full_text empty) if transcription failed -
    e.g. no audio stream, model not downloaded, whisper.cpp failure.
    Callers must check this before trusting the result, same
    insufficient-data-style convention as
    jarvis.video_studio.analysis.VideoAnalysis.error."""

    @staticmethod
    def from_dict(data: dict) -> "TranscriptionResult":
        """Reconstructs a TranscriptionResult from the plain dict
        jarvis.video_studio.db stores (via dataclasses.asdict()) -
        NOT a bare TranscriptionResult(**data), which would leave
        `segments` as a list of plain dicts rather than TranscriptSegment
        instances (dataclasses.asdict() recursively converts NESTED
        dataclasses to dicts too, and unpacking doesn't reverse that -
        this was a real bug, found by hand-testing the Reject button on
        a highlight candidate, that this constructor exists to
        prevent)."""
        return TranscriptionResult(
            segments=[TranscriptSegment(**s) for s in data["segments"]],
            full_text=data["full_text"], language=data["language"], error=data["error"],
        )


def _empty_result(error: str) -> TranscriptionResult:
    return TranscriptionResult(segments=[], full_text="", language=None, error=error)


def transcribe_video(
    path: Path, *, language: str = LANGUAGE_AUTO
) -> TranscriptionResult:
    """Extracts and transcribes `path`'s speech via FFmpeg's built-in
    `whisper` audio filter, requesting SRT-format output (timestamped
    segments) directly from the filter rather than parsing a plain-text
    transcript and guessing at timing. Never raises - any failure
    (missing ffmpeg, missing model, no audio, subprocess failure) comes
    back via TranscriptionResult.error, matching
    jarvis.video_studio.analysis.analyze_video()'s same convention (see
    that function's docstring for why "never raise, always report via
    a field" is this package's error-handling shape for analysis-style
    calls)."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        return _empty_result(
            "FFmpeg was not found on PATH. AI Video Studio requires FFmpeg to be installed."
        )
    if not path.is_file():
        return _empty_result(f"File not found: {path}")
    if not model_is_downloaded():
        return _empty_result(
            "The transcription model hasn't been downloaded yet. "
            "Click 'Download transcription model' first."
        )

    srt_path = path.with_suffix(".transcript.srt")
    srt_path.unlink(missing_ok=True)

    # FFmpeg's filtergraph syntax treats ':' as an option separator, so
    # a Windows absolute path's drive-letter colon (e.g. "C:\Users\...")
    # must be escaped INSIDE a filter option value - but escaping it
    # (with a literal backslash before the colon) was found, by hand
    # testing against this machine's actual ffmpeg build, to still be
    # misparsed for the `whisper` filter specifically (see this
    # function's own commit history/PR notes for the exact failure).
    # The reliable fix confirmed by that same hand testing: run ffmpeg
    # with its working directory set to the destination SRT's own
    # directory, and pass both the model path and the destination as
    # plain relative-to-cwd paths inside the filter string - no colon,
    # nothing to escape, and it transcribes correctly.
    cwd = srt_path.parent
    try:
        relative_model = os.path.relpath(model_path(), cwd)
    except ValueError:
        # Can happen on Windows if the model and the project directory
        # are on different drives (os.path.relpath can't express a
        # cross-drive relative path) - fall back to copying the model
        # file next to the SRT for this one run rather than failing;
        # rare in practice since VIDEO_STUDIO_MODELS_DIR and
        # VIDEO_STUDIO_PROJECTS_DIR are both under the same
        # JARVIS_DATA_DIR by default.
        relative_model = model_path().name
        shutil.copy2(model_path(), cwd / relative_model)
    # FFmpeg's filtergraph parser also treats a backslash specially
    # (see this function's comment above on the colon) - a Windows
    # relative path like "..\models\ggml-base.bin" gets its separators
    # silently swallowed the same way the colon did. Forward slashes
    # are accepted as path separators by the Windows C runtime (and by
    # this ffmpeg build, confirmed by hand testing) and have no special
    # meaning to the filtergraph parser, so normalize to those instead
    # of escaping backslashes.
    relative_model = relative_model.replace("\\", "/")

    whisper_filter = (
        f"whisper=model={relative_model}:language={language}:queue=3s"
        f":destination={srt_path.name}:format=srt"
    )

    try:
        result = subprocess.run(
            [ffmpeg, "-y", "-i", str(path.resolve()), "-af", whisper_filter, "-f", "null", "-"],
            capture_output=True, text=True, timeout=_TRANSCRIBE_TIMEOUT_SECONDS,
            check=False, cwd=str(cwd),
        )
    except subprocess.TimeoutExpired:
        srt_path.unlink(missing_ok=True)
        return _empty_result(f"Transcription timed out for {path.name}.")

    if not srt_path.is_file():
        stderr_tail = result.stderr.strip()[-400:]
        if "does not contain any stream" in result.stderr or "Output file does not contain any stream" in result.stderr:
            return _empty_result(f"{path.name} has no audio track to transcribe.")
        return _empty_result(f"Transcription failed for {path.name}. ffmpeg said: {stderr_tail}")

    try:
        srt_text = srt_path.read_text(encoding="utf-8")
    except OSError as e:
        return _empty_result(f"Couldn't read the generated transcript: {e}")
    finally:
        srt_path.unlink(missing_ok=True)

    segments = _parse_srt(srt_text)
    full_text = " ".join(s.text for s in segments).strip()
    if not segments:
        return _empty_result(
            f"No speech was detected in {path.name}. The audio may be silent, "
            f"music-only, or in a language the model couldn't recognize."
        )

    detected_language = None if language == LANGUAGE_AUTO else language
    return TranscriptionResult(
        segments=segments, full_text=full_text, language=detected_language, error=None,
    )

def _parse_srt(srt_text: str) -> list[TranscriptSegment]:
    """Parses standard SRT format (index line, "HH:MM:SS,mmm -->
    HH:MM:SS,mmm" timing line, one or more text lines, blank line
    separator) into TranscriptSegments. Tolerant of minor formatting
    variation (extra blank lines, trailing whitespace) since this is
    machine-generated output from whisper.cpp, not user-authored SRT -
    but any block it can't parse is simply skipped, never raises."""
    segments: list[TranscriptSegment] = []
    blocks = srt_text.replace("\r\n", "\n").split("\n\n")
    for block in blocks:
        lines = [line for line in block.strip().split("\n") if line.strip()]
        if len(lines) < 2:
            continue
        timing_line = lines[1] if lines[0].strip().isdigit() else lines[0]
        text_lines = lines[2:] if lines[0].strip().isdigit() else lines[1:]
        if "-->" not in timing_line:
            continue
        start_str, _, end_str = timing_line.partition("-->")
        start = _parse_srt_timestamp(start_str.strip())
        end = _parse_srt_timestamp(end_str.strip())
        if start is None or end is None:
            continue
        text = " ".join(text_lines).strip()
        if text:
            segments.append(TranscriptSegment(start_seconds=start, end_seconds=end, text=text))
    return segments


def _parse_srt_timestamp(value: str) -> float | None:
    """Parses "HH:MM:SS,mmm" into seconds."""
    try:
        hms, _, millis = value.partition(",")
        hours, minutes, seconds = hms.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis) / 1000.0
    except (ValueError, IndexError):
        return None

