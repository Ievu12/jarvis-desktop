"""Free-standing text overlays (titles, callouts, labels) - distinct
from jarvis.video_editor.captions (which only ever shows TRANSCRIBED
speech text, timed by real whisper measurements). A TextOverlay is
person-typed text shown during an explicit, chosen time window, with
its own font/color/size/position (anywhere on the 0.0-1.0 canvas, not
just captions.py's own fixed top/center/bottom three choices) - the
genuinely different, free-text-entry style the Video Editor's own
"teksto įterpimas" requirement asks for, never a generalization of
CaptionStyle (which stays scoped to transcription-driven styling only).

Same drawtext-overlay-on-[outv] mechanism and fontfile= workaround as
captions.py (see that module's own docstring for the real, hand-hit
fontconfig access-violation finding this reuses, not re-derives) -
duplicated here (not imported from captions.py) because this module's
own isolation rule for jarvis.video_editor is "no module imports a
sibling's internals," the same rule captions.py/audio_mixing.py already
follow relative to multisource_export.py.

Composited as an ADDITIONAL overlay stage, chainable with captions - a
timeline can have both real transcribed captions AND free-typed text
overlays at the same time, each its own drawtext stage reading from
whichever label came before it (export_timeline()'s own
text_overlay_filter parameter, applied after caption_filter if both are
given, mirrors exactly how audio_mix_filter already chains after
caption_filter for video/audio respectively)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

_DEFAULT_FONT_FILE = "C:/Windows/Fonts/arialbd.ttf"
# Same confirmed-working path captions.py's own _DEFAULT_FONT_FILE uses
# - see that module's docstring for the real fontconfig crash this
# avoids. Duplicated, not imported, per this module's own isolation
# rule stated above.

DEFAULT_TEXT_FONT_SIZE = 56
DEFAULT_TEXT_COLOR = "white"

TextAnimation = Literal["none", "fade"]
TEXT_ANIMATION_CHOICES: tuple[TextAnimation, ...] = ("none", "fade")

_MIN_POSITION = 0.0
_MAX_POSITION = 1.0


class TextOverlayError(Exception):
    """Raised for an invalid TextOverlay (bad time window, position
    outside 0.0-1.0) - a LOCAL exception, never subclassing
    CaptionError/MultiSourceExportError, matching this package's
    established per-module isolation convention."""


@dataclass(frozen=True)
class TextOverlay:
    """One free-typed text element, visible on screen from
    `start_seconds` to `end_seconds` (positions in the ASSEMBLED
    timeline's own timeline, same convention Timeline.total_duration_seconds()
    itself uses - NOT a single source clip's own timeline the way
    captions.py's own WordTiming is, since a text overlay is placed by
    the person directly against the final, assembled video, with no
    source-clip offset adjustment ever needed).

    `x_fraction`/`y_fraction` (0.0-1.0) position this text's own
    top-left corner as a fraction of the canvas width/height - a
    genuinely free position, not CaptionStyle's own fixed top/center/
    bottom three choices, since free text (a title, a callout) commonly
    needs placement captions never do."""

    text: str
    start_seconds: float
    end_seconds: float
    x_fraction: float = 0.5
    y_fraction: float = 0.1
    font_size: int = DEFAULT_TEXT_FONT_SIZE
    color: str = DEFAULT_TEXT_COLOR
    animation: TextAnimation = "none"
    fade_seconds: float = 0.3

    def validate(self) -> list[str]:
        """Never raises - matches Timeline.validate()/EffectSpec.validate()'s
        own established "describe problems, don't throw" convention."""
        problems: list[str] = []
        if not self.text.strip():
            problems.append("Text overlay has no text.")
        if self.end_seconds <= self.start_seconds:
            problems.append("Text overlay end time must be after its start time.")
        if not (_MIN_POSITION <= self.x_fraction <= _MAX_POSITION):
            problems.append(f"Text overlay x position {self.x_fraction} must be between 0.0 and 1.0.")
        if not (_MIN_POSITION <= self.y_fraction <= _MAX_POSITION):
            problems.append(f"Text overlay y position {self.y_fraction} must be between 0.0 and 1.0.")
        if self.font_size <= 0:
            problems.append("Text overlay font size must be greater than zero.")
        if self.animation == "fade" and self.fade_seconds <= 0.0:
            problems.append("Text overlay fade duration must be greater than zero when fade animation is selected.")
        return problems


def _escape_drawtext_text(text: str) -> str:
    """Identical escaping rule to captions.py's own
    _escape_drawtext_text() (duplicated, not imported - see this
    module's own isolation-rule docstring above)."""
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def build_text_overlay_filter(
    overlays: list[TextOverlay], *, video_label: str = "outv", output_label: str = "textv",
) -> str:
    """Builds the ffmpeg filter clause(s) compositing every overlay in
    `overlays` on top of `[{video_label}]`, producing `[{output_label}]` -
    mirrors jarvis.video_editor.captions.build_caption_filter()'s own
    contract exactly (same label-chaining shape), so
    multisource_export.export_timeline()'s own _extract_output_label()
    helper works identically on this function's return value too.

    Raises TextOverlayError if any overlay in `overlays` fails its own
    validate() - never silently drops/clamps an invalid overlay the
    caller didn't ask for."""
    for overlay in overlays:
        problems = overlay.validate()
        if problems:
            raise TextOverlayError("; ".join(problems))

    if not overlays:
        return f"[{video_label}]null[{output_label}]"

    font_file_arg = _escape_drawtext_text(_DEFAULT_FONT_FILE)
    clauses: list[str] = []
    for overlay in overlays:
        escaped_text = _escape_drawtext_text(overlay.text)
        x_expr = f"(w-text_w)*{overlay.x_fraction}"
        y_expr = f"(h-text_h)*{overlay.y_fraction}"
        alpha_expr = ""
        if overlay.animation == "fade":
            fade_in_end = overlay.start_seconds + overlay.fade_seconds
            fade_out_start = max(overlay.start_seconds, overlay.end_seconds - overlay.fade_seconds)
            alpha_expr = (
                f":alpha='if(lt(t,{overlay.start_seconds}),0,"
                f"if(lt(t,{fade_in_end}),(t-{overlay.start_seconds})/{overlay.fade_seconds},"
                f"if(lt(t,{fade_out_start}),1,"
                f"if(lt(t,{overlay.end_seconds}),({overlay.end_seconds}-t)/{overlay.fade_seconds},0))))'"
            )
        clauses.append(
            f"drawtext=fontfile='{font_file_arg}':text='{escaped_text}':fontsize={overlay.font_size}:"
            f"fontcolor={overlay.color}:x='{x_expr}':y='{y_expr}'{alpha_expr}:"
            f"enable='between(t,{overlay.start_seconds},{overlay.end_seconds})'"
        )

    chain = ",".join(clauses)
    return f"[{video_label}]{chain}[{output_label}]"
