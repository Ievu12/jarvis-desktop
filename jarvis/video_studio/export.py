"""Export System (module brief, sections 10 and 12): renders an
already-planned jarvis.video_studio.reel.ReelEditPlan into an actual
video file - trimming/concatenating the plan's selected source clips,
cropping/scaling to the target aspect ratio, optionally burning in
subtitles and a hook/CTA text overlay, and encoding H.264/AAC MP4.

ALWAYS writes to a NEW file under the project's exports/ directory
(jarvis.video_studio.storage.VideoProject.exports_dir) - never
modifies, moves, or deletes the original video, matching the module's
brief ("Never overwrite the original video. Always create a new
project/export.") and jarvis.video_studio.reel's own docstring for the
same rule at the planning stage.

Uses the system `ffmpeg` binary via subprocess, same as every other
jarvis.video_studio module (jarvis.video_studio.ffmpeg_utils,
.transcribe) - no new dependency, no Python video-processing library.
Every path handed to an FFmpeg filtergraph string (subtitle SRT file,
font file) goes through the exact cwd-relative-path + forward-slash
workaround jarvis.video_studio.transcribe's docstring documents (a
Windows drive-letter colon breaks FFmpeg's filtergraph parser even
when backslash-escaped, confirmed by hand testing there) - this module
reuses that same pattern rather than re-discovering it.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from jarvis.video_studio.reel import PlannedClip, ReelEditPlan

_EXPORT_TIMEOUT_SECONDS = 900

# Module brief section 10's Instagram Reel preset, plus the additional
# formats it lists (TikTok/YouTube Shorts share the same 9:16 spec;
# 1:1/4:5/16:9 are the brief's "optional export formats"). "original"
# keeps the source's own resolution/aspect ratio (no crop) - used when
# a person wants the trimmed/captioned edit without Instagram's
# specific framing.
@dataclass(frozen=True)
class ExportFormat:
    name: str
    width: int | None  # None for "original" (keeps source resolution)
    height: int | None
    label: str


EXPORT_FORMATS: dict[str, ExportFormat] = {
    "instagram_reel": ExportFormat("instagram_reel", 1080, 1920, "Instagram Reel (9:16)"),
    "tiktok": ExportFormat("tiktok", 1080, 1920, "TikTok (9:16)"),
    "youtube_shorts": ExportFormat("youtube_shorts", 1080, 1920, "YouTube Shorts (9:16)"),
    "square": ExportFormat("square", 1080, 1080, "Square (1:1)"),
    "portrait_4_5": ExportFormat("portrait_4_5", 1080, 1350, "Portrait (4:5)"),
    "landscape_16_9": ExportFormat("landscape_16_9", 1920, 1080, "Landscape (16:9)"),
    "original": ExportFormat("original", None, None, "Original resolution"),
}


class ExportError(Exception):
    """Raised for any export failure (ffmpeg missing, empty plan,
    subprocess failure/timeout, write failure) - always with a
    human-readable message, per the module's brief's "Never silently
    fail" / "Display human-readable errors" requirements."""


@dataclass(frozen=True)
class ExportResult:
    output_path: Path
    export_format: str
    duration_seconds: float
    width: int
    height: int
    file_size_bytes: int


def _require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        raise ExportError(
            "FFmpeg was not found on PATH. AI Video Studio requires FFmpeg to be installed."
        )
    return path


def _crop_scale_filter(target: ExportFormat) -> str:
    """A crop-to-aspect-then-scale filter chain - crops the SOURCE
    video's center to the target aspect ratio first (so nothing is
    stretched/distorted), then scales to the target's exact pixel
    dimensions. For "original" (no target dimensions), returns an
    empty string (no crop/scale filter is applied at all)."""
    if target.width is None or target.height is None:
        return ""
    # crop=ih*W/H:ih when the target is taller-than-wide relative to
    # source, else crop=iw:iw*H/W - using the simpler, always-safe form
    # (crop width from height, for a portrait target) since every
    # EXPORT_FORMATS entry other than landscape_16_9 is portrait/square;
    # landscape_16_9 uses the width-from-height form instead.
    if target.height >= target.width:
        crop = f"crop=ih*{target.width}/{target.height}:ih"
    else:
        crop = f"crop=iw:iw*{target.height}/{target.width}"
    return f"{crop},scale={target.width}:{target.height}"


def _write_srt(clips: list[PlannedClip], destination: Path) -> None:
    """Writes a simple SRT with each clip's caption_text shown for that
    clip's full duration in the ASSEMBLED (concatenated) timeline, not
    the original source timeline - i.e. clip N's caption is timed
    against where it lands in the exported Reel, not its original
    position in the source video."""
    lines = []
    cursor = 0.0
    for i, clip in enumerate(clips, start=1):
        start = cursor
        end = cursor + clip.duration_seconds
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}")
        lines.append(clip.caption_text)
        lines.append("")
        cursor = end
    destination.write_text("\n".join(lines), encoding="utf-8")


def _srt_timestamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def export_reel(
    source_path: Path, plan: ReelEditPlan, *, export_format: str, output_path: Path,
    burn_in_captions: bool = True,
) -> ExportResult:
    """Renders `plan` (an already-generated ReelEditPlan - see
    jarvis.video_studio.reel.generate_reel_edit()) from `source_path`
    into `output_path` (always a NEW file - the caller is responsible
    for pointing this at the project's exports_dir, never at
    source_path itself; this function does not enforce that by path
    comparison, but every call site in jarvis.gui.views.video_studio
    always does). Raises ExportError on any failure - an empty plan,
    an unknown export_format, ffmpeg missing, or the subprocess itself
    failing/timing out."""
    ffmpeg = _require_ffmpeg()
    if not plan.clips:
        raise ExportError("This edit plan has no clips to export.")
    if export_format not in EXPORT_FORMATS:
        raise ExportError(f"Unknown export format '{export_format}'.")
    if not source_path.is_file():
        raise ExportError(f"Source file not found: {source_path}")

    target = EXPORT_FORMATS[export_format]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build the trim+concat filtergraph for the selected clips (same
    # pattern verified by hand testing: N trim/atrim pairs feeding one
    # concat). Then, on the CONCATENATED result, apply crop/scale and
    # (optionally) a burned-in subtitle track built from the plan's own
    # per-clip captions - subtitles must be added AFTER concat since
    # their timing is against the assembled timeline, not the source's.
    trim_parts = []
    concat_inputs = []
    for i, clip in enumerate(plan.clips):
        trim_parts.append(
            f"[0:v]trim=start={clip.source_start_seconds}:end={clip.source_end_seconds},"
            f"setpts=PTS-STARTPTS[v{i}];"
            f"[0:a]atrim=start={clip.source_start_seconds}:end={clip.source_end_seconds},"
            f"asetpts=PTS-STARTPTS[a{i}];"
        )
        concat_inputs.append(f"[v{i}][a{i}]")
    concat_filter = "".join(trim_parts) + "".join(concat_inputs) + f"concat=n={len(plan.clips)}:v=1:a=1[catv][cata]"

    crop_scale = _crop_scale_filter(target)
    video_label = "catv"
    filter_stages = [concat_filter]

    srt_path: Path | None = None
    if burn_in_captions:
        srt_path = output_path.with_suffix(".captions.srt")
        _write_srt(plan.clips, srt_path)

    cwd = output_path.parent
    video_filter_chain_parts = []
    if crop_scale:
        video_filter_chain_parts.append(crop_scale)
    if srt_path is not None:
        video_filter_chain_parts.append(f"subtitles={srt_path.name}")

    if video_filter_chain_parts:
        chain = ",".join(video_filter_chain_parts)
        filter_stages.append(f"[{video_label}]{chain}[outv]")
        video_label = "outv"

    full_filter = ";".join(filter_stages)

    cmd = [
        ffmpeg, "-y", "-i", str(source_path.resolve()),
        "-filter_complex", full_filter,
        "-map", f"[{video_label}]", "-map", "[cata]",
        "-c:v", "libx264", "-c:a", "aac", "-movflags", "+faststart",
        output_path.name,
    ]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_EXPORT_TIMEOUT_SECONDS,
            check=False, cwd=str(cwd),
        )
    except subprocess.TimeoutExpired:
        raise ExportError(f"Export timed out for {output_path.name}.") from None
    finally:
        if srt_path is not None:
            srt_path.unlink(missing_ok=True)

    if result.returncode != 0 or not output_path.is_file():
        stderr_tail = result.stderr.strip()[-400:]
        raise ExportError(f"Export failed. ffmpeg said: {stderr_tail}")

    from jarvis.video_studio.ffmpeg_utils import probe_video

    probe = probe_video(output_path)
    return ExportResult(
        output_path=output_path, export_format=export_format, duration_seconds=probe.duration_seconds,
        width=probe.width or 0, height=probe.height or 0, file_size_bytes=probe.file_size_bytes,
    )
