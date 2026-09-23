"""Deterministic, structured commit plan: the last decision point before a
human actually runs `git commit`. Builds directly on top of
jarvis.core.project_precommit.build_precommit_check (git state, findings,
concerns, readiness) and adds the staged/unstaged file split needed to
know exactly what a commit would include right now.

This module NEVER runs `git commit`, `git add`, or any other
git-mutating command, and never writes a file - it only reads: an
already-computed PrecommitCheck plus one fresh, read-only
`git status --short` split into staged vs. unstaged file names
(jarvis.core.review_view.get_staged_and_unstaged_file_names). Actually
running `git commit` is done elsewhere (jarvis.cli.main.run_cli_commit /
the REPL 'commit' command), reusing the same argv-building and validation
already used by the approval-gated shell tool
(jarvis.tools.shell._git_commit) - this module has no subprocess access
and cannot mutate anything, by construction.

Deliberately conservative about what "ready to commit" means here: only
files already staged are considered committable. Unstaged changes are
surfaced as a separate, explicit warning so nothing is committed by
surprise - `jarvis commit` never runs `git add` on the caller's behalf.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.core.project_precommit import PrecommitCheck
from jarvis.core.review_view import get_staged_and_unstaged_file_names


@dataclass
class CommitPlan:
    """Structured commit plan/decision point. Every field is plain data -
    no side effects are implied or triggered by constructing one, and
    nothing here performs or authorizes an actual commit."""

    root_summary: str
    is_git_repo: bool
    staged_files: list[str]
    unstaged_files: list[str]
    findings: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    can_commit: bool = False
    block_reason: str = ""


def _determine_commit_gate(
    check: PrecommitCheck, staged: list[str], unstaged_only: bool
) -> tuple[bool, str]:
    if not check.is_git_repo:
        return False, "Not a git repository yet - initialize git before committing."

    if not check.ready_for_commit:
        return False, check.readiness_reason or "The pre-commit check did not pass."

    if not staged:
        if unstaged_only:
            return False, (
                "Nothing is staged for commit, but there are unstaged changes - "
                "stage the files you want to commit first (e.g. with 'git-add' in "
                "the JARVIS REPL)."
            )
        return False, "Nothing is staged for commit."

    return True, "Staged changes are present and the pre-commit check passed."


def build_commit_plan(check: PrecommitCheck) -> CommitPlan:
    """Derive a structured commit plan from an already-computed
    PrecommitCheck, plus one fresh, read-only staged/unstaged file
    listing. Never runs git commit, never writes anything. Deterministic
    given the same check and git state.

    Note: get_staged_and_unstaged_file_names() always checks JARVIS_ROOT
    (same as project_review's/project_precommit's own git-reading
    functions), not any particular check.root_summary - callers are
    expected to build `check` from a scan of JARVIS_ROOT itself, exactly
    as run_cli_commit() does.
    """
    split = get_staged_and_unstaged_file_names()
    staged, unstaged = split if split is not None else ([], [])

    can_commit, block_reason = _determine_commit_gate(check, staged, bool(unstaged) and not staged)

    return CommitPlan(
        root_summary=check.root_summary,
        is_git_repo=check.is_git_repo,
        staged_files=staged,
        unstaged_files=unstaged,
        findings=list(check.findings),
        concerns=list(check.concerns),
        can_commit=can_commit,
        block_reason=block_reason,
    )
