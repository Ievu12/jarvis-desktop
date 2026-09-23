"""Single source of truth for Task Execution risk classification.

Kept as its own module (rather than inlined into task_execution_plan.py)
specifically so the safety rules are easy to find, easy to audit, and
easy to unit-test in isolation - the property that matters most here is
that risk classification is a small, pure, deterministic function with
no hidden branches elsewhere in the codebase.

v1 scope: this module never executes anything and never inspects the
project's actual filesystem/git state - it only classifies the *textual
description* of a step (or a whole task request) into a risk level and,
where relevant, a specific block reason. Actually turning an
ExecutionStep into a real action (a run_command call, a write_file call,
...) still goes through the existing, separately-approval-gated tools
(jarvis.tools.shell, jarvis.tools.fs, ...) - nothing here grants any new
capability.
"""

from __future__ import annotations

from dataclasses import dataclass

# Risk levels, ordered from safest to most dangerous. Kept as plain
# strings (not an enum) to match the rest of the codebase's dataclass
# style (ProjectPlan, WorkItem, ...), which favors plain str fields with
# a fixed, documented set of values over enum types.
READ_ONLY = "read_only"
REVERSIBLE = "reversible"
IRREVERSIBLE = "irreversible"

_VALID_RISK_LEVELS = {READ_ONLY, REVERSIBLE, IRREVERSIBLE}

# Services that are entirely out of scope for v1. Matched as a
# case-insensitive substring of the step/task text - deliberately broad
# (better to over-flag and let a human notice than silently miss one).
# This is the one and only place this list lives; task_intent.py and
# task_execution_plan.py both import it rather than keeping their own
# copies, so extending or auditing this list never requires touching
# more than one file.
BLOCKED_EXTERNAL_SERVICES: tuple[str, ...] = (
    "instagram",
    "facebook",
    "gmail",
    "stripe",
    "twitter",
    "x.com",
    "linkedin",
    "slack",
    "discord",
    "whatsapp",
    "telegram",
    "google calendar",
    "google drive",
    "aws",
    "azure",
    "gcp",
)

# Shell/command execution keywords that must never be planned as an
# auto-runnable step in v1, even though jarvis.tools.shell already has
# its own separate, independent enforcement (allowlist + approval). This
# is a second, independent layer at the planning stage: a task
# description that talks about running shell commands should be
# labelled and routed to a human via the REPL, not silently promised as
# something Task Execution will run for them.
#
# Includes both English and Lithuanian phrasings - the command console
# (jarvis.core.command_console, reachable via the REPL's 'komanda:'
# prefix) accepts Lithuanian-language text, and this rule must catch a
# shell-execution request regardless of which language it's phrased in.
_SHELL_KEYWORDS: tuple[str, ...] = (
    "run command",
    "run a command",
    "run shell",
    "shell command",
    "execute command",
    "execute shell",
    "terminal command",
    "bash",
    "powershell",
    "subprocess",
    # Lithuanian
    "paleisti komandą",
    "paleisk komandą",
    "vykdyti komandą",
    "vykdyk komandą",
    "shell komandą",
    "apvalkalo komandą",
    "terminalo komandą",
    "komandinės eilutės",
)

# Keywords that make a step irreversible or high-consequence even though
# no external service or shell command is involved - these still require
# an explicit human decision (via the existing approval-gated REPL/shell
# path) and are never planned as "safe to do automatically".
#
# Includes both English and Lithuanian phrasings - see _SHELL_KEYWORDS's
# comment above for why.
_IRREVERSIBLE_KEYWORDS: tuple[str, ...] = (
    "delete",
    "remove",
    "force push",
    "push",
    "rm -rf",
    "drop table",
    "drop database",
    "overwrite",
    "reset --hard",
    "uninstall",
    "format",
    # Lithuanian
    "ištrinti",
    "ištrink",
    "pašalinti",
    "pašalink",
    "perrašyti",
    "perrašyk",
    "priverstinis push",
    "priverstin",  # covers "priverstinai", "priverstinis", etc.
)

# Keywords for steps that change project state but can be undone through
# ordinary means (git history, re-editing a file, ...) - still requires
# approval through the existing tools, just not flagged as irreversible.
#
# Includes both English and Lithuanian phrasings - see _SHELL_KEYWORDS's
# comment above for why.
_REVERSIBLE_KEYWORDS: tuple[str, ...] = (
    "write",
    "create",
    "add",
    "edit",
    "modify",
    "update",
    "rename",
    "move",
    "commit",
    "install",
    # Lithuanian
    "rašyti",
    "parašyk",
    "parašyti",
    "sukurti",
    "sukurk",
    "pridėti",
    "pridėk",
    "redaguoti",
    "redaguok",
    "atnaujinti",
    "atnaujink",
    "pervadinti",
    "pervadink",
    "perkelti",
    "perkelk",
    "commit'inti",
    "commit'ink",
    "įdiegti",
    "įdiek",
)


@dataclass(frozen=True)
class RiskClassification:
    """Plain result of classifying one piece of text. No side effects are
    implied or triggered by constructing one."""

    risk_level: str
    is_blocked: bool
    blocked_reason: str | None


def find_blocked_external_service(text: str) -> str | None:
    """Return the first blocked external service name mentioned in `text`
    (case-insensitive substring match), or None if none is mentioned.
    Pure string matching - no network access, no credential lookup."""
    lowered = text.lower()
    for service in BLOCKED_EXTERNAL_SERVICES:
        if service in lowered:
            return service
    return None


def mentions_shell_execution(text: str) -> bool:
    """Whether `text` talks about running a shell/terminal command. Pure
    string matching against _SHELL_KEYWORDS."""
    lowered = text.lower()
    return any(keyword in lowered for keyword in _SHELL_KEYWORDS)


def classify_risk(text: str) -> RiskClassification:
    """Classify a single piece of text (a step description, or a whole
    task request) into a RiskClassification. Deterministic and pure - no
    filesystem, network, or subprocess access of any kind.

    Precedence (most severe wins, checked in this fixed order):
      1. Mentions a blocked external service -> irreversible + blocked.
      2. Mentions shell/command execution -> irreversible + blocked
         (shell execution stays disabled in this stage regardless of
         wording).
      3. Mentions an irreversible-action keyword -> irreversible, not
         blocked (still routed through the existing approval-gated path).
      4. Mentions a reversible-action keyword -> reversible, not blocked.
      5. Otherwise -> read_only, not blocked.
    """
    service = find_blocked_external_service(text)
    if service is not None:
        return RiskClassification(
            risk_level=IRREVERSIBLE,
            is_blocked=True,
            blocked_reason=(
                f"Mentions '{service}', an external integration that is not enabled in "
                "this stage of JARVIS. Task Execution v1 never plans or performs actions "
                "against external services."
            ),
        )

    if mentions_shell_execution(text):
        return RiskClassification(
            risk_level=IRREVERSIBLE,
            is_blocked=True,
            blocked_reason=(
                "Mentions running a shell/terminal command. Shell command execution is "
                "not enabled in this stage of JARVIS - Task Execution v1 never plans an "
                "auto-run shell step."
            ),
        )

    lowered = text.lower()

    if any(keyword in lowered for keyword in _IRREVERSIBLE_KEYWORDS):
        return RiskClassification(risk_level=IRREVERSIBLE, is_blocked=False, blocked_reason=None)

    if any(keyword in lowered for keyword in _REVERSIBLE_KEYWORDS):
        return RiskClassification(risk_level=REVERSIBLE, is_blocked=False, blocked_reason=None)

    return RiskClassification(risk_level=READ_ONLY, is_blocked=False, blocked_reason=None)
