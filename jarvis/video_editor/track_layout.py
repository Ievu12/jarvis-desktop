"""The multi-track timeline's model: which bars sit on which track
(video, effects, clip sound, music, captions, text, stickers) and the edits a
person can make by dragging them - move, trim either edge, reorder,
split, duplicate, delete. Pure functions over EditorState, no GUI, so
every drag the track timeline supports is unit-testable on its own.

Times here are ASSEMBLED-timeline seconds (what the preview shows and
the export produces, transitions overlapping), the same clock the text,
sticker and caption start/end times already use."""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from jarvis.video_editor.editor_state import EditorState
from jarvis.video_editor.effects import LOOK_LABELS, EffectSpec
from jarvis.video_editor.media_import import MediaItem
from jarvis.video_editor.playback import assembled_duration, timeline_segments
from jarvis.video_editor.timeline import MAX_CLIP_VOLUME, TimelineClip, TimelineItem, TimelineStill, TransitionSpec

TRACKS: tuple[str, ...] = ("video", "effects", "sound", "audio", "captions", "text", "stickers")
TRACK_LABELS: dict[str, str] = {
    "video": "🎬 Vaizdas",
    "effects": "🎨 Efektai",
    "sound": "🔊 Klipų garsas",
    "audio": "🎵 Muzika",
    "captions": "💬 Subtitrai",
    "text": "🔤 Tekstas",
    "stickers": "✨ Lipdukai, GIF",
}
MIN_DURATION_SECONDS = 0.1

_OVERLAY_FIELDS = {"text": "text_overlays", "stickers": "stickers", "captions": "caption_lines"}


@dataclass(frozen=True)
class TrackBar:
    track: str
    index: int
    start: float
    end: float
    label: str
    can_move: bool = True
    can_trim_start: bool = True
    can_trim_end: bool = True


# --- reading ------------------------------------------------------------------------------------


def total_duration(state: EditorState, media_items: dict[str, MediaItem]) -> float:
    return assembled_duration(timeline_segments(state.timeline, media_items))


def effect_label(effect: EffectSpec) -> str | None:
    """A short name for a clip's effect, or None when it has none."""
    parts: list[str] = []
    if effect.motion != "none":
        parts.append({"zoom_in": "Priartinimas", "zoom_out": "Nutolinimas"}.get(effect.motion, effect.motion))
    if effect.fade != "none":
        parts.append("Išblukimas")
    if effect.brightness != 0.0 or effect.contrast != 1.0 or effect.saturation != 1.0:
        parts.append("Spalvos")
    if effect.look != "none" and effect.look_intensity > 0.0:
        parts.insert(0, f"{LOOK_LABELS[effect.look]} {round(effect.look_intensity * 100)}%")
    return " + ".join(parts) if parts else None


def sound_label(clip: TimelineClip) -> str:
    """The clip-sound lane's text: volume and fades at a glance."""
    if clip.volume <= 0.0:
        return "🔇 Nutildyta"
    label = f"🔊 {round(clip.volume * 100)}%"
    if clip.audio_fade_in_seconds > 0 or clip.audio_fade_out_seconds > 0:
        label += " ◢◣"
    return label


def set_clip_sound(
    state: EditorState, index: int | None, *, volume: float | None = None,
    fade_in: float | None = None, fade_out: float | None = None,
) -> EditorState:
    """Volume/fades of one video clip's own sound, or of every clip's
    when `index` is None (photos have no sound and are left alone)."""
    items = list(state.timeline.items)
    for n, item in enumerate(items):
        if (index is not None and n != index) or not isinstance(item, TimelineClip):
            continue
        changes = {}
        if volume is not None:
            changes["volume"] = round(max(0.0, min(MAX_CLIP_VOLUME, volume)), 2)
        if fade_in is not None:
            changes["audio_fade_in_seconds"] = round(max(0.0, fade_in), 2)
        if fade_out is not None:
            changes["audio_fade_out_seconds"] = round(max(0.0, fade_out), 2)
        items[n] = dataclasses.replace(item, **changes)
    return _with_items(state, items)


def build_track_bars(state: EditorState, media_items: dict[str, MediaItem]) -> dict[str, list[TrackBar]]:
    bars: dict[str, list[TrackBar]] = {track: [] for track in TRACKS}
    for segment in timeline_segments(state.timeline, media_items):
        name = segment.media.original_filename
        bars["video"].append(TrackBar("video", segment.index, segment.start_seconds, segment.end_seconds, name))
        if isinstance(segment.item, TimelineClip):
            bars["sound"].append(TrackBar(
                "sound", segment.index, segment.start_seconds, segment.end_seconds, sound_label(segment.item),
                can_move=False, can_trim_start=False, can_trim_end=False,
            ))
        label = effect_label(segment.item.effect)
        if label is not None:
            bars["effects"].append(TrackBar(
                "effects", segment.index, segment.start_seconds, segment.end_seconds, label,
                can_move=False, can_trim_start=False, can_trim_end=False,
            ))

    total = total_duration(state, media_items)
    if state.music_track is not None:
        music = state.music_track
        end = total
        if music.trim_end_seconds is not None:
            end = min(total, max(0.0, music.trim_end_seconds - music.trim_start_seconds))
        bars["audio"].append(TrackBar(
            "audio", 0, 0.0, end, music.source_path.name, can_move=False, can_trim_start=False,
        ))

    if state.caption_lines:
        for n, line in enumerate(state.caption_lines):
            bars["captions"].append(TrackBar("captions", n, line.start_seconds, line.end_seconds, line.text))
    elif state.caption_style is not None:
        bars["captions"].append(TrackBar(
            "captions", 0, 0.0, total, "Subtitrai sukuriami eksportuojant",
            can_move=False, can_trim_start=False, can_trim_end=False,
        ))

    for n, overlay in enumerate(state.text_overlays):
        bars["text"].append(TrackBar("text", n, overlay.start_seconds, overlay.end_seconds, overlay.text))
    for n, sticker in enumerate(state.stickers):
        name = sticker.custom_path.name if sticker.custom_path is not None else sticker.shape
        bars["stickers"].append(TrackBar("stickers", n, sticker.start_seconds, sticker.end_seconds, name))
    return bars


def snap(t: float, candidates: list[float], tolerance: float) -> float:
    """`t` moved onto the nearest candidate within `tolerance`."""
    best, best_distance = t, tolerance
    for candidate in candidates:
        distance = abs(candidate - t)
        if distance <= best_distance:
            best, best_distance = candidate, distance
    return best


def drop_index(state: EditorState, media_items: dict[str, MediaItem], t: float, *, moving: int) -> int:
    """Where a video item dragged so its center is at time `t` lands."""
    segments = [s for s in timeline_segments(state.timeline, media_items) if s.index != moving]
    target = 0
    for segment in segments:
        if t > (segment.start_seconds + segment.end_seconds) / 2:
            target += 1
    return target


# --- editing overlays (text, stickers, caption lines) --------------------------------------------


def _overlays(state: EditorState, track: str) -> tuple:
    return tuple(getattr(state, _OVERLAY_FIELDS[track]) or ())


def _with_overlays(state: EditorState, track: str, items: tuple) -> EditorState:
    return dataclasses.replace(state, **{_OVERLAY_FIELDS[track]: items})


def _replace_at(items: tuple, index: int, new) -> tuple:
    return items[:index] + (new,) + items[index + 1:]


def move_overlay(state: EditorState, track: str, index: int, new_start: float, *, total: float) -> EditorState:
    items = _overlays(state, track)
    item = items[index]
    length = item.end_seconds - item.start_seconds
    start = max(0.0, min(new_start, max(0.0, total - length)))
    moved = dataclasses.replace(item, start_seconds=round(start, 2), end_seconds=round(start + length, 2))
    return _with_overlays(state, track, _replace_at(items, index, moved))


def trim_overlay(
    state: EditorState, track: str, index: int, *, start: float | None = None, end: float | None = None,
    total: float,
) -> EditorState:
    items = _overlays(state, track)
    item = items[index]
    new_start, new_end = item.start_seconds, item.end_seconds
    if start is not None:
        new_start = max(0.0, min(start, new_end - MIN_DURATION_SECONDS))
    if end is not None:
        new_end = min(max(total, new_start + MIN_DURATION_SECONDS), max(end, new_start + MIN_DURATION_SECONDS))
    trimmed = dataclasses.replace(item, start_seconds=round(new_start, 2), end_seconds=round(new_end, 2))
    return _with_overlays(state, track, _replace_at(items, index, trimmed))


def set_music_length(state: EditorState, length: float) -> EditorState:
    music = state.music_track
    if music is None:
        return state
    length = max(MIN_DURATION_SECONDS, length)
    return dataclasses.replace(
        state, music_track=dataclasses.replace(music, trim_end_seconds=round(music.trim_start_seconds + length, 2)),
    )


# --- editing video items ------------------------------------------------------------------------


def _with_items(state: EditorState, items: list[TimelineItem]) -> EditorState:
    if items and items[-1].transition_out.kind != "cut":
        # The last item has nothing to transition into.
        items[-1] = dataclasses.replace(items[-1], transition_out=TransitionSpec())
    return dataclasses.replace(state, timeline=dataclasses.replace(state.timeline, items=tuple(items)))


def set_item_duration(
    state: EditorState, index: int, duration: float, media_items: dict[str, MediaItem],
) -> EditorState:
    """Drags a video item's RIGHT edge: a photo shows longer/shorter, a
    clip plays more/less of its source (never past the source's end)."""
    items = list(state.timeline.items)
    item = items[index]
    duration = max(MIN_DURATION_SECONDS, duration)
    if isinstance(item, TimelineStill):
        items[index] = dataclasses.replace(item, display_duration_seconds=round(duration, 2))
    else:
        media = media_items.get(item.media_item_id)
        source_end = media.duration_seconds if media is not None and media.duration_seconds else None
        new_out = item.source_in_seconds + duration * item.speed_factor
        if source_end is not None:
            new_out = min(new_out, source_end)
        items[index] = dataclasses.replace(item, source_out_seconds=round(new_out, 3))
    return _with_items(state, items)


def trim_item_start(state: EditorState, index: int, delta: float) -> EditorState:
    """Drags a video item's LEFT edge by `delta` timeline seconds
    (positive = later, i.e. shorter): a clip starts later in its source,
    a photo simply gets shorter."""
    items = list(state.timeline.items)
    item = items[index]
    if isinstance(item, TimelineStill):
        new_duration = max(MIN_DURATION_SECONDS, item.display_duration_seconds - delta)
        items[index] = dataclasses.replace(item, display_duration_seconds=round(new_duration, 2))
    else:
        latest_in = item.source_out_seconds - MIN_DURATION_SECONDS * item.speed_factor
        new_in = max(0.0, min(item.source_in_seconds + delta * item.speed_factor, latest_in))
        items[index] = dataclasses.replace(item, source_in_seconds=round(new_in, 3))
    return _with_items(state, items)


def move_item(state: EditorState, index: int, new_index: int) -> EditorState:
    items = list(state.timeline.items)
    new_index = max(0, min(new_index, len(items) - 1))
    if new_index == index:
        return state
    item = items.pop(index)
    items.insert(new_index, item)
    return _with_items(state, items)


def split_item(state: EditorState, index: int, at_local: float, *, new_clip_id: str) -> EditorState | None:
    """Cuts item `index` in two at `at_local` seconds into it. None when
    the cut would leave a piece shorter than MIN_DURATION_SECONDS."""
    items = list(state.timeline.items)
    item = items[index]
    duration = item.on_screen_duration_seconds
    if not (MIN_DURATION_SECONDS <= at_local <= duration - MIN_DURATION_SECONDS):
        return None
    if isinstance(item, TimelineStill):
        first = dataclasses.replace(item, display_duration_seconds=round(at_local, 2), transition_out=TransitionSpec())
        second = dataclasses.replace(
            item, clip_id=new_clip_id, display_duration_seconds=round(duration - at_local, 2),
        )
    else:
        cut = round(item.source_in_seconds + at_local * item.speed_factor, 3)
        first = dataclasses.replace(item, source_out_seconds=cut, transition_out=TransitionSpec())
        second = dataclasses.replace(item, clip_id=new_clip_id, source_in_seconds=cut)
    items[index:index + 1] = [first, second]
    return _with_items(state, items)


def split_at(
    state: EditorState, media_items: dict[str, MediaItem], t: float, *, new_clip_id: str,
) -> EditorState | None:
    """Splits whichever video item is showing at timeline time `t`."""
    for segment in timeline_segments(state.timeline, media_items):
        if segment.start_seconds < t < segment.end_seconds:
            return split_item(state, segment.index, t - segment.start_seconds, new_clip_id=new_clip_id)
    return None


# --- filters and transitions ----------------------------------------------------------------------

TRANSITION_LABELS: dict[str, str] = {
    "cut": "Be perėjimo",
    "fade": "Užtemimas",
    "dissolve": "Ištirpimas",
    "slide_left": "Slinkimas į kairę",
    "slide_right": "Slinkimas į dešinę",
}
MIN_TRANSITION_SECONDS = 0.2
MAX_TRANSITION_SECONDS = 2.0


def max_transition_seconds(state: EditorState, index: int) -> float:
    """The longest transition out of item `index`: the crossfade
    overlaps both neighbors, so it must stay shorter than either."""
    items = state.timeline.items
    if not (0 <= index < len(items) - 1):
        return 0.0
    shortest = min(items[index].on_screen_duration_seconds, items[index + 1].on_screen_duration_seconds)
    return round(max(0.0, min(MAX_TRANSITION_SECONDS, shortest * 0.9)), 2)


def set_transition(state: EditorState, index: int, kind: str, duration: float) -> EditorState:
    """Sets the transition from item `index` into the next one (a no-op
    for the last item). The duration is kept within what both clips
    allow; a "cut" always has none."""
    items = list(state.timeline.items)
    if not (0 <= index < len(items) - 1):
        return state
    limit = max_transition_seconds(state, index)
    if kind == "cut" or limit < MIN_TRANSITION_SECONDS / 2:
        spec = TransitionSpec()
    else:
        spec = TransitionSpec(kind=kind, duration_seconds=round(max(min(duration, limit), 0.05), 2))
    items[index] = dataclasses.replace(items[index], transition_out=spec)
    return _with_items(state, items)


def set_transition_everywhere(state: EditorState, kind: str, duration: float) -> EditorState:
    for index in range(len(state.timeline.items) - 1):
        state = set_transition(state, index, kind, duration)
    return state


def set_look(state: EditorState, index: int | None, look: str, intensity: float) -> EditorState:
    """Puts the filter `look` at `intensity` (0-1) on item `index`, or
    on every item when `index` is None. Other effect settings stay."""
    items = list(state.timeline.items)
    targets = range(len(items)) if index is None else [index]
    intensity = round(max(0.0, min(1.0, intensity)), 2)
    for n in targets:
        if 0 <= n < len(items):
            effect = dataclasses.replace(items[n].effect, look=look, look_intensity=intensity)
            items[n] = dataclasses.replace(items[n], effect=effect)
    return _with_items(state, items)


def transition_markers(
    state: EditorState, media_items: dict[str, MediaItem],
) -> list[tuple[int, float, float, str]]:
    """`(index, start, end, kind)` for every non-cut transition on the
    assembled timeline: where the two clips overlap."""
    markers = []
    segments = timeline_segments(state.timeline, media_items)
    for segment, following in zip(segments, segments[1:]):
        transition = segment.item.transition_out
        if transition.kind != "cut":
            markers.append((segment.index, following.start_seconds, segment.end_seconds, transition.kind))
    return markers


def reorder_overlay(state: EditorState, track: str, index: int, delta: int) -> tuple[EditorState, int]:
    """Moves text/sticker `index` `delta` layers up (+, drawn later so
    on top of the others of its kind) or down (-). Returns the new
    state and the element's new index."""
    items = list(_overlays(state, track))
    if not 0 <= index < len(items):
        return state, index
    target = max(0, min(len(items) - 1, index + delta))
    if target == index:
        return state, index
    items.insert(target, items.pop(index))
    return _with_overlays(state, track, tuple(items)), target


# --- delete / duplicate (any track) ----------------------------------------------------------------


def delete_element(state: EditorState, track: str, index: int) -> EditorState:
    if track in ("video", "effects"):
        items = list(state.timeline.items)
        if track == "effects":
            items[index] = dataclasses.replace(items[index], effect=EffectSpec())
            return _with_items(state, items)
        del items[index]
        return _with_items(state, items)
    if track == "audio":
        return dataclasses.replace(state, music_track=None)
    if track == "sound":  # a clip's own sound can't be removed, only muted
        return set_clip_sound(state, index, volume=0.0)
    items = _overlays(state, track)
    remaining = items[:index] + items[index + 1:]
    if track == "captions":
        return dataclasses.replace(state, caption_lines=remaining or None)
    return _with_overlays(state, track, remaining)


def duplicate_element(
    state: EditorState, track: str, index: int, *, new_clip_id: str, total: float,
) -> tuple[EditorState, int] | None:
    """The copy goes right after the original. Returns the new state and
    the copy's index, or None for tracks that can't be duplicated."""
    if track == "video":
        items = list(state.timeline.items)
        copy = dataclasses.replace(items[index], clip_id=new_clip_id, transition_out=TransitionSpec())
        items.insert(index + 1, copy)
        return _with_items(state, items), index + 1
    if track not in _OVERLAY_FIELDS or (track == "captions" and not state.caption_lines):
        return None
    items = _overlays(state, track)
    item = items[index]
    length = item.end_seconds - item.start_seconds
    start = item.end_seconds if item.end_seconds + length <= total + 1e-6 else item.start_seconds
    copy = dataclasses.replace(item, start_seconds=round(start, 2), end_seconds=round(start + length, 2))
    if track == "captions":
        return dataclasses.replace(state, caption_lines=items[:index + 1] + (copy,) + items[index + 1:]), index + 1
    return _with_overlays(state, track, items + (copy,)), len(items)

