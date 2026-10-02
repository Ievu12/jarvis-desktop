"""Ken Burns-style photo animation (zoom/pan) and whole-clip color
filters/fade-in/fade-out for jarvis.video_editor's own Timeline -
genuinely new work (neither jarvis.video_studio nor jarvis.reel_generator
has a per-still zoom/pan effect; jarvis.reel_generator's own motion
comes from a paid, separate Runway video-generation call, not a local
ffmpeg filter).

Unlike jarvis.video_editor.captions/.audio_mixing (which overlay the
WHOLE assembled `[outv]`/`[outa]` output AFTER concat), an effect here
applies to ONE segment's own video label BEFORE it's scaled/padded and
fed into concat - a zoom/pan motion only makes sense relative to a
single photo/clip's own source frame, not the already-letterboxed,
multi-source assembled timeline. This is why EffectSpec lives as a new
optional field on TimelineStill/TimelineClip themselves (see
jarvis.video_editor.timeline's own additive field), and why
build_segment_effect_filter() is called from INSIDE
multisource_export.build_filtergraph()'s own per-item loop, not
threaded through export_timeline()'s own opaque caption_filter/
audio_mix_filter parameters - a per-segment effect has no single,
well-defined "video_out label" the way a whole-timeline overlay does.

ffmpeg's `zoompan` filter (not a second, competing animation engine)
drives every PhotoMotion kind here - the SAME filter
jarvis.reel_generator's own earlier zoompan-fps-mismatch bug (referenced
in multisource_export.py's own _TARGET_FPS docstring) already taught
this codebase to always pin an explicit, matching `fps=` after zoompan,
which this module's own build_segment_effect_filter() does too."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PhotoMotionKind = Literal[
    "none", "zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down",
]
PHOTO_MOTION_CHOICES: tuple[PhotoMotionKind, ...] = (
    "none", "zoom_in", "zoom_out", "pan_left", "pan_right", "pan_up", "pan_down",
)

FadeKind = Literal["none", "fade_in", "fade_out", "fade_both"]
FADE_CHOICES: tuple[FadeKind, ...] = ("none", "fade_in", "fade_out", "fade_both")

_MIN_ZOOM_INTENSITY = 1.0
_MAX_ZOOM_INTENSITY = 1.5
# zoompan's own "zoom ratio reached by the end of the segment" - 1.0 is
# a no-op (included so a caller can dial intensity down to nothing
# without switching `kind` back to "none"), 1.5 is a deliberately sane
# upper bound (a 50% zoom over a typical 2-6s still is already a strong
# Ken Burns effect; ffmpeg accepts higher values but they look like a
# mistake, not a style choice, past this).

_ZOOMPAN_SUPERSAMPLE = 2
# zoompan operates on whatever resolution it's given - without first
# upscaling a modest source photo (e.g. 800x600) by a supersampling
# factor, zooming in on it reveals visible pixelation/aliasing, since
# zoompan has no interpolation of its own beyond what it's fed. This
# is the standard, documented ffmpeg-community workaround (scale up
# before zoompan, scale back down after) - not a new technique invented
# here. 2x (not a higher factor like 4x) is a deliberate speed/quality
# tradeoff - a real, hand-timed 720p zoom export at 4x supersample
# (effectively processing at 5120x2880) took long enough to be a real
# usability problem; 2x still visibly reduces pixelation over no
# supersampling at all while keeping export times reasonable for an
# interactive GUI workflow.


class EffectError(Exception):
    """Raised for an invalid EffectSpec (e.g. intensity outside its own
    sane bound) - a LOCAL exception, never subclassing a sibling
    module's own error type, matching jarvis.video_editor's established
    per-module isolation convention (see CaptionError/
    AudioMixingError/MultiSourceExportError's own docstrings)."""


@dataclass(frozen=True)
class EffectSpec:
    """One segment's own optional motion + fade + color-filter
    configuration - every field defaults to its own "no effect" value,
    so adding this dataclass to TimelineStill/TimelineClip never changes
    behavior for a project that predates this feature (see
    jarvis.video_editor.timeline's own backward-compatibility note on
    the new `effect` field)."""

    motion: PhotoMotionKind = "none"
    motion_intensity: float = 1.2
    fade: FadeKind = "none"
    fade_seconds: float = 0.5
    brightness: float = 0.0
    contrast: float = 1.0
    saturation: float = 1.0

    def validate(self) -> list[str]:
        """Never raises - see Timeline.validate()'s own established
        "describe problems, don't throw" convention this mirrors."""
        problems: list[str] = []
        if self.motion not in PHOTO_MOTION_CHOICES:
            problems.append(f"Unknown motion effect: {self.motion!r}.")
        if not (_MIN_ZOOM_INTENSITY <= self.motion_intensity <= _MAX_ZOOM_INTENSITY):
            problems.append(
                f"Motion intensity {self.motion_intensity} is outside the supported "
                f"{_MIN_ZOOM_INTENSITY}-{_MAX_ZOOM_INTENSITY} range."
            )
        if self.fade not in FADE_CHOICES:
            problems.append(f"Unknown fade effect: {self.fade!r}.")
        if self.fade != "none" and self.fade_seconds <= 0.0:
            problems.append("Fade duration must be greater than zero when a fade is selected.")
        if not (-1.0 <= self.brightness <= 1.0):
            problems.append(f"Brightness {self.brightness} is outside the supported -1.0 to 1.0 range.")
        if not (0.0 <= self.contrast <= 3.0):
            problems.append(f"Contrast {self.contrast} is outside the supported 0.0 to 3.0 range.")
        if not (0.0 <= self.saturation <= 3.0):
            problems.append(f"Saturation {self.saturation} is outside the supported 0.0 to 3.0 range.")
        return problems


def _zoompan_clause(
    spec: EffectSpec, *, width: int, height: int, duration_seconds: float, fps: int, label_in: str, label_out: str,
) -> str:
    """Builds one `zoompan=...` clause implementing `spec.motion` -
    `z=` (the zoom expression) and `x=`/`y=` (the pan offset
    expressions) are ffmpeg frame-number expressions evaluated once per
    output frame (`on`/`in` in zoompan's own vocabulary), which is why
    every expression below is written in terms of `on`/`ih`/`iw`/`out_w`/
    `out_h` rather than wall-clock time - zoompan's own documented
    convention, not this module's invention.

    `d=1` (NOT the segment's own total frame count) - a real, hand-hit
    bug found while first testing this function: the upstream input
    here is already a continuous stream of `duration_seconds * fps`
    individual frames (every TimelineStill segment is fed through
    `-loop 1 -t <duration>` before reaching this filter, and a
    TimelineClip is already a real video stream), so zoompan's own
    `d=` (frames to hold/process PER INPUT FRAME) must stay at its
    default of 1 - setting it to the segment's own total frame count
    instead made zoompan multiply duration by itself (a 3-second clip
    at `d=90` became a measured, ffprobe-confirmed 270-second output:
    90 input frames x 90 held-frames-each = 8100 output frames). `on`
    (the running output-frame counter zoompan itself exposes) already
    correctly counts up to `total_frames` across the real input stream
    without this parameter's help."""
    total_frames = max(1, round(duration_seconds * fps))
    zoom_end = spec.motion_intensity

    if spec.motion == "zoom_in":
        z_expr = f"min(zoom+({zoom_end - 1.0}/{total_frames}),{zoom_end})"
        x_expr, y_expr = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif spec.motion == "zoom_out":
        z_expr = f"if(eq(on,0),{zoom_end},max(zoom-({zoom_end - 1.0}/{total_frames}),1.0))"
        x_expr, y_expr = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif spec.motion == "pan_left":
        z_expr = f"{zoom_end}"
        x_expr = f"iw/2-(iw/zoom/2)+(iw/zoom/2)*(1-on/{total_frames})"
        y_expr = "ih/2-(ih/zoom/2)"
    elif spec.motion == "pan_right":
        z_expr = f"{zoom_end}"
        x_expr = f"iw/2-(iw/zoom/2)-(iw/zoom/2)*(1-on/{total_frames})"
        y_expr = "ih/2-(ih/zoom/2)"
    elif spec.motion == "pan_up":
        z_expr = f"{zoom_end}"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = f"ih/2-(ih/zoom/2)+(ih/zoom/2)*(1-on/{total_frames})"
    elif spec.motion == "pan_down":
        z_expr = f"{zoom_end}"
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = f"ih/2-(ih/zoom/2)-(ih/zoom/2)*(1-on/{total_frames})"
    else:
        raise EffectError(f"_zoompan_clause() called with a non-motion spec: {spec.motion!r}")

    return (
        f"{label_in}scale={width * _ZOOMPAN_SUPERSAMPLE}:{height * _ZOOMPAN_SUPERSAMPLE},"
        f"zoompan=z='{z_expr}':x='{x_expr}':y='{y_expr}':d=1:s={width}x{height}:fps={fps}"
        f"{label_out}"
    )


def _fade_clause(spec: EffectSpec, *, duration_seconds: float, label_in: str, label_out: str) -> str:
    stages: list[str] = []
    if spec.fade in ("fade_in", "fade_both"):
        stages.append(f"fade=t=in:st=0:d={spec.fade_seconds}")
    if spec.fade in ("fade_out", "fade_both"):
        fade_out_start = max(0.0, duration_seconds - spec.fade_seconds)
        stages.append(f"fade=t=out:st={fade_out_start}:d={spec.fade_seconds}")
    if not stages:
        return f"{label_in}null{label_out}"
    return f"{label_in}{','.join(stages)}{label_out}"


def _color_clause(spec: EffectSpec, *, label_in: str, label_out: str) -> str:
    if spec.brightness == 0.0 and spec.contrast == 1.0 and spec.saturation == 1.0:
        return f"{label_in}null{label_out}"
    return (
        f"{label_in}eq=brightness={spec.brightness}:contrast={spec.contrast}:saturation={spec.saturation}{label_out}"
    )


def build_segment_effect_filter(
    spec: EffectSpec, *, width: int, height: int, duration_seconds: float, fps: int,
    video_label: str, output_label: str,
) -> str:
    """Builds the complete per-segment filter clause chain for one
    TimelineClip/TimelineStill - motion (zoompan, if any) then fade then
    color, each stage reading from the previous one's own output label,
    so this function's own caller (multisource_export.build_filtergraph())
    can splice the result in BEFORE its own `scale_pad` normalization
    step, exactly where that function currently does
    `f"[{i}:v]{scale_pad}[v{n}];"` for a plain, effect-less segment.

    Raises EffectError if `spec.validate()` finds problems - never
    silently clamps/guesses a value the caller didn't ask for."""
    problems = spec.validate()
    if problems:
        raise EffectError("; ".join(problems))

    stages: list[str] = []
    current_label = video_label
    stage_n = 0

    if spec.motion != "none":
        stage_n += 1
        next_label = f"[{output_label}_m{stage_n}]"
        stages.append(_zoompan_clause(
            spec, width=width, height=height, duration_seconds=duration_seconds, fps=fps,
            label_in=current_label, label_out=next_label,
        ))
        current_label = next_label

    if spec.fade != "none":
        stage_n += 1
        next_label = f"[{output_label}_f{stage_n}]"
        stages.append(_fade_clause(
            spec, duration_seconds=duration_seconds, label_in=current_label, label_out=next_label,
        ))
        current_label = next_label

    if spec.brightness != 0.0 or spec.contrast != 1.0 or spec.saturation != 1.0:
        stage_n += 1
        next_label = f"[{output_label}_c{stage_n}]"
        stages.append(_color_clause(spec, label_in=current_label, label_out=next_label))
        current_label = next_label

    if not stages:
        return f"{video_label}null[{output_label}]"

    # Relabel the final stage's own output to the conventional
    # output_label the caller asked for, so it never needs to know how
    # many intermediate stages this function used internally.
    last_clause = stages[-1]
    stages[-1] = last_clause.rsplit("[", 1)[0] + f"[{output_label}]"
    return ";".join(stages)


_PREVIEW_TIMEOUT_SECONDS = 60
# A preview renders ONE short segment (not a whole timeline/export), so
# this is deliberately much tighter than
# multisource_export._EXPORT_TIMEOUT_SECONDS (1800s) - a preview that
# takes longer than this indicates something is genuinely wrong
# (ffmpeg hung/misconfigured), not just "a big project."


def render_effect_preview(
    source_path, *, spec: EffectSpec, width: int, height: int, duration_seconds: float,
    is_still: bool, output_dir,
) -> list:
    """Renders a REAL, short clip of `source_path` with `spec` applied
    (the exact same build_segment_effect_filter() this media item's own
    real export would use) and extracts 3 real frames (start/mid/end)
    from it into `output_dir` - "animacijų peržiūra prieš eksportavimą"
    (requirement: preview the animation before exporting), implemented
    as real, cheap frame extraction rather than a full in-app video
    player (this package's own established "no new in-app video
    decoder" convention - see jarvis.gui.views.video_editor
    .timeline_panel's own docstring for the precedent this follows).

    Returns the 3 real PNG file paths (start/mid/end), in order. Raises
    EffectError if `spec` is invalid (same validation
    build_segment_effect_filter() itself already enforces) or
    MultiSourceExportError-equivalent (a local, generic RuntimeError -
    this module never imports multisource_export, preserving this
    package's own per-module isolation rule) if ffmpeg itself fails."""
    import shutil
    import subprocess
    from pathlib import Path

    problems = spec.validate()
    if problems:
        raise EffectError("; ".join(problems))

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise RuntimeError("FFmpeg was not found on PATH.")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    preview_path = output_dir / "effect_preview.mp4"

    scale_pad = (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,fps=30"
    )
    effect_clause = build_segment_effect_filter(
        spec, width=width, height=height, duration_seconds=duration_seconds, fps=30,
        video_label="[vraw]", output_label="outv",
    )
    filter_complex = f"[0:v]{scale_pad}[vraw];{effect_clause}"

    input_args = ["-loop", "1", "-t", str(duration_seconds), "-i", str(source_path)] if is_still else (
        ["-ss", "0", "-t", str(duration_seconds), "-i", str(source_path)]
    )
    cmd = [
        ffmpeg, "-y", *input_args, "-filter_complex", filter_complex, "-map", "[outv]",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(preview_path),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=_PREVIEW_TIMEOUT_SECONDS, text=True)
    if result.returncode != 0 or not preview_path.is_file():
        raise RuntimeError(f"Couldn't render animation preview. ffmpeg said: {result.stderr.strip()[-400:]}")

    frame_paths = []
    for label, timestamp in (("start", 0.05), ("mid", duration_seconds / 2), ("end", max(0.05, duration_seconds - 0.1))):
        frame_path = output_dir / f"effect_preview_{label}.png"
        subprocess.run(
            [ffmpeg, "-y", "-ss", str(timestamp), "-i", str(preview_path), "-frames:v", "1", str(frame_path)],
            capture_output=True, timeout=15, check=False,
        )
        frame_paths.append(frame_path)
    return frame_paths
