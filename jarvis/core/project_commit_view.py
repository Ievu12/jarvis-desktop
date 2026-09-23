"""Formats a CommitPlan (jarvis.core.project_commit) into human-readable
output for the CLI's 'jarvis commit' entry point and the REPL 'commit'
command. Kept separate from the plan-derivation logic itself, mirroring
project_precommit_view's separation from project_precommit - no
reasoning lives here, only presentation."""

from __future__ import annotations

from jarvis.core.project_commit import CommitPlan


def format_commit_plan(plan: CommitPlan) -> str:
    lines: list[str] = []

    lines.append(f"Commit plan: {plan.root_summary}")
    lines.append("")

    lines.append("1. Findings:")
    if plan.findings:
        lines.extend(f"  - {item}" for item in plan.findings)
    else:
        lines.append("  (no notable findings)")
    lines.append("")

    lines.append("2. Staged for commit:")
    if plan.staged_files:
        lines.extend(f"  - {name}" for name in plan.staged_files)
    elif plan.is_git_repo:
        lines.append("  (none)")
    else:
        lines.append("  (not applicable - not a git repository)")
    lines.append("")

    lines.append("3. Unstaged (will NOT be committed):")
    if plan.unstaged_files:
        lines.extend(f"  - {name}" for name in plan.unstaged_files)
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("4. Possible concerns:")
    if plan.concerns:
        lines.extend(f"  - {item}" for item in plan.concerns)
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("5. Can commit now:")
    verdict = "yes" if plan.can_commit else "no"
    lines.append(f"  {verdict} - {plan.block_reason}")
    lines.append("")
    lines.append(
        "jarvis commit never runs 'git commit' without your explicit y/N confirmation, "
        "and never runs 'git add' on your behalf."
    )

    return "\n".join(lines)
