"""Formats a ProjectReview (jarvis.core.project_review) into
human-readable output for the CLI's 'jarvis review' entry point. Kept
separate from the review-derivation logic itself, mirroring
project_plan_view's/project_work_view's separation - no reasoning lives
here, only presentation."""

from __future__ import annotations

from jarvis.core.project_review import ProjectReview


def format_review_result(review: ProjectReview) -> str:
    lines: list[str] = []

    lines.append(f"Project review: {review.root_summary}")
    lines.append("")

    lines.append("1. Findings:")
    if review.findings:
        lines.extend(f"  - {item}" for item in review.findings)
    else:
        lines.append("  (no notable findings)")
    lines.append("")

    lines.append("2. Possible concerns:")
    if review.concerns:
        lines.extend(f"  - {item}" for item in review.concerns)
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("3. What to check:")
    if review.checks_to_run:
        lines.extend(f"  - {item}" for item in review.checks_to_run)
    else:
        lines.append("  (nothing further to check)")
    lines.append("")

    lines.append("4. Ready for commit:")
    verdict = "yes" if review.ready_for_commit else "no"
    lines.append(f"  {verdict} - {review.readiness_reason}")

    return "\n".join(lines)
