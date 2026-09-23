"""Deterministic, rule-based parsing of a free-text user task request into
a structured TaskIntent - the first stage of the Task Execution layer.

Mirrors jarvis.core.project_scan's own posture: no LLM call, no
filesystem/git access, no side effects of any kind. parse_task_intent()
is a pure function of its input string - the same text always produces
the same TaskIntent. This module only *reads and classifies the request
text itself*; reasoning about the project's current state (to turn the
intent into concrete steps) is task_execution_plan.py's job, exactly as
project_plan.py reasons about project_scan.py's output rather than
re-detecting anything itself.

v1 deliberately supports only a small, fixed set of action categories.
Anything it can't confidently categorize is labelled "unknown" rather
than guessed at - an unrecognized request should be routed to a human
via the REPL, never silently forced into the wrong bucket.
"""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.core.task_safety import find_blocked_external_service, mentions_shell_execution

# Fixed, deterministic action categories. Checked in this order - the
# first matching category wins, so ordering here doubles as priority
# (e.g. a request that mentions both git and an external service is
# categorized as "external_integration" first, since that's the one that
# blocks everything else regardless of what else the text mentions).
EXTERNAL_INTEGRATION = "external_integration"
SHELL_EXECUTION = "shell_execution"
GIT_OPERATION = "git_operation"
TEST_RUN = "test_run"
FILE_CHANGE = "file_change"
UNKNOWN = "unknown"

_VALID_CATEGORIES = (
    EXTERNAL_INTEGRATION,
    SHELL_EXECUTION,
    GIT_OPERATION,
    TEST_RUN,
    FILE_CHANGE,
    UNKNOWN,
)

# Categories v1 can actually turn into an ExecutionPlan with concrete
# steps. external_integration and shell_execution are recognized (so the
# user gets a clear, specific "why not" message) but never "supported" -
# there is no plan v1 will build for them beyond the block explanation.
_SUPPORTED_CATEGORIES = {GIT_OPERATION, TEST_RUN, FILE_CHANGE}

# Includes both English and Lithuanian phrasings - the command console
# (jarvis.core.command_console, reachable via the REPL's 'komanda:'
# prefix) accepts Lithuanian-language text, and classification must work
# regardless of which language a request is phrased in.
_GIT_KEYWORDS: tuple[str, ...] = (
    "git",
    "commit",
    "branch",
    "merge",
    "repository",
    "repo",
    # Lithuanian
    "commit'ą",
    "commit'inti",
    "šaką",  # branch
    "saugyklą",  # repository
    "repozitorij",
)

_TEST_KEYWORDS: tuple[str, ...] = (
    "test",
    "pytest",
    "unit test",
    "test suite",
    # Lithuanian
    "testus",
    "testą",
    "testavim",  # covers "testavimą", "testavimo"
)

_FILE_CHANGE_KEYWORDS: tuple[str, ...] = (
    "file",
    "readme",
    "write",
    "create",
    "edit",
    "update",
    "rename",
    "move",
    "delete",
    "add a",
    "modify",
    # Lithuanian
    "failą",
    "failo",
    "failus",
    "rašyk",
    "parašyk",
    "parašyti",
    "sukurk",
    "sukurti",
    "redaguok",
    "redaguoti",
    "atnaujink",
    "atnaujinti",
    "pervadink",
    "pervadinti",
    "perkelk",
    "perkelti",
    "ištrink",
    "ištrinti",
    "pridėk",
    "pridėti",
)


@dataclass(frozen=True)
class TaskIntent:
    """Plain, structured description of what a free-text task request
    appears to be about. No side effects are implied or triggered by
    constructing one - this is pure classification of text."""

    raw_text: str
    action_category: str
    mentions_external_service: bool
    external_service_name: str | None
    mentions_shell_execution: bool
    is_supported: bool


def _classify_category(text: str, *, blocked_service: str | None, wants_shell: bool) -> str:
    if blocked_service is not None:
        return EXTERNAL_INTEGRATION
    if wants_shell:
        return SHELL_EXECUTION

    lowered = text.lower()
    if any(keyword in lowered for keyword in _GIT_KEYWORDS):
        return GIT_OPERATION
    if any(keyword in lowered for keyword in _TEST_KEYWORDS):
        return TEST_RUN
    if any(keyword in lowered for keyword in _FILE_CHANGE_KEYWORDS):
        return FILE_CHANGE
    return UNKNOWN


def parse_task_intent(raw_text: str) -> TaskIntent:
    """Classify a free-text task request. Deterministic and pure - never
    touches the filesystem, git, network, or the LLM. An empty/blank
    request is classified as UNKNOWN and not supported, rather than
    raising - callers decide how to present that to the user."""
    text = raw_text.strip()

    if not text:
        return TaskIntent(
            raw_text=raw_text,
            action_category=UNKNOWN,
            mentions_external_service=False,
            external_service_name=None,
            mentions_shell_execution=False,
            is_supported=False,
        )

    blocked_service = find_blocked_external_service(text)
    wants_shell = mentions_shell_execution(text)
    category = _classify_category(text, blocked_service=blocked_service, wants_shell=wants_shell)

    return TaskIntent(
        raw_text=raw_text,
        action_category=category,
        mentions_external_service=blocked_service is not None,
        external_service_name=blocked_service,
        mentions_shell_execution=wants_shell,
        is_supported=category in _SUPPORTED_CATEGORIES,
    )
