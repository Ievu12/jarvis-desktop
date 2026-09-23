"""Deterministic, structured final pre-commit check: the last safe gate
before a human runs `git commit` themselves. Builds directly on top of
jarvis.core.project_review.build_review (git state, findings, concerns,
readiness) and adds the actual list of changed file names, plus an
explicit list of checks to perform before committing.

Distinct from the existing REPL 'precommit' keyword
(jarvis.core.precommit_view.format_precommit, unchanged), which actually
runs the test suite via subprocess. This module is intentionally
execution-free: it never runs tests, never writes files, never mutates
git history, never calls the LLM. It only reads: an already-computed
ProjectReview plus one read-only `git status --short` listing
(jarvis.core.review_view.get_changed_file_names). Running the actual test
suite remains the REPL 'precommit' command's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.core.project_review import ProjectReview
from jarvis.core.review_view import get_changed_file_names


@dataclass
class PrecommitCheck:
    """Structured final pre-commit verdict. Every field is plain data -
    no side effects are implied or triggered by constructing one."""

    root_summary: str
    is_git_repo: bool
    working_tree_clean: bool | None
    changed_files: list[str]
    findings: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    checks_to_run: list[str] = field(default_factory=list)
    ready_for_commit: bool = False
    readiness_reason: str = ""


def build_precommit_check(review: ProjectReview) -> PrecommitCheck:
    """Derive a final pre-commit verdict from an already-computed
    ProjectReview, plus one fresh, read-only listing of changed file
    names. Never runs tests, never writes anything, never mutates git.
    Deterministic given the same review and git state.

    Note: get_changed_file_names() always checks JARVIS_ROOT (same as
    project_review's own get_change_summary() usage), not any particular
    review.root_summary - callers are expected to build `review` from a
    scan of JARVIS_ROOT itself, exactly as run_cli_precommit() does.
    """
    changed_files = get_changed_file_names() or []

    checks = list(review.checks_to_run)
    checks.append("Never run 'git commit' automatically - only a human should decide to commit.")

    return PrecommitCheck(
        root_summary=review.root_summary,
        is_git_repo=review.is_git_repo,
        working_tree_clean=review.working_tree_clean,
        changed_files=changed_files,
        findings=list(review.findings),
        concerns=list(review.concerns),
        checks_to_run=checks,
        ready_for_commit=review.ready_for_commit,
        readiness_reason=review.readiness_reason,
    )
