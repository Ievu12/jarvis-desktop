"""The Reels mode in the real Video Editor view: a new Reels project,
subtitles from typed text and from (stubbed) speech recognition, key
words, presets, dragging in the preview, undo, reopening, and a real
export with the Reels layer."""

from __future__ import annotations

import queue
import subprocess
import time

import customtkinter as ctk
import pytest
from PIL import Image, ImageChops

from jarvis.gui.views.video_editor import dashboard as dashboard_module
from jarvis.gui.views.video_editor.dashboard import VideoEditorView
from jarvis.video_editor import db, reels, storage
from jarvis.video_editor.captions import WordTiming
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    db_file = tmp_path / "video_editor.db"
    projects_dir = tmp_path / "video_editor_projects"
    monkeypatch.setattr(db, "VIDEO_EDITOR_DB_FILE", db_file)
    monkeypatch.setattr(storage, "VIDEO_EDITOR_PROJECTS_DIR", projects_dir)


@pytest.fixture(scope="module")
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture(autouse=True)
def _destroy_child_views(root):
    yield
    for child in list(root.winfo_children()):
        child.destroy()


def _drain_until(view, predicate, *, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = view._result_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        view._handle_result(result)
        if predicate():
            return
    raise AssertionError("Timed out waiting for the expected queue result")


def _reels_view(root, tmp_path, *, seconds: int = 4):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_reels_project_clicked()
    clip_path = tmp_path / "talk.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x335577:s=540x960:r=30:d={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(clip_path)],
        check=True,
    )
    view._on_files_chosen([clip_path])
    _drain_until(view, lambda: len(view._media_items) == 1)
    view._on_add_to_timeline_clicked(next(iter(view._media_items.values())))
    root.update()
    return view


def test_new_reels_project_is_vertical_and_opens_the_reels_tools(root):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_reels_project_clicked()
    root.update()
    assert view._timeline_panel.timeline.aspect_ratio == "9:16"
    assert view._active_category == "reels"
    assert view._reels_panel.winfo_ismapped()
    assert view._reels is not None and view._reels.captions is not None
    assert db.get_project(view._current_project.project_id).name.startswith("Reels")
    canvas, _frame = view._preview_formats()
    assert (canvas.width, canvas.height) == (1080, 1920)


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_typed_subtitles_presets_drag_undo_and_reopen(root, tmp_path):
    view = _reels_view(root, tmp_path)
    panel = view._reels_panel
    panel._typed_text.insert("1.0", "Labas čia JARVIS ir automatizacija")
    panel._create_from_text()
    root.update()
    words = view._reels.captions.words
    assert [w.text for w in words] == ["Labas", "čia", "JARVIS", "ir", "automatizacija"]
    assert words[-1].end_seconds == pytest.approx(4.0, abs=0.01)
    assert view._track_timeline._bars["reels_captions"]

    panel._keywords_entry.insert(0, "jarvis")
    panel._emphasize_entry()
    assert [w.text for w in view._reels.captions.words if w.emphasized] == ["JARVIS"]

    panel._apply_preset("Hormozi")
    assert view._reels.captions.style.active_effect == "box"

    # Dragging the subtitles in the preview moves them up the screen.
    captions = view._reels.captions
    import dataclasses

    moved = dataclasses.replace(captions, style=dataclasses.replace(captions.style, y_fraction=0.3))
    view._on_element_edited("reels_caption", 0, moved, False)
    view._on_element_edited("reels_caption", 0, moved, True)
    assert view._reels.captions.style.y_fraction == 0.3

    view._on_undo()
    assert view._reels.captions.style.y_fraction != 0.3
    view._on_redo()
    assert view._reels.captions.style.y_fraction == 0.3

    view._save_overlays_now()
    project_id = view._current_project.project_id
    reopened = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    reopened._open_project(project_id)
    assert reopened._reels == view._reels
    assert reopened._reels_panel._captions == view._reels.captions


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_speech_recognition_fills_the_subtitles_and_marks_key_words(root, tmp_path, monkeypatch):
    view = _reels_view(root, tmp_path)
    heard = {}

    def fake_word_timings(path, *, language):
        heard["path"], heard["language"] = path, language
        assert path.is_file()  # the timeline's own sound was rendered for recognition
        return [WordTiming(0.2, 0.6, " Sveiki"), WordTiming(0.6, 1.0, " čia"), WordTiming(1.0, 1.6, " Jarvis"),
                WordTiming(2.0, 2.8, " automatizacija")]

    monkeypatch.setattr(dashboard_module, "generate_word_timings", fake_word_timings)
    view._reels_panel._transcribe()
    _drain_until(view, lambda: bool(view._reels.captions.words))
    assert heard["language"] == "lt"
    assert not heard["path"].exists()  # cleaned up
    words = view._reels.captions.words
    assert [w.start_seconds for w in words] == [0.2, 0.6, 1.0, 2.0]
    assert {w.text for w in words if w.emphasized} >= {"Jarvis", "automatizacija"}
    assert "Atpažinta 4" in view._reels_panel._status.cget("text")


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_export_burns_in_the_reels_subtitles(root, tmp_path):
    view = _reels_view(root, tmp_path, seconds=3)
    panel = view._reels_panel
    panel._typed_text.insert("1.0", "Vienas du trys")
    panel._create_from_text()
    view._on_export_clicked("720p")
    _drain_until(view, lambda: view._cancel_event is None and view._export_panel._last_result is not None)
    result = view._export_panel._last_result
    assert (result.width, result.height) == (720, 1280)
    assert not list(view._current_project.exports_dir.glob("_reels_*.mov"))

    frame_path = tmp_path / "frame.png"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", "1.5", "-i", str(result.output_path),
                    "-frames:v", "1", str(frame_path)], check=True)
    with Image.open(frame_path) as frame:
        lower = frame.convert("RGB").crop((0, 800, 720, 1150))
    plain = Image.new("RGB", lower.size, (0x33, 0x55, 0x77))
    assert ImageChops.difference(lower, plain).convert("L").point(lambda v: 255 if v > 80 else 0).getbbox()


# --- stage 2: text cards and picture/video inserts ---------------------------------------------------------


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_cards_and_inserts_from_the_reels_tabs_preview_timeline_undo_and_reopen(root, tmp_path):
    import dataclasses

    view = _reels_view(root, tmp_path)
    view._reels_tabs.show_tab("Kortelės")
    root.update()
    cards = view._reels_cards_panel
    assert cards.winfo_ismapped() and not view._reels_panel.winfo_ismapped()

    view._on_preview_seek(1.0)
    cards._add_card("STORYTELLING")
    cards._text_entry.insert(0, "Mano žodis")
    cards._add_own()
    root.update()
    assert [c.text for c in view._reels.cards] == ["STORYTELLING", "Mano žodis"]
    first, second = view._reels.cards
    assert first.start_seconds == pytest.approx(1.0, abs=0.05) and second.y_fraction > first.y_fraction
    assert len(view._track_timeline._bars["reels_cards"]) == 2
    assert view._selection == ("reels_card", 1)

    cards._apply_design("neon")
    assert view._reels.cards[1].design == "neon"

    # Dragging the card in the preview moves it; undo puts it back.
    moved = dataclasses.replace(view._reels.cards[0], x_fraction=0.3, y_fraction=0.6, rotation_degrees=-8)
    view._on_element_edited("reels_card", 0, moved, False)
    view._on_element_edited("reels_card", 0, moved, True)
    assert view._reels.cards[0] == moved
    view._on_undo()
    assert view._reels.cards[0].x_fraction != 0.3
    view._on_redo()
    assert view._reels.cards[0] == moved

    # Picture insert: the file is copied into the project.
    photo = tmp_path / "product.png"
    Image.new("RGB", (600, 400), (200, 30, 80)).save(photo)
    view._reels_tabs.show_tab("Intarpai")
    view._on_reels_insert_file_chosen(str(photo))
    root.update()
    insert = view._reels.inserts[0]
    assert insert.kind == "image" and insert.aspect_ratio == pytest.approx(1.5)
    assert insert.path != str(photo) and str(view._current_project.media_dir) in insert.path
    assert view._track_timeline._bars["reels_inserts"]
    assert view._selection == ("reels_insert", 0)

    # Picking a card on the timeline opens its tab and selects it.
    view._on_track_selection_changed(("reels_cards", 0))
    root.update()
    assert view._reels_tabs.current == "Kortelės" and cards.selected == 0
    assert view._reels_inserts_panel.selected is None

    # The Delete key in the preview removes the selected insert.
    view._on_element_delete_requested("reels_insert", 0)
    assert view._reels.inserts == ()
    view._on_undo()
    assert len(view._reels.inserts) == 1

    view._save_overlays_now()
    reopened = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    reopened._open_project(view._current_project.project_id)
    assert reopened._reels == view._reels
    assert reopened._reels_cards_panel._items == view._reels.cards
    assert reopened._reels_inserts_panel._items == view._reels.inserts


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_export_burns_in_a_card(root, tmp_path):
    view = _reels_view(root, tmp_path, seconds=3)
    view._on_preview_seek(0.0)
    view._reels_cards_panel._add_card("JARVIS")
    view._on_export_clicked("720p")
    _drain_until(view, lambda: view._cancel_event is None and view._export_panel._last_result is not None)
    result = view._export_panel._last_result
    frame_path = tmp_path / "frame.png"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", "1.5", "-i", str(result.output_path),
                    "-frames:v", "1", str(frame_path)], check=True)
    with Image.open(frame_path) as frame:
        top = frame.convert("RGB").crop((0, 80, 720, 280))
    plain = Image.new("RGB", top.size, (0x33, 0x55, 0x77))
    assert ImageChops.difference(top, plain).convert("L").point(lambda v: 255 if v > 80 else 0).getbbox()
