"""Multi-source ffmpeg export for the Professional Video Editor's own
Timeline - renders an arbitrary ordered sequence of trimmed clips and
timed stills, possibly drawn from MANY DIFFERENT source files, into one
MP4. This is genuinely new work: jarvis.video_studio.export.export_reel()
builds its own filtergraph around exactly ONE `-i` input (every trim
node reads `[0:v]`/`[0:a]`) and cannot merge clips from different files
- that module is untouched by this one's existence (see
jarvis.video_editor's own package docstring for the full isolation
reasoning).

Same "plain ffmpeg subprocess, no Python video-processing library"
convention as jarvis.video_studio.export/.ffmpeg_utils - no new
dependency, no moviepy/opencv. Same cwd-relative-path + forward-slash
workaround jarvis.video_studio.export._crop_scale_filter()'s own
docstring documents (a Windows drive-letter colon breaks FFmpeg's
filtergraph parser even when backslash-escaped) - replicated here
inline, since no shared, importable helper function for this exists in
either jarvis.video_studio.export.py or jarvis.reel_generator.export.py
(both modules repeat the same inline pattern at their own call sites
rather than exposing a shared utility) - this module does the same,
rather than wrongly assuming a reusable helper exists to import.

Design: N inputs (one `-i` per DISTINCT source file, deduped - the same
file can be trimmed into multiple TimelineClips via multiple filtergraph
nodes reading the same input index), each clip/still trimmed and
normalized (scale/pad/setsar/fps) to the target canvas BEFORE the
concat node, joined via ffmpeg's `concat` FILTER (not the concat
DEMUXER, which needs identical codecs/intermediate files on disk and is
fragile across mixed photo/video sources) - one ffmpeg process, one
pass, no intermediate files written."""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from jarvis.video_editor.effects import EffectError, build_segment_effect_filter
from jarvis.video_editor.media_import import MediaItem
from jarvis.video_editor.timeline import Timeline, TimelineClip, TimelineStill
from jarvis.video_studio.ffmpeg_utils import FFmpegError, probe_video

_EXPORT_TIMEOUT_SECONDS = 1800
# Longer than jarvis.video_studio.export's own 900s budget - a
# multi-source, multi-input filtergraph with normalization per segment
# is more work per second of output than a single-source trim+concat,
# and a 4K export (a later resolution tier this function's own
# target_width/target_height already support) is slower still.

_TARGET_FPS = 30
# A single, fixed output framerate for every segment - ffmpeg's concat
# filter does not implicitly resample; without a shared fps across every
# normalized segment, a mismatched-fps join can corrupt the assembled
# duration (the exact failure mode jarvis.reel_generator.export's own
# zoompan-vs-plain-scale fps mismatch bug - from an earlier stage of
# this codebase's own history - already demonstrated for a different
# but structurally identical reason).

_CANCEL_POLL_INTERVAL_SECONDS = 0.5


class MultiSourceExportError(Exception):
    """Raised for any export failure (ffmpeg missing, an empty/invalid
    timeline, a missing source file, subprocess failure/timeout) -
    always with a human-readable message. A LOCAL exception, never
    subclassing jarvis.video_studio.export.ExportError - see this
    module's own isolation rules in the package docstring."""


@dataclass(frozen=True)
class ExportFormat:
    """A target canvas - distinct from, but structurally identical to,
    jarvis.video_studio.export.ExportFormat (that dataclass is NOT
    reused directly, since this module also carries a resolution TIER
    - 720p/1080p/4K - that sibling dataclass has no concept of; its own
    EXPORT_FORMATS dict is fixed at one pixel size per aspect ratio)."""

    aspect_ratio: str  # one of jarvis.video_editor.timeline.ASPECT_RATIO_CHOICES
    width: int
    height: int
    label: str


_RESOLUTION_TIERS: dict[str, dict[str, tuple[int, int]]] = {
    "9:16": {"720p": (720, 1280), "1080p": (1080, 1920), "4k": (2160, 3840)},
    "1:1": {"720p": (720, 720), "1080p": (1080, 1080), "4k": (2160, 2160)},
    "16:9": {"720p": (1280, 720), "1080p": (1920, 1080), "4k": (3840, 2160)},
    # "4:5" (Instagram's own standard portrait FEED-post crop, e.g.
    # 1080x1350 at 1080p) - a real, previously-flagged-missing export
    # format, added for Stage 6 of the "professional Reels editor"
    # plan. Every width/height here is an exact 4:5 ratio (864x1080,
    # 1080x1350, 2160x2700), matching the same "exact ratio per tier"
    # precedent the other three aspect ratios above already establish.
    "4:5": {"720p": (864, 1080), "1080p": (1080, 1350), "4k": (2160, 2700)},
}
RESOLUTION_TIER_CHOICES = ("720p", "1080p", "4k")


def _require_ffmpeg() -> str:
    """Same local pattern jarvis.video_studio.export._require_ffmpeg()
    already uses (a private, un-exported helper in that sibling module
    too - not something this module imports, since it isn't a public
    symbol there either)."""
    path = shutil.which("ffmpeg")
    if path is None:
        raise MultiSourceExportError(
            "FFmpeg was not found on PATH. The Video Editor requires FFmpeg to be installed."
        )
    return path


def resolve_export_format(aspect_ratio: str, resolution_tier: str) -> ExportFormat:
    """Combines an aspect ratio (Timeline.aspect_ratio) and a resolution
    tier (720p/1080p/4k - requirement 8's own choice) into one concrete
    pixel-dimension ExportFormat. Raises MultiSourceExportError for an
    unrecognized combination - never guesses/defaults silently."""
    tiers = _RESOLUTION_TIERS.get(aspect_ratio)
    if tiers is None:
        raise MultiSourceExportError(f"Unknown aspect ratio '{aspect_ratio}'.")
    dimensions = tiers.get(resolution_tier)
    if dimensions is None:
        raise MultiSourceExportError(f"Unknown resolution tier '{resolution_tier}'.")
    width, height = dimensions
    return ExportFormat(aspect_ratio=aspect_ratio, width=width, height=height, label=f"{aspect_ratio} {resolution_tier}")


@dataclass(frozen=True)
class ExportResult:
    output_path: Path
    duration_seconds: float
    width: int
    height: int
    file_size_bytes: int


def _relative_or_absolute(path: Path, cwd: Path) -> str:
    """The exact cwd-relative-path + forward-slash workaround every
    ffmpeg-filtergraph-building module in this codebase already uses
    (jarvis.reel_generator.export/.video_studio.export) - a path placed
    INSIDE a filter expression (not a plain `-i` arg) must avoid a raw
    Windows drive-letter colon, which otherwise breaks ffmpeg's own
    filtergraph parser. `-i` arguments themselves don't need this (they
    aren't parsed as filtergraph syntax), but this function is used for
    both for consistency with the established precedent and because the
    cwd-relative form also keeps command lines shorter/more readable."""
    try:
        relative = path.resolve().relative_to(cwd.resolve())
        return str(relative).replace("\\", "/")
    except ValueError:
        return str(path.resolve()).replace("\\", "/")


def _scale_pad_filter(width: int, height: int, *, fps: int = _TARGET_FPS) -> str:
    # format=yuv420p is required here, not left to libx264's own default -
    # a still photo (PNG/lavfi source) or certain video sources decode
    # into a 4:4:4 or 10-bit chroma layout, and without an explicit
    # downconversion libx264 happily encodes that layout as-is, producing
    # a real, valid "High 4:4:4 Predictive"/yuv444p H.264 stream that
    # ffprobe (and VLC) read back successfully but Windows Media
    # Player's own built-in H.264 decoder outright refuses (it only
    # supports Baseline/Main/High profile at 8-bit 4:2:0) - surfaced to
    # the user as a bogus "unsupported encoding settings" / 0x80004005
    # error despite the exported file being completely intact. A real,
    # hand-hit bug found by probing an actual still-photo export's own
    # pix_fmt/profile after a user report of this exact WMP error.
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
        f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p,fps={fps}"
    )


def build_filtergraph(
    timeline: Timeline, media_items: dict[str, MediaItem], export_format: ExportFormat, *, cwd: Path,
) -> tuple[list[str], str, str, str]:
    """Returns (input_args, filter_complex_string, video_out_label,
    audio_out_label). `input_args` is the flat list of `-i <path>` (and
    `-loop 1 -t <duration> -i <path>` for a still) arguments to place on
    the ffmpeg command line, in order; `filter_complex_string` is the
    single `-filter_complex` value; the two label strings (without
    brackets) are what `-map "[<video_out_label>]"` should reference -
    always `"outv"`/`"outa"` today (see _apply_crossfades()'s own
    relabeling step), returned explicitly rather than hardcoded at the
    call site so a future filtergraph shape change here can't silently
    desync from export_timeline()'s own `-map` arguments.

    One `-i` per DISTINCT media_item_id referenced by the timeline
    (deduped via a dict keyed by media_item_id, preserving first-seen
    order) - the same source file trimmed into multiple TimelineClips
    still only appears once on the ffmpeg command line; multiple
    filtergraph nodes simply read the same input index with different
    trim windows."""
    if not timeline.items:
        raise MultiSourceExportError("This timeline has no clips or photos to export.")

    input_args: list[str] = []
    input_index_by_media_id: dict[str, int] = {}
    for item in timeline.items:
        if item.media_item_id in input_index_by_media_id:
            continue
        media = media_items.get(item.media_item_id)
        if media is None:
            raise MultiSourceExportError(f"Timeline references an unknown media item: {item.media_item_id}")
        if not media.stored_path.is_file():
            raise MultiSourceExportError(f"Media file is missing on disk: {media.stored_path}")

        path_arg = _relative_or_absolute(media.stored_path, cwd)
        input_index_by_media_id[item.media_item_id] = len(input_index_by_media_id)
        if isinstance(item, TimelineStill):
            # The exact precedent cited from jarvis.reel_generator
            # .export's own still-to-video-segment pattern ("-loop 1 -t
            # <duration> -i <image>") - a photo input is looped into a
            # video stream for the duration the FIRST TimelineStill
            # referencing it needs; if the SAME photo appears twice with
            # different display durations, this uses the first
            # occurrence's duration for the shared input (a real,
            # documented limitation - the common case of the same photo
            # used twice with the SAME duration is unaffected; a second,
            # differently-timed use of the same photo should re-import
            # it as a second MediaItem in practice, which the caller/UI
            # should encourage rather than this function silently
            # guessing).
            input_args += ["-loop", "1", "-t", str(item.display_duration_seconds), "-i", path_arg]
        else:
            input_args += ["-i", path_arg]

    trim_parts: list[str] = []
    concat_video_labels: list[str] = []
    concat_audio_labels: list[str] = []
    xfade_boundaries: list[tuple[int, str, float]] = []
    # (item_index, kind, duration_seconds) for each boundary using a
    # real crossfade - collected here, applied as a second pass below,
    # since xfade/acrossfade need to replace a plain concat join for
    # that ONE boundary rather than feeding the shared concat node.

    scale_pad = _scale_pad_filter(export_format.width, export_format.height)

    for n, item in enumerate(timeline.items):
        i = input_index_by_media_id[item.media_item_id]
        media = media_items[item.media_item_id]

        # Every segment is scaled/padded to the final canvas into an
        # intermediate [vraw{n}] label FIRST, regardless of whether it
        # has an effect - build_segment_effect_filter() (Ken Burns
        # zoom/pan, fade, color) then reads from that already-correctly-
        # sized frame as a SECOND stage, rather than duplicating
        # scale/pad logic inside effects.py itself. An item with no
        # effect at all skips straight to [v{n}] via a plain relabel
        # (see the `has_effect` branch below) - never pays for an extra
        # filter stage it didn't ask for.
        has_effect = item.effect.motion != "none" or item.effect.fade != "none" or (
            item.effect.brightness != 0.0 or item.effect.contrast != 1.0 or item.effect.saturation != 1.0
        )
        raw_label = f"vraw{n}" if has_effect else f"v{n}"

        if isinstance(item, TimelineClip):
            speed_suffix = f",setpts=PTS/{item.speed_factor}" if item.speed_factor != 1.0 else ""
            trim_parts.append(
                f"[{i}:v]trim=start={item.source_in_seconds}:end={item.source_out_seconds},"
                f"setpts=PTS-STARTPTS{speed_suffix},{scale_pad}[{raw_label}];"
            )
            if media.duration_seconds is not None and _has_audio_track(media):
                atempo = _atempo_chain(item.speed_factor)
                trim_parts.append(
                    f"[{i}:a]atrim=start={item.source_in_seconds}:end={item.source_out_seconds},"
                    f"asetpts=PTS-STARTPTS{atempo}[a{n}];"
                )
            else:
                trim_parts.append(
                    f"anullsrc=channel_layout=stereo:sample_rate=44100,"
                    f"atrim=duration={item.on_screen_duration_seconds}[a{n}];"
                )
        else:  # TimelineStill
            trim_parts.append(f"[{i}:v]{scale_pad}[{raw_label}];")
            trim_parts.append(
                f"anullsrc=channel_layout=stereo:sample_rate=44100,"
                f"atrim=duration={item.on_screen_duration_seconds}[a{n}];"
            )

        if has_effect:
            try:
                effect_clause = build_segment_effect_filter(
                    item.effect, width=export_format.width, height=export_format.height,
                    duration_seconds=item.on_screen_duration_seconds, fps=_TARGET_FPS,
                    video_label=f"[{raw_label}]", output_label=f"v{n}",
                )
            except EffectError as e:
                raise MultiSourceExportError(f"Item {n + 1}: {e}") from e
            trim_parts.append(f"{effect_clause};")

        concat_video_labels.append(f"[v{n}]")
        concat_audio_labels.append(f"[a{n}]")

        transition = item.transition_out
        if transition.kind != "cut" and n < len(timeline.items) - 1:
            xfade_boundaries.append((n, transition.kind, transition.duration_seconds))

    if xfade_boundaries:
        video_out, audio_out = _apply_crossfades(
            trim_parts, concat_video_labels, concat_audio_labels, xfade_boundaries, timeline,
        )
    else:
        n_items = len(timeline.items)
        concat_filter = (
            "".join(f"{v}{a}" for v, a in zip(concat_video_labels, concat_audio_labels))
            + f"concat=n={n_items}:v=1:a=1[outv][outa];"
        )
        trim_parts.append(concat_filter)
        video_out, audio_out = "outv", "outa"

    # Every trim_parts entry (per-segment trim/normalize clauses, plus
    # the final concat/crossfade stage) already ends with its own ';' -
    # a plain "".join() then one trailing-semicolon strip produces a
    # valid -filter_complex string, matching jarvis.video_studio.export's
    # own "".join(trim_parts) + final-stage convention exactly.
    full_filter = "".join(trim_parts).rstrip(";")
    return input_args, full_filter, video_out, audio_out


def _has_audio_track(media: MediaItem) -> bool:
    """True if this media item's own real, probed properties indicate
    an audio stream - a photo (duration_seconds is None) never has
    audio; a video's own has_audio isn't stored directly on MediaItem
    today (see jarvis.video_editor.media_import.MediaItem's own field
    list), so this re-probes once here rather than silently assuming
    audio always exists - matching this codebase's "measure the real
    file, don't guess" convention. A cheap, already-timeout-bounded call
    (probe_video()'s own _PROBE_TIMEOUT_SECONDS applies)."""
    if media.kind != "video":
        return False
    try:
        return probe_video(media.stored_path).has_audio
    except FFmpegError:
        return False


def _atempo_chain(speed_factor: float) -> str:
    """ffmpeg's `atempo` filter only accepts a [0.5, 2.0] factor per
    stage - chains multiple atempo stages to reach a wider factor (this
    module's own supported range is
    jarvis.video_editor.timeline._MIN_SPEED_FACTOR..MAX, 0.25x-4.0x).
    Returns "" for speed_factor == 1.0 (no-op, the common case)."""
    if speed_factor == 1.0:
        return ""
    stages: list[float] = []
    remaining = speed_factor
    while remaining > 2.0:
        stages.append(2.0)
        remaining /= 2.0
    while remaining < 0.5:
        stages.append(0.5)
        remaining /= 0.5
    stages.append(remaining)
    return "".join(f",atempo={stage}" for stage in stages)


_XFADE_TRANSITION_NAMES = {
    "fade": "fade",
    "dissolve": "dissolve",
    "slide_left": "slideleft",
    "slide_right": "slideright",
}
# Maps this package's own TransitionKind to ffmpeg xfade's own
# `transition=` name - a real, hand-hit bug this dict replaces: every
# non-"cut" kind (including "dissolve") previously hardcoded
# `transition=fade` regardless of which kind was actually requested, so
# "dissolve" silently produced an IDENTICAL visual result to "fade"
# (never its own real cross-dissolve). ffmpeg's own xfade filter
# natively supports "fade" and "dissolve" as genuinely distinct named
# transitions (dissolve mixes pixels with a randomized/non-uniform
# blend rather than a uniform cross-fade) plus "slideleft"/"slideright"
# for a real slide transition - confirmed via `ffmpeg -h filter=xfade`
# listing these as real, built-in transition names, not an invented
# mapping.


def _apply_crossfades(trim_parts, concat_video_labels, concat_audio_labels, xfade_boundaries, timeline) -> tuple[str, str]:
    """A real crossfade (xfade/acrossfade) REPLACES the plain concat
    join at exactly the boundaries that asked for one, chaining
    pairwise rather than feeding one shared concat node for those
    segments - ffmpeg's xfade/acrossfade filters are inherently
    two-input, pairwise operations (unlike concat, which accepts N
    inputs at once), so a timeline with one or more crossfade boundaries
    builds a left-to-right reduction: first two segments combine via
    xfade/acrossfade into one intermediate label, that intermediate
    combines with the third segment (via xfade/acrossfade if THAT
    boundary also wants one, or via concat otherwise), and so on."""
    video_acc = concat_video_labels[0]
    audio_acc = concat_audio_labels[0]
    on_screen_seconds_acc = timeline.items[0].on_screen_duration_seconds
    xfade_by_index = {index: (kind, duration) for index, kind, duration in xfade_boundaries}

    for n in range(1, len(timeline.items)):
        next_video = concat_video_labels[n]
        next_audio = concat_audio_labels[n]
        boundary = xfade_by_index.get(n - 1)
        out_video = f"[xv{n}]"
        out_audio = f"[xa{n}]"
        if boundary is not None:
            kind, duration = boundary
            offset = max(0.0, on_screen_seconds_acc - duration)
            xfade_name = _XFADE_TRANSITION_NAMES.get(kind, "fade")
            trim_parts.append(
                f"{video_acc}{next_video}xfade=transition={xfade_name}:duration={duration}:offset={offset}{out_video};"
            )
            trim_parts.append(f"{audio_acc}{next_audio}acrossfade=d={duration}{out_audio};")
            on_screen_seconds_acc = on_screen_seconds_acc + timeline.items[n].on_screen_duration_seconds - duration
        else:
            trim_parts.append(f"{video_acc}{next_video}concat=n=2:v=1:a=0{out_video};")
            trim_parts.append(f"{audio_acc}{next_audio}concat=n=2:v=0:a=1{out_audio};")
            on_screen_seconds_acc += timeline.items[n].on_screen_duration_seconds
        video_acc, audio_acc = out_video, out_audio

    # Relabel the final accumulator to the conventional [outv]/[outa]
    # names so the rest of this module (and its own caller) never needs
    # to know whether crossfades were used.
    trim_parts.append(f"{video_acc}null[outv];{audio_acc}anull[outa];")
    return "outv", "outa"


def extract_output_label(filter_clause: str) -> str:
    """Parses the trailing `[label]` off a filter clause string like
    `"[outv]drawtext=...[capv]"` - returns `"capv"`. Used so
    export_timeline() can `-map` the REAL output label a caller-supplied
    filter clause (e.g. jarvis.video_editor.captions
    .build_caption_filter()'s own return value) actually produces,
    without export_timeline() needing to know or assume what that
    module names its own output label (captions.py's own default is
    "capv", but this function works for any label, keeping the two
    modules genuinely decoupled rather than coupled via an assumed
    constant)."""
    label = filter_clause.rsplit("[", 1)[-1]
    return label.rstrip("]")


def _extract_leading_input_index(filter_clause: str) -> int | None:
    """Parses the leading `[<digits>:a]`/`[<digits>:v]` ffmpeg input
    reference off the START of a filter clause string like
    `"[1:a]atrim=...[music];..."` - returns `1`. Returns None if the
    clause doesn't start with a plain numeric input reference (e.g. an
    already-labeled stage reading from a named label like `[outa]`
    instead of a raw input index) - a best-effort check used only by
    export_timeline()'s own defensive audio_mix_filter collision check
    above, never relied on for anything export-correctness-critical."""
    import re

    match = re.match(r"^\[(\d+):[av]\]", filter_clause)
    return int(match.group(1)) if match else None


def export_timeline(
    timeline: Timeline, media_items: dict[str, MediaItem], *, export_format: ExportFormat, output_path: Path,
    progress_callback: Callable[[float], None] | None = None, cancel_event: threading.Event | None = None,
    caption_filter: str | None = None, audio_mix_filter: tuple[list[str], str, str] | None = None,
    text_overlay_filter: str | None = None, sticker_filters: list[tuple[list[str], str]] | None = None,
) -> ExportResult:
    """Renders `timeline` into `output_path` - ALWAYS a new file (the
    caller is responsible for pointing this at the project's own
    exports_dir, never at any source media path). Raises
    MultiSourceExportError on any failure (empty/invalid timeline,
    missing media, ffmpeg missing, subprocess failure/timeout).

    `progress_callback`, if given, is called repeatedly with a float
    0.0-100.0 as ffmpeg reports its own real encoding progress via
    `-progress pipe:1` (parsed line-by-line from the subprocess's own
    stdout) - never a fabricated/interpolated progress value. `cancel_event`,
    if given and set while export is running, terminates the ffmpeg
    subprocess (graceful `terminate()` first, `kill()` after a short
    grace period if it hasn't exited) and raises MultiSourceExportError
    with a message indicating cancellation, rather than returning a
    partial/corrupt ExportResult.

    `caption_filter` (Stage 4), if given, is a complete filter CLAUSE
    string already built by jarvis.video_editor.captions
    .build_caption_filter() - e.g. "[outv]drawtext=...[capv]" - reading
    FROM this function's own "outv" video label (build_filtergraph()'s
    own default) and producing a new label this function then `-map`s
    instead of the un-captioned one. This function never calls into
    jarvis.video_editor.captions itself (keeping the two modules
    decoupled - see jarvis.video_editor's own package docstring's
    isolation rules) - the caller (the GUI/orchestration layer) is
    responsible for generating real word timings and building this
    string BEFORE calling export_timeline(), using the SAME video_out
    label this function would otherwise map directly. Omitting this
    parameter (the default, None) produces the exact same export as
    before this parameter existed.

    `audio_mix_filter` (Stage 4's own music feature), if given, is a
    tuple of (extra_input_args, filter_clause, output_audio_label) -
    see jarvis.video_editor.audio_mixing.build_music_mix_filter()'s own
    docstring for how this is constructed. `extra_input_args` (e.g. a
    music file's own `-i <path>`) are appended to this function's own
    input_args BEFORE the filter_complex is assembled (so the music
    input's own index is `len(timeline's own distinct media inputs)`,
    a fact that function already accounts for via its own
    `audio_input_index` parameter); `filter_clause` reads FROM this
    function's own audio_out label and produces `output_audio_label`,
    which is `-map`ped instead of the timeline's own plain audio
    output. Omitting this parameter reproduces the exact previous
    export behavior.

    `text_overlay_filter` (free-typed text overlays, distinct from
    transcribed captions), if given, is a complete filter CLAUSE string
    built by jarvis.video_editor.text_overlay.build_text_overlay_filter() -
    same opaque, decoupled contract as `caption_filter` above. Applied
    AFTER `caption_filter` (if both are given) so a timeline can have
    both real transcribed captions AND free text overlays at once, each
    reading from whichever video label came before it - this function
    never assumes only one of the two is ever used.

    `sticker_filters` (animated stickers/GIF/emoji -
    jarvis.video_editor.stickers.build_sticker_filter()), if given, is a
    list of (extra_input_args, filter_clause) tuples - one per sticker,
    applied IN ORDER after text_overlay_filter, each sticker's own
    extra_input_args appended to input_args before that sticker's own
    filter_clause runs (so a sticker's own image occupies the NEXT input
    index after whatever came before it - the caller is responsible for
    choosing each sticker's own `input_index` to match this ordering,
    exactly as jarvis.video_editor.audio_mixing.build_music_mix_filter()'s
    own `timeline_audio_input_count` parameter already requires for its
    one music input). Every sticker's own filter_clause already bakes in
    which video label it reads from at build time - the caller builds
    each one's own `video_label` to match whatever the PREVIOUS stage
    (the raw timeline, captions, text overlays, or an earlier sticker)
    actually produced, mirroring the same chaining responsibility
    dashboard.py's own _start_export() already has for text_overlay_filter
    reading from "capv" vs "outv"."""
    ffmpeg = _require_ffmpeg()
    problems = timeline.validate()
    if problems:
        raise MultiSourceExportError("; ".join(problems))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cwd = output_path.parent

    input_args, full_filter, video_out, audio_out = build_filtergraph(timeline, media_items, export_format, cwd=cwd)

    filter_stages = [full_filter]
    if caption_filter is not None:
        filter_stages.append(caption_filter)
        video_out = extract_output_label(caption_filter)
    if text_overlay_filter is not None:
        filter_stages.append(text_overlay_filter)
        video_out = extract_output_label(text_overlay_filter)
    for extra_input_args, sticker_filter_clause in (sticker_filters or []):
        input_args = input_args + extra_input_args
        filter_stages.append(sticker_filter_clause)
        video_out = extract_output_label(sticker_filter_clause)
    if audio_mix_filter is not None:
        extra_input_args, audio_filter_clause, mixed_audio_label = audio_mix_filter
        # Real, hand-hit regression this check guards against: a caller
        # (jarvis.gui.views.video_editor.dashboard's own _start_export())
        # once computed audio_mix_filter's own input index without
        # accounting for sticker_filters' own already-claimed indices,
        # so the music input's own `[N:a]` reference collided with a
        # sticker's PNG input - ffmpeg then failed deep inside its own
        # filtergraph binding step with a cryptic "matches no streams" /
        # "output unconnected" error that gave no indication the real
        # cause was a caller-side index collision. This check reads the
        # real input index audio_filter_clause's own `[N:a]` reference
        # names (the first `[<digits>:a]` token in the clause, the same
        # convention build_music_mix_filter()'s own docstring documents
        # for how its filter_clause addresses its own music input) and
        # fails FAST, before ever invoking ffmpeg, with a message that
        # names the actual problem - the caller miscounted inputs -
        # rather than letting ffmpeg's own far more cryptic filtergraph
        # parser error be the only signal.
        referenced_index = _extract_leading_input_index(audio_filter_clause)
        # input_args is a flat argv list (e.g. ["-loop","1","-t","3","-i",path,...]) -
        # its own len() is NOT the input COUNT (a still contributes 6
        # list elements for ONE real `-i`, a plain clip contributes 2) -
        # a real bug in this check's own first draft, caught by the
        # existing audio_mixing test suite immediately. Count actual
        # "-i" occurrences instead.
        already_claimed = input_args.count("-i")
        if referenced_index is not None and referenced_index < already_claimed:
            raise MultiSourceExportError(
                f"Internal error building the export: the music track's own ffmpeg input "
                f"(index {referenced_index}) collides with an input already used by a sticker or other "
                f"overlay (there are already {already_claimed} other inputs). This indicates a bug in "
                f"how the export filter chain was assembled, not a problem with your project - "
                f"please report this."
            )
        input_args = input_args + extra_input_args
        filter_stages.append(audio_filter_clause)
        audio_out = mixed_audio_label
    combined_filter = ";".join(filter_stages)

    total_duration_seconds = timeline.total_duration_seconds()
    cmd = [
        ffmpeg, "-y", *input_args,
        "-filter_complex", combined_filter,
        "-map", f"[{video_out}]", "-map", f"[{audio_out}]",
        # -profile:v high -level 4.0 -pix_fmt yuv420p pin the MUXED
        # stream's own declared profile/level/chroma to values every
        # mainstream H.264 decoder (including Windows Media Player's
        # own built-in one) actually supports - belt-and-suspenders
        # alongside _scale_pad_filter()'s own format=yuv420p, in case a
        # later filter stage (captions/crossfade) ever reintroduces a
        # different pixel format before encoding.
        "-c:v", "libx264", "-profile:v", "high", "-level", "4.0", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-movflags", "+faststart",
        "-progress", "pipe:1", "-nostats",
        output_path.name,
    ]

    # stderr is redirected to a real temp FILE, never subprocess.PIPE -
    # this module reads stdout line-by-line (-progress pipe:1) WHILE
    # ffmpeg is still running, and ffmpeg can write a substantial amount
    # to stderr (its own normal per-frame/filter diagnostic output) in
    # the same window; two unread PIPE buffers can each fill up and
    # deadlock the subprocess (ffmpeg blocks writing to a full pipe,
    # this process blocks reading the other pipe, neither ever
    # proceeds) - a real, hand-hit bug during this module's own
    # development, fixed by giving stderr an unbounded sink that needs
    # no concurrent draining.
    import tempfile

    stderr_file = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")
    try:
        proc = subprocess.Popen(
            cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=stderr_file,
            text=True, bufsize=1,
        )
    except OSError as e:
        stderr_file.close()
        raise MultiSourceExportError(f"Couldn't start ffmpeg: {e}") from e

    cancelled = False
    try:
        cancelled = _pump_progress(
            proc, total_duration_seconds=total_duration_seconds,
            progress_callback=progress_callback, cancel_event=cancel_event,
        )
        try:
            proc.wait(timeout=_EXPORT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            if not cancelled:
                raise MultiSourceExportError(f"Export timed out for {output_path.name}.") from None
    finally:
        stderr_file.seek(0)
        stderr_tail = stderr_file.read().strip()[-400:]
        stderr_file.close()

    if cancelled:
        # A terminated ffmpeg process commonly still leaves a partial,
        # unplayable (or misleadingly short/corrupt) MP4 behind - never
        # left in place for a caller to mistake for a real result;
        # cleaned up unconditionally before raising.
        output_path.unlink(missing_ok=True)
        raise MultiSourceExportError("Export was cancelled.")
    if proc.returncode != 0 or not output_path.is_file():
        raise MultiSourceExportError(f"Export failed. ffmpeg said: {stderr_tail}")

    _verify_playable_encoding(output_path, ffmpeg_path=ffmpeg)

    probe = probe_video(output_path)
    return ExportResult(
        output_path=output_path, duration_seconds=probe.duration_seconds,
        width=probe.width or export_format.width, height=probe.height or export_format.height,
        file_size_bytes=probe.file_size_bytes,
    )


_WMP_COMPATIBLE_PIX_FMTS = frozenset({"yuv420p", "yuvj420p"})
# Windows Media Player's own built-in H.264 decoder only decodes 8-bit
# 4:2:0 chroma - yuv444p/yuv422p (and 10-bit variants like yuv420p10le)
# all probe and play fine in ffprobe/VLC but WMP rejects them outright
# with a generic "unsupported encoding settings" / 0x80004005 error,
# even though the file is a completely valid, intact MP4. yuvj420p (the
# JPEG full-swing color-range variant libx264 sometimes chooses when a
# JPEG-sourced still's own color range metadata says "full" rather than
# "limited" - a real case this check's own test suite hit) is STILL
# 8-bit 4:2:0, just a different range tag, and is equally WMP-playable -
# confirmed by hand, not assumed. This set is intentionally narrow (not
# "every pix_fmt libx264 can ever produce") since yuv420p/yuvj420p are
# the two this module's own -pix_fmt yuv420p encoder flag (and
# _scale_pad_filter()'s own format=yuv420p) are meant to guarantee -
# this check exists to catch a REGRESSION of that guarantee, not to
# classify every possible format.


def _verify_playable_encoding(output_path: Path, *, ffmpeg_path: str) -> None:
    """Real post-export verification (not just "did ffmpeg exit 0 and
    did the file get created") - reads the ACTUAL encoded video
    stream's own pix_fmt/profile back via ffprobe and raises
    MultiSourceExportError if it isn't something mainstream decoders
    (Windows Media Player's own built-in H.264 decoder, specifically)
    can actually play. Without this, a file that is structurally valid
    but encoded in an incompatible chroma/profile (see this module's
    own _scale_pad_filter()/`-pix_fmt yuv420p` comments for the real,
    user-reported bug this guards against) would be reported to the
    caller as a successful export, only to fail silently later when
    the person actually tries to open it."""
    ffprobe_path = shutil.which("ffprobe")
    if ffprobe_path is None:
        # ffprobe is the sibling binary installed alongside ffmpeg in
        # every supported setup (see jarvis.video_studio.ffmpeg_utils's
        # own ffmpeg_available() precedent, which always checks both) -
        # if it's genuinely missing, this check simply can't run; the
        # export itself already succeeded, so this doesn't fail it.
        return
    try:
        result = subprocess.run(
            [
                ffprobe_path, "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=pix_fmt,profile", "-print_format", "json",
                str(output_path),
            ],
            capture_output=True, text=True, timeout=30, check=False,
        )
    except subprocess.TimeoutExpired:
        return
    if result.returncode != 0 or not result.stdout.strip():
        return
    try:
        streams = json.loads(result.stdout).get("streams", [])
    except json.JSONDecodeError:
        return
    if not streams:
        return
    # JSON output, not -of csv - ffprobe's own csv writer orders fields
    # by its internal struct layout, NOT by the -show_entries list order
    # given on the command line (confirmed by hand: requesting
    # "pix_fmt,profile" still printed "High,yuv420p" - profile first) -
    # a real, hand-hit bug while building this exact check. JSON keys
    # are unambiguous regardless of field ordering.
    pix_fmt = str(streams[0].get("pix_fmt") or "").strip()
    profile = str(streams[0].get("profile") or "").strip()
    if pix_fmt and pix_fmt not in _WMP_COMPATIBLE_PIX_FMTS:
        raise MultiSourceExportError(
            f"Export produced an incompatible video stream (pix_fmt={pix_fmt or 'unknown'}, "
            f"profile={profile or 'unknown'}) that common players like Windows Media Player "
            f"cannot decode, even though the file itself is intact. This indicates a real bug "
            f"in the export encoder settings, not a corrupted file."
        )


def _pump_progress(
    proc: subprocess.Popen, *, total_duration_seconds: float,
    progress_callback: Callable[[float], None] | None, cancel_event: threading.Event | None,
) -> bool:
    """Reads ffmpeg's own `-progress pipe:1` key=value lines from
    stdout, calling `progress_callback(percent)` as real `out_time_ms`
    values arrive, and checking `cancel_event` between reads. Returns
    True if cancellation was requested and the subprocess was
    terminated, False if ffmpeg's own progress stream ended normally
    (reaching `progress=end`)."""
    if proc.stdout is None:
        return False
    for line in proc.stdout:
        if cancel_event is not None and cancel_event.is_set():
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
            return True

        line = line.strip()
        if line.startswith("out_time_ms=") and progress_callback is not None and total_duration_seconds > 0:
            try:
                out_time_seconds = int(line.split("=", 1)[1]) / 1_000_000
            except (ValueError, IndexError):
                continue
            percent = min(100.0, max(0.0, (out_time_seconds / total_duration_seconds) * 100.0))
            progress_callback(percent)
        elif line == "progress=end" and progress_callback is not None:
            progress_callback(100.0)
    return False
