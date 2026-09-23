"""Tests for jarvis.core.project_work_view.format_work_item: pure
formatting of a WorkItem, no derivation logic, no side effects."""

from __future__ import annotations

from jarvis.core.project_work import WorkItem
from jarvis.core.project_work_view import format_work_item


def _item(**overrides) -> WorkItem:
    defaults = dict(
        has_plan=True,
        is_complete=False,
        step_description="do the thing",
        action_kind="repl_prompt",
        action_detail="Ask JARVIS: \"do the thing\"",
        remaining_step_count=1,
    )
    defaults.update(overrides)
    return WorkItem(**defaults)


def test_no_plan_message():
    item = _item(has_plan=False, is_complete=False, step_description=None, action_kind="no_plan", action_detail=None, remaining_step_count=0)
    output = format_work_item(item)
    assert "no plan" in output.lower()
    assert "jarvis plan" in output.lower()


def test_complete_message():
    item = _item(is_complete=True, step_description=None, action_kind="none", action_detail=None, remaining_step_count=0)
    output = format_work_item(item)
    assert "nothing to do" in output.lower()


def test_command_kind_shows_exact_command():
    item = _item(action_kind="command", action_detail="git init")
    output = format_work_item(item)
    assert "git init" in output
    assert "safe and mechanical" in output.lower()


def test_repl_prompt_kind_shows_suggested_prompt():
    item = _item(action_kind="repl_prompt", action_detail="Ask JARVIS: \"write tests\"")
    output = format_work_item(item)
    assert "write tests" in output
    assert "needs judgment" in output.lower()


def test_step_description_included():
    item = _item(step_description="Initialize a git repository")
    output = format_work_item(item)
    assert "Initialize a git repository" in output


def test_safety_disclaimer_always_present_when_step_shown():
    item = _item()
    output = format_work_item(item)
    assert "never writes files or runs git itself" in output.lower()


def test_remaining_steps_note_shown_when_more_than_one():
    item = _item(remaining_step_count=3)
    output = format_work_item(item)
    assert "2 more steps" in output


def test_remaining_steps_note_absent_when_only_one_step():
    item = _item(remaining_step_count=1)
    output = format_work_item(item)
    assert "more step" not in output.lower()


def test_output_is_deterministic():
    item = _item()
    assert format_work_item(item) == format_work_item(item)
