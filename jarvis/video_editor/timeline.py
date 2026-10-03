"""The Video Editor's own general timeline data model - an ORDERED list
of trimmed clips and timed stills, each possibly from a DIFFERENT
source file, with per-item retime and an optional transition into the
next item.

Deliberately NOT jarvis.video_studio.reel.ReelEditPlan/PlannedClip, and
never a modification of that module - see jarvis.video_editor's own
package docstring for why that existing dataclass models a genuinely
different problem (a single-source, highlight-driven reel-assembly
plan, not an arbitrary multi-clip/multi-file user-directed timeline).
jarvis.video_studio.reel.py is untouched by this module's existence.

Deliberately a FLAT, single-track ordered list, not a multi-track
model - overlays (captions, stickers, effects - Stage 4+) are designed
to be composited as a separate pass ON TOP OF this timeline's own
assembled output, not as additional tracks inside this data model
itself. This keeps Stage 1's export path a single linear concat while
leaving room to layer compositing on later without ever changing this
file.

Every dataclass here is a plain, frozen, JSON-serializable value object
(only str/float/int/bool/None/nested-dataclass fields) - no Path
objects, no I/O, no ffmpeg calls anywhere in this module. This is what
makes it trivially unit-testable without ffmpeg at all (only
jarvis.video_editor.multisource_export, which actually RUNS ffmpeg
against a Timeline's own items, needs the ffmpeg_available() skip-guard
other tests in this codebase already use), and what makes
jarvis.video_editor.storage.save_project()'s own JSON serialization of
a Timeline straightforward (dataclasses.asdict() + json.dumps() is
enough - no custom encoder needed)."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import Literal

from jarvis.video_editor.effects import EffectSpec

TransitionKind = Literal["cut", "fade", "dissolve", "slide_left", "slide_right"]
TRANSITION_KIND_CHOICES: tuple[TransitionKind, ...] = ("cut", "fade", "dissolve", "slide_left", "slide_right")

AspectRatio = Literal["9:16", "1:1", "16:9", "4:5"]
ASPECT_RATIO_CHOICES: tuple[AspectRatio, ...] = ("9:16", "1:1", "16:9", "4:5")
# "4:5" (Instagram's own standard portrait FEED-post crop, distinct
# from "9:16" Reels/Stories) added for Stage 6 of the "professional
# Reels editor" plan - the user's own plan named 4:5 explicitly as a
# previously-missing export format (jarvis.video_editor.multisource
# _export.resolve_export_format() is the other half of this addition,
# since THIS tuple alone only controls what TimelinePanel's own aspect
# dropdown offers - the actual pixel dimensions per resolution tier
# live in that sibling module's own _RESOLUTION_TIERS).

_MIN_SPEED_FACTOR = 0.25
_MAX_SPEED_FACTOR = 4.0
MAX_CLIP_VOLUME = 2.0
# ffmpeg's own atempo filter only accepts [0.5, 2.0] per stage (chained
# for a wider range, see multisource_export.py's own atempo-chaining
# docstring) - this module's own sane outer bound is wider than that to
# allow a human-meaningful "quarter speed"/"4x speed" choice in the UI,
# with the chaining-to-reach-that-range handled entirely in the export
# layer, never here (this file has no ffmpeg knowledge at all).


@dataclass(frozen=True)
class TransitionSpec:
    """A transition applied BETWEEN this timeline item and the next one
    - lives on the item it follows (`transition_out`), never as a
    separate list element of its own, so a timeline's own item count
    always matches what a person sees as "my clips," with transitions
    as a per-boundary property of each clip rather than an item needing
    its own trim/retime fields that would never apply to it.

    `kind="cut"` (the default - a plain hard cut, mirroring Static
    Reel's own existing hard-cut-only behavior elsewhere in this
    codebase) means `duration_seconds` is ignored/must be 0.0; `"fade"`/
    `"dissolve"` require `duration_seconds > 0`, checked by this
    item's own `validate()`-time caller (Timeline.validate() below),
    never enforced by this frozen dataclass's own constructor, so a
    momentarily-invalid value during UI editing is not model-breaking."""

    kind: TransitionKind = "cut"
    duration_seconds: float = 0.0


@dataclass(frozen=True)
class TimelineClip:
    """One trimmed window of a video-type MediaItem, placed at some
    position in the timeline's own `items` order. `media_item_id` is a
    plain string foreign key into whatever dict[str, MediaItem] the
    caller (jarvis.video_editor.storage/.multisource_export) is working
    with - this module never holds a MediaItem reference directly, so a
    Timeline stays a pure, serializable value independent of where its
    media actually lives on disk.

    `source_in_seconds`/`source_out_seconds` are positions in the
    ORIGINAL source file's own timeline (never the assembled timeline's
    position - that's implicit from this item's own place in
    `Timeline.items` plus every earlier item's own duration), mirroring
    jarvis.video_studio.reel.PlannedClip's own
    source_start_seconds/source_end_seconds naming exactly, for anyone
    already familiar with that sibling module's own convention.

    `speed_factor` (1.0 = unchanged) retimes this clip - see this
    module's own _MIN_SPEED_FACTOR/_MAX_SPEED_FACTOR for the sane outer
    bound; the actual ffmpeg `setpts`/`atempo` mechanics live entirely
    in jarvis.video_editor.multisource_export, never here."""

    clip_id: str
    media_item_id: str
    source_in_seconds: float
    source_out_seconds: float
    speed_factor: float = 1.0
    transition_out: TransitionSpec = field(default_factory=TransitionSpec)
    effect: EffectSpec = field(default_factory=EffectSpec)
    volume: float = 1.0
    """The clip's own sound: 1.0 unchanged, 0.0 muted, up to
    MAX_CLIP_VOLUME (2.0) louder."""
    audio_fade_in_seconds: float = 0.0
    audio_fade_out_seconds: float = 0.0

    @property
    def source_duration_seconds(self) -> float:
        """The RAW duration trimmed from the source, before retiming -
        the actual on-screen duration after `speed_factor` is applied is
        `source_duration_seconds / speed_factor` (a 2x speed_factor
        halves on-screen duration), computed by the caller when it needs
        the assembled-timeline duration (see Timeline.total_duration_seconds()
        below), not duplicated as a second property here."""
        return max(0.0, self.source_out_seconds - self.source_in_seconds)

    @property
    def on_screen_duration_seconds(self) -> float:
        return self.source_duration_seconds / self.speed_factor if self.speed_factor else 0.0


@dataclass(frozen=True)
class TimelineStill:
    """One photo-type MediaItem, shown for a fixed duration - the
    timeline's own equivalent of jarvis.reel_generator.export's
    established "-loop 1 -t <duration> -i <image>" still-to-video-
    segment pattern (see jarvis.video_editor.multisource_export's own
    docstring for where that precedent is actually applied). A still has
    no in/out trim (the whole photo is always shown) and no speed_factor
    (retiming a still has no meaning - its "speed" IS its display
    duration, which is itself already the adjustable value)."""

    clip_id: str
    media_item_id: str
    display_duration_seconds: float
    transition_out: TransitionSpec = field(default_factory=TransitionSpec)
    effect: EffectSpec = field(default_factory=EffectSpec)

    @property
    def on_screen_duration_seconds(self) -> float:
        return max(0.0, self.display_duration_seconds)


TimelineItem = TimelineClip | TimelineStill


@dataclass(frozen=True)
class Timeline:
    """A complete, ordered edit - `items` IS the edit; there is no
    separate "selected clips" concept layered on top. Every item's own
    assembled-timeline START time is implicit: the sum of every earlier
    item's own `on_screen_duration_seconds` (plus any `fade`/`dissolve`
    transition's own time overlap - see multisource_export.py's own
    xfade/acrossfade offset computation for the one place that overlap
    math actually happens; this dataclass itself stays agnostic to it,
    since a hard-cut-only timeline's assembled duration is simply the
    sum of every item's own on-screen duration)."""

    items: tuple[TimelineItem, ...] = ()
    aspect_ratio: AspectRatio = "9:16"

    def total_duration_seconds(self) -> float:
        """The assembled timeline's own real total duration for
        hard-cut (`kind="cut"`) boundaries - the sum of every item's own
        on-screen duration. For a timeline that also uses `fade`/
        `dissolve` transitions, the TRUE total is shorter by each such
        transition's own `duration_seconds` (the two adjacent clips
        overlap during the crossfade rather than playing back-to-back) -
        this method intentionally reports the pre-overlap sum (a useful,
        simple upper bound for UI display/validation) and leaves the
        exact post-overlap total to multisource_export.py's own real,
        ffmpeg-probed ExportResult.duration_seconds, which is always the
        measured ground truth, never a value computed here and trusted
        blindly (matching this codebase's established "probe the real
        file, don't trust a computed estimate" convention - see
        jarvis.reel_generator.video_generation's own docstring for the
        same principle applied to a provider's self-reported duration)."""
        return sum(item.on_screen_duration_seconds for item in self.items)

    def validate(self) -> list[str]:
        """Returns a list of human-readable problems, or an empty list
        if the timeline is well-formed - NEVER raises, matching this
        codebase's established "describe problems, don't throw" GUI-
        facing convention (e.g. jarvis.reel_generator.quality_control's
        own QualityIssue list). Checked by the GUI before enabling the
        Export button, and by multisource_export.export_timeline()
        itself as a defensive first step before building any ffmpeg
        command."""
        problems: list[str] = []
        if not self.items:
            problems.append("The timeline has no clips or photos yet.")
        if self.aspect_ratio not in ASPECT_RATIO_CHOICES:
            problems.append(f"Unknown aspect ratio: {self.aspect_ratio!r}.")

        for index, item in enumerate(self.items, start=1):
            if isinstance(item, TimelineClip):
                if item.source_out_seconds <= item.source_in_seconds:
                    problems.append(f"Clip {index}: trim end must be after trim start.")
                if not (0.0 <= item.volume <= MAX_CLIP_VOLUME):
                    problems.append(f"Clip {index}: volume {item.volume}x is outside the supported 0x-{MAX_CLIP_VOLUME:g}x range.")
                if item.audio_fade_in_seconds < 0.0 or item.audio_fade_out_seconds < 0.0:
                    problems.append(f"Clip {index}: sound fade durations cannot be negative.")
                if not (_MIN_SPEED_FACTOR <= item.speed_factor <= _MAX_SPEED_FACTOR):
                    problems.append(
                        f"Clip {index}: speed {item.speed_factor}x is outside the supported "
                        f"{_MIN_SPEED_FACTOR}x-{_MAX_SPEED_FACTOR}x range."
                    )
            elif isinstance(item, TimelineStill):
                if item.display_duration_seconds <= 0:
                    problems.append(f"Photo {index}: display duration must be greater than zero.")
            else:
                problems.append(f"Item {index} is neither a clip nor a photo.")

            problems.extend(f"Item {index}: {p}" for p in item.effect.validate())

            transition = item.transition_out
            if transition.kind == "cut" and transition.duration_seconds != 0.0:
                problems.append(f"Item {index}: a 'cut' transition cannot have a nonzero duration.")
            if transition.kind in ("fade", "dissolve", "slide_left", "slide_right") and transition.duration_seconds <= 0.0:
                problems.append(f"Item {index}: a '{transition.kind}' transition needs a duration greater than zero.")
            if index == len(self.items) and transition.kind != "cut":
                problems.append("The last item cannot have a transition into a next clip that doesn't exist.")

        return problems


def apply_effect_to_every_item(timeline: Timeline, effect: EffectSpec) -> Timeline:
    """Returns a NEW Timeline with every item's own `effect` replaced
    by `effect` - clip/still identity, order, in/out trim, speed, and
    transitions are NEVER touched. The one real mechanism both
    jarvis.video_editor.reel_templates.apply_template() (a curated
    ReelTemplate's own default_effect) and
    jarvis.video_editor.ai_assistant's own proposal-apply path (an
    AI-suggested EffectSpec) build on, so a hand-picked template and an
    AI suggestion are applied through the exact same code, never two
    competing implementations of "replace every item's effect"."""
    new_items = tuple(dataclasses.replace(item, effect=effect) for item in timeline.items)
    return dataclasses.replace(timeline, items=new_items)
