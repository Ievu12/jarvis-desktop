"""Tests for jarvis.gui.views.video_editor.reel_templates_panel
.ReelTemplatesPanel and the owning dashboard's "Apply Reel Template"
wiring (Stage 4 of the "professional Reels editor" plan)."""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk
import pytest
from PIL import Image

from jarvis.gui.views.video_editor.dashboard import VideoEditorView
from jarvis.gui.views.video_editor.reel_templates_panel import ReelTemplatesPanel
from jarvis.video_editor.reel_templates import get_reel_template
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


def test_panel_constructs_and_shows_templates_for_the_default_category(root):
    applied = []
    panel = ReelTemplatesPanel(root, on_template_applied=applied.append)
    assert len(panel._list_container.winfo_children()) >= 1


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def test_switching_category_shows_a_different_set_of_templates(root):
    from jarvis.video_editor.reel_templates import TEMPLATE_CATEGORY_LABELS

    applied = []
    panel = ReelTemplatesPanel(root, on_template_applied=applied.append)
    panel._category_dropdown.set(TEMPLATE_CATEGORY_LABELS["cosmetics_ads"])
    panel._render()
    labels = [
        w.cget("text") for w in _walk(panel)
        if isinstance(w, ctk.CTkLabel)
    ]
    assert "Glow Up" in labels


def test_clicking_apply_template_button_fires_the_callback(root):
    from jarvis.video_editor.reel_templates import TEMPLATE_CATEGORY_LABELS

    applied = []
    panel = ReelTemplatesPanel(root, on_template_applied=applied.append)
    panel._category_dropdown.set(TEMPLATE_CATEGORY_LABELS["yoga_meditation"])
    panel._render()

    def _walk(widget):
        yield widget
        for child in widget.winfo_children():
            yield from _walk(child)

    apply_buttons = [
        w for w in _walk(panel) if isinstance(w, ctk.CTkButton) and w.cget("text") == "✨ Apply Template"
    ]
    assert apply_buttons
    apply_buttons[0].invoke()
    assert len(applied) == 1
    assert applied[0].category == "yoga_meditation"


def _status_text(view) -> str:
    children = view._status_container.winfo_children()
    return children[0].cget("text") if children else ""


def test_applying_template_without_an_open_project_shows_an_error(root):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    template = get_reel_template("Calm Flow")
    view._on_apply_reel_template(template)
    assert "project" in _status_text(view).lower()


def test_applying_template_without_any_timeline_items_shows_an_error(root):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()
    template = get_reel_template("Calm Flow")
    view._on_apply_reel_template(template)
    assert "clip" in _status_text(view).lower() or "photo" in _status_text(view).lower()


def test_applying_template_sets_timeline_aspect_ratio_and_effect(root, tmp_path):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()
    photo_path = tmp_path / "photo.jpg"
    Image.new("RGB", (400, 300), color=(1, 2, 3)).save(photo_path, "JPEG")

    from jarvis.video_editor.media_import import MediaItem

    media = MediaItem(
        media_item_id="m1", original_filename="photo.jpg", stored_path=photo_path,
        kind="photo", duration_seconds=None, width=400, height=300, fps=None,
    )
    view._media_items["m1"] = media
    view._timeline_panel.add_clip(media)

    template = get_reel_template("Square Feed Post")
    view._on_apply_reel_template(template)

    timeline = view._timeline_panel.timeline
    assert timeline.aspect_ratio == "1:1"
    assert timeline.items[0].effect == template.default_effect


def test_applying_template_sets_caption_style_and_enables_captions(root, tmp_path):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()
    from jarvis.video_editor.media_import import MediaItem

    media = MediaItem(
        media_item_id="m1", original_filename="clip.mp4", stored_path=Path("clip.mp4"),
        kind="video", duration_seconds=3.0, width=640, height=360, fps=30.0,
    )
    view._media_items["m1"] = media
    view._timeline_panel.add_clip(media)

    template = get_reel_template("Morning Mantra")
    view._on_apply_reel_template(template)

    assert view._caption_style is not None
    assert view._caption_style.animation == template.caption_style.animation
    assert view._caption_style.position == template.caption_style.position


def test_applying_template_with_stickers_adds_placed_stickers(root):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()
    from jarvis.video_editor.media_import import MediaItem

    media = MediaItem(
        media_item_id="m1", original_filename="clip.mp4", stored_path=Path("clip.mp4"),
        kind="video", duration_seconds=3.0, width=640, height=360, fps=30.0,
    )
    view._media_items["m1"] = media
    view._timeline_panel.add_clip(media)

    template = get_reel_template("Glow Up")
    assert template.sticker_presets  # sanity: this template really has stickers
    before = len(view._stickers)
    view._on_apply_reel_template(template)
    assert len(view._stickers) == before + len(template.sticker_presets)


def test_applying_a_template_is_undoable_on_the_timeline(root, tmp_path):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()
    from jarvis.video_editor.media_import import MediaItem

    media = MediaItem(
        media_item_id="m1", original_filename="clip.mp4", stored_path=Path("clip.mp4"),
        kind="video", duration_seconds=3.0, width=640, height=360, fps=30.0,
    )
    view._media_items["m1"] = media
    view._timeline_panel.add_clip(media)
    original_effect = view._timeline_panel.timeline.items[0].effect

    template = get_reel_template("Cozy Autumn")
    view._on_apply_reel_template(template)
    assert view._timeline_panel.timeline.items[0].effect == template.default_effect

    view._timeline_panel._on_undo_clicked()
    assert view._timeline_panel.timeline.items[0].effect == original_effect
