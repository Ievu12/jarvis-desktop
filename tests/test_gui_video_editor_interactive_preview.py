"""Tests for the real-time preview's interactive editing
(InteractivePreviewPanel) and the selected-element settings panel
(ElementInspectorPanel). Pointer gestures are fed straight to the
panel's own press/motion/release handlers with real canvas
coordinates, so the hit-testing and drag math run for real."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import customtkinter as ctk
import pytest
from PIL import Image

from jarvis.gui.views.video_editor.element_inspector_panel import ElementInspectorPanel
from jarvis.gui.views.video_editor.interactive_preview_panel import InteractivePreviewPanel, format_timecode
from jarvis.video_editor import preview_compositor as pc
from jarvis.video_editor.stickers import StickerInstance
from jarvis.video_editor.text_overlay import TextOverlay


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


class _Recorder:
    def __init__(self):
        self.edits = []
        self.selections = []
        self.deletes = []
        self.toggles = 0
        self.seeks = []


def _panel(root, scene: pc.Scene) -> tuple[InteractivePreviewPanel, _Recorder]:
    rec = _Recorder()

    def toggled():
        rec.toggles += 1

    panel = InteractivePreviewPanel(
        root,
        on_play_toggled=toggled,
        on_seek=rec.seeks.append,
        on_selection_changed=rec.selections.append,
        on_element_edited=lambda kind, index, new, final: rec.edits.append((kind, index, new, final)),
        on_delete_requested=lambda kind, index: rec.deletes.append((kind, index)),
    )
    panel.configure_canvas(frame_size=(360, 640), canvas_size=(1080, 1920))
    panel.update_scene(scene)
    panel.show_base(Image.new("RGB", (360, 640), "gray"), 1.0)
    return panel, rec


def _event(x, y):
    return SimpleNamespace(x=x, y=y)


def _drag(panel, start, end):
    panel._on_press(_event(*start))
    panel._on_motion(_event((start[0] + end[0]) / 2, (start[1] + end[1]) / 2))
    panel._on_motion(_event(*end))
    panel._on_release(_event(*end))


_STICKER = StickerInstance(start_seconds=0, end_seconds=5, shape="heart", x_fraction=0.5, y_fraction=0.5,
                           size_fraction=0.2)
_TEXT = TextOverlay(text="Labas ąčę", start_seconds=0, end_seconds=5, x_fraction=0.1, y_fraction=0.1, font_size=80)


def test_format_timecode():
    assert format_timecode(0) == "00:00.0"
    assert format_timecode(75.25) == "01:15.2"


def test_click_selects_and_empty_click_deselects(root):
    panel, rec = _panel(root, pc.Scene(stickers=(_STICKER,)))
    w, h = panel.display_size
    panel._on_press(_event(w / 2, h / 2))
    panel._on_release(_event(w / 2, h / 2))
    assert panel.selected == ("sticker", 0)
    assert rec.selections == [("sticker", 0)]
    assert rec.edits == []  # a click without moving edits nothing

    panel._on_press(_event(3, h - 3))
    assert panel.selected is None and rec.selections[-1] is None


def test_drag_moves_a_sticker(root):
    panel, rec = _panel(root, pc.Scene(stickers=(_STICKER,)))
    w, h = panel.display_size
    _drag(panel, (w / 2, h / 2), (w / 2 + w / 4, h / 2 - h / 4))
    finals = [e for e in rec.edits if e[3]]
    assert len(finals) == 1 and len(rec.edits) == 3
    moved = finals[0][2]
    assert moved.x_fraction == pytest.approx(0.75, abs=0.01)
    assert moved.y_fraction == pytest.approx(0.25, abs=0.01)
    assert dataclasses.replace(moved, x_fraction=0.5, y_fraction=0.5) == _STICKER


def test_drag_moves_text_keeping_its_grab_point(root):
    panel, rec = _panel(root, pc.Scene(text_overlays=(_TEXT,)))
    box = panel.boxes[0]
    _drag(panel, (box.center_x, box.center_y), (box.center_x + 50, box.center_y + 100))
    moved = rec.edits[-1][2]
    panel.update_scene(pc.Scene(text_overlays=(moved,)))
    new_box = panel.boxes[0]
    assert new_box.center_x == pytest.approx(box.center_x + 50, abs=2)
    assert new_box.center_y == pytest.approx(box.center_y + 100, abs=2)


def test_corner_handle_resizes(root):
    panel, rec = _panel(root, pc.Scene(stickers=(_STICKER,)))
    panel.select(("sticker", 0))
    box = panel.boxes[0]
    corner = box.corners()[2]
    far = (box.center_x + (corner[0] - box.center_x) * 2, box.center_y + (corner[1] - box.center_y) * 2)
    _drag(panel, corner, far)
    resized = rec.edits[-1][2]
    assert resized.size_fraction == pytest.approx(0.4, abs=0.02)
    assert (resized.x_fraction, resized.y_fraction) == (0.5, 0.5)

    panel, rec = _panel(root, pc.Scene(text_overlays=(_TEXT,)))
    panel.select(("text", 0))
    box = panel.boxes[0]
    corner = box.corners()[2]
    far = (box.center_x + (corner[0] - box.center_x) * 1.5, box.center_y + (corner[1] - box.center_y) * 1.5)
    _drag(panel, corner, far)
    assert rec.edits[-1][2].font_size == pytest.approx(120, abs=3)


def test_rotate_handle_rotates_and_snaps(root):
    panel, rec = _panel(root, pc.Scene(stickers=(_STICKER,)))
    panel.select(("sticker", 0))
    box = panel.boxes[0]
    handle = panel._rotate_handle_position(box)
    _drag(panel, handle, (box.center_x + 100, box.center_y))  # pointer to the right: 90 degrees
    assert rec.edits[-1][2].rotation_degrees == 90.0

    _drag(panel, panel._rotate_handle_position(box), (box.center_x + 100, box.center_y + 100))
    assert rec.edits[-1][2].rotation_degrees == pytest.approx(135.0, abs=0.2)


def test_spinning_sticker_has_no_rotate_handle(root):
    spinning = dataclasses.replace(_STICKER, animation="spin")
    panel, rec = _panel(root, pc.Scene(stickers=(spinning,)))
    panel.select(("sticker", 0))
    box = panel.boxes[0]
    hx, hy = panel._rotate_handle_position(box)
    panel._on_press(_event(hx, hy))  # above the sticker - just deselects
    assert panel.selected is None
    assert rec.edits == []


def test_delete_key_and_space(root):
    panel, rec = _panel(root, pc.Scene(stickers=(_STICKER,)))
    panel.select(("sticker", 0))
    panel._on_delete_key()
    assert rec.deletes == [("sticker", 0)] and panel.selected is None
    panel._on_delete_key()
    assert rec.deletes == [("sticker", 0)]


def test_transport_reflects_time_and_playing(root):
    panel, rec = _panel(root, pc.Scene())
    panel.set_time(2.5, 10.0)
    assert panel._time_label.cget("text") == "00:02.5 / 00:10.0"
    panel.set_playing(True)
    assert panel._play_button.cget("text") == "⏸"
    panel.set_playing(False)
    assert panel._play_button.cget("text") == "▶"


def test_inspector_edits_text_and_sticker(root):
    edits, deletes, duplicates = [], [], []
    inspector = ElementInspectorPanel(
        root,
        on_element_edited=lambda kind, index, new, final: edits.append((kind, index, new, final)),
        on_delete_requested=lambda kind, index: deletes.append((kind, index)),
        on_duplicate_requested=lambda kind, index: duplicates.append((kind, index)),
    )
    inspector.show_nothing()
    assert inspector.shown is None

    inspector.show_element("text", 0, _TEXT)
    assert inspector.shown == ("text", 0)
    inspector._edit(text="Sveiki šypsena")  # typing: live, not yet final
    assert edits[-1] == ("text", 0, dataclasses.replace(_TEXT, text="Sveiki šypsena"), False)
    inspector._edit(font_size=120)
    inspector.show_element("sticker", 1, _STICKER)  # leaving the element commits the pending edit
    assert edits[-1] == ("text", 0, dataclasses.replace(_TEXT, text="Sveiki šypsena", font_size=120), True)
    assert inspector.shown == ("sticker", 1)

    inspector._edit(final=True, opacity=0.5)
    assert edits[-1] == ("sticker", 1, dataclasses.replace(_STICKER, opacity=0.5), True)
    inspector.refresh_values(dataclasses.replace(_STICKER, opacity=0.5, size_fraction=0.3))
    inspector._on_delete_clicked()
    assert deletes == [("sticker", 1)]
