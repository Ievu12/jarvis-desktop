"""Tests for jarvis.gui.sidebar.Sidebar: pure UI navigation component,
no JARVIS core/backend logic of its own. Uses a real (withdrawn) CTk
root, module-scoped per the pattern established in
test_gui_confirmation_dialog.py/test_gui_app.py (creating many CTk()
roots in quick succession has been observed to intermittently exhaust
the Windows Tcl interpreter - see those files' docstrings)."""

from __future__ import annotations

import customtkinter as ctk
import pytest

from jarvis.gui.sidebar import NAV_ITEMS, Sidebar


@pytest.fixture(scope="module")
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


def test_sidebar_creates_a_button_for_every_nav_item(root):
    sidebar = Sidebar(root, on_navigate=lambda k: None)
    assert set(sidebar._nav_buttons.keys()) == {key for key, _, _ in NAV_ITEMS}
    sidebar.destroy()


def test_clicking_a_nav_item_calls_on_navigate_with_its_key(root):
    clicked = []
    sidebar = Sidebar(root, on_navigate=lambda k: clicked.append(k))
    sidebar._handle_click("tasks")
    assert clicked == ["tasks"]
    sidebar.destroy()


def test_clicking_updates_the_active_highlight(root):
    sidebar = Sidebar(root, on_navigate=lambda k: None)
    sidebar._handle_click("gmail")
    assert sidebar._active_key == "gmail"
    sidebar.destroy()


def test_set_active_changes_highlight_without_calling_on_navigate(root):
    called = []
    sidebar = Sidebar(root, on_navigate=lambda k: called.append(k))
    sidebar.set_active("stripe")
    assert sidebar._active_key == "stripe"
    assert called == []
    sidebar.destroy()


def test_set_active_with_unknown_key_does_not_raise(root):
    sidebar = Sidebar(root, on_navigate=lambda k: None)
    sidebar.set_active("nonexistent")  # must not raise
    sidebar.destroy()


def test_default_active_item_is_home(root):
    sidebar = Sidebar(root, on_navigate=lambda k: None)
    assert sidebar._active_key == "home"
    sidebar.destroy()


# --- status indicator ---------------------------------------------------------------


def test_set_status_online_shows_online_text(root):
    sidebar = Sidebar(root, on_navigate=lambda k: None)
    sidebar.set_status("ONLINE")
    assert sidebar._status_label.cget("text") == "ONLINE"
    sidebar.destroy()


@pytest.mark.parametrize("status", ["THINKING", "WORKING", "WAITING", "ERROR"])
def test_set_status_known_values_show_matching_text(root, status):
    sidebar = Sidebar(root, on_navigate=lambda k: None)
    sidebar.set_status(status)
    assert sidebar._status_label.cget("text") == status
    sidebar.destroy()


def test_set_status_unknown_value_does_not_raise(root):
    sidebar = Sidebar(root, on_navigate=lambda k: None)
    sidebar.set_status("SOMETHING_UNEXPECTED")  # must not raise
    assert sidebar._status_label.cget("text") == "SOMETHING_UNEXPECTED"
    sidebar.destroy()


def test_set_status_stops_previous_pulse_before_starting_a_new_one(root):
    sidebar = Sidebar(root, on_navigate=lambda k: None)
    sidebar.set_status("ONLINE")
    first_stop = sidebar._stop_pulse
    assert first_stop is not None
    sidebar.set_status("THINKING")
    # THINKING has no pulse - confirm the ONLINE pulse was stopped, not left running.
    assert sidebar._stop_pulse is None
    sidebar.destroy()


def test_sidebar_construction_and_destruction_does_not_raise(root):
    # A broader smoke test: build, let a few .after() ticks run (the
    # ONLINE pulse schedules itself repeatedly), then destroy - the
    # pulse's own winfo_exists() check must prevent a stale callback
    # from raising after destroy().
    sidebar = Sidebar(root, on_navigate=lambda k: None)
    for _ in range(5):
        root.update()
    sidebar.destroy()
    for _ in range(5):
        root.update()  # let any already-scheduled callback fire against the destroyed widget
