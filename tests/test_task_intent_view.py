"""Tests for jarvis.core.task_intent_view.format_task_intent: pure
formatting of a TaskIntent, no classification logic, no side effects."""

from __future__ import annotations

from jarvis.core.task_intent import TaskIntent
from jarvis.core.task_intent_view import format_task_intent


def _intent(**overrides) -> TaskIntent:
    defaults = dict(
        raw_text="write a README",
        action_category="file_change",
        mentions_external_service=False,
        external_service_name=None,
        mentions_shell_execution=False,
        is_supported=True,
    )
    defaults.update(overrides)
    return TaskIntent(**defaults)


def test_includes_raw_text():
    output = format_task_intent(_intent(raw_text="commit my changes"))
    assert "commit my changes" in output


def test_includes_category():
    output = format_task_intent(_intent(action_category="git_operation"))
    assert "git_operation" in output


def test_shows_external_service_when_mentioned():
    output = format_task_intent(
        _intent(mentions_external_service=True, external_service_name="stripe", is_supported=False)
    )
    assert "stripe" in output.lower()


def test_omits_external_service_line_when_not_mentioned():
    output = format_task_intent(_intent(mentions_external_service=False, external_service_name=None))
    assert "external service" not in output.lower()


def test_shows_shell_execution_note_when_mentioned():
    output = format_task_intent(_intent(mentions_shell_execution=True, is_supported=False))
    assert "shell" in output.lower()


def test_supported_yes():
    output = format_task_intent(_intent(is_supported=True))
    assert "supported in this stage: yes" in output.lower()


def test_supported_no():
    output = format_task_intent(_intent(is_supported=False))
    assert "supported in this stage: no" in output.lower()


def test_output_is_deterministic():
    intent = _intent()
    assert format_task_intent(intent) == format_task_intent(intent)
