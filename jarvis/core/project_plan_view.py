"""Formats a ProjectPlan (jarvis.core.project_plan) into human-readable
output for the CLI's 'plan' entry point. Kept separate from the planning
logic itself, mirroring project_scan_view's separation from project_scan
- no reasoning lives here, only presentation."""

from __future__ import annotations

from jarvis.core.project_plan import ProjectPlan


def _numbered(items: list[str]) -> list[str]:
    return [f"  {i}. {item}" for i, item in enumerate(items, start=1)]


def format_plan(plan: ProjectPlan) -> str:
    lines: list[str] = []

    lines.append(f"Project plan: {plan.root_summary}")
    lines.append("")

    lines.append("1. Current state:")
    lines.extend(f"  - {item}" for item in plan.current_state)
    lines.append("")

    lines.append("2. Findings:")
    if plan.findings:
        lines.extend(f"  - {item}" for item in plan.findings)
    else:
        lines.append("  (no notable findings)")
    lines.append("")

    lines.append("3. Recommended next steps:")
    lines.extend(f"  - {item}" for item in plan.recommended_steps)
    lines.append("")

    lines.append("4. Suggested order of work:")
    lines.extend(_numbered(plan.suggested_order))

    return "\n".join(lines)
