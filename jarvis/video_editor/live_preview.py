"""Live Preview - Stage 1: renders ONE real, fully-composited frame of
the Timeline at a chosen timestamp, using the EXACT SAME filtergraph
pieces the real export already builds (multisource_export
.build_filtergraph() for the base timeline, plus whichever of
captions/text-overlay/sticker filter clauses the caller already has) -
this is a real preview of what export would actually produce, not an
approximation, and it never duplicates any filter-building logic
already in jarvis.video_editor.captions/.text_overlay/.stickers/
.multisource_export.

Same "no new in-app video decoder, extract a real frame via ffmpeg"
convention jarvis.gui.views.video_editor.timeline_panel's own docstring
and jarvis.video_editor.effects.render_effect_preview() already
establish (see effects.py's own docstring for the precedent) -
generalized here to the WHOLE assembled timeline (every clip/still,
every active overlay) rather than one segment's own effect in
isolation, since that is what "redaguojant iškart matyti rezultatą"
(see the result immediately while editing) requires.

Deliberately renders ONE frame per call, not a continuously-decoded
video stream - Stage 1 of the user's own staged Live Preview request is
"static frame + interactive overlay editing," not real-time video
playback (a later stage adds Play/Pause stepping through frames, still
one extracted frame at a time, never a persistent video decoder
process - see this module's own docstring note on why that scope choice
was made: no new dependency like opencv-python, which risks being
blocked by this machine's own documented Windows Smart App Control
history, exactly as jarvis.video_studio.transcribe's own faster-whisper/
PyAV dependency once was)."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from jarvis.video_editor.media_import import MediaItem
from jarvis.video_editor.multisource_export import (
    ExportFormat,
    MultiSourceExportError,
    build_filtergraph,
    extract_output_label,
)
from jarvis.video_editor.timeline import Timeline

_PREVIEW_TIMEOUT_SECONDS = 20
# A single-frame render is cheap (ffmpeg only has to decode up to the
# requested timestamp and apply the filtergraph to ONE frame, never the
# whole timeline) - this is deliberately much tighter than
# jarvis.video_editor.effects._PREVIEW_TIMEOUT_SECONDS (which renders a
# real short CLIP, not one frame) and far tighter than
# multisource_export._EXPORT_TIMEOUT_SECONDS; a preview that takes
# longer than this indicates something is genuinely wrong, and Live
# Preview's whole point is fast, interactive feedback while editing.


class LivePreviewError(Exception):
    """Raised for any preview render failure (ffmpeg missing, an empty/
    invalid timeline, a missing source file, subprocess failure/
    timeout) - always with a human-readable message. A LOCAL exception,
    never subclassing MultiSourceExportError - a failed PREVIEW must
    never be confused with a failed EXPORT by a caller's own except
    clause, even though both ultimately come from the same underlying
    ffmpeg machinery (same per-module isolation convention this
    package's other local exceptions already establish)."""


@dataclass(frozen=True)
class PreviewFilters:
    """The SAME opaque filter-clause contract
    jarvis.video_editor.multisource_export.export_timeline() already
    takes (caption_filter/text_overlay_filter/sticker_filters) - a
    live preview renders the CURRENT, possibly-unsaved state of every
    overlay the person is editing, which is exactly these same already-
    built clause strings, never a second, parallel way to express an
    overlay. All fields default to "none active," so a caller with no
    overlays yet can render a plain preview with
    PreviewFilters() and nothing else."""

    caption_filter: str | None = None
    text_overlay_filter: str | None = None
    sticker_filters: list[tuple[list[str], str]] | None = None


def render_preview_frame(
    timeline: Timeline, media_items: dict[str, MediaItem], *, export_format: ExportFormat,
    timestamp_seconds: float, filters: PreviewFilters, cwd: Path,
) -> Path:
    """Renders the fully-composited frame at `timestamp_seconds` (clamped
    into [0, timeline.total_duration_seconds())) into a real PNG under
    `cwd` and returns its path. Raises LivePreviewError on any failure -
    never returns a partial/missing file.

    Reuses build_filtergraph() for the base timeline exactly as
    export_timeline() does, then layers caption_filter/
    text_overlay_filter/sticker_filters in the SAME documented order
    export_timeline() itself applies them (captions, then text overlay,
    then stickers) - a caller building these filter clauses the same
    way it already does for a real export (see
    jarvis.gui.views.video_editor.dashboard._start_export()'s own
    chaining logic) gets an IDENTICAL compositing result here, which is
    the whole point of Live Preview genuinely reflecting what export
    would produce."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise LivePreviewError("FFmpeg was not found on PATH.")

    problems = timeline.validate()
    if problems:
        raise LivePreviewError("; ".join(problems))

    total_duration = timeline.total_duration_seconds()
    if total_duration <= 0:
        raise LivePreviewError("This timeline has no real duration to preview.")
    clamped_timestamp = max(0.0, min(timestamp_seconds, max(0.0, total_duration - 0.01)))

    try:
        input_args, full_filter, video_out, audio_out = build_filtergraph(
            timeline, media_items, export_format, cwd=cwd,
        )
    except MultiSourceExportError as e:
        raise LivePreviewError(str(e)) from e

    filter_stages = [full_filter]
    if filters.caption_filter is not None:
        filter_stages.append(filters.caption_filter)
        video_out = extract_output_label(filters.caption_filter)
    if filters.text_overlay_filter is not None:
        filter_stages.append(filters.text_overlay_filter)
        video_out = extract_output_label(filters.text_overlay_filter)
    for extra_input_args, sticker_filter_clause in (filters.sticker_filters or []):
        input_args = input_args + extra_input_args
        filter_stages.append(sticker_filter_clause)
        video_out = extract_output_label(sticker_filter_clause)
    # The base filtergraph always produces BOTH a video and an audio
    # output (build_filtergraph()'s own concat node emits [outv][outa]
    # together) - export_timeline() maps both, but a single-frame PNG
    # preview has no use for audio and a PNG muxer cannot carry an audio
    # stream at all. ffmpeg's filtergraph parser rejects ANY declared
    # output that isn't consumed by something (a real, hand-hit bug this
    # fixes: `Filter 'concat' has output 1 (outa) unconnected` / `Error
    # binding filtergraph inputs/outputs: Invalid argument` on every
    # single preview render, caught by this module's own first real test
    # run) - `anullsink` is ffmpeg's own real, built-in "consume this
    # audio stream and discard it" filter (confirmed via `ffmpeg
    # -filters`), the correct fix rather than trying to `-map` an audio
    # stream into a format that cannot hold one.
    filter_stages.append(f"[{audio_out}]anullsink")
    combined_filter = ";".join(filter_stages)

    cwd.mkdir(parents=True, exist_ok=True)
    fd, raw_path = tempfile.mkstemp(suffix=".png", dir=str(cwd))
    os.close(fd)
    # mkstemp() opens the file descriptor itself - never left open past
    # this point. A real, hand-hit bug this fixes: ffmpeg's own "-y"
    # overwrite of this path, and this function's own error-path
    # unlink(), both failed with a Windows-only `PermissionError:
    # [WinError 32] The process cannot access the file because it is
    # being used by another process` while the fd stayed open (Windows,
    # unlike POSIX, refuses to delete/overwrite a file that still has an
    # open handle) - caught immediately by every real test in this
    # module's own test suite failing with that exact error.
    output_path = Path(raw_path)

    cmd = [
        ffmpeg, "-y", *input_args,
        "-filter_complex", combined_filter,
        "-map", f"[{video_out}]",
        # Seeking the OUTPUT (not a per-input -ss) is deliberate: the
        # assembled timeline's own timestamp does not correspond to any
        # single input's own timestamp once multiple clips/stills are
        # concatenated - -ss here means "skip this far into the already-
        # filtered, already-concatenated output stream," which is the
        # real, correct way to seek a multi-source filtergraph (an
        # input-side -ss would only ever seek the FIRST input, wrongly
        # assuming a single-source timeline).
        "-ss", str(clamped_timestamp),
        "-frames:v", "1", "-q:v", "2",
        str(output_path),
    ]
    try:
        result = subprocess.run(
            cmd, cwd=str(cwd), capture_output=True, text=True, timeout=_PREVIEW_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired:
        output_path.unlink(missing_ok=True)
        raise LivePreviewError("Preview render timed out.") from None

    if result.returncode != 0 or not output_path.is_file():
        output_path.unlink(missing_ok=True)
        raise LivePreviewError(f"Couldn't render preview. ffmpeg said: {result.stderr.strip()[-400:]}")

    return output_path
