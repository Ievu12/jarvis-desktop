"""Tests for the get_workflow_state tool: lets the LLM-driven agent read
the same deterministic scan/plan/work/review/precommit views a human sees
via the 'jarvis <stage>' CLI arguments. Purely read-only - no approval
prompt, no filesystem writes, no git mutations, no LLM call of its own.
Verified both via mocking (confirms exact delegation to the same
pure/format functions main.py's run_cli_* entry points use) and via real
end-to-end runs against isolated temp directories."""

from __future__ import annotations

import subprocess
from unittest.mock import patch

from jarvis.tools.base import ToolResult
from jarvis.tools.workflow_state import WorkflowStateTool, _STAGES


def _init_git_repo(path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


# --- tool metadata -------------------------------------------------------------


def test_tool_name():
    assert WorkflowStateTool().name == "get_workflow_state"


def test_tool_schema_lists_all_five_stages_and_excludes_commit():
    schema = WorkflowStateTool().input_schema
    enum = schema["properties"]["stage"]["enum"]
    assert set(enum) == {"scan", "plan", "work", "review", "precommit"}
    assert "commit" not in enum


def test_tool_requires_stage_argument():
    assert WorkflowStateTool().input_schema["required"] == ["stage"]


def test_stages_constant_matches_schema_enum():
    schema = WorkflowStateTool().input_schema
    assert list(_STAGES) == schema["properties"]["stage"]["enum"]


# --- unknown stage is denied, not crashed ---------------------------------------


def test_unknown_stage_denied():
    result = WorkflowStateTool().run(stage="commit")
    assert result.ok is False
    assert "unknown stage" in result.output.lower()


def test_unknown_stage_never_touches_filesystem(tmp_path, monkeypatch):
    # Denial must happen before any scan_project()/JARVIS_ROOT access.
    monkeypatch.setattr("jarvis.tools.workflow_state.JARVIS_ROOT", tmp_path / "does_not_exist")
    result = WorkflowStateTool().run(stage="nonsense")
    assert result.ok is False


# --- delegation: each stage calls the exact same pipeline as jarvis.cli.main ------


def test_scan_stage_reuses_scan_project_and_format_scan_result():
    with patch("jarvis.tools.workflow_state.scan_project") as mock_scan:
        with patch("jarvis.tools.workflow_state.format_scan_result") as mock_format:
            mock_scan.return_value = "a scan result"
            mock_format.return_value = "formatted scan"
            result = WorkflowStateTool().run(stage="scan")
    mock_scan.assert_called_once()
    mock_format.assert_called_once_with("a scan result")
    assert result == ToolResult(ok=True, output="formatted scan")


def test_plan_stage_reuses_scan_project_and_build_plan():
    with patch("jarvis.tools.workflow_state.scan_project") as mock_scan:
        with patch("jarvis.tools.workflow_state.build_plan") as mock_build_plan:
            with patch("jarvis.tools.workflow_state.format_plan") as mock_format:
                mock_scan.return_value = "a scan result"
                mock_build_plan.return_value = "a plan"
                mock_format.return_value = "formatted plan"
                result = WorkflowStateTool().run(stage="plan")
    mock_build_plan.assert_called_once_with("a scan result")
    mock_format.assert_called_once_with("a plan")
    assert result.output == "formatted plan"


def test_work_stage_reuses_build_plan_and_build_work_item():
    with patch("jarvis.tools.workflow_state.scan_project") as mock_scan:
        with patch("jarvis.tools.workflow_state.build_plan") as mock_build_plan:
            with patch("jarvis.tools.workflow_state.build_work_item") as mock_build_work:
                with patch("jarvis.tools.workflow_state.format_work_item") as mock_format:
                    mock_scan.return_value = "scan"
                    mock_build_plan.return_value = "plan"
                    mock_build_work.return_value = "work item"
                    mock_format.return_value = "formatted work"
                    result = WorkflowStateTool().run(stage="work")
    mock_build_work.assert_called_once_with("plan")
    mock_format.assert_called_once_with("work item")
    assert result.output == "formatted work"


def test_review_stage_reuses_build_review():
    with patch("jarvis.tools.workflow_state.scan_project") as mock_scan:
        with patch("jarvis.tools.workflow_state.build_plan") as mock_build_plan:
            with patch("jarvis.tools.workflow_state.build_review") as mock_build_review:
                with patch("jarvis.tools.workflow_state.format_review_result") as mock_format:
                    mock_scan.return_value = "scan"
                    mock_build_plan.return_value = "plan"
                    mock_build_review.return_value = "review"
                    mock_format.return_value = "formatted review"
                    result = WorkflowStateTool().run(stage="review")
    mock_build_review.assert_called_once_with("scan", "plan")
    mock_format.assert_called_once_with("review")
    assert result.output == "formatted review"


def test_precommit_stage_reuses_build_precommit_check():
    with patch("jarvis.tools.workflow_state.scan_project") as mock_scan:
        with patch("jarvis.tools.workflow_state.build_plan") as mock_build_plan:
            with patch("jarvis.tools.workflow_state.build_review") as mock_build_review:
                with patch("jarvis.tools.workflow_state.build_precommit_check") as mock_build_check:
                    with patch("jarvis.tools.workflow_state.format_precommit_check") as mock_format:
                        mock_scan.return_value = "scan"
                        mock_build_plan.return_value = "plan"
                        mock_build_review.return_value = "review"
                        mock_build_check.return_value = "check"
                        mock_format.return_value = "formatted precommit"
                        result = WorkflowStateTool().run(stage="precommit")
    mock_build_check.assert_called_once_with("review")
    mock_format.assert_called_once_with("check")
    assert result.output == "formatted precommit"


# --- always succeeds (ok=True) for a valid stage, even on degenerate project state ---


def test_every_valid_stage_returns_ok_true_on_empty_project(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.tools.workflow_state.JARVIS_ROOT", tmp_path)
    for stage in _STAGES:
        result = WorkflowStateTool().run(stage=stage)
        assert result.ok is True
        assert result.output


# --- no approval required (unlike side-effecting tools) -----------------------------


def test_no_approval_prompt_required(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.tools.workflow_state.JARVIS_ROOT", tmp_path)
    with patch("builtins.input") as mock_input:
        WorkflowStateTool().run(stage="scan")
    mock_input.assert_not_called()


# --- purity: never writes, never runs git-mutating commands -------------------------


def test_never_writes_files(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("hello")
    monkeypatch.setattr("jarvis.tools.workflow_state.JARVIS_ROOT", tmp_path)
    with patch("jarvis.core.review_view.JARVIS_ROOT", tmp_path):
        before = sorted(p.name for p in tmp_path.iterdir())
        for stage in _STAGES:
            WorkflowStateTool().run(stage=stage)
        after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after


def test_never_calls_git_add_or_commit(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("hello")
    monkeypatch.setattr("jarvis.tools.workflow_state.JARVIS_ROOT", tmp_path)

    real_run = subprocess.run

    def _guarded_run(argv, *a, **k):
        if isinstance(argv, list) and any(x in argv for x in ("add", "commit")):
            raise AssertionError("get_workflow_state must never invoke git add or git commit")
        return real_run(argv, *a, **k)

    monkeypatch.setattr(subprocess, "run", _guarded_run)
    with patch("jarvis.core.review_view.JARVIS_ROOT", tmp_path):
        for stage in _STAGES:
            WorkflowStateTool().run(stage=stage)


# --- end-to-end against a real temp directory, matching the CLI's own output ----------


def test_end_to_end_review_stage_matches_run_cli_review_output(tmp_path, monkeypatch):
    _init_git_repo(tmp_path)
    (tmp_path / "notes.txt").write_text("hello")
    monkeypatch.setattr("jarvis.tools.workflow_state.JARVIS_ROOT", tmp_path)

    with patch("jarvis.core.review_view.JARVIS_ROOT", tmp_path):
        tool_output = WorkflowStateTool().run(stage="review").output

        from jarvis.core.project_plan import build_plan
        from jarvis.core.project_review import build_review
        from jarvis.core.project_review_view import format_review_result
        from jarvis.core.project_scan import scan_project

        scan_result = scan_project(tmp_path)
        plan = build_plan(scan_result)
        review = build_review(scan_result, plan)
        expected = format_review_result(review)

    assert tool_output == expected


def test_end_to_end_no_git_repo_scan_stage(tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.tools.workflow_state.JARVIS_ROOT", tmp_path)
    result = WorkflowStateTool().run(stage="scan")
    assert result.ok is True
    assert str(tmp_path) in result.output
