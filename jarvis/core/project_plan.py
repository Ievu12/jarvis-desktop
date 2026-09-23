"""Deterministic project plan derived from a ProjectScanResult
(jarvis.core.project_scan): a structured, rule-based recommendation of
what to do next, built entirely from scan findings - no LLM, no
re-scanning, no filesystem/git access of its own. This module never
touches the project directly; it only consumes the already-computed scan
result passed in, keeping scan and plan modular (scan owns detection,
plan owns reasoning about what the detected state implies).

Never modifies the project - build_plan() is a pure function over its
input.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jarvis.core.project_scan import ProjectScanResult


@dataclass
class ProjectPlan:
    """Structured plan derived from a scan. Every field is plain data so
    a caller (CLI formatter, or any future consumer) can use it without
    re-deriving anything from the scan itself."""

    root_summary: str
    current_state: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    recommended_steps: list[str] = field(default_factory=list)
    suggested_order: list[str] = field(default_factory=list)


def _describe_current_state(scan: ProjectScanResult) -> list[str]:
    state: list[str] = []

    if not scan.top_level_entries:
        state.append("The project directory is empty (or unreadable).")
    else:
        count = len(scan.top_level_entries)
        entry_word = "entry" if count == 1 else "entries"
        state.append(f"{count} top-level {entry_word} present.")

    if scan.readme_files:
        state.append(f"Documented with: {', '.join(scan.readme_files)}.")
    else:
        state.append("No README present.")

    if scan.manifest_files:
        state.append(f"Dependency/package manifest present: {', '.join(scan.manifest_files)}.")
    else:
        state.append("No recognized dependency/package manifest.")

    if scan.has_tests:
        state.append(f"Tests found at: {', '.join(scan.test_locations)}.")
    else:
        state.append("No tests found.")

    git = scan.git
    if not git.is_repo:
        state.append("Not under git version control.")
    elif git.error:
        state.append(f"Git repository present, but its state could not be fully read ({git.error}).")
    elif git.commit_count == 0:
        state.append("Git repository initialized, but no commits yet.")
    else:
        commit_word = "commit" if git.commit_count == 1 else "commits"
        tree_state = "with uncommitted changes" if git.has_uncommitted_changes else "with a clean working tree"
        state.append(f"Git repository with {git.commit_count} {commit_word}, {tree_state}.")

    return state


def _describe_findings(scan: ProjectScanResult) -> list[str]:
    # scan.notes already captures the "missing X" signals in scan's own
    # words; reuse them directly rather than re-deriving the same facts
    # under slightly different wording, which would risk the two
    # diverging as either module evolves.
    findings = list(scan.notes)

    if scan.has_jarvis_md:
        findings.append("JARVIS.md is present - JARVIS has standing project context.")
    else:
        findings.append("No JARVIS.md yet - JARVIS has no standing project context between sessions.")

    if scan.git.is_repo and scan.git.has_uncommitted_changes:
        findings.append("There are uncommitted changes in the working tree.")

    return findings


def _recommend_steps(scan: ProjectScanResult) -> list[str]:
    steps: list[str] = []
    git = scan.git

    if not git.is_repo:
        steps.append("Initialize a git repository (`git-init`) to start tracking history.")
    elif git.commit_count == 0:
        steps.append("Make an initial commit once there is something worth saving.")

    if not scan.readme_files:
        steps.append("Add a README describing what the project is and how to use it.")

    if not scan.manifest_files:
        steps.append(
            "Add a dependency/package manifest appropriate for the project's language "
            "(e.g. pyproject.toml, requirements.txt, package.json)."
        )

    if not scan.has_tests:
        steps.append("Add a test suite (or at least a first test) to verify behavior.")

    if not scan.has_jarvis_md:
        steps.append(
            "Create a JARVIS.md with project context and conventions (run 'scan' in the "
            "REPL for a proposed draft)."
        )

    if git.is_repo and git.has_uncommitted_changes:
        steps.append("Review the uncommitted changes ('review' or 'precommit') before committing them.")

    if not steps:
        steps.append(
            "No gaps detected by this scan - the project has a README, a manifest, tests, "
            "git history, and JARVIS.md. Proceed with whatever feature or fix is next."
        )

    return steps


def _suggest_order(steps: list[str]) -> list[str]:
    """Order recommended_steps by a fixed priority: foundational
    setup (git/manifest) before documentation/tests before JARVIS-specific
    conveniences (JARVIS.md) before review/commit hygiene. Deterministic
    given the same input list - no randomness, no LLM judgment call."""
    priority_keywords = (
        "Initialize a git repository",
        "Make an initial commit",
        "Add a dependency/package manifest",
        "Add a README",
        "Add a test suite",
        "Create a JARVIS.md",
        "Review the uncommitted changes",
    )

    def sort_key(step: str) -> int:
        for i, keyword in enumerate(priority_keywords):
            if step.startswith(keyword):
                return i
        return len(priority_keywords)  # anything unrecognized sorts last, stable order preserved

    return sorted(steps, key=sort_key)


def build_plan(scan: ProjectScanResult) -> ProjectPlan:
    """Derive a structured plan purely from an already-computed
    ProjectScanResult. Never touches the filesystem or git itself - all
    project detection is scan's responsibility; this function only
    reasons about what the scan already found. Deterministic: the same
    scan result always produces the same plan.
    """
    recommended = _recommend_steps(scan)
    return ProjectPlan(
        root_summary=str(scan.root),
        current_state=_describe_current_state(scan),
        findings=_describe_findings(scan),
        recommended_steps=recommended,
        suggested_order=_suggest_order(recommended),
    )
