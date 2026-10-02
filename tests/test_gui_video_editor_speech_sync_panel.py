"""Tests for jarvis.gui.views.video_editor.speech_sync_panel.SpeechSyncPanel:
a pure search-UI widget over jarvis.video_editor.speech_sync's own
real, synchronous text matching (no ffmpeg call of its own)."""

from __future__ import annotations

import customtkinter as ctk
import pytest

from jarvis.gui.views.video_editor.speech_sync_panel import SpeechSyncPanel
from jarvis.video_editor.captions import WordTiming


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


_WORDS = [
    WordTiming(start_seconds=0.0, end_seconds=0.3, text="This"),
    WordTiming(start_seconds=0.3, end_seconds=0.5, text="is"),
    WordTiming(start_seconds=0.5, end_seconds=0.6, text="a"),
    WordTiming(start_seconds=0.6, end_seconds=1.0, text="great"),
    WordTiming(start_seconds=1.0, end_seconds=1.5, text="offer"),
]


def test_constructs_with_no_results(root):
    panel = SpeechSyncPanel(root, get_words=lambda: [])
    assert len(panel._results_container.winfo_children()) == 0


def test_search_with_no_words_shows_a_helpful_error(root):
    panel = SpeechSyncPanel(root, get_words=lambda: [])
    panel._phrase_entry.insert(0, "great offer")
    panel._on_search_clicked()
    children = panel._results_container.winfo_children()
    assert len(children) == 1
    assert "Generate & Edit Subtitles" in children[0].cget("text")


def test_search_finds_a_real_match(root):
    panel = SpeechSyncPanel(root, get_words=lambda: _WORDS)
    panel._phrase_entry.insert(0, "great offer")
    panel._on_search_clicked()
    children = panel._results_container.winfo_children()
    assert len(children) == 1
    assert "0.60s" in children[0].cget("text")
    assert "1.50s" in children[0].cget("text")


def test_search_with_no_match_shows_a_message(root):
    panel = SpeechSyncPanel(root, get_words=lambda: _WORDS)
    panel._phrase_entry.insert(0, "nonexistent phrase")
    panel._on_search_clicked()
    children = panel._results_container.winfo_children()
    assert len(children) == 1
    assert "not found" in children[0].cget("text")


def test_search_with_empty_phrase_does_nothing(root):
    panel = SpeechSyncPanel(root, get_words=lambda: _WORDS)
    panel._on_search_clicked()
    assert len(panel._results_container.winfo_children()) == 0


def test_search_uses_current_words_not_stale_ones(root):
    current_words = []
    panel = SpeechSyncPanel(root, get_words=lambda: current_words)
    panel._phrase_entry.insert(0, "great")
    panel._on_search_clicked()
    assert "Generate & Edit Subtitles" in panel._results_container.winfo_children()[0].cget("text")

    current_words.extend(_WORDS)
    panel._on_search_clicked()
    children = panel._results_container.winfo_children()
    assert "0.60s" in children[0].cget("text")
