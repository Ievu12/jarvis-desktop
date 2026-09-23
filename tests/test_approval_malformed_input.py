"""Tests confirming approval prompts fail closed (deny) for any answer
other than exactly 'y' (case-insensitive, whitespace-trimmed) - empty
string, 'yes', whitespace-only, mixed case, and garbage input all deny.
This behavior already existed; these tests make it explicit rather than
implicit/never-verified."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from jarvis.core.approval import confirm_outside_sandbox, confirm_side_effect


@pytest.mark.parametrize(
    "answer",
    ["", " ", "yes", "Yes", "YES", "no", "n", "sure", "1", "true", "yy"],
)
def test_confirm_side_effect_denies_anything_other_than_bare_y(answer):
    with patch("builtins.input", return_value=answer):
        assert confirm_side_effect("do something") is False


def test_confirm_side_effect_approves_lowercase_y():
    with patch("builtins.input", return_value="y"):
        assert confirm_side_effect("do something") is True


def test_confirm_side_effect_approves_uppercase_y():
    with patch("builtins.input", return_value="Y"):
        assert confirm_side_effect("do something") is True


def test_confirm_side_effect_approves_y_with_surrounding_whitespace():
    with patch("builtins.input", return_value="  y  "):
        assert confirm_side_effect("do something") is True


@pytest.mark.parametrize(
    "answer",
    ["", " ", "yes", "Yes", "YES", "no", "n", "sure", "1", "true", "yy"],
)
def test_confirm_outside_sandbox_denies_anything_other_than_bare_y(answer):
    with patch("builtins.input", return_value=answer):
        assert confirm_outside_sandbox(Path("C:/outside/file.txt")) is False


def test_confirm_outside_sandbox_approves_lowercase_y():
    with patch("builtins.input", return_value="y"):
        assert confirm_outside_sandbox(Path("C:/outside/file.txt")) is True


def test_malformed_answer_is_still_logged(monkeypatch):
    log_calls = []
    monkeypatch.setattr("jarvis.core.approval.log_event", lambda *a, **k: log_calls.append(k))

    with patch("builtins.input", return_value="yes"):
        confirm_side_effect("do something")

    assert len(log_calls) == 1
    assert log_calls[0]["approved"] is False
