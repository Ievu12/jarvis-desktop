"""Non-interactive entry point for an automated (e.g. Windows Task
Scheduler) morning briefing run: 'python -m jarvis.cli.morning_briefing'.

Sets up the same LLMClient/Agent/tool registry/session history the
interactive REPL and 'jarvis voice' use (see jarvis.cli.main.run_cli_voice,
which this mirrors), then runs jarvis.morning_routine.run_morning_routine
exactly once and exits. Needs ANTHROPIC_API_KEY (a real Agent.step() call
is made, same as any conversational turn) and speaks aloud via the
existing jarvis.voice.text_to_speech module - both unmodified by this
script.

To actually change WHEN this runs automatically, change the trigger time
on the "JARVIS Morning Briefing" Windows Task Scheduler task (see
jarvis.morning_routine's module docstring for the exact PowerShell), not
this file - this script has no built-in timer of its own; every run
performs the briefing immediately.

Exit code is 0 if the briefing completed (a content brief was produced
and spoken, even if Instagram context was unavailable - that is a
documented graceful-degradation case, not a failure), non-zero if it
could not run at all (e.g. ANTHROPIC_API_KEY missing). Mirrors
jarvis.cli.daily_instagram_report's exit-code convention so a scheduled
task's "Last Run Result" is meaningful without parsing stdout.
"""

from __future__ import annotations

import sys

from jarvis.cli.main import _ensure_utf8_stdio, build_registry
from jarvis.config import ANTHROPIC_API_KEY, JARVIS_ROOT
from jarvis.core.agent import Agent
from jarvis.core.llm import LLMClient
from jarvis.core.project_notes import load_project_notes
from jarvis.core.secrets import mask_secret
from jarvis.morning_routine import run_morning_routine
from jarvis.session.store import load_history


def run() -> int:
    """Runs one morning briefing and returns a process exit code (0 =
    completed, 1 = could not start) rather than calling sys.exit()
    itself, so it stays testable without a real process exit - mirrors
    jarvis.cli.daily_instagram_report.run()'s shape."""
    _ensure_utf8_stdio()

    print(f"JARVIS - Rytinis pranešimas ({JARVIS_ROOT})")
    print(f"API key: {mask_secret(ANTHROPIC_API_KEY)}")

    if not ANTHROPIC_API_KEY:
        print(
            "ANTHROPIC_API_KEY nenustatytas - rytinis pranešimas negali veikti "
            "be jo (reikalingas tikras Agent.step() iškvietimas)."
        )
        return 1

    notes_result = load_project_notes(JARVIS_ROOT)
    if notes_result.notice:
        print(f"[notice] {notes_result.notice}")

    llm = LLMClient(project_notes=notes_result.content)
    registry = build_registry()
    agent = Agent(llm, registry)

    load_result = load_history()
    if load_result.warning:
        print(f"[warning] {load_result.warning}")
    history = load_result.history

    run_morning_routine(agent, history)
    return 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
