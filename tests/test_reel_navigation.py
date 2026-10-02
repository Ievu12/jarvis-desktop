"""Tests for jarvis.tools.reel_navigation: the small, settable "please
navigate the GUI to this Reel project" signal (same pattern as
jarvis.core.approval's own settable side-effect handler). Confirms:
a request is returned exactly once (consuming clears it), no request
returns None, and a second request overwrites an unconsumed first one
(only the most recent navigation matters)."""

from __future__ import annotations

import pytest

from jarvis.tools import reel_navigation


@pytest.fixture(autouse=True)
def _clear_pending_navigation():
    """reel_navigation's own _pending_project_id is a plain MODULE-LEVEL
    global (see that module's own docstring for why - the same
    lightweight settable-signal pattern jarvis.core.approval uses),
    shared by every test in this process, including
    tests/test_reel_chat_tool.py (a real, separate test file that also
    calls request_navigation()/consume_navigation_request()). A real,
    hand-observed flake: if a test in THAT file raises before reaching
    its own consume_navigation_request() call (e.g. an earlier assert in
    the same test fails first), the pending request is left set and
    leaks into whichever test in THIS file happens to run next in the
    same session - consumed here unconditionally before/after every test
    so this file's own tests are never order-dependent on another file's
    test outcomes."""
    reel_navigation.consume_navigation_request()
    yield
    reel_navigation.consume_navigation_request()


def test_no_pending_request_returns_none():
    assert reel_navigation.consume_navigation_request() is None


def test_request_is_returned_once_then_cleared():
    reel_navigation.request_navigation("proj-123")
    assert reel_navigation.consume_navigation_request() == "proj-123"
    assert reel_navigation.consume_navigation_request() is None


def test_second_request_overwrites_first_unconsumed_one():
    reel_navigation.request_navigation("proj-first")
    reel_navigation.request_navigation("proj-second")
    assert reel_navigation.consume_navigation_request() == "proj-second"
    assert reel_navigation.consume_navigation_request() is None
