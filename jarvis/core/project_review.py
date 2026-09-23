"""Deterministic, structured pre-commit review: combines an already-
computed ProjectScanResult and ProjectPlan (jarvis.core.project_scan,
jarvis.core.project_plan) with a fresh, read-only git diff summary
(jarvis.core.review_view) into a single structured verdict answering
"what changed, what might be wrong, what to check, and is this ready for
the next workflow step (commit)".

Distinct from the existing REPL 'review' keyword (jarvis.core.review_view
.format_review, unchanged) and from 'precommit' (which additionally runs
the test suite): this module never runs tests and never writes anything.
It is the structured counterpart to scan/plan/work - same "logic module +
formatter module" separation, same purity guarantee.

Never modifies the project: it re-uses scan_project()'s and
review_view's own read-only git invocations, and otherwise only reasons
over the ProjectScanResult/ProjectPlan objects passed in. No file writes,
no git mutations, no LLM calls, no API key required.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.core.project_plan import ProjectPlan
from jarvis.core.project_scan import ProjectScanResult
from jarvis.core.review_view import get_change_summary


@dataclass
class ProjectReview:
    """Structured pre-commit review. Every field is plain data - no side
    effects are implied or triggered by constructing one."""

    root_summary: str
    is_git_repo: bool
    working_tree_clean: bool | None  # None when unknown (not a repo, or git error)
    changed_file_count: int | None
    findings: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    checks_to_run: list[str] = field(default_factory=list)
    ready_for_commit: bool = False
    readiness_reason: str = ""


def _describe_git_state(is_repo: bool, changed_files: int | None, error: str | None) -> list[str]:
    findings: list[str] = []

    if not is_repo:
        findings.append("Not a git repository yet - there is no commit history to review against.")
        return findings

    if error is not None:
        findings.append(f"Git status could not be read ({error}).")
        return findings

    if changed_files == 0:
        findings.append("Working tree is clean - no uncommitted changes.")
    else:
        count = changed_files or 0
        file_word = "file" if count == 1 else "files"
        findings.append(f"{count} {file_word} with uncommitted changes.")

    return findings


def _describe_plan_state(plan: ProjectPlan | None) -> list[str]:
    if plan is None:
        return []

    findings: list[str] = []
    if not plan.suggested_order:
        return findings

    first_step = plan.suggested_order[0]
    if first_step.lower().startswith("no gaps detected"):
        findings.append("Latest plan found no outstanding gaps.")
    else:
        findings.append(f"Latest plan's next recommended step: {first_step}")

    return findings


def _gather_concerns(scan: ProjectScanResult, is_repo: bool, error: str | None) -> list[str]:
    concerns: list[str] = []

    if error is not None:
        concerns.append(f"Could not fully read git state: {error}")

    if not is_repo:
        concerns.append("No git history exists - there is nothing to compare uncommitted changes against yet.")

    if not scan.has_tests:
        concerns.append("No test suite was found - changes cannot be verified by an automated test run.")

    if is_repo and scan.git.commit_count == 0:
        concerns.append("Repository has no commits yet - this would be the first commit.")

    return concerns


def _suggest_checks(scan: ProjectScanResult, is_repo: bool, changed_files: int | None) -> list[str]:
    checks: list[str] = []

    if is_repo and changed_files:
        checks.append("Run 'review' in the JARVIS REPL to see the full diff of what changed.")

    if scan.has_tests:
        checks.append("Run 'precommit' in the JARVIS REPL to run the test suite before committing.")
    else:
        checks.append("Consider adding at least one test before committing further changes.")

    if is_repo and changed_files:
        checks.append("Confirm the changes match what was intended - no unrelated or accidental edits.")

    return checks


def _determine_readiness(
    is_repo: bool, error: str | None, changed_files: int | None, has_tests: bool
) -> tuple[bool, str]:
    if not is_repo:
        return False, "Not a git repository yet - initialize git before committing."

    if error is not None:
        return False, f"Git state could not be fully read ({error}) - resolve this before committing."

    if changed_files == 0:
        return False, "Nothing to commit - the working tree is already clean."

    if not has_tests:
        return True, "Changes are present and reviewable, but no test suite exists to verify them."

    return True, "Changes are present and a test suite exists - run 'precommit' to verify before committing."


def build_review(scan: ProjectScanResult, plan: ProjectPlan | None = None) -> ProjectReview:
    """Derive a structured pre-commit review from an already-computed
    ProjectScanResult (and optionally a ProjectPlan), plus one fresh,
    read-only git status check (jarvis.core.review_view.get_change_summary)
    for up-to-the-moment uncommitted-change info. Never writes anything.
    Deterministic given the same scan/plan/git state.

    Note: get_change_summary() always checks JARVIS_ROOT (same as
    status_view's usage), not scan.root - callers are expected to pass a
    scan of JARVIS_ROOT itself, exactly as run_cli_review() does.
    """
    change_summary = get_change_summary()

    findings: list[str] = []
    findings.extend(
        _describe_git_state(
            change_summary.is_repo, change_summary.changed_files, change_summary.error
        )
    )
    findings.extend(_describe_plan_state(plan))

    concerns = _gather_concerns(scan, change_summary.is_repo, change_summary.error)
    checks = _suggest_checks(scan, change_summary.is_repo, change_summary.changed_files)
    ready, reason = _determine_readiness(
        change_summary.is_repo, change_summary.error, change_summary.changed_files, scan.has_tests
    )

    working_tree_clean: bool | None
    if not change_summary.is_repo or change_summary.error is not None:
        working_tree_clean = None
    else:
        working_tree_clean = change_summary.changed_files == 0

    return ProjectReview(
        root_summary=str(scan.root),
        is_git_repo=change_summary.is_repo,
        working_tree_clean=working_tree_clean,
        changed_file_count=change_summary.changed_files,
        findings=findings,
        concerns=concerns,
        checks_to_run=checks,
        ready_for_commit=ready,
        readiness_reason=reason,
    )
