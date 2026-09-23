"""Tests for jarvis.core.help_view.format_help: pure display, no state or
side effects. Confirms the output includes the sandbox root, every CLI
command, example requests, and pointers to the planning and JARVIS.md
features - the actual content a new user needs, not just that it doesn't
crash."""

from __future__ import annotations

from jarvis.config import JARVIS_ROOT
from jarvis.core.help_view import CLI_COMMANDS, EXAMPLE_REQUESTS, format_help


def test_help_mentions_the_sandbox_root():
    result = format_help()
    assert str(JARVIS_ROOT) in result


def test_help_lists_every_cli_command():
    result = format_help()
    for name, _ in CLI_COMMANDS:
        # Strip anything after a space/slash (e.g. "history [N]" -> "history")
        base_name = name.split()[0].split("/")[0].strip()
        assert base_name in result


def test_help_includes_example_requests():
    result = format_help()
    for example in EXAMPLE_REQUESTS:
        assert example in result


def test_help_mentions_planning_workflow():
    result = format_help()
    assert "plan" in result.lower()
    assert "tasks" in result.lower()


def test_help_mentions_jarvis_md():
    result = format_help()
    assert "JARVIS.md" in result


def test_help_mentions_komanda_prefix():
    result = format_help()
    assert "komanda:" in result


def test_help_is_non_empty_and_multi_line():
    result = format_help()
    assert len(result.splitlines()) > 5


def test_help_never_raises():
    # Pure display function - must not raise under normal conditions.
    format_help()
