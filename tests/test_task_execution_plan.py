"""Tests for jarvis.core.task_execution_plan.build_execution_plan:
deterministic derivation of a structured, unexecuted ExecutionPlan from a
TaskIntent plus an already-computed ProjectScanResult/ProjectPlan.
build_execution_plan() never touches the filesystem, git, network, or
subprocess - all inputs are constructed directly."""

from __future__ import annotations

from jarvis.core.project_plan import ProjectPlan
from jarvis.core.project_scan import GitInfo, ProjectScanResult
from jarvis.core.task_execution_plan import ExecutionPlan, build_execution_plan
from jarvis.core.task_intent import parse_task_intent
from jarvis.core.task_safety import IRREVERSIBLE


def _scan(**overrides) -> ProjectScanResult:
    defaults = dict(root="/x", git=GitInfo(is_repo=True, commit_count=1, has_uncommitted_changes=False))
    defaults.update(overrides)
    return ProjectScanResult(**defaults)


def _plan(**overrides) -> ProjectPlan:
    defaults = dict(root_summary="/x")
    defaults.update(overrides)
    return ProjectPlan(**defaults)


# --- blocked: empty request -----------------------------------------------------


def test_empty_request_is_blocked():
    intent = parse_task_intent("")
    result = build_execution_plan(intent, _scan(), _plan())
    assert result.can_proceed is False
    assert "description" in result.blocked_reason.lower()


# --- blocked: external integration -----------------------------------------------------


def test_external_integration_request_is_blocked():
    intent = parse_task_intent("post an update to Instagram")
    result = build_execution_plan(intent, _scan(), _plan())
    assert result.can_proceed is False
    assert "instagram" in result.blocked_reason.lower()


def test_external_integration_step_is_marked_blocked_and_irreversible():
    intent = parse_task_intent("send a receipt via Stripe")
    result = build_execution_plan(intent, _scan(), _plan())
    assert len(result.steps) == 1
    step = result.steps[0]
    assert step.action_kind == "blocked"
    assert step.risk_level == IRREVERSIBLE
    assert step.blocked_reason is not None


def test_every_documented_blocked_service_produces_a_blocked_plan():
    from jarvis.core.task_safety import BLOCKED_EXTERNAL_SERVICES

    for service in BLOCKED_EXTERNAL_SERVICES:
        intent = parse_task_intent(f"do something with {service}")
        result = build_execution_plan(intent, _scan(), _plan())
        assert result.can_proceed is False, f"{service} should be blocked"


# --- blocked: shell execution -----------------------------------------------------


def test_shell_execution_request_is_blocked():
    intent = parse_task_intent("run a shell command to clean up temp files")
    result = build_execution_plan(intent, _scan(), _plan())
    assert result.can_proceed is False
    assert "shell" in result.blocked_reason.lower()


# --- blocked: unknown -----------------------------------------------------


def test_unknown_category_is_blocked_with_guidance():
    intent = parse_task_intent("make the project better somehow")
    result = build_execution_plan(intent, _scan(), _plan())
    assert result.can_proceed is False
    assert "describe" in result.blocked_reason.lower() or "specific" in result.blocked_reason.lower()


# --- supported: git operation -----------------------------------------------------


def test_git_operation_without_repo_suggests_git_init_first():
    intent = parse_task_intent("commit my changes")
    scan = _scan(git=GitInfo(is_repo=False))
    result = build_execution_plan(intent, scan, _plan())
    assert result.can_proceed is True
    descriptions = " ".join(s.description for s in result.steps)
    assert "git-init" in descriptions.lower() or "git init" in descriptions.lower()


def test_git_operation_with_existing_repo_skips_git_init_step():
    intent = parse_task_intent("commit my changes")
    scan = _scan(git=GitInfo(is_repo=True, commit_count=2, has_uncommitted_changes=True))
    result = build_execution_plan(intent, scan, _plan())
    assert result.can_proceed is True
    descriptions = " ".join(s.description for s in result.steps)
    assert "git-init" not in descriptions.lower()


def test_git_operation_steps_all_require_approval():
    intent = parse_task_intent("create a new branch")
    result = build_execution_plan(intent, _scan(), _plan())
    assert result.can_proceed is True
    assert all(step.requires_approval for step in result.steps)


def test_git_operation_steps_never_marked_blocked():
    intent = parse_task_intent("commit my changes")
    result = build_execution_plan(intent, _scan(), _plan())
    assert all(step.action_kind != "blocked" for step in result.steps)


# --- supported: test run -----------------------------------------------------


def test_test_run_request_is_supported():
    intent = parse_task_intent("run the test suite")
    result = build_execution_plan(intent, _scan(), _plan())
    assert result.can_proceed is True
    assert len(result.steps) >= 1
    assert all(step.requires_approval for step in result.steps)


# --- supported: file change -----------------------------------------------------


def test_file_change_request_is_supported():
    intent = parse_task_intent("write a README describing the project")
    result = build_execution_plan(intent, _scan(), _plan())
    assert result.can_proceed is True
    descriptions = " ".join(s.description for s in result.steps)
    assert "write a README describing the project" in descriptions


def test_file_change_step_reflects_risk_classification():
    intent = parse_task_intent("delete the old draft file")
    result = build_execution_plan(intent, _scan(), _plan())
    assert result.can_proceed is True
    assert result.steps[0].risk_level == IRREVERSIBLE


def test_file_change_step_requires_approval():
    intent = parse_task_intent("create a config file")
    result = build_execution_plan(intent, _scan(), _plan())
    assert all(step.requires_approval for step in result.steps)


# --- purity / no side effects -----------------------------------------------------


def test_build_execution_plan_never_touches_subprocess(monkeypatch):
    import subprocess

    def _boom(*a, **k):
        raise AssertionError("build_execution_plan must never call subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)
    intent = parse_task_intent("run a command to delete everything and push to Stripe")
    build_execution_plan(intent, _scan(), _plan())


def test_returns_executionplan_dataclass():
    intent = parse_task_intent("write a README")
    result = build_execution_plan(intent, _scan(), _plan())
    assert isinstance(result, ExecutionPlan)


def test_deterministic_same_inputs_same_result():
    intent = parse_task_intent("write a README")
    scan = _scan()
    plan = _plan()
    first = build_execution_plan(intent, scan, plan)
    second = build_execution_plan(intent, scan, plan)
    assert first == second
