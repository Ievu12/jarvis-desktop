"""Formats a PrecommitCheck (jarvis.core.project_precommit) into
human-readable output for the CLI's 'jarvis precommit' entry point. Kept
separate from the check-derivation logic itself, mirroring
project_review_view's separation from project_review - no reasoning
lives here, only presentation."""

from __future__ import annotations

from jarvis.core.project_precommit import PrecommitCheck


def format_precommit_check(check: PrecommitCheck) -> str:
    lines: list[str] = []

    lines.append(f"Pre-commit check: {check.root_summary}")
    lines.append("")

    lines.append("1. Findings:")
    if check.findings:
        lines.extend(f"  - {item}" for item in check.findings)
    else:
        lines.append("  (no notable findings)")
    lines.append("")

    lines.append("2. Changed files:")
    if check.changed_files:
        lines.extend(f"  - {name}" for name in check.changed_files)
    elif check.is_git_repo:
        lines.append("  (none - working tree is clean)")
    else:
        lines.append("  (not applicable - not a git repository)")
    lines.append("")

    lines.append("3. Possible concerns:")
    if check.concerns:
        lines.extend(f"  - {item}" for item in check.concerns)
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("4. Checks to run before committing:")
    if check.checks_to_run:
        lines.extend(f"  - {item}" for item in check.checks_to_run)
    else:
        lines.append("  (nothing further to check)")
    lines.append("")

    lines.append("5. Ready for commit:")
    verdict = "yes" if check.ready_for_commit else "no"
    lines.append(f"  {verdict} - {check.readiness_reason}")
    lines.append("")
    lines.append(
        "jarvis precommit never runs git commit or any other git-mutating command - "
        "committing is always your decision, done by hand."
    )

    return "\n".join(lines)
