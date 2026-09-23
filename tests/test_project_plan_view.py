"""Tests for jarvis.core.project_plan_view.format_plan: pure formatting
of a ProjectPlan, no planning logic, no side effects."""

from __future__ import annotations

from jarvis.core.project_plan import ProjectPlan
from jarvis.core.project_plan_view import format_plan


def test_includes_root_summary():
    plan = ProjectPlan(root_summary="/some/project")
    output = format_plan(plan)
    assert "/some/project" in output


def test_all_four_numbered_sections_present():
    plan = ProjectPlan(
        root_summary="/x",
        current_state=["state item"],
        findings=["finding item"],
        recommended_steps=["step item"],
        suggested_order=["step item"],
    )
    output = format_plan(plan)
    assert "1. Current state:" in output
    assert "2. Findings:" in output
    assert "3. Recommended next steps:" in output
    assert "4. Suggested order of work:" in output


def test_current_state_items_listed():
    plan = ProjectPlan(root_summary="/x", current_state=["item one", "item two"])
    output = format_plan(plan)
    assert "item one" in output
    assert "item two" in output


def test_findings_items_listed():
    plan = ProjectPlan(root_summary="/x", findings=["finding A", "finding B"])
    output = format_plan(plan)
    assert "finding A" in output
    assert "finding B" in output


def test_empty_findings_shows_placeholder():
    plan = ProjectPlan(root_summary="/x", findings=[])
    output = format_plan(plan)
    assert "no notable findings" in output.lower()


def test_recommended_steps_listed():
    plan = ProjectPlan(root_summary="/x", recommended_steps=["do this", "do that"])
    output = format_plan(plan)
    assert "do this" in output
    assert "do that" in output


def test_suggested_order_is_numbered():
    plan = ProjectPlan(root_summary="/x", suggested_order=["first step", "second step"])
    output = format_plan(plan)
    assert "1. first step" in output
    assert "2. second step" in output


def test_suggested_order_numbering_follows_list_order_not_alphabetical():
    plan = ProjectPlan(root_summary="/x", suggested_order=["zebra step", "alpha step"])
    output = format_plan(plan)
    zebra_pos = output.index("zebra step")
    alpha_pos = output.index("alpha step")
    assert zebra_pos < alpha_pos  # order preserved, not re-sorted by the formatter


def test_output_is_deterministic():
    plan = ProjectPlan(
        root_summary="/x",
        current_state=["a"],
        findings=["b"],
        recommended_steps=["c"],
        suggested_order=["c"],
    )
    assert format_plan(plan) == format_plan(plan)
