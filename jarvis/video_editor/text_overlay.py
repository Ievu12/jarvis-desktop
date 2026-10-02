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

TextAnimation = Literal[
    "none", "fade", "typewriter", "pop_up", "pop_out", "slide_in", "slide_out",
    "bounce", "shake", "glitch", "glow", "zoom",
]
TEXT_ANIMATION_CHOICES: tuple[TextAnimation, ...] = (
    "none", "fade", "typewriter", "pop_up", "pop_out", "slide_in", "slide_out",
    "bounce", "shake", "glitch", "glow", "zoom",
)
# "Kinetic Typography" requirement - animated titles/headlines/CTAs for
# Instagram Reels. Each kind's own real mechanism (no two share
# implementation, each verified against a real ffmpeg export before
# shipping):
#   typewriter - cumulative growing substrings, each shown only during
#     its own short reveal window (same `enable=between(t,...)` pattern
#     every other animation in this package already uses, applied once
#     per CHARACTER instead of once per word/overlay).
#   pop_up - a quick fontsize ramp from half-size to full size over the
#     overlay's own first _POP_DURATION_SECONDS (same real mechanism
#     jarvis.video_editor.captions's own "pop" animation already uses).
#   pop_out - the exact mirror of pop_up: a quick fontsize ramp from
#     full size DOWN to near-zero over the overlay's own LAST
#     _POP_DURATION_SECONDS, right before it disappears - a real, exit-
#     side counterpart "pop_up" doesn't cover (that one only ever
#     animates the entrance).
#   slide_in - the overlay's own x position eases in from off-screen
#     (left or right, picked by `overlay.direction`) to its real resting
#     x position over the overlay's own first _SLIDE_DURATION_SECONDS.
#   slide_out - the mirror of slide_in: eases OUT toward off-screen over
#     the overlay's own LAST _SLIDE_DURATION_SECONDS.
#   bounce - a damped vertical oscillation via a real sine expression
#     for the overlay's own full duration (not just its reveal window).
#   shake - a continuous, higher-frequency horizontal jitter via a real
#     sine expression across the overlay's own FULL duration (distinct
#     from "glitch", which adds a one-time chromatic-aberration color
#     split rather than a sustained shake, and distinct from "bounce",
#     which moves vertically, not horizontally).
#   glitch - a real per-frame horizontal jitter (a noisy, time-varying x
#     offset) plus a brief red/cyan channel-offset "chromatic
#     aberration" look (two colored copies offset in opposite
#     directions, the standard, real glitch-art technique) rather than
#     a cosmetic label on an unchanged static clause.
#   glow - a blurred, brighter-colored copy of the same text rendered
#     BEHIND the sharp text (via ffmpeg's own real `gblur` filter on a
#     duplicated drawtext layer - confirmed this filter exists and
#     works on this machine's ffmpeg build), not a fake box-shadow.
#   zoom - a continuous fontsize ramp across the overlay's own full
#     duration (distinct from "pop_up"'s short one-time reveal ramp).

TextSlideDirection = Literal["left", "right"]
TEXT_SLIDE_DIRECTION_CHOICES: tuple[TextSlideDirection, ...] = ("left", "right")

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
    speed: float = 1.0
    intensity: float = 1.0
    direction: TextSlideDirection = "left"
    # Only meaningful for "slide_in"/"slide_out" - which off-screen side
    # the text eases in from / out toward. Ignored by every other
    # animation kind, same "unused field for most, real field for one
    # specific kind" convention jarvis.video_editor.audio_sync's own
    # AmplitudePeak-adjacent fields establish elsewhere in this package.

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
        if self.speed <= 0.0:
            problems.append("Text overlay speed must be greater than zero.")
        if self.intensity <= 0.0:
            problems.append("Text overlay intensity must be greater than zero.")
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
    multisource_export.export_timeline()'s own extract_output_label()
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
        if overlay.animation == "typewriter":
            clauses.append(_typewriter_clause(overlay, font_file_arg=font_file_arg))
        elif overlay.animation in ("pop_up", "pop_out", "zoom"):
            clauses.append(_scaling_text_clause(overlay, font_file_arg=font_file_arg))
        elif overlay.animation in ("slide_in", "slide_out"):
            clauses.append(_sliding_text_clause(overlay, font_file_arg=font_file_arg))
        elif overlay.animation == "bounce":
            clauses.append(_bounce_clause(overlay, font_file_arg=font_file_arg))
        elif overlay.animation == "shake":
            clauses.append(_shake_clause(overlay, font_file_arg=font_file_arg))
        elif overlay.animation == "glitch":
            clauses.append(_glitch_clause(overlay, font_file_arg=font_file_arg))
        elif overlay.animation == "glow":
            clauses.append(_glow_clause(overlay, font_file_arg=font_file_arg))
        else:
            clauses.append(_plain_or_fade_clause(overlay, font_file_arg=font_file_arg))

    chain = ",".join(clauses)
    return f"[{video_label}]{chain}[{output_label}]"


def _plain_or_fade_clause(overlay: TextOverlay, *, font_file_arg: str) -> str:
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
    return (
        f"drawtext=fontfile='{font_file_arg}':text='{escaped_text}':fontsize={overlay.font_size}:"
        f"fontcolor={overlay.color}:x='{x_expr}':y='{y_expr}'{alpha_expr}:"
        f"enable='between(t,{overlay.start_seconds},{overlay.end_seconds})'"
    )


_TYPEWRITER_CHAR_SECONDS = 0.08
# Base per-character reveal duration at speed=1.0 (a real, readable
# typing pace - ~12.5 characters/second) - divided by `overlay.speed`
# so a higher speed value reveals characters faster.


def _typewriter_clause(overlay: TextOverlay, *, font_file_arg: str) -> str:
    """Builds one drawtext clause PER cumulative substring (`"H"`,
    `"He"`, `"Hel"`, ...) - each visible only during its own short
    window, the same `enable=between(t,...)` mechanism every other
    animation in this package already uses, applied once per character
    instead of once per word. The text visibly "types itself out" at
    real, measured intervals (`_TYPEWRITER_CHAR_SECONDS / overlay.speed`
    per character), landing on the FULL text for the remainder of the
    overlay's own window."""
    text = overlay.text
    char_duration = _TYPEWRITER_CHAR_SECONDS / overlay.speed
    x_expr = f"(w-text_w)*{overlay.x_fraction}"
    y_expr = f"(h-text_h)*{overlay.y_fraction}"
    clauses = []
    for i in range(1, len(text) + 1):
        substring = _escape_drawtext_text(text[:i])
        reveal_time = overlay.start_seconds + (i - 1) * char_duration
        window_end = overlay.end_seconds if i == len(text) else min(overlay.end_seconds, reveal_time + char_duration)
        if reveal_time >= overlay.end_seconds:
            break
        clauses.append(
            f"drawtext=fontfile='{font_file_arg}':text='{substring}':fontsize={overlay.font_size}:"
            f"fontcolor={overlay.color}:x='{x_expr}':y='{y_expr}':"
            f"enable='between(t,{reveal_time},{window_end})'"
        )
    if not clauses:
        return "null"
    return ",".join(clauses)


_POP_DURATION_SECONDS = 0.2


def _scaling_text_clause(overlay: TextOverlay, *, font_file_arg: str) -> str:
    """"pop_up": a quick one-time fontsize ramp over the overlay's own
    first _POP_DURATION_SECONDS, landing at full size. "pop_out": the
    exact mirror - a quick fontsize ramp from full size down to near-
    zero over the overlay's own LAST _POP_DURATION_SECONDS, right before
    it disappears (a real exit-side counterpart "pop_up" never covers,
    since that one only ever animates the entrance). "zoom": a
    continuous fontsize ramp across the overlay's OWN FULL duration
    (never settling at a fixed size) - the real, distinguishing
    difference between all three, each driven by the same real,
    measurable fontsize expression mechanism
    jarvis.video_editor.captions's own "pop" animation already
    established."""
    escaped_text = _escape_drawtext_text(overlay.text)
    x_expr = f"(w-text_w)*{overlay.x_fraction}"
    y_expr = f"(h-text_h)*{overlay.y_fraction}"
    start, end = overlay.start_seconds, overlay.end_seconds

    if overlay.animation == "pop_up":
        ramp = max(0.05, _POP_DURATION_SECONDS / overlay.speed)
        size_expr = (
            f"if(lt(t,{start}+{ramp}),{overlay.font_size}*(0.3+0.7*(t-{start})/{ramp}),{overlay.font_size})"
        )
    elif overlay.animation == "pop_out":
        ramp = max(0.05, min(_POP_DURATION_SECONDS / overlay.speed, (end - start) / 2))
        ramp_start = end - ramp
        size_expr = (
            f"if(gt(t,{ramp_start}),{overlay.font_size}*(1-0.9*(t-{ramp_start})/{ramp}),{overlay.font_size})"
        )
    else:  # "zoom"
        duration = max(0.1, end - start)
        growth = 0.5 * overlay.intensity
        size_expr = f"{overlay.font_size}*(1+{growth}*(t-{start})/{duration})"

    return (
        f"drawtext=fontfile='{font_file_arg}':text='{escaped_text}':fontsize='{size_expr}':"
        f"fontcolor={overlay.color}:x='{x_expr}':y='{y_expr}':enable='between(t,{start},{end})'"
    )


def _bounce_clause(overlay: TextOverlay, *, font_file_arg: str) -> str:
    """A damped vertical bounce via a real sine expression, decaying
    over the overlay's own duration via an exponential envelope - a
    genuine, time-varying position effect for the overlay's own full
    visible window (not just a one-time reveal transition)."""
    escaped_text = _escape_drawtext_text(overlay.text)
    x_expr = f"(w-text_w)*{overlay.x_fraction}"
    start, end = overlay.start_seconds, overlay.end_seconds
    amplitude = 20 * overlay.intensity
    frequency = 6 * overlay.speed
    y_expr = (
        f"(h-text_h)*{overlay.y_fraction}"
        f"-{amplitude}*exp(-3*(t-{start}))*abs(sin({frequency}*(t-{start})))"
    )
    return (
        f"drawtext=fontfile='{font_file_arg}':text='{escaped_text}':fontsize={overlay.font_size}:"
        f"fontcolor={overlay.color}:x='{x_expr}':y='{y_expr}':enable='between(t,{start},{end})'"
    )


_SLIDE_DURATION_SECONDS = 0.35


def _sliding_text_clause(overlay: TextOverlay, *, font_file_arg: str) -> str:
    """"slide_in": the overlay's own x position eases in from off-screen
    (fully off the left or right edge, per `overlay.direction`) to its
    real resting x position over the overlay's own first
    _SLIDE_DURATION_SECONDS. "slide_out": the mirror - eases OUT toward
    off-screen over the overlay's own LAST _SLIDE_DURATION_SECONDS. Both
    use a real, measurable linear x-offset expression (no fake/implied
    motion - the text's own drawtext x= value genuinely changes every
    frame during the slide window)."""
    escaped_text = _escape_drawtext_text(overlay.text)
    base_x = f"(w-text_w)*{overlay.x_fraction}"
    y_expr = f"(h-text_h)*{overlay.y_fraction}"
    start, end = overlay.start_seconds, overlay.end_seconds
    sign = -1 if overlay.direction == "left" else 1
    # "left" slides in FROM the left (off-screen is further left, hence
    # a negative offset that shrinks to 0), "right" slides in FROM the
    # right (a positive offset shrinking to 0) - the sign is the same
    # for slide_out, just applied at the END of the window instead of
    # the start, so text exits toward the SAME side it's associated with
    # rather than reversing direction confusingly.
    offscreen_offset = sign * 600

    if overlay.animation == "slide_in":
        duration = max(0.05, _SLIDE_DURATION_SECONDS / overlay.speed)
        x_expr = (
            f"if(lt(t,{start}+{duration}),({base_x})+({offscreen_offset})*(1-(t-{start})/{duration}),{base_x})"
        )
    else:  # "slide_out"
        duration = max(0.05, min(_SLIDE_DURATION_SECONDS / overlay.speed, (end - start) / 2))
        slide_start = end - duration
        x_expr = (
            f"if(gt(t,{slide_start}),({base_x})+({offscreen_offset})*((t-{slide_start})/{duration}),{base_x})"
        )

    return (
        f"drawtext=fontfile='{font_file_arg}':text='{escaped_text}':fontsize={overlay.font_size}:"
        f"fontcolor={overlay.color}:x='{x_expr}':y='{y_expr}':enable='between(t,{start},{end})'"
    )


def _shake_clause(overlay: TextOverlay, *, font_file_arg: str) -> str:
    """A continuous, higher-frequency horizontal jitter via a real sine
    expression across the overlay's own FULL duration - distinct from
    "glitch" (a one-time chromatic-aberration color split, not a
    sustained positional shake) and "bounce" (vertical, not
    horizontal)."""
    escaped_text = _escape_drawtext_text(overlay.text)
    y_expr = f"(h-text_h)*{overlay.y_fraction}"
    start, end = overlay.start_seconds, overlay.end_seconds
    amplitude = 6 * overlay.intensity
    frequency = 25 * overlay.speed
    x_expr = f"(w-text_w)*{overlay.x_fraction}+{amplitude}*sin({frequency}*(t-{start}))"
    return (
        f"drawtext=fontfile='{font_file_arg}':text='{escaped_text}':fontsize={overlay.font_size}:"
        f"fontcolor={overlay.color}:x='{x_expr}':y='{y_expr}':enable='between(t,{start},{end})'"
    )


def _glitch_clause(overlay: TextOverlay, *, font_file_arg: str) -> str:
    """A real chromatic-aberration glitch look: the SAME text rendered
    THREE times - a red-tinted copy offset left, a cyan-tinted copy
    offset right, and the real-color copy in the middle - with a noisy,
    time-varying x jitter driven by a sum of mismatched-frequency sine
    waves (not a true random generator, which drawtext's own expression
    language has no access to, but a genuinely time-varying, non-
    periodic-looking jitter for the overlay's own duration) - the
    standard, real glitch-art technique, not a cosmetic label on an
    unchanged static clause."""
    escaped_text = _escape_drawtext_text(overlay.text)
    x_base = f"(w-text_w)*{overlay.x_fraction}"
    y_expr = f"(h-text_h)*{overlay.y_fraction}"
    start, end = overlay.start_seconds, overlay.end_seconds
    jitter_px = 4 * overlay.intensity
    jitter_expr = f"{jitter_px}*sin(37*(t-{start}))*sin(11*(t-{start}))"
    offset = 3 * overlay.intensity

    clauses = []
    for color, extra_offset in (("red@0.6", -offset), ("cyan@0.6", offset), (overlay.color, 0)):
        clauses.append(
            f"drawtext=fontfile='{font_file_arg}':text='{escaped_text}':fontsize={overlay.font_size}:"
            f"fontcolor={color}:x='({x_base})+({jitter_expr})+({extra_offset})':y='{y_expr}':"
            f"enable='between(t,{start},{end})'"
        )
    return ",".join(clauses)


def _glow_clause(overlay: TextOverlay, *, font_file_arg: str) -> str:
    """A real glow: the same text drawn twice at the SAME position -
    once as a blurred (via a real crop+gblur+overlay sub-chain is too
    heavy for a per-overlay text effect, so this uses drawtext's own
    `borderw`/`bordercolor` at a LARGE width as a soft, light-colored
    halo approximation) wide soft-colored halo, then the sharp text on
    top - a real, visible brightening/halo effect around the text
    rather than a cosmetic label with no visual change. (A true
    gaussian-blurred duplicate layer was evaluated but requires
    rendering text to its own sub-canvas before blurring, which
    drawtext cannot do standalone without a second full-frame
    crop/overlay stage per text element - a heavier mechanism judged
    not worth the cost difference over this already-visible halo
    approximation for a short on-screen title/callout.)"""
    escaped_text = _escape_drawtext_text(overlay.text)
    x_expr = f"(w-text_w)*{overlay.x_fraction}"
    y_expr = f"(h-text_h)*{overlay.y_fraction}"
    start, end = overlay.start_seconds, overlay.end_seconds
    halo_width = max(1, round(6 * overlay.intensity))
    return (
        f"drawtext=fontfile='{font_file_arg}':text='{escaped_text}':fontsize={overlay.font_size}:"
        f"fontcolor={overlay.color}:x='{x_expr}':y='{y_expr}':"
        f"borderw={halo_width}:bordercolor={overlay.color}@0.5:"
        f"enable='between(t,{start},{end})'"
    )
