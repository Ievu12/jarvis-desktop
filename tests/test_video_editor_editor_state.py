"""Tests for jarvis.video_editor.editor_state (project-wide undo/redo)
and jarvis.video_editor.track_layout (the multi-track timeline's bars
and drag edits)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from jarvis.video_editor import track_layout as tl
from jarvis.video_editor.audio_mixing import MusicTrack
from jarvis.video_editor.captions import CaptionLine, CaptionStyle
from jarvis.video_editor.editor_state import EditorHistory, EditorState
from jarvis.video_editor.effects import EffectSpec
from jarvis.video_editor.media_import import MediaItem
from jarvis.video_editor.stickers import StickerInstance
from jarvis.video_editor.text_overlay import TextOverlay
from jarvis.video_editor.timeline import Timeline, TimelineClip, TimelineStill, TransitionSpec

MEDIA = {
    "v": MediaItem(media_item_id="v", original_filename="klipas.mp4", stored_path=Path("a.mp4"), kind="video",
                   duration_seconds=10, width=1080, height=1920, fps=30),
    "p": MediaItem(media_item_id="p", original_filename="foto.jpg", stored_path=Path("b.jpg"), kind="photo",
                   duration_seconds=None, width=800, height=600, fps=None),
}
CLIP = TimelineClip(clip_id="c1", media_item_id="v", source_in_seconds=2, source_out_seconds=6)
STILL = TimelineStill(clip_id="s1", media_item_id="p", display_duration_seconds=3)
TEXT = TextOverlay(text="Labas", start_seconds=1, end_seconds=3)
STICKER = StickerInstance(start_seconds=0, end_seconds=2, shape="heart")


def _state(**changes) -> EditorState:
    base = EditorState(timeline=Timeline(items=(CLIP, STILL)), text_overlays=(TEXT,), stickers=(STICKER,))
    return dataclasses.replace(base, **changes)


class _Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


# --- history --------------------------------------------------------------------------------------


def test_undo_redo_across_every_kind_of_edit():
    history = EditorHistory()
    s0 = _state()
    history.reset(s0)
    assert not history.can_undo()

    s1 = dataclasses.replace(s0, stickers=())
    s2 = dataclasses.replace(s1, music_track=MusicTrack(source_path=Path("m.mp3")))
    s3 = dataclasses.replace(s2, timeline=Timeline(items=(STILL,)))
    for state, label in ((s1, "Ištrintas lipdukas"), (s2, "Muzika"), (s3, "Ištrintas klipas")):
        assert history.record(state, label=label)

    assert history.undo_label == "Ištrintas klipas"
    assert history.undo() == s2
    assert history.undo() == s1
    assert history.redo_label == "Muzika"
    assert history.redo() == s2
    assert history.undo() == s1
    assert history.undo() == s0
    assert history.undo() is None and history.current == s0

    # a new edit after undoing drops the redo branch
    history.redo()
    history.record(s3)
    assert not history.can_redo()


def test_identical_state_is_not_an_undo_step():
    history = EditorHistory()
    history.reset(_state())
    assert not history.record(_state())
    assert not history.can_undo()


def test_quick_repeated_edits_with_one_key_merge_into_one_step():
    clock = _Clock()
    history = EditorHistory(clock=clock)
    s0 = _state()
    history.reset(s0)
    states = [_state(text_overlays=(dataclasses.replace(TEXT, font_size=size),)) for size in (60, 70, 80)]
    history.record(states[0], coalesce_key="text:0")
    clock.now += 0.4
    history.record(states[1], coalesce_key="text:0")
    clock.now += 0.4
    history.record(states[2], coalesce_key="text:0")
    assert history.current == states[2]
    assert history.undo() == s0  # one step back to before the slider drag

    history.redo()
    clock.now += 5  # a pause: a separate step
    later = _state(text_overlays=(dataclasses.replace(TEXT, font_size=99),))
    history.record(later, coalesce_key="text:0")
    assert history.undo() == states[2]


def test_history_is_bounded():
    history = EditorHistory(max_entries=3)
    history.reset(_state())
    for n in range(10):
        history.record(_state(text_overlays=(dataclasses.replace(TEXT, text=str(n)),)))
    undos = 0
    while history.undo() is not None:
        undos += 1
    assert undos == 3


# --- track bars ------------------------------------------------------------------------------------


def test_track_bars_for_every_track():
    state = _state(
        caption_lines=(CaptionLine(text="Sveiki", start_seconds=0.5, end_seconds=1.5),),
        caption_style=CaptionStyle(),
        music_track=MusicTrack(source_path=Path("daina.mp3"), trim_start_seconds=1, trim_end_seconds=4),
    )
    state = dataclasses.replace(state, timeline=Timeline(items=(
        dataclasses.replace(CLIP, effect=EffectSpec(motion="zoom_in", brightness=0.1)), STILL,
    )))
    bars = tl.build_track_bars(state, MEDIA)
    assert [(b.start, b.end, b.label) for b in bars["video"]] == [(0, 4, "klipas.mp4"), (4, 7, "foto.jpg")]
    assert [b.label for b in bars["effects"]] == ["Priartinimas + Spalvos"]
    assert [(b.start, b.end) for b in bars["audio"]] == [(0, 3)]
    assert [b.label for b in bars["captions"]] == ["Sveiki"]
    assert [(b.start, b.end) for b in bars["text"]] == [(1, 3)]
    assert [b.label for b in bars["stickers"]] == ["heart"]


def test_captions_made_at_export_show_one_fixed_bar():
    bars = tl.build_track_bars(_state(caption_style=CaptionStyle()), MEDIA)
    (bar,) = bars["captions"]
    assert (bar.start, bar.end) == (0, 7) and not bar.can_move


def test_snap_and_drop_index():
    assert tl.snap(2.04, [0, 2.0, 5], 0.1) == 2.0
    assert tl.snap(2.5, [0, 2.0, 5], 0.1) == 2.5
    state = _state()
    assert tl.drop_index(state, MEDIA, 6.5, moving=0) == 1  # clip dragged past the photo's middle
    assert tl.drop_index(state, MEDIA, 0.5, moving=1) == 0


# --- overlay edits ---------------------------------------------------------------------------------


def test_move_overlay_keeps_length_and_stays_inside_the_video():
    state = tl.move_overlay(_state(), "text", 0, 2.5, total=7)
    assert (state.text_overlays[0].start_seconds, state.text_overlays[0].end_seconds) == (2.5, 4.5)
    state = tl.move_overlay(state, "text", 0, 9, total=7)
    assert (state.text_overlays[0].start_seconds, state.text_overlays[0].end_seconds) == (5, 7)
    state = tl.move_overlay(state, "stickers", 0, -3, total=7)
    assert state.stickers[0].start_seconds == 0


def test_trim_overlay_edges():
    state = tl.trim_overlay(_state(), "text", 0, start=0.5, total=7)
    assert state.text_overlays[0].start_seconds == 0.5
    state = tl.trim_overlay(state, "text", 0, end=0.2, total=7)  # can't go before the start
    assert state.text_overlays[0].end_seconds == pytest.approx(0.6)
    state = tl.trim_overlay(state, "text", 0, end=50, total=7)
    assert state.text_overlays[0].end_seconds == 7


def test_caption_lines_and_music_edits():
    line = CaptionLine(text="A", start_seconds=0, end_seconds=1)
    state = _state(caption_lines=(line,), music_track=MusicTrack(source_path=Path("m.mp3"), trim_start_seconds=2))
    state = tl.move_overlay(state, "captions", 0, 3, total=7)
    assert state.caption_lines == (CaptionLine(text="A", start_seconds=3, end_seconds=4),)
    state = tl.set_music_length(state, 5)
    assert state.music_track.trim_end_seconds == 7
    assert tl.delete_element(state, "captions", 0).caption_lines is None
    assert tl.delete_element(state, "audio", 0).music_track is None


# --- video edits -----------------------------------------------------------------------------------


def test_right_edge_changes_duration_within_the_source():
    state = tl.set_item_duration(_state(), 0, 3, MEDIA)
    assert state.timeline.items[0].source_out_seconds == 5
    state = tl.set_item_duration(state, 0, 100, MEDIA)
    assert state.timeline.items[0].source_out_seconds == 10  # the source is 10 s long
    state = tl.set_item_duration(state, 1, 5.5, MEDIA)
    assert state.timeline.items[1].display_duration_seconds == 5.5


def test_left_edge_trims_the_start():
    state = tl.trim_item_start(_state(), 0, 1.5)
    assert state.timeline.items[0].source_in_seconds == 3.5
    state = tl.trim_item_start(state, 0, -10)
    assert state.timeline.items[0].source_in_seconds == 0
    state = tl.trim_item_start(state, 1, 1)
    assert state.timeline.items[1].display_duration_seconds == 2


def test_speed_is_respected_when_trimming():
    fast = dataclasses.replace(CLIP, speed_factor=2.0)  # 4 s of source -> 2 s on screen
    state = tl.set_item_duration(_state(timeline=Timeline(items=(fast,))), 0, 1, MEDIA)
    assert state.timeline.items[0].source_out_seconds == 4


def test_reorder_resets_the_last_items_transition():
    with_fade = dataclasses.replace(CLIP, transition_out=TransitionSpec(kind="fade", duration_seconds=0.5))
    state = tl.move_item(_state(timeline=Timeline(items=(with_fade, STILL))), 0, 1)
    assert [i.clip_id for i in state.timeline.items] == ["s1", "c1"]
    assert state.timeline.items[-1].transition_out.kind == "cut"
    assert state.timeline.validate() == []


def test_split_at_the_playhead():
    state = tl.split_at(_state(), MEDIA, 1.5, new_clip_id="new")
    first, second, still = state.timeline.items
    assert (first.source_in_seconds, first.source_out_seconds) == (2, 3.5)
    assert (second.clip_id, second.source_in_seconds, second.source_out_seconds) == ("new", 3.5, 6)
    state = tl.split_at(state, MEDIA, 5, new_clip_id="new2")
    assert [i.on_screen_duration_seconds for i in state.timeline.items] == [1.5, 2.5, 1, 2]
    assert tl.split_at(state, MEDIA, 0.05, new_clip_id="x") is None  # too close to an edge
    assert tl.split_at(state, MEDIA, 50, new_clip_id="x") is None


def test_delete_and_duplicate():
    state = tl.delete_element(_state(), "video", 0)
    assert state.timeline.items == (STILL,)
    state = tl.delete_element(_state(timeline=Timeline(items=(
        dataclasses.replace(CLIP, effect=EffectSpec(fade="fade_in")),))), "effects", 0)
    assert state.timeline.items[0].effect == EffectSpec()

    new_state, index = tl.duplicate_element(_state(), "video", 0, new_clip_id="dup", total=7)
    assert index == 1 and new_state.timeline.items[1].clip_id == "dup"
    new_state, index = tl.duplicate_element(_state(), "text", 0, new_clip_id="", total=7)
    assert index == 1 and new_state.text_overlays[1].start_seconds == 3
    near_end = _state(text_overlays=(dataclasses.replace(TEXT, start_seconds=5, end_seconds=7),))
    new_state, _ = tl.duplicate_element(near_end, "text", 0, new_clip_id="", total=7)
    assert new_state.text_overlays[1].start_seconds == 5  # no room after it: same time
    assert tl.duplicate_element(_state(), "audio", 0, new_clip_id="", total=7) is None
