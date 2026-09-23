"""Tests for jarvis.gui.confirmation_dialog.AskUserConfirmation: the GUI
replacement for the terminal y/N approval prompt.

customtkinter's CTk.mainloop() on Windows touches focus_get(), which
Tcl/Tk restricts to the exact OS thread that owns the interpreter - more
strictly than plain Tkinter's usual "creator thread" rule - making a
real cross-thread mainloop()-driven test impractical here (confirmed:
it raises "main thread is not in main loop" from inside customtkinter's
own titlebar-color code, not from application code). So these tests
exercise AskUserConfirmation's actual logic (the threading.Event
handoff, exactly-once dialog construction, Allow/Deny/close-as-deny
outcomes) against a real CTk root synchronously on the main thread,
standing in for root.after(0, ...) with an immediate call - the same
approach jarvis.gui.app's own tests use for widget-level behavior. What
is NOT re-tested here (because it would require the real cross-thread
path) is Tkinter's own thread-safety guarantee for .after() itself -
that is Tkinter's contract, not this module's code.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import customtkinter as ctk
import pytest

from jarvis.gui.confirmation_dialog import AskUserConfirmation


@pytest.fixture(scope="module")
def root():
    # Module-scoped (one Tk root shared across every test in this file)
    # rather than per-test: creating/destroying many CTk() root windows
    # in quick succession has been observed to intermittently exhaust
    # the Windows Tcl interpreter's own resources ("invalid command
    # name", "couldn't read file ...ttk/*.tcl") - a platform/Tcl
    # limitation unrelated to this module's logic (each such failure
    # happens inside Tcl's own interpreter bootstrap, before any
    # application code runs, and re-running in isolation always passes).
    # Sharing one root across this file's tests avoids the churn.
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


def _run_immediately(root_mock_target, func_holder: list) -> None:
    """Patches root.after(0, callback) to invoke `callback` immediately
    and synchronously instead of scheduling it - equivalent to what
    happens once Tk's event loop actually processes the scheduled call,
    but without needing a real running mainloop() (see module docstring
    for why that's impractical on Windows here)."""

    def _fake_after(delay, callback=None, *args):
        if callback is not None:
            func_holder.append(callback)
            callback()
        return "fake-after-id"

    return _fake_after


# --- __call__ (confirm_side_effect handler shape): Allow / Deny --------------------


def test_allow_button_click_returns_true(root):
    confirmation = AskUserConfirmation(root)
    captured_dialogs: list[ctk.CTkToplevel] = []

    real_toplevel = ctk.CTkToplevel

    def _capture_toplevel(*args, **kwargs):
        dialog = real_toplevel(*args, **kwargs)
        captured_dialogs.append(dialog)
        return dialog

    with patch.object(root, "after", side_effect=_run_immediately(root, [])):
        with patch("jarvis.gui.confirmation_dialog.ctk.CTkToplevel", side_effect=_capture_toplevel):
            # _show_and_wait() blocks on threading.Event.wait() - since
            # .after() now runs _show_dialog() synchronously above, the
            # dialog (and its buttons) already exist by the time we get
            # here, so we click Allow before calling the blocking method
            # by pre-wiring a fake .after that also clicks Allow right
            # after building the dialog.
            def _after_and_click(delay, callback=None, *args):
                callback()
                dialog = captured_dialogs[-1]
                _click_button(dialog, "Leisti")
                return "id"

            with patch.object(root, "after", side_effect=_after_and_click):
                result = confirmation("delete a file")

    assert result is True


def test_deny_button_click_returns_false(root):
    confirmation = AskUserConfirmation(root)
    captured_dialogs: list[ctk.CTkToplevel] = []
    real_toplevel = ctk.CTkToplevel

    def _capture_toplevel(*args, **kwargs):
        dialog = real_toplevel(*args, **kwargs)
        captured_dialogs.append(dialog)
        return dialog

    def _after_and_click(delay, callback=None, *args):
        callback()
        _click_button(captured_dialogs[-1], "Atmesti")
        return "id"

    with patch("jarvis.gui.confirmation_dialog.ctk.CTkToplevel", side_effect=_capture_toplevel):
        with patch.object(root, "after", side_effect=_after_and_click):
            result = confirmation("delete a file")

    assert result is False


def test_dialog_message_includes_the_description(root):
    confirmation = AskUserConfirmation(root)
    captured_dialogs: list[ctk.CTkToplevel] = []
    real_toplevel = ctk.CTkToplevel

    def _capture_toplevel(*args, **kwargs):
        dialog = real_toplevel(*args, **kwargs)
        captured_dialogs.append(dialog)
        return dialog

    def _after_and_inspect(delay, callback=None, *args):
        callback()
        dialog = captured_dialogs[-1]
        label_texts = [
            w.cget("text") for w in dialog.winfo_children() if isinstance(w, ctk.CTkLabel)
        ]
        assert any("delete config.py" in t for t in label_texts)
        _click_button(dialog, "Atmesti")
        return "id"

    with patch("jarvis.gui.confirmation_dialog.ctk.CTkToplevel", side_effect=_capture_toplevel):
        with patch.object(root, "after", side_effect=_after_and_inspect):
            confirmation("delete config.py")


# --- for_path (confirm_outside_sandbox handler shape) -------------------------------


def test_for_path_allow_returns_true(root):
    from pathlib import Path

    confirmation = AskUserConfirmation(root)
    captured_dialogs: list[ctk.CTkToplevel] = []
    real_toplevel = ctk.CTkToplevel

    def _capture_toplevel(*args, **kwargs):
        dialog = real_toplevel(*args, **kwargs)
        captured_dialogs.append(dialog)
        return dialog

    def _after_and_click(delay, callback=None, *args):
        callback()
        _click_button(captured_dialogs[-1], "Leisti")
        return "id"

    with patch("jarvis.gui.confirmation_dialog.ctk.CTkToplevel", side_effect=_capture_toplevel):
        with patch.object(root, "after", side_effect=_after_and_click):
            result = confirmation.for_path(Path("/outside/project"))

    assert result is True


def test_for_path_deny_returns_false(root):
    from pathlib import Path

    confirmation = AskUserConfirmation(root)
    captured_dialogs: list[ctk.CTkToplevel] = []
    real_toplevel = ctk.CTkToplevel

    def _capture_toplevel(*args, **kwargs):
        dialog = real_toplevel(*args, **kwargs)
        captured_dialogs.append(dialog)
        return dialog

    def _after_and_click(delay, callback=None, *args):
        callback()
        _click_button(captured_dialogs[-1], "Atmesti")
        return "id"

    with patch("jarvis.gui.confirmation_dialog.ctk.CTkToplevel", side_effect=_capture_toplevel):
        with patch.object(root, "after", side_effect=_after_and_click):
            result = confirmation.for_path(Path("/outside/project"))

    assert result is False


# --- closing the dialog window is treated as a denial --------------------------------


def test_closing_dialog_window_is_treated_as_denial(root):
    confirmation = AskUserConfirmation(root)
    captured_dialogs: list[ctk.CTkToplevel] = []
    real_toplevel = ctk.CTkToplevel

    def _capture_toplevel(*args, **kwargs):
        dialog = real_toplevel(*args, **kwargs)
        captured_dialogs.append(dialog)
        return dialog

    def _after_and_close(delay, callback=None, *args):
        callback()
        dialog = captured_dialogs[-1]
        handler_name = dialog.tk.call("wm", "protocol", dialog._w, "WM_DELETE_WINDOW")
        assert handler_name, "WM_DELETE_WINDOW protocol handler was never set"
        dialog.tk.call(handler_name)
        return "id"

    with patch("jarvis.gui.confirmation_dialog.ctk.CTkToplevel", side_effect=_capture_toplevel):
        with patch.object(root, "after", side_effect=_after_and_close):
            result = confirmation("delete a file")

    assert result is False


def _click_button(dialog: ctk.CTkToplevel, text: str) -> None:
    for frame in dialog.winfo_children():
        for widget in getattr(frame, "winfo_children", lambda: [])():
            if isinstance(widget, ctk.CTkButton) and widget.cget("text") == text:
                command = widget.cget("command")
                assert callable(command), f"button {text!r} has no command"
                command()
                return
    raise AssertionError(f"No button with text {text!r} found in dialog")
