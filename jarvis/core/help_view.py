"""Formats the 'help' command's output: a genuinely useful orientation
for a new session, not just a command list. Pure display - no state, no
side effects, nothing to persist or recover."""

from __future__ import annotations

from jarvis.config import JARVIS_ROOT

CLI_COMMANDS = (
    ("help", "Show this help."),
    ("status", "One-glance view of where you are in the workflow, with a suggested next step."),
    ("integrations", "Status of every configured external integration (email, etc.) - never shows secrets."),
    ("scan", "Survey this project and propose a JARVIS.md draft (never saved automatically)."),
    ("voice", "Start voice mode: microphone in (Lithuanian, lt-LT) / speaker out. Say a stop phrase (e.g. 'sustok') or Ctrl+C to return here."),
    ("morning", "Run the morning briefing now (greeting, date, Instagram content ideas) - the same routine the scheduled 'JARVIS Morning Briefing' task runs automatically."),
    ("history [N]", "Review recent approval/denial decisions (default: last 20)."),
    ("tasks", "See the current task list / plan."),
    ("review", "See everything currently uncommitted (git status + diff)."),
    ("precommit", "Same as review, plus a real run of the test suite."),
    ("komanda: <text>", "Natural-language command (Lithuanian-friendly) run through TaskPlanner -> TaskRunner, with approval prompts - separate from normal conversation."),
    ("exit / quit", "Leave JARVIS."),
)

EXAMPLE_REQUESTS = (
    "list the files in this project",
    "read config.py and explain what it does",
    "search for every place that uses the word TODO",
    "create a script called hello.py that prints 'hello world', then run it",
    "stage and commit the changes with the message 'fix typo'",
)


def format_help() -> str:
    lines: list[str] = []

    lines.append(f"JARVIS is a sandboxed project assistant, currently working in:")
    lines.append(f"  {JARVIS_ROOT}")
    lines.append("")
    lines.append(
        "It can read, write, search, and edit files in this project; run tests and "
        "scripts; and work with git (init/add/commit) - all sandboxed to this "
        "directory, and every action that changes something asks for your approval "
        "first."
    )
    lines.append("")

    lines.append("CLI commands:")
    width = max(len(name) for name, _ in CLI_COMMANDS)
    for name, desc in CLI_COMMANDS:
        lines.append(f"  {name.ljust(width)}  {desc}")
    lines.append("")

    lines.append("Just type what you want in plain language. A few examples:")
    for example in EXAMPLE_REQUESTS:
        lines.append(f'  "{example}"')
    lines.append("")

    lines.append(
        "For a multi-step request, JARVIS will lay out a plan first and check off "
        "each step as it goes - type 'tasks' any time to see progress."
    )
    lines.append("")
    lines.append(
        "To give JARVIS standing context about this project (what it is, "
        "conventions, things to avoid), create a JARVIS.md file in the project "
        "root - JARVIS loads it automatically at the start of every session. Not "
        "sure where to start? Type 'scan' and JARVIS will survey the project and "
        "propose a first draft for you to review."
    )
    lines.append("")
    lines.append(
        "The typical workflow ties these together: scan (understand the project) "
        "-> plan (JARVIS lays out steps for multi-step work) -> do the work "
        "(approval-gated as it goes) -> review/precommit (check everything before "
        "committing) -> commit. Type 'status' any time to see where you are in "
        "that sequence and what to do next."
    )

    return "\n".join(lines)
