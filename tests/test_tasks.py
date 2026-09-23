"""Tests for jarvis.session.tasks (persistence) and
jarvis.tools.tasks.ManageTasksTool (the tool surface): add/complete/
list/clear operations, id assignment, corrupted-file resilience, and
confirmation that no approval prompt is required for any mutation. All
fixtures use an isolated TASKS_FILE via monkeypatch - never the real
project's tasks.json."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jarvis.session import tasks as tasks_module
from jarvis.tools.tasks import ManageTasksTool


@pytest.fixture
def isolated_tasks_file(tmp_path, monkeypatch):
    path = tmp_path / "tasks.json"
    monkeypatch.setattr(tasks_module, "TASKS_FILE", path)
    return path


# --- session/tasks.py persistence layer --------------------------------------


def test_load_tasks_empty_when_no_file(isolated_tasks_file):
    assert tasks_module.load_tasks() == []


def test_add_task_assigns_incrementing_ids(isolated_tasks_file):
    t1 = tasks_module.add_task("first")
    t2 = tasks_module.add_task("second")
    assert t1.id == 1
    assert t2.id == 2


def test_add_task_persists_across_loads(isolated_tasks_file):
    tasks_module.add_task("persisted task")
    loaded = tasks_module.load_tasks()
    assert len(loaded) == 1
    assert loaded[0].text == "persisted task"
    assert loaded[0].done is False


def test_complete_task_marks_done(isolated_tasks_file):
    t = tasks_module.add_task("do this")
    found = tasks_module.complete_task(t.id)
    assert found is True
    loaded = tasks_module.load_tasks()
    assert loaded[0].done is True


def test_complete_nonexistent_task_returns_false(isolated_tasks_file):
    tasks_module.add_task("real task")
    found = tasks_module.complete_task(999)
    assert found is False
    # Existing task untouched.
    assert tasks_module.load_tasks()[0].done is False


def test_clear_completed_only_removes_done_tasks(isolated_tasks_file):
    t1 = tasks_module.add_task("done one")
    tasks_module.add_task("pending one")
    tasks_module.complete_task(t1.id)

    removed = tasks_module.clear_completed_tasks()
    assert removed == 1
    remaining = tasks_module.load_tasks()
    assert len(remaining) == 1
    assert remaining[0].text == "pending one"


def test_clear_completed_with_nothing_done_is_a_no_op(isolated_tasks_file):
    tasks_module.add_task("pending")
    removed = tasks_module.clear_completed_tasks()
    assert removed == 0
    assert len(tasks_module.load_tasks()) == 1


def test_clear_all_removes_everything(isolated_tasks_file):
    tasks_module.add_task("one")
    tasks_module.add_task("two")
    removed = tasks_module.clear_all_tasks()
    assert removed == 2
    assert tasks_module.load_tasks() == []


def test_ids_continue_incrementing_after_clear(isolated_tasks_file):
    tasks_module.add_task("first")
    tasks_module.clear_all_tasks()
    t = tasks_module.add_task("after clear")
    assert t.id == 1  # ids reset since the list is genuinely empty


def test_corrupted_tasks_file_treated_as_empty(isolated_tasks_file):
    isolated_tasks_file.write_text("not valid json{{{", encoding="utf-8")
    assert tasks_module.load_tasks() == []


def test_non_list_tasks_file_treated_as_empty(isolated_tasks_file):
    isolated_tasks_file.write_text('{"not": "a list"}', encoding="utf-8")
    assert tasks_module.load_tasks() == []


# --- tools/tasks.py: ManageTasksTool -----------------------------------------


def test_manage_tasks_add_requires_no_approval_prompt(isolated_tasks_file):
    tool = ManageTasksTool()
    with patch("builtins.input") as mock_input:
        result = tool.run(action="add", text="a new task")
    assert result.ok
    mock_input.assert_not_called()


def test_manage_tasks_complete_requires_no_approval_prompt(isolated_tasks_file):
    tool = ManageTasksTool()
    tool.run(action="add", text="something")
    with patch("builtins.input") as mock_input:
        result = tool.run(action="complete", task_id=1)
    assert result.ok
    mock_input.assert_not_called()


def test_manage_tasks_clear_all_requires_no_approval_prompt(isolated_tasks_file):
    tool = ManageTasksTool()
    tool.run(action="add", text="something")
    with patch("builtins.input") as mock_input:
        result = tool.run(action="clear_all")
    assert result.ok
    mock_input.assert_not_called()


def test_manage_tasks_add_without_text_denied():
    tool = ManageTasksTool()
    result = tool.run(action="add")
    assert not result.ok


def test_manage_tasks_add_with_empty_text_denied():
    tool = ManageTasksTool()
    result = tool.run(action="add", text="   ")
    assert not result.ok


def test_manage_tasks_complete_without_task_id_denied():
    tool = ManageTasksTool()
    result = tool.run(action="complete")
    assert not result.ok


def test_manage_tasks_list_empty_reports_no_tasks(isolated_tasks_file):
    tool = ManageTasksTool()
    result = tool.run(action="list")
    assert result.ok
    assert "no tasks" in result.output.lower()


def test_manage_tasks_list_shows_done_and_pending_distinctly(isolated_tasks_file):
    tool = ManageTasksTool()
    tool.run(action="add", text="pending task")
    tool.run(action="add", text="done task")
    tool.run(action="complete", task_id=2)

    result = tool.run(action="list")
    lines = result.output.splitlines()
    assert any(line.startswith("[ ]") and "pending task" in line for line in lines)
    assert any(line.startswith("[x]") and "done task" in line for line in lines)


def test_manage_tasks_unknown_action_denied():
    tool = ManageTasksTool()
    result = tool.run(action="destroy_everything")
    assert not result.ok
    assert "unknown action" in result.output.lower()


def test_manage_tasks_complete_nonexistent_id_reports_failure(isolated_tasks_file):
    tool = ManageTasksTool()
    tool.run(action="add", text="one task")
    result = tool.run(action="complete", task_id=999)
    assert not result.ok
    assert "no task" in result.output.lower()
