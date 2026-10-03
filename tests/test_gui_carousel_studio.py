"""GUI tests for the carousel studio (jarvis.gui.views.carousel_studio)
using a real withdrawn CTk root: library -> editor, mouse move/resize/
rotate on the slide canvas, inspector edits, slide strip reordering,
keyboard shortcuts, undo/redo and autosave."""

from __future__ import annotations

import math
from types import SimpleNamespace

import customtkinter as ctk
import pytest

from jarvis.carousel_studio import storage
from jarvis.gui.views.carousel_studio.dashboard import CarouselStudioView


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CAROUSEL_STUDIO_DB_FILE", tmp_path / "carousel.db")
    monkeypatch.setattr(storage, "CAROUSEL_STUDIO_PROJECTS_DIR", tmp_path / "projects")


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


@pytest.fixture
def view(root):
    v = CarouselStudioView(root)
    v.pack(fill="both", expand=True)
    root.update_idletasks()
    return v


def _event(canvas, x, y, shift=False):
    sx, sy = canvas.to_screen(x, y)
    return SimpleNamespace(x=sx, y=sy, state=0x0001 if shift else 0)


def _drag(canvas, start, end, steps=4, shift=False):
    canvas._press(_event(canvas, *start))
    for i in range(1, steps + 1):
        t = i / steps
        canvas._motion(_event(canvas, start[0] + (end[0] - start[0]) * t, start[1] + (end[1] - start[1]) * t, shift))
    canvas._release(_event(canvas, *end))


def _open_new(view, name="Testinė karuselė", count="4"):
    view.library.name_entry.insert(0, name)
    view.library.count_var.set(count)
    view.library._create()
    view.editor.canvas.configure(width=800, height=1000)  # real size; the root is withdrawn
    view.editor.canvas.redraw()
    return view.editor


def test_create_project_opens_editor_and_lists_in_library(view, root):
    editor = _open_new(view)
    assert editor.winfo_manager() == "pack"
    assert len(editor.doc.slides) == 4
    assert editor.count_label.cget("text") == "4 / 20 skaidrių"
    editor.back()
    names = [p.name for p in storage.list_projects()]
    assert names == ["Testinė karuselė"]


def test_click_selects_and_drag_moves_with_undo(view):
    editor = _open_new(view)
    canvas = editor.canvas
    heading = editor.current_slide().elements[1]
    cx, cy = heading.center
    canvas._press(_event(canvas, cx, cy))
    canvas._release(_event(canvas, cx, cy))
    assert editor.selected_id == heading.id
    assert "text" in editor.inspector._fields
    x0, y0 = heading.x, heading.y
    _drag(canvas, (cx, cy), (cx + 23, cy + 51), shift=True)  # shift: no snapping
    moved = editor.current_slide().find(heading.id)
    assert moved.x == pytest.approx(x0 + 23, abs=0.5) and moved.y == pytest.approx(y0 + 51, abs=0.5)
    editor.undo()
    restored = editor.current_slide().find(heading.id)
    assert restored.x == pytest.approx(x0) and restored.y == pytest.approx(y0)


def test_drag_snaps_to_slide_center(view):
    editor = _open_new(view)
    editor.add_shape("rect")
    el = editor.selected_element()
    cx, cy = el.center
    _drag(editor.canvas, (cx, cy), (cx + 3, cy + 200))
    assert editor.selected_element().center[0] == pytest.approx(540)


def test_resize_handle_keeps_opposite_corner(view):
    editor = _open_new(view)
    editor.add_shape("rect")
    el = editor.selected_element()
    left, top = el.x, el.y
    br = (el.x + el.w, el.y + el.h)
    _drag(editor.canvas, br, (br[0] + 100, br[1] + 40))
    el = editor.selected_element()
    assert el.x == pytest.approx(left, abs=0.5) and el.y == pytest.approx(top, abs=0.5)
    assert el.w == pytest.approx(460, abs=1) and el.h == pytest.approx(400, abs=1)


def test_resize_rotated_element_keeps_opposite_corner(view):
    editor = _open_new(view)
    editor.add_shape("rect")
    editor.update_selected(rotation=30)
    el = editor.selected_element()
    corners = editor.canvas.corners(el)
    anchor = corners[0]
    target = corners[2]
    a = math.radians(30)
    end = (target[0] + 50 * math.cos(a), target[1] + 50 * math.sin(a))
    _drag(editor.canvas, target, end)
    el = editor.selected_element()
    assert el.w == pytest.approx(410, abs=1.5)
    new_anchor = editor.canvas.corners(el)[0]
    assert new_anchor[0] == pytest.approx(anchor[0], abs=0.5) and new_anchor[1] == pytest.approx(anchor[1], abs=0.5)


def test_rotate_handle(view):
    editor = _open_new(view)
    editor.add_shape("rect")
    el = editor.selected_element()
    canvas = editor.canvas
    hx, hy = canvas.to_slide(*canvas._rotate_handle(el))
    cx, cy = el.center
    radius = math.hypot(hx - cx, hy - cy)
    _drag(canvas, (hx, hy), (cx + radius, cy))  # handle to the right of center = 90°
    assert editor.selected_element().rotation == pytest.approx(90)


def test_inspector_text_edit_and_style(view):
    editor = _open_new(view)
    editor.add_text("body")
    textbox = editor.inspector._fields["text"]
    textbox.delete("1.0", "end")
    textbox.insert("1.0", "Rudens *odos* priežiūra")
    editor.inspector._text_changed()
    assert editor.selected_element().props["text"] == "Rudens *odos* priežiūra"
    editor.apply_text_style("heading")
    assert editor.selected_element().props["size"] == 96
    editor.update_selected(color="#FF0000")
    assert editor.selected_element().props["color"] == "#FF0000"


def test_keyboard_shortcuts(view):
    editor = _open_new(view)
    editor.add_shape("ellipse")
    el_id = editor.selected_id
    count = len(editor.current_slide().elements)
    editor.duplicate_selected()
    assert len(editor.current_slide().elements) == count + 1
    editor.copy_selected()
    editor.open_slide(1)
    editor.paste()
    assert any(e.props.get("shape") == "ellipse" for e in editor.current_slide().elements)
    x = editor.selected_element().x
    editor.nudge(1, 0, big=True)
    assert editor.selected_element().x == pytest.approx(x + 10)
    editor.delete_selected()
    assert editor.selected_id is None
    editor.open_slide(0)
    assert editor.current_slide().find(el_id) is not None


def test_slide_strip_add_duplicate_delete_and_reorder(view):
    editor = _open_new(view, count="3")
    first = editor.doc.slides[0].id
    editor.add_slide()
    assert len(editor.doc.slides) == 4 and editor.slide_index == 1
    editor.duplicate_slide()
    assert len(editor.doc.slides) == 5
    editor.delete_slide()
    assert len(editor.doc.slides) == 4
    strip = editor.strip
    strip.redraw()
    a0, a1 = strip._slots[0]
    b0, b1 = strip._slots[2]
    strip._drag = {"index": 0, "x": (a0 + a1) / 2, "moved": True}
    strip._release(SimpleNamespace(x=(b1 + strip._slots[3][0]) / 2 + 2))
    assert editor.doc.slides[2].id == first


def test_cannot_go_below_two_slides(view, monkeypatch):
    shown = []
    editor = _open_new(view, count="2")
    monkeypatch.setattr(editor, "_error", lambda exc: shown.append(str(exc)))
    editor.delete_slide()
    assert len(editor.doc.slides) == 2 and shown


def test_palette_and_background_from_inspector(view):
    editor = _open_new(view)
    editor.set_palette("autumn")
    assert editor.doc.project.theme.palette_key == "autumn"
    editor.update_background(type="gradient")
    editor.background_to_all()
    assert all(s.background["type"] == "gradient" for s in editor.doc.slides)


def test_autosave_persists_changes(view, root):
    editor = _open_new(view)
    editor.add_text("caption")
    editor.update_selected(text="Išsaugota automatiškai")
    editor.flush_save()
    loaded = storage.load_project(editor.doc.project.id)
    assert any(e.props.get("text") == "Išsaugota automatiškai" for e in loaded.slides[0].elements)


def test_format_switch_from_top_bar(view):
    editor = _open_new(view)
    editor._format_chosen("Kvadratas 1:1 (1080 × 1080)")
    assert editor.doc.project.format == "square"
    editor.undo()
    assert editor.doc.project.format == "portrait"
