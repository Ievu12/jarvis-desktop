"""Tests for jarvis.core.approval's swappable handler mechanism
(set_side_effect_handler/set_outside_sandbox_handler), added so
jarvis.gui.app can redirect approval prompts to a GUI dialog instead of
terminal input() - without jarvis.tools.fs/shell (which import
confirm_side_effect/confirm_outside_sandbox by name) needing any change,
since the redirection happens inside those two public functions rather
than by rebinding the imported names. Confirms: a custom handler is used
when set, the default terminal behavior is restored by passing None,
audit logging still happens exactly once per call (not duplicated,
not skipped) for both the custom-handler and terminal paths, and a
KeyboardInterrupt from a custom handler still propagates and is still
logged as an interrupted/denied event."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from jarvis.core import approval
from jarvis.core.approval import (
    confirm_outside_sandbox,
    confirm_side_effect,
    set_outside_sandbox_handler,
    set_side_effect_handler,
)


@pytest.fixture(autouse=True)
def _restore_default_handlers():
    """Every test in this file swaps a handler - always reset back to
    the terminal default afterward so no test here leaks a custom
    handler into a later, unrelated test module."""
    yield
    set_side_effect_handler(None)
    set_outside_sandbox_handler(None)


# --- custom handler is used when set --------------------------------------------


def test_confirm_side_effect_uses_custom_handler_when_set():
    custom_handler = lambda description: True  # noqa: E731
    set_side_effect_handler(custom_handler)
    with patch("builtins.input") as mock_input:
        result = confirm_side_effect("delete a file")
    assert result is True
    mock_input.assert_not_called()  # never fell through to terminal input()


def test_confirm_side_effect_custom_handler_can_deny():
    set_side_effect_handler(lambda description: False)
    assert confirm_side_effect("delete a file") is False


def test_confirm_side_effect_custom_handler_receives_the_description():
    received = []
    set_side_effect_handler(lambda description: received.append(description) or True)
    confirm_side_effect("write to config.py")
    assert received == ["write to config.py"]


def test_confirm_outside_sandbox_uses_custom_handler_when_set():
    set_outside_sandbox_handler(lambda path: True)
    with patch("builtins.input") as mock_input:
        result = confirm_outside_sandbox(Path("/some/outside/path"))
    assert result is True
    mock_input.assert_not_called()


def test_confirm_outside_sandbox_custom_handler_receives_the_path():
    received = []
    set_outside_sandbox_handler(lambda path: received.append(path) or True)
    target = Path("/outside/path")
    confirm_outside_sandbox(target)
    assert received == [target]


# --- passing None restores the default terminal behavior -----------------------


def test_set_side_effect_handler_none_restores_terminal_input():
    set_side_effect_handler(lambda description: True)
    set_side_effect_handler(None)
    with patch("builtins.input", return_value="y") as mock_input:
        result = confirm_side_effect("do something")
    assert result is True
    mock_input.assert_called_once()


def test_set_outside_sandbox_handler_none_restores_terminal_input():
    set_outside_sandbox_handler(lambda path: True)
    set_outside_sandbox_handler(None)
    with patch("builtins.input", return_value="y") as mock_input:
        confirm_outside_sandbox(Path("/x"))
    mock_input.assert_called_once()


# --- audit logging still happens exactly once, for both paths ------------------


def test_confirm_side_effect_logs_exactly_once_with_custom_handler():
    set_side_effect_handler(lambda description: True)
    with patch("jarvis.core.approval.log_event") as mock_log:
        confirm_side_effect("do something")
    mock_log.assert_called_once_with(
        "side_effect_confirmation", description="do something", approved=True
    )


def test_confirm_side_effect_logs_exactly_once_with_terminal_handler():
    with patch("builtins.input", return_value="y"):
        with patch("jarvis.core.approval.log_event") as mock_log:
            confirm_side_effect("do something")
    mock_log.assert_called_once_with(
        "side_effect_confirmation", description="do something", approved=True
    )


def test_confirm_outside_sandbox_logs_exactly_once_with_custom_handler():
    set_outside_sandbox_handler(lambda path: False)
    target = Path("/x")
    with patch("jarvis.core.approval.log_event") as mock_log:
        confirm_outside_sandbox(target)
    mock_log.assert_called_once_with(
        "outside_sandbox_request", path=str(target), approved=False
    )


# --- KeyboardInterrupt from a custom handler still propagates and is logged -----


def test_keyboard_interrupt_from_custom_side_effect_handler_propagates():
    def _raising_handler(description):
        raise KeyboardInterrupt()

    set_side_effect_handler(_raising_handler)
    with pytest.raises(KeyboardInterrupt):
        confirm_side_effect("do something")


def test_keyboard_interrupt_from_custom_side_effect_handler_is_logged_as_denied():
    def _raising_handler(description):
        raise KeyboardInterrupt()

    set_side_effect_handler(_raising_handler)
    with patch("jarvis.core.approval.log_event") as mock_log:
        with pytest.raises(KeyboardInterrupt):
            confirm_side_effect("do something")
    mock_log.assert_called_once_with(
        "side_effect_confirmation",
        description="do something",
        approved=False,
        interrupted=True,
    )


def test_keyboard_interrupt_from_terminal_input_still_logged_exactly_once():
    # Regression guard: the refactor that introduced swappable handlers
    # must not cause the terminal path to log the interrupted event
    # twice (once inside the old inline handler, once in the wrapper).
    with patch("builtins.input", side_effect=KeyboardInterrupt):
        with patch("jarvis.core.approval.log_event") as mock_log:
            with pytest.raises(KeyboardInterrupt):
                confirm_side_effect("do something")
    assert mock_log.call_count == 1


# --- the module's default handlers are the original terminal functions ---------


def test_default_side_effect_handler_is_the_terminal_function():
    assert approval._side_effect_handler is approval._terminal_confirm_side_effect


def test_default_outside_sandbox_handler_is_the_terminal_function():
    assert approval._outside_sandbox_handler is approval._terminal_confirm_outside_sandbox
