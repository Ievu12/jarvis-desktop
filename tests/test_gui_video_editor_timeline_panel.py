"""Tests for jarvis.gui.views.video_editor.timeline_panel.TimelinePanel's
undo/redo and copy/paste wiring (Stage 3 of the "professional Reels
editor" plan) - the panel's own pre-existing add/trim/move/remove
behavior is exercised indirectly through these same real methods, no
mocking, same convention as the other video_editor GUI panel tests."""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk
import pytest

from jarvis.gui.views.video_editor.timeline_panel import TimelinePanel
from jarvis.video_editor.media_import import MediaItem
from jarvis.video_editor.timeline import TimelineClip


@pytest.fixture(scope="module")
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture(autouse=True)
def _destroy_children(root):
    yield
    for child in list(root.winfo_children()):
        child.destroy()


def _make_panel(root, on_timeline_changed=None):
    return TimelinePanel(
        root, on_timeline_changed=on_timeline_changed or (lambda t: None),
        get_thumbnail=lambda media: None,
    )


_MEDIA_1 = MediaItem(
    media_item_id="m1", original_filename="clip1.mp4", stored_path=Path("clip1.mp4"),
    kind="video", duration_seconds=5.0, width=640, height=360, fps=30.0,
)
_MEDIA_2 = MediaItem(
    media_item_id="m2", original_filename="clip2.mp4", stored_path=Path("clip2.mp4"),
    kind="video", duration_seconds=4.0, width=640, height=360, fps=30.0,
)


def test_new_panel_has_nothing_to_undo_or_redo(root):
    panel = _make_panel(root)
    assert panel._history.can_undo() is False
    assert panel._history.can_redo() is False
    assert str(panel._undo_button.cget("state")) == "disabled"
    assert str(panel._redo_button.cget("state")) == "disabled"


def test_adding_a_clip_enables_undo_but_not_redo(root):
    panel = _make_panel(root)
    panel.add_clip(_MEDIA_1)
    assert panel._history.can_undo() is True
    assert panel._history.can_redo() is False
    assert str(panel._undo_button.cget("state")) == "normal"


def test_undo_removes_the_just_added_clip(root):
    panel = _make_panel(root)
    panel.add_clip(_MEDIA_1)
    assert len(panel.timeline.items) == 1
    panel._on_undo_clicked()
    assert len(panel.timeline.items) == 0
    assert panel._history.can_redo() is True


def test_redo_restores_the_undone_clip(root):
    panel = _make_panel(root)
    panel.add_clip(_MEDIA_1)
    panel._on_undo_clicked()
    panel._on_redo_clicked()
    assert len(panel.timeline.items) == 1
    assert panel.timeline.items[0].media_item_id == "m1"


def test_undo_then_a_new_edit_clears_redo(root):
    panel = _make_panel(root)
    panel.add_clip(_MEDIA_1)
    panel._on_undo_clicked()
    assert panel._history.can_redo() is True
    panel.add_clip(_MEDIA_2)
    assert panel._history.can_redo() is False


def test_multiple_edits_can_all_be_undone_back_to_empty(root):
    panel = _make_panel(root)
    panel.add_clip(_MEDIA_1)
    panel.add_clip(_MEDIA_2)
    panel._move_item(0, 1)
    assert len(panel.timeline.items) == 2
    panel._on_undo_clicked()
    panel._on_undo_clicked()
    panel._on_undo_clicked()
    assert len(panel.timeline.items) == 0
    assert panel._history.can_undo() is False


def test_on_timeline_changed_fires_on_undo_and_redo(root):
    seen = []
    panel = _make_panel(root, on_timeline_changed=seen.append)
    panel.add_clip(_MEDIA_1)
    seen.clear()
    panel._on_undo_clicked()
    assert len(seen) == 1
    assert len(seen[-1].items) == 0
    panel._on_redo_clicked()
    assert len(seen[-1].items) == 1


def test_loading_a_project_via_render_resets_history(root):
    panel = _make_panel(root)
    panel.add_clip(_MEDIA_1)
    assert panel._history.can_undo() is True
    panel.render(panel.timeline, {"m1": _MEDIA_1})
    assert panel._history.can_undo() is False
    assert panel._history.can_redo() is False


def test_copy_then_paste_duplicates_the_item_after_it(root):
    panel = _make_panel(root)
    panel.add_clip(_MEDIA_1)
    panel._on_copy_clicked(0)
    panel._on_paste_clicked(0)
    assert len(panel.timeline.items) == 2
    assert panel.timeline.items[0].media_item_id == "m1"
    assert panel.timeline.items[1].media_item_id == "m1"
    assert panel.timeline.items[0].clip_id != panel.timeline.items[1].clip_id


def test_paste_without_a_prior_copy_does_nothing(root):
    panel = _make_panel(root)
    panel.add_clip(_MEDIA_1)
    panel._on_paste_clicked(0)
    assert len(panel.timeline.items) == 1


def test_paste_is_itself_undoable(root):
    panel = _make_panel(root)
    panel.add_clip(_MEDIA_1)
    panel._on_copy_clicked(0)
    panel._on_paste_clicked(0)
    assert len(panel.timeline.items) == 2
    panel._on_undo_clicked()
    assert len(panel.timeline.items) == 1


def test_pasted_item_transition_is_reset_to_a_plain_cut(root):
    import dataclasses

    from jarvis.video_editor.timeline import TransitionSpec

    panel = _make_panel(root)
    panel.add_clip(_MEDIA_1)
    panel.add_clip(_MEDIA_2)
    clip_with_transition = dataclasses.replace(
        panel.timeline.items[0], transition_out=TransitionSpec(kind="fade", duration_seconds=1.0),
    )
    panel._replace_item(0, clip_with_transition)
    panel._on_copy_clicked(0)
    panel._on_paste_clicked(1)
    pasted = panel.timeline.items[2]
    assert pasted.transition_out == TransitionSpec()
