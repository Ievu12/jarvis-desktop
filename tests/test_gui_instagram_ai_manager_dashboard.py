"""Tests for jarvis.gui.views.instagram_ai_manager.dashboard
.InstagramAIManagerView: the top-level view shown for the sidebar's "📱
Instagram AI Manager" nav item. Uses a real (withdrawn) CTk root; no
real LLM/Anthropic call is made since these tests only check widget
construction, not generation (Content Studio's 7 tool panels already
have their own, separately-run, real-API verification - this file
covers the dashboard shell itself: that it builds with/without an
LLMClient, exposes Content Studio, and satisfies the refresh() contract
jarvis.gui.app._navigate() calls on every panel).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import customtkinter as ctk
import pytest

from jarvis.gui.views.instagram_ai_manager.content_studio import ContentStudioView
from jarvis.gui.views.instagram_ai_manager.dashboard import InstagramAIManagerView


@pytest.fixture(scope="module")
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


def test_builds_with_llm(root):
    view = InstagramAIManagerView(root, llm=MagicMock())
    assert isinstance(view.content_studio, ContentStudioView)


def test_builds_without_llm():
    # No ANTHROPIC_API_KEY configured -> JarvisApp.llm is None; the
    # dashboard (and everything under it) must still construct without
    # raising, matching how every generator panel already treats a
    # missing LLMClient as a disabled-but-visible state, not a crash.
    r = ctk.CTk()
    r.withdraw()
    try:
        view = InstagramAIManagerView(r, llm=None)
        assert view.content_studio is not None
    finally:
        r.destroy()


def test_refresh_does_not_raise(root):
    view = InstagramAIManagerView(root, llm=MagicMock())
    view.refresh()  # must be a no-op, not an error - see its own docstring


def test_has_content_studio_and_analytics_tabs(root):
    view = InstagramAIManagerView(root, llm=MagicMock())
    tab_children = [
        child for child in view.winfo_children() if isinstance(child, ctk.CTkTabview)
    ]
    assert len(tab_children) == 1
    tabview = tab_children[0]
    assert set(tabview._tab_dict.keys()) == {"Content Studio", "Analytics"}
