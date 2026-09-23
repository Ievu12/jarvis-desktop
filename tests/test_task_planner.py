"""Tests for jarvis.core.task_planner.TaskPlanner: wires
parse_task_intent -> scan_project -> build_plan -> build_execution_plan
into a Task. Uses the real scan_project() against JARVIS_ROOT (same
posture as test_cli_task_argument.py's end-to-end tests and
test_cli_work_argument.py) rather than mocking every stage - the point of
this test module is to verify the *wiring and summarization* (id
assignment, status/risk_level/requires_approval derivation), which only a
real, unmocked call can meaningfully exercise end to end."""

from __future__ import annotations

from jarvis.core.task import BLOCKED, PENDING_APPROVAL, Task
from jarvis.core.task_planner import TaskPlanner
from jarvis.core.task_safety import IRREVERSIBLE


# --- id assignment -----------------------------------------------------


def test_first_task_from_a_fresh_planner_has_id_1():
    task = TaskPlanner().plan("write a README")
    assert task.id == 1


def test_ids_increment_sequentially_on_the_same_planner():
    planner = TaskPlanner()
    first = planner.plan("write a README")
    second = planner.plan("run the test suite")
    third = planner.plan("commit my changes")
    assert (first.id, second.id, third.id) == (1, 2, 3)


def test_separate_planners_each_start_at_1():
    a = TaskPlanner().plan("write a README")
    b = TaskPlanner().plan("write a README")
    assert a.id == b.id == 1


# --- return type -----------------------------------------------------


def test_plan_returns_task_dataclass():
    task = TaskPlanner().plan("write a README")
    assert isinstance(task, Task)


def test_task_description_matches_input():
    task = TaskPlanner().plan("write a README describing the project")
    assert task.description == "write a README describing the project"


# --- status/approval derivation: blocked cases -----------------------------------------------------


def test_external_integration_request_is_blocked_with_no_approval_needed():
    task = TaskPlanner().plan("post an update to Instagram")
    assert task.status == BLOCKED
    assert task.requires_approval is False


def test_shell_execution_request_is_blocked():
    task = TaskPlanner().plan("run a shell command to list files")
    assert task.status == BLOCKED
    assert task.requires_approval is False


def test_empty_request_is_blocked():
    task = TaskPlanner().plan("")
    assert task.status == BLOCKED


def test_unknown_request_is_blocked():
    task = TaskPlanner().plan("make everything better somehow")
    assert task.status == BLOCKED


def test_blocked_task_has_irreversible_overall_risk_level():
    # A blocked plan's single synthetic step is always IRREVERSIBLE (see
    # task_execution_plan._blocked_plan) - never silently summarized as safe.
    task = TaskPlanner().plan("post an update to Facebook")
    assert task.risk_level == IRREVERSIBLE


# --- status/approval derivation: supported cases -----------------------------------------------------


def test_file_change_request_is_pending_approval():
    task = TaskPlanner().plan("write a README describing the project")
    assert task.status == PENDING_APPROVAL
    assert task.requires_approval is True


def test_test_run_request_is_pending_approval():
    task = TaskPlanner().plan("run the test suite")
    assert task.status == PENDING_APPROVAL
    assert task.requires_approval is True


def test_git_operation_request_is_pending_approval():
    task = TaskPlanner().plan("commit my changes")
    assert task.status == PENDING_APPROVAL
    assert task.requires_approval is True


def test_pending_approval_task_risk_level_matches_highest_step_risk():
    task = TaskPlanner().plan("delete the old draft file")
    # 'delete' classifies as IRREVERSIBLE per task_safety.classify_risk,
    # and the file_change plan has exactly one step using that risk.
    assert task.risk_level == IRREVERSIBLE
    assert task.status == PENDING_APPROVAL


def test_every_step_in_a_pending_approval_task_is_present_on_the_plan():
    task = TaskPlanner().plan("commit my changes")
    assert len(task.plan.steps) >= 1
    assert all(step.action_kind != "blocked" for step in task.plan.steps)


# --- purity / no forbidden side effects -----------------------------------------------------


def test_task_planner_never_calls_a_mutating_subprocess_command(monkeypatch):
    import subprocess

    real_run = subprocess.run

    def _guarded(argv, *a, **k):
        joined = " ".join(argv) if isinstance(argv, list) else str(argv)
        forbidden = ("commit", "push", "rm ", "delete", "init")
        if any(word in joined.lower() for word in forbidden):
            raise AssertionError(f"TaskPlanner.plan must never run a mutating command: {argv}")
        return real_run(argv, *a, **k)

    monkeypatch.setattr(subprocess, "run", _guarded)
    TaskPlanner().plan("delete the old draft file and commit it, then push")


def test_deterministic_same_description_same_task_shape():
    planner_a = TaskPlanner()
    planner_b = TaskPlanner()
    task_a = planner_a.plan("write a README")
    task_b = planner_b.plan("write a README")
    # Ids match (both are each planner's first task) and every other field
    # is identical given the same project state.
    assert task_a == task_b
