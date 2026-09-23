"""Formats a ProjectScanResult (jarvis.core.project_scan) into
human-readable output for the CLI's 'scan' entry point. Kept separate
from the scanning logic itself so the structured result stays the
reusable, machine-consumable artifact (e.g. for a future 'plan' command)
and this module is purely presentation - no scanning logic lives here."""

from __future__ import annotations

from jarvis.core.project_scan import ProjectScanResult


def format_scan_result(result: ProjectScanResult) -> str:
    lines: list[str] = []

    lines.append(f"Project scan: {result.root}")
    lines.append("")

    lines.append("Top-level contents:")
    if result.top_level_entries:
        for entry in result.top_level_entries:
            lines.append(f"  {entry}")
    else:
        lines.append("  (empty or unreadable)")
    lines.append("")

    lines.append("README: " + (", ".join(result.readme_files) if result.readme_files else "none found"))
    lines.append(
        "Manifest/dependency files: "
        + (", ".join(result.manifest_files) if result.manifest_files else "none found")
    )
    lines.append(".gitignore: " + ("present" if result.has_gitignore else "not present"))
    lines.append("JARVIS.md: " + ("present" if result.has_jarvis_md else "not present"))
    lines.append(
        "Tests: "
        + (", ".join(result.test_locations) if result.has_tests else "none found")
    )
    lines.append("")

    lines.append("Git:")
    git = result.git
    if not git.is_repo:
        lines.append("  Not a git repository.")
    else:
        if git.error:
            lines.append(f"  Could not fully inspect git state: {git.error}")
        else:
            commit_word = "commit" if git.commit_count == 1 else "commits"
            lines.append(f"  {git.commit_count} {commit_word}.")
            if git.latest_commit_summary:
                lines.append(f"  Latest: {git.latest_commit_summary}")
            if git.has_uncommitted_changes:
                lines.append("  Uncommitted changes present.")
            elif git.has_uncommitted_changes is False:
                lines.append("  Working tree clean.")

    if result.notes:
        lines.append("")
        lines.append("Notes:")
        for note in result.notes:
            lines.append(f"  - {note}")

    return "\n".join(lines)
