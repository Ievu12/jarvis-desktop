"""Tests for jarvis.video_editor.timeline_history: pure, I/O-free
undo/redo stack logic and single-item copy/paste - no ffmpeg/GUI
needed anywhere in this file."""

from __future__ import annotations

import dataclasses

from jarvis.video_editor.timeline import Timeline, TimelineClip, TimelineStill, TransitionSpec
from jarvis.video_editor.timeline_history import TimelineClipboard, TimelineHistory

_CLIP_A = TimelineClip(clip_id="a", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=5.0)
_CLIP_B = TimelineClip(clip_id="b", media_item_id="m2", source_in_seconds=0.0, source_out_seconds=3.0)


def test_new_history_starts_with_an_empty_timeline_and_nothing_to_undo_or_redo():
    history = TimelineHistory()
    assert history.current == Timeline()
    assert history.can_undo() is False
    assert history.can_redo() is False


def test_push_updates_current_and_enables_undo():
    history = TimelineHistory()
    timeline_with_a = Timeline(items=(_CLIP_A,))
    history.push(timeline_with_a)
    assert history.current == timeline_with_a
    assert history.can_undo() is True
    assert history.can_redo() is False


def test_push_with_the_same_value_as_current_is_a_no_op():
    history = TimelineHistory()
    history.push(Timeline())  # same as the default current - must not create a history entry
    assert history.can_undo() is False


def test_undo_restores_the_previous_timeline():
    history = TimelineHistory()
    timeline_with_a = Timeline(items=(_CLIP_A,))
    timeline_with_ab = Timeline(items=(_CLIP_A, _CLIP_B))
    history.push(timeline_with_a)
    history.push(timeline_with_ab)
    restored = history.undo()
    assert restored == timeline_with_a
    assert history.current == timeline_with_a


def test_undo_with_nothing_to_undo_returns_current_unchanged():
    history = TimelineHistory()
    assert history.undo() == Timeline()
    assert history.can_undo() is False


def test_redo_restores_the_undone_timeline():
    history = TimelineHistory()
    timeline_with_a = Timeline(items=(_CLIP_A,))
    timeline_with_ab = Timeline(items=(_CLIP_A, _CLIP_B))
    history.push(timeline_with_a)
    history.push(timeline_with_ab)
    history.undo()
    redone = history.redo()
    assert redone == timeline_with_ab
    assert history.current == timeline_with_ab


def test_redo_with_nothing_to_redo_returns_current_unchanged():
    history = TimelineHistory()
    history.push(Timeline(items=(_CLIP_A,)))
    assert history.redo() == Timeline(items=(_CLIP_A,))
    assert history.can_redo() is False


def test_a_new_push_after_undo_clears_the_redo_stack():
    history = TimelineHistory()
    timeline_with_a = Timeline(items=(_CLIP_A,))
    timeline_with_ab = Timeline(items=(_CLIP_A, _CLIP_B))
    timeline_with_a_only_again = Timeline(items=(_CLIP_A,), aspect_ratio="1:1")
    history.push(timeline_with_a)
    history.push(timeline_with_ab)
    history.undo()
    assert history.can_redo() is True
    history.push(timeline_with_a_only_again)
    assert history.can_redo() is False


def test_multiple_undos_walk_back_through_every_recorded_state():
    history = TimelineHistory()
    states = [Timeline(items=(_CLIP_A,) * n) for n in range(1, 5)]
    for state in states:
        history.push(state)
    history.undo()
    history.undo()
    assert history.current == states[1]
    history.undo()
    history.undo()
    assert history.current == Timeline()
    assert history.can_undo() is False


def test_history_cap_discards_the_oldest_entries_beyond_the_limit():
    history = TimelineHistory()
    for n in range(1, 60):
        history.push(Timeline(items=(_CLIP_A,) * n))
    undo_count = 0
    while history.can_undo():
        history.undo()
        undo_count += 1
    assert undo_count <= 50


def test_reset_replaces_current_and_clears_both_stacks():
    history = TimelineHistory()
    history.push(Timeline(items=(_CLIP_A,)))
    history.push(Timeline(items=(_CLIP_A, _CLIP_B)))
    history.undo()
    new_timeline = Timeline(items=(_CLIP_B,))
    history.reset(new_timeline)
    assert history.current == new_timeline
    assert history.can_undo() is False
    assert history.can_redo() is False


def test_clipboard_starts_empty():
    clipboard = TimelineClipboard()
    assert clipboard.has_item() is False
    assert clipboard.paste(new_clip_id="new-1") is None


def test_clipboard_copy_then_paste_returns_an_equivalent_item_with_a_new_id():
    clipboard = TimelineClipboard()
    clipboard.copy(_CLIP_A)
    assert clipboard.has_item() is True
    pasted = clipboard.paste(new_clip_id="pasted-1")
    assert pasted is not None
    assert pasted.clip_id == "pasted-1"
    assert pasted.media_item_id == _CLIP_A.media_item_id
    assert pasted.source_in_seconds == _CLIP_A.source_in_seconds
    assert pasted.source_out_seconds == _CLIP_A.source_out_seconds


def test_clipboard_paste_resets_transition_out_to_a_plain_cut():
    clip_with_transition = dataclasses.replace(
        _CLIP_A, transition_out=TransitionSpec(kind="fade", duration_seconds=1.0),
    )
    clipboard = TimelineClipboard()
    clipboard.copy(clip_with_transition)
    pasted = clipboard.paste(new_clip_id="pasted-2")
    assert pasted.transition_out == TransitionSpec()


def test_clipboard_can_paste_the_same_copied_item_multiple_times_with_distinct_ids():
    clipboard = TimelineClipboard()
    clipboard.copy(_CLIP_A)
    first = clipboard.paste(new_clip_id="p1")
    second = clipboard.paste(new_clip_id="p2")
    assert first.clip_id != second.clip_id
    assert first.media_item_id == second.media_item_id == _CLIP_A.media_item_id


def test_clipboard_works_with_a_timeline_still_too():
    still = TimelineStill(clip_id="s1", media_item_id="photo1", display_duration_seconds=4.0)
    clipboard = TimelineClipboard()
    clipboard.copy(still)
    pasted = clipboard.paste(new_clip_id="s1-copy")
    assert isinstance(pasted, TimelineStill)
    assert pasted.display_duration_seconds == 4.0
    assert pasted.clip_id == "s1-copy"
