"""Tests for jarvis.session.tasks.create_plan (persistence) and
jarvis.tools.plan.CreatePlanTool (tool surface): atomic plan creation,
replacing an existing plan with a clear reported count (never silent),
empty/whitespace-only step filtering, no approval prompt, and correct
interaction with manage_tasks' complete/list actions afterward. All
fixtures use an isolated TASKS_FILE via monkeypatch - never the real
project's tasks.json."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jarvis.session import tasks as tasks_module
from jarvis.tools.plan import CreatePlanTool
from jarvis.tools.tasks import ManageTasksTool


@pytest.fixture
def isolated_tasks_file(tmp_path, monkeypatch):
    path = tmp_path / "tasks.json"
    monkeypatch.setattr(tasks_module, "TASKS_FILE", path)
    return path


# --- session/tasks.py: create_plan persistence layer -------------------------


def test_create_plan_creates_ordered_tasks(isolated_tasks_file):
    new_tasks, replaced = tasks_module.create_plan(["first", "second", "third"])
    assert [t.text for t in new_tasks] == ["first", "second", "third"]
    assert [t.id for t in new_tasks] == [1, 2, 3]
    assert all(not t.done for t in new_tasks)
    assert replaced == 0


def test_create_plan_persists(isolated_tasks_file):
    tasks_module.create_plan(["a", "b"])
    loaded = tasks_module.load_tasks()
    assert [t.text for t in loaded] == ["a", "b"]


def test_create_plan_replaces_existing_tasks_and_reports_count(isolated_tasks_file):
    tasks_module.add_task("old task 1")
    tasks_module.add_task("old task 2")

    new_tasks, replaced = tasks_module.create_plan(["new step"])
    assert replaced == 2
    assert [t.text for t in tasks_module.load_tasks()] == ["new step"]


def test_create_plan_replaces_even_completed_tasks(isolated_tasks_file):
    t = tasks_module.add_task("done already")
    tasks_module.complete_task(t.id)

    new_tasks, replaced = tasks_module.create_plan(["fresh plan"])
    assert replaced == 1
    assert [t.text for t in tasks_module.load_tasks()] == ["fresh plan"]


def test_create_plan_with_empty_steps_list_creates_no_tasks(isolated_tasks_file):
    new_tasks, replaced = tasks_module.create_plan([])
    assert new_tasks == []
    assert tasks_module.load_tasks() == []


def test_create_plan_ids_start_from_one_each_time(isolated_tasks_file):
    tasks_module.create_plan(["a", "b", "c"])
    new_tasks, _ = tasks_module.create_plan(["x"])
    assert new_tasks[0].id == 1  # fresh plan restarts numbering


# --- tools/plan.py: CreatePlanTool -------------------------------------------


def test_create_plan_tool_requires_no_approval_prompt(isolated_tasks_file):
    tool = CreatePlanTool()
    with patch("builtins.input") as mock_input:
        result = tool.run(steps=["step one", "step two"])
    assert result.ok
    mock_input.assert_not_called()


def test_create_plan_tool_reports_all_steps(isolated_tasks_file):
    tool = CreatePlanTool()
    result = tool.run(steps=["Create hello.py", "Write a test", "Run the test"])
    assert result.ok
    assert "Create hello.py" in result.output
    assert "Write a test" in result.output
    assert "Run the test" in result.output
    assert "3 step(s)" in result.output


def test_create_plan_tool_empty_steps_denied():
    tool = CreatePlanTool()
    result = tool.run(steps=[])
    assert not result.ok
    assert "at least one" in result.output.lower()


def test_create_plan_tool_whitespace_only_steps_denied():
    tool = CreatePlanTool()
    result = tool.run(steps=["   ", "", "\t"])
    assert not result.ok


def test_create_plan_tool_filters_blank_steps_but_keeps_valid_ones(isolated_tasks_file):
    tool = CreatePlanTool()
    result = tool.run(steps=["real step", "   ", "another real step", ""])
    assert result.ok
    assert "real step" in result.output
    assert "another real step" in result.output
    loaded = tasks_module.load_tasks()
    assert len(loaded) == 2


def test_create_plan_tool_reports_replaced_count_when_prior_plan_existed(isolated_tasks_file):
    tool = CreatePlanTool()
    tool.run(steps=["old step 1", "old step 2"])

    result = tool.run(steps=["new step"])
    assert result.ok
    assert "Replaced 2 step" in result.output


def test_create_plan_tool_no_replaced_note_on_first_plan(isolated_tasks_file):
    tool = CreatePlanTool()
    result = tool.run(steps=["first plan ever"])
    assert "Replaced" not in result.output


# --- interaction with manage_tasks afterward ---------------------------------


def test_plan_then_execute_and_complete_via_manage_tasks(isolated_tasks_file):
    plan_tool = CreatePlanTool()
    tasks_tool = ManageTasksTool()

    plan_tool.run(steps=["step A", "step B"])

    complete_result = tasks_tool.run(action="complete", task_id=1)
    assert complete_result.ok

    list_result = tasks_tool.run(action="list")
    lines = list_result.output.splitlines()
    assert any(line.startswith("[x]") and "step A" in line for line in lines)
    assert any(line.startswith("[ ]") and "step B" in line for line in lines)


def test_manage_tasks_add_after_create_plan_continues_id_sequence(isolated_tasks_file):
    plan_tool = CreatePlanTool()
    tasks_tool = ManageTasksTool()

    plan_tool.run(steps=["step 1", "step 2"])
    add_result = tasks_tool.run(action="add", text="an extra step discovered mid-execution")
    assert add_result.ok
    assert "#3" in add_result.output  # continues from the plan's existing ids
