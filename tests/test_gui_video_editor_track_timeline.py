"""Tests for the multi-track timeline widget (TrackTimelinePanel):
clicking selects, dragging a bar's body moves it, dragging an edge
trims it, the ruler seeks, and the toolbar splits/duplicates/deletes.
Gestures go through the widget's own press/motion/release handlers at
real canvas coordinates."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import customtkinter as ctk
import pytest

from jarvis.gui.views.video_editor.track_timeline_panel import TrackTimelinePanel, format_ruler_time
from jarvis.video_editor.editor_state import EditorState
from jarvis.video_editor.media_import import MediaItem
from jarvis.video_editor.stickers import StickerInstance
from jarvis.video_editor.text_overlay import TextOverlay
from jarvis.video_editor.timeline import Timeline, TimelineClip, TimelineStill

MEDIA = {
    "v": MediaItem(media_item_id="v", original_filename="klipas.mp4", stored_path=Path("a.mp4"), kind="video",
                   duration_seconds=10, width=1080, height=1920, fps=30),
    "p": MediaItem(media_item_id="p", original_filename="foto.jpg", stored_path=Path("b.jpg"), kind="photo",
                   duration_seconds=None, width=800, height=600, fps=None),
}
STATE = EditorState(
    timeline=Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="v", source_in_seconds=0, source_out_seconds=4),
        TimelineStill(clip_id="s1", media_item_id="p", display_duration_seconds=4),
    )),
    text_overlays=(TextOverlay(text="Labas", start_seconds=1, end_seconds=3),),
    stickers=(StickerInstance(start_seconds=4, end_seconds=6, shape="star"),),
)


@pytest.fixture(scope="module")
def root():
    r = ctk.CTk()
    r.geometry("900x300")
    yield r
    r.destroy()


@pytest.fixture(autouse=True)
def _destroy_children(root):
    yield
    for child in list(root.winfo_children()):
        child.destroy()


class _Rec:
    def __init__(self):
        self.seeks, self.selections, self.edits = [], [], []


def _panel(root, state=STATE):
    rec = _Rec()
    panel = TrackTimelinePanel(
        root, on_seek=rec.seeks.append, on_selection_changed=rec.selections.append,
        on_state_edited=lambda s, final, label, key: rec.edits.append((s, final, label)),
    )
    panel.pack(fill="both", expand=True)
    root.update()
    panel.render(state, MEDIA)
    panel.zoom(1)  # leave fit mode: 1 s is a fixed number of pixels from here on
    panel._canvas.xview_moveto(0)
    root.update()
    return panel, rec


def _event(x, y):
    return SimpleNamespace(x=x, y=y)


def _drag(panel, track, start_t, end_t):
    y = panel.lane_center_y(track)
    x0, x1 = panel.x_for(start_t), panel.x_for(end_t)
    panel._on_press(_event(x0, y))
    panel._on_motion(_event((x0 + x1) / 2, y))
    panel._on_motion(_event(x1, y))
    panel._on_release(_event(x1, y))


def test_ruler_time_format():
    assert format_ruler_time(0) == "0:00"
    assert format_ruler_time(75) == "1:15"
    assert format_ruler_time(2.5) == "0:02.5"


def test_bars_are_drawn_for_each_track(root):
    panel, _ = _panel(root)
    assert [b.label for b in panel._bars["video"]] == ["klipas.mp4", "foto.jpg"]
    assert len(panel._bars["text"]) == 1 and len(panel._bars["stickers"]) == 1
    assert len(panel._canvas.find_all()) > 20


def test_click_selects_and_ruler_click_seeks(root):
    panel, rec = _panel(root)
    panel._on_press(_event(panel.x_for(2), panel.lane_center_y("text")))
    panel._on_release(_event(panel.x_for(2), panel.lane_center_y("text")))
    assert panel.selected == ("text", 0) and rec.selections == [("text", 0)]
    assert rec.edits == []

    panel._on_press(_event(panel.x_for(5), 5))  # the ruler
    panel._on_release(_event(panel.x_for(5), 5))
    assert rec.seeks[-1] == pytest.approx(5, abs=0.05)
    assert panel.selected is None


def test_dragging_a_text_bar_moves_it_live_then_final(root):
    panel, rec = _panel(root)
    _drag(panel, "text", 2, 4.5)
    finals = [e for e in rec.edits if e[1]]
    assert len(finals) == 1 and len(rec.edits) == 3  # two live updates + the final one
    moved = finals[0][0].text_overlays[0]
    assert (moved.start_seconds, moved.end_seconds) == pytest.approx((3.5, 5.5), abs=0.05)
    assert finals[0][2] == "Perkelta"


def test_edges_snap_to_other_bars(root):
    panel, rec = _panel(root)
    # drag the text's end to 3.95 s: snaps onto the sticker's start (4 s)
    _drag(panel, "text", 3, 3.95)
    assert rec.edits[-1][0].text_overlays[0].end_seconds == 4


def test_video_trim_applies_on_release_only(root):
    panel, rec = _panel(root)
    _drag(panel, "video", 3.97, 2.97)  # the clip's right edge, 1 s to the left
    assert len(rec.edits) == 1 and rec.edits[0][1]
    assert rec.edits[0][0].timeline.items[0].source_out_seconds == pytest.approx(3, abs=0.05)


def test_video_reorder_by_dragging(root):
    panel, rec = _panel(root)
    _drag(panel, "video", 2, 7)
    assert [i.clip_id for i in rec.edits[-1][0].timeline.items] == ["s1", "c1"]


def test_toolbar_split_duplicate_delete(root):
    panel, rec = _panel(root)
    panel.set_playhead(1.0)
    panel.split_at_playhead()
    assert [i.on_screen_duration_seconds for i in rec.edits[-1][0].timeline.items] == [1, 3, 4]

    panel.select(("stickers", 0))
    panel.duplicate_selected()
    assert len(rec.edits[-1][0].stickers) == 2
    assert rec.selections[-1] == ("stickers", 1)

    panel.render(STATE, MEDIA)
    panel.select(("text", 0))
    panel.delete_selected()
    assert rec.edits[-1][0].text_overlays == ()
    assert panel.selected is None


def test_zoom_changes_scale(root):
    panel, _ = _panel(root)
    before = panel.px_per_second
    panel.zoom(2)
    assert panel.px_per_second == pytest.approx(before * 2)
    panel.zoom_to_fit()
    assert panel.px_per_second != pytest.approx(before * 2)
