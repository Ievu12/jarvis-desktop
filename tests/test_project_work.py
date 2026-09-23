"""Tests for jarvis.core.project_work.build_work_item: deterministic
derivation of the single next actionable step from an already-computed
ProjectPlan. build_work_item() never touches the filesystem, git, or the
network - all inputs are constructed directly."""

from __future__ import annotations

from jarvis.core.project_plan import ProjectPlan
from jarvis.core.project_work import WorkItem, build_work_item


def _plan(**overrides) -> ProjectPlan:
    defaults = dict(root_summary="/x")
    defaults.update(overrides)
    return ProjectPlan(**defaults)


# --- missing / empty plan -----------------------------------------------------


def test_empty_suggested_order_reports_no_plan():
    item = build_work_item(_plan(suggested_order=[]))
    assert item.has_plan is False
    assert item.is_complete is False
    assert item.step_description is None
    assert item.action_kind == "no_plan"


def test_no_plan_case_returns_workitem_dataclass():
    item = build_work_item(_plan(suggested_order=[]))
    assert isinstance(item, WorkItem)


# --- "no gaps detected" complete case ------------------------------------------


def test_no_gaps_detected_marks_complete():
    item = build_work_item(
        _plan(suggested_order=["No gaps detected by this scan - proceed with whatever is next."])
    )
    assert item.has_plan is True
    assert item.is_complete is True
    assert item.action_kind == "none"
    assert item.step_description is None


def test_no_gaps_detected_is_case_insensitive_prefix_match():
    item = build_work_item(_plan(suggested_order=["no gaps detected here"]))
    assert item.is_complete is True


# --- picks the first step from suggested_order, not recommended_steps ---------


def test_picks_first_item_of_suggested_order():
    item = build_work_item(
        _plan(
            recommended_steps=["Add a README describing what the project is and how to use it."],
            suggested_order=[
                "Initialize a git repository (`git-init`) to start tracking history.",
                "Add a README describing what the project is and how to use it.",
            ],
        )
    )
    assert item.step_description == "Initialize a git repository (`git-init`) to start tracking history."


def test_remaining_step_count_reflects_full_suggested_order_length():
    item = build_work_item(
        _plan(
            suggested_order=[
                "Initialize a git repository (`git-init`) to start tracking history.",
                "Add a README describing what the project is and how to use it.",
                "Add a test suite (or at least a first test) to verify behavior.",
            ]
        )
    )
    assert item.remaining_step_count == 3


# --- classification: command vs repl_prompt ------------------------------------


def test_git_init_step_yields_exact_command():
    item = build_work_item(
        _plan(suggested_order=["Initialize a git repository (`git-init`) to start tracking history."])
    )
    assert item.action_kind == "command"
    assert item.action_detail == "git init"


def test_initial_commit_step_yields_repl_prompt():
    item = build_work_item(_plan(suggested_order=["Make an initial commit once there is something worth saving."]))
    assert item.action_kind == "repl_prompt"
    assert item.action_detail is not None


def test_readme_step_yields_repl_prompt():
    item = build_work_item(
        _plan(suggested_order=["Add a README describing what the project is and how to use it."])
    )
    assert item.action_kind == "repl_prompt"
    assert "README" in item.action_detail


def test_manifest_step_yields_repl_prompt():
    item = build_work_item(
        _plan(
            suggested_order=[
                "Add a dependency/package manifest appropriate for the project's language "
                "(e.g. pyproject.toml, requirements.txt, package.json)."
            ]
        )
    )
    assert item.action_kind == "repl_prompt"


def test_tests_step_yields_repl_prompt():
    item = build_work_item(
        _plan(suggested_order=["Add a test suite (or at least a first test) to verify behavior."])
    )
    assert item.action_kind == "repl_prompt"


def test_jarvis_md_step_yields_repl_prompt_mentioning_scan():
    item = build_work_item(
        _plan(
            suggested_order=[
                "Create a JARVIS.md with project context and conventions (run 'scan' in the "
                "REPL for a proposed draft)."
            ]
        )
    )
    assert item.action_kind == "repl_prompt"
    assert "scan" in item.action_detail.lower()


def test_review_uncommitted_changes_step_yields_repl_prompt():
    item = build_work_item(
        _plan(suggested_order=["Review the uncommitted changes ('review' or 'precommit') before committing them."])
    )
    assert item.action_kind == "repl_prompt"


def test_unrecognized_step_wording_gets_generic_fallback_not_crash():
    item = build_work_item(_plan(suggested_order=["Some future step type nobody anticipated."]))
    assert item.action_kind == "repl_prompt"
    assert "Some future step type nobody anticipated." in item.action_detail


# --- purity / no side effects ---------------------------------------------------


def test_build_work_item_never_imports_subprocess_at_call_time(monkeypatch):
    # Sanity check that no subprocess call happens as a side effect -
    # patch subprocess.run to raise if called at all.
    import subprocess

    def _boom(*a, **k):
        raise AssertionError("build_work_item must never call subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)
    build_work_item(
        _plan(suggested_order=["Initialize a git repository (`git-init`) to start tracking history."])
    )


def test_deterministic_same_plan_same_result():
    plan = _plan(suggested_order=["Add a README describing what the project is and how to use it."])
    first = build_work_item(plan)
    second = build_work_item(plan)
    assert first == second
