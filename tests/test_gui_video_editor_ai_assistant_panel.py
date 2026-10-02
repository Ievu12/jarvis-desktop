"""Tests for jarvis.gui.views.video_editor.ai_assistant_panel
.AiAssistantPanel and the owning dashboard's AI-proposal preview/apply
wiring (Stage 5 of the "professional Reels editor" plan) - the
LLMClient is always mocked/stubbed, no real API calls anywhere."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import customtkinter as ctk
import pytest

from jarvis.gui.views.video_editor.ai_assistant_panel import AiAssistantPanel
from jarvis.gui.views.video_editor.dashboard import VideoEditorView
from jarvis.gui.worker import GenerationTaskResult
from jarvis.video_editor.ai_assistant import AiReelProposal, StickerSuggestion
from jarvis.video_editor.captions import CaptionStyle
from jarvis.video_editor.effects import EffectSpec
from jarvis.video_editor.media_import import MediaItem


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


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


_PROPOSAL = AiReelProposal(
    effect=EffectSpec(motion="zoom_in", motion_intensity=1.2),
    caption_style=CaptionStyle(position="bottom", animation="pop"),
    text_template_name="Bold Reveal",
    suggested_caption_text="Breathe in, breathe out.",
    music_mood_suggestion="soft ambient piano",
    stickers=(StickerSuggestion(shape="lotus", animation="fade_in_out", x_fraction=0.5, y_fraction=0.85),),
)


# --- AiAssistantPanel on its own --------------------------------------------------


def test_propose_without_a_brief_shows_an_error_without_calling_back(root):
    proposed = []
    panel = AiAssistantPanel(root, on_propose_requested=proposed.append, on_apply_requested=lambda p: None)
    panel._on_propose_clicked()
    assert proposed == []


def test_entering_a_brief_and_clicking_propose_fires_the_callback(root):
    proposed = []
    panel = AiAssistantPanel(root, on_propose_requested=proposed.append, on_apply_requested=lambda p: None)
    panel._brief_entry.insert(0, "calm yoga reel")
    panel._on_propose_clicked()
    assert proposed == ["calm yoga reel"]


def test_show_proposal_renders_a_real_preview_with_apply_and_discard(root):
    panel = AiAssistantPanel(root, on_propose_requested=lambda b: None, on_apply_requested=lambda p: None)
    panel.show_proposal(_PROPOSAL)

    labels = [w.cget("text") for w in _walk(panel) if isinstance(w, ctk.CTkLabel)]
    assert any("zoom_in" in text for text in labels)
    assert any("Breathe in, breathe out." in text for text in labels)

    buttons = [w.cget("text") for w in _walk(panel) if isinstance(w, ctk.CTkButton)]
    assert "✅ Apply" in buttons
    assert "✖ Discard" in buttons


def test_clicking_apply_fires_on_apply_requested_with_the_shown_proposal(root):
    applied = []
    panel = AiAssistantPanel(root, on_propose_requested=lambda b: None, on_apply_requested=applied.append)
    panel.show_proposal(_PROPOSAL)

    apply_buttons = [w for w in _walk(panel) if isinstance(w, ctk.CTkButton) and w.cget("text") == "✅ Apply"]
    assert apply_buttons
    apply_buttons[0].invoke()
    assert applied == [_PROPOSAL]


def test_clicking_discard_clears_the_preview_without_applying(root):
    applied = []
    panel = AiAssistantPanel(root, on_propose_requested=lambda b: None, on_apply_requested=applied.append)
    panel.show_proposal(_PROPOSAL)

    discard_buttons = [w for w in _walk(panel) if isinstance(w, ctk.CTkButton) and w.cget("text") == "✖ Discard"]
    assert discard_buttons
    discard_buttons[0].invoke()
    assert applied == []
    assert len(panel._preview_container.winfo_children()) == 0


def test_show_error_displays_the_message(root):
    panel = AiAssistantPanel(root, on_propose_requested=lambda b: None, on_apply_requested=lambda p: None)
    panel.show_error("something went wrong")
    assert "something went wrong" in panel._status.cget("text")


# --- dashboard wiring --------------------------------------------------------------


def _status_text(view) -> str:
    children = view._status_container.winfo_children()
    return children[0].cget("text") if children else ""


def test_propose_without_an_llm_configured_shows_an_error_immediately(root):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_ai_propose_requested("calm yoga reel")
    assert "not available" in view._ai_assistant_panel._status.cget("text").lower()


def test_propose_with_an_llm_runs_in_the_background_and_shows_the_result(root, monkeypatch):
    import jarvis.gui.views.video_editor.dashboard as dashboard_module

    monkeypatch.setattr(dashboard_module, "propose_reel_style", lambda llm, brief: _PROPOSAL)
    view = VideoEditorView(root, llm=MagicMock(), navigate=lambda k, **kw: None)
    view._on_ai_propose_requested("calm yoga reel")

    import time

    deadline = time.time() + 5.0
    while time.time() < deadline and len(view._ai_assistant_panel._preview_container.winfo_children()) == 0:
        view.update()
    assert len(view._ai_assistant_panel._preview_container.winfo_children()) > 0


def test_applying_proposal_without_an_open_project_shows_an_error(root):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_apply_ai_proposal(_PROPOSAL)
    assert "project" in _status_text(view).lower()


def test_applying_proposal_without_timeline_items_shows_an_error(root):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()
    view._on_apply_ai_proposal(_PROPOSAL)
    assert "clip" in _status_text(view).lower() or "photo" in _status_text(view).lower()


def test_applying_proposal_sets_timeline_effect_and_caption_style(root):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()
    media = MediaItem(
        media_item_id="m1", original_filename="clip.mp4", stored_path=Path("clip.mp4"),
        kind="video", duration_seconds=3.0, width=640, height=360, fps=30.0,
    )
    view._media_items["m1"] = media
    view._timeline_panel.add_clip(media)

    view._on_apply_ai_proposal(_PROPOSAL)

    assert view._timeline_panel.timeline.items[0].effect == _PROPOSAL.effect
    assert view._caption_style is not None
    assert view._caption_style.animation == _PROPOSAL.caption_style.animation


def test_applying_proposal_with_stickers_adds_placed_stickers(root):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()
    media = MediaItem(
        media_item_id="m1", original_filename="clip.mp4", stored_path=Path("clip.mp4"),
        kind="video", duration_seconds=3.0, width=640, height=360, fps=30.0,
    )
    view._media_items["m1"] = media
    view._timeline_panel.add_clip(media)

    before = len(view._stickers)
    view._on_apply_ai_proposal(_PROPOSAL)
    assert len(view._stickers) == before + len(_PROPOSAL.stickers)


def test_applying_a_proposal_is_undoable_on_the_timeline(root):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()
    media = MediaItem(
        media_item_id="m1", original_filename="clip.mp4", stored_path=Path("clip.mp4"),
        kind="video", duration_seconds=3.0, width=640, height=360, fps=30.0,
    )
    view._media_items["m1"] = media
    view._timeline_panel.add_clip(media)
    original_effect = view._timeline_panel.timeline.items[0].effect

    view._on_apply_ai_proposal(_PROPOSAL)
    assert view._timeline_panel.timeline.items[0].effect == _PROPOSAL.effect

    view._timeline_panel._on_undo_clicked()
    assert view._timeline_panel.timeline.items[0].effect == original_effect
