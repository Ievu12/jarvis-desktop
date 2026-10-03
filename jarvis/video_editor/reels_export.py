"""Exporting the Reels layers: jarvis.video_editor.reels_render draws
every frame at the export resolution into a transparent overlay video
(QuickTime Animation, lossless RGBA), and the regular export
(multisource_export.export_timeline) puts it on top of the timeline as
one more overlay input - through its existing `sticker_filters` hook,
so the export pipeline itself is unchanged.

Because the overlay is drawn by the same code as the live preview, the
MP4 shows exactly what the preview showed. Frames whose drawing plan
equals the previous frame's are not redrawn (a phrase standing still
costs nothing)."""

from __future__ import annotations

import math
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Callable

from PIL import Image

from jarvis.video_editor import reels_render
from jarvis.video_editor.multisource_export import export_timeline
from jarvis.video_editor.reels import ReelsLayers

OVERLAY_FPS = 30
"""Must equal multisource_export's output frame rate so overlay frame n
lands on output frame n."""
OVERLAY_OUTPUT_LABEL = "reelsv"
_RENDER_SHARE = 35.0
"""Percent of the export progress bar the overlay rendering takes."""


class ReelsExportError(Exception):
    """Rendering the Reels overlay failed (ffmpeg missing or failed)."""


def overlay_scale(width: int, height: int) -> float:
    return reels_render.scale_for(width, height)


def build_overlay_filter(
    overlay_file_name: str, *, video_label: str, input_index: int, output_label: str = OVERLAY_OUTPUT_LABEL,
) -> tuple[list[str], str]:
    """(extra input args, filter clause) in export_timeline()'s
    sticker_filters format."""
    clause = f"[{video_label}][{input_index}:v]overlay=0:0:eof_action=pass:format=auto[{output_label}]"
    return ["-i", overlay_file_name], clause


def render_overlay_video(
    layers: ReelsLayers, *, duration_seconds: float, width: int, height: int, output_path: Path,
    scale: float | None = None, fps: int = OVERLAY_FPS,
    progress_callback: Callable[[float], None] | None = None, cancel_event: threading.Event | None = None,
) -> Path:
    """Writes the Reels layers of [0, duration] as a transparent video
    of width x height. Progress is reported 0-100."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ReelsExportError("FFmpeg nerastas. Vaizdo redaktoriui reikia FFmpeg.")
    scale = overlay_scale(width, height) if scale is None else scale
    frame_count = max(1, math.ceil(duration_seconds * fps))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg, "-y", "-loglevel", "error",
        "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0",
        "-c:v", "qtrle", "-pix_fmt", "argb", str(output_path),
    ]
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except OSError as e:
        raise ReelsExportError(f"Nepavyko paleisti FFmpeg: {e}") from e

    empty = bytes(width * height * 4)
    last_plan: tuple | None = None
    last_bytes = empty
    cancelled = False
    try:
        for n in range(frame_count):
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                break
            t = n / fps
            ops = reels_render.plan(layers, t=t, frame_width=width, frame_height=height, scale=scale)
            if ops != last_plan:
                if ops:
                    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
                    reels_render.paint(layer, ops)
                    last_bytes = layer.tobytes()
                else:
                    last_bytes = empty
                last_plan = ops
            proc.stdin.write(last_bytes)
            if progress_callback is not None and n % 15 == 0:
                progress_callback(100.0 * n / frame_count)
        proc.stdin.close()
    except (BrokenPipeError, OSError):
        pass  # ffmpeg died; its own error is reported below
    finally:
        if cancelled:
            proc.kill()
        proc.wait()
        stderr = proc.stderr.read().decode("utf-8", "replace").strip()[-400:] if proc.stderr else ""
        if proc.stderr:
            proc.stderr.close()

    if cancelled:
        output_path.unlink(missing_ok=True)
        raise ReelsExportError("Eksportas atšauktas.")
    if proc.returncode != 0 or not output_path.is_file():
        raise ReelsExportError(f"Nepavyko sukurti Reels sluoksnio. FFmpeg: {stderr}")
    if progress_callback is not None:
        progress_callback(100.0)
    return output_path


def export_timeline_with_reels(
    *, reels_layers: ReelsLayers, overlay_path: Path,
    progress_callback: Callable[[float], None] | None = None, cancel_event: threading.Event | None = None,
    **export_kwargs,
):
    """multisource_export.export_timeline(), after first rendering the
    Reels overlay video to `overlay_path` (which the caller has already
    wired into `sticker_filters` with build_overlay_filter()). Removes
    the overlay file afterwards."""
    export_format = export_kwargs["export_format"]
    timeline = export_kwargs["timeline"]

    def overlay_progress(percent: float) -> None:
        if progress_callback is not None:
            progress_callback(percent * _RENDER_SHARE / 100)

    def export_progress(percent: float) -> None:
        if progress_callback is not None:
            progress_callback(_RENDER_SHARE + percent * (100 - _RENDER_SHARE) / 100)

    try:
        render_overlay_video(
            reels_layers, duration_seconds=timeline.total_duration_seconds(), width=export_format.width,
            height=export_format.height, output_path=overlay_path, progress_callback=overlay_progress,
            cancel_event=cancel_event,
        )
        return export_timeline(progress_callback=export_progress, cancel_event=cancel_event, **export_kwargs)
    finally:
        overlay_path.unlink(missing_ok=True)


def compose_onto_frame(frame_path: Path, layers: ReelsLayers | None, *, t: float) -> Path:
    """Draws the Reels layers onto an already-rendered exact export
    frame (live_preview.render_preview_frame()) in place - the paused
    preview's "what the export makes" frame, with the Reels layers drawn
    by the same code the export overlay uses."""
    if layers is None or layers.is_empty:
        return frame_path
    with Image.open(frame_path) as opened:
        frame = opened.convert("RGBA")
    layer = reels_render.render_frame(
        layers, t=t, width=frame.width, height=frame.height, scale=overlay_scale(frame.width, frame.height),
    )
    frame.alpha_composite(layer)
    save_kwargs = {"quality": 95} if frame_path.suffix.lower() in (".jpg", ".jpeg") else {}
    frame.convert("RGB").save(frame_path, **save_kwargs)
    return frame_path
