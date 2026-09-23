"""Sandboxed shell tool.

Design constraints, all enforced in code (not just documented):
  - Fixed allowlist of logical command names. No override, not even via
    approval - this exists specifically so an approval-prompt mistake or a
    prompt-injected instruction cannot smuggle through an arbitrary command.
  - No shell interpretation. subprocess.run() is called with a list of
    argv tokens and shell=False - there is no string for a shell to parse,
    so metacharacters (&&, |, ;, backticks, $(...)) have no special effect.
  - Every argument that looks like a filesystem path is resolved through
    the sandbox gate (jarvis.core.sandbox) before the command runs.
  - Working directory is always pinned to JARVIS_ROOT.
  - Every invocation requires approval via confirm_side_effect() - no
    "remember for this session" shortcut.
  - Hard timeout ceiling the caller cannot exceed.
  - Output is captured and length-capped before being returned.
  - v0.1 allowlist is limited to read-only inspection commands, 'pytest'
    (runs this project's own test suite under its own venv interpreter),
    'python' (runs a single .py script file - no -c inline code, no -m
    arbitrary module execution), 'git-init' (no arguments, refuses if
    already a repo), 'git-add' (stages explicit file paths only - no
    wildcards, no -A/--all/-f/-p), and 'git-commit' (requires an explicit
    -m message, no --amend/-a/--all/--no-verify): nothing here pushes,
    installs, or deletes.
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass

from jarvis.config import JARVIS_ROOT
from jarvis.core.approval import confirm_side_effect
from jarvis.core.sandbox import check
from jarvis.tools.base import Tool, ToolResult

DEFAULT_TIMEOUT_SECONDS = 30
MAX_TIMEOUT_SECONDS = 120
MAX_OUTPUT_CHARS = 8000

_POWERSHELL = "powershell.exe"
_PS_BASE_ARGS = ["-NoProfile", "-NonInteractive", "-Command"]


@dataclass(frozen=True)
class AllowedCommand:
    """One allowlisted logical command: how to build its real argv."""

    description: str
    # Given the user-supplied args (already validated), returns the full
    # argv to pass to subprocess.run(shell=False).
    build_argv: Callable[[list[str]], list[str]]


def _ps_quote(value: str) -> str:
    """Escape a value for safe interpolation inside a single-quoted
    PowerShell string literal. PowerShell's own escaping rule for a
    literal single quote inside '...' is to double it - this is not
    shell=True, so this only protects the PowerShell -Command string
    itself from being broken out of, not from OS-level injection."""
    return value.replace("'", "''")


def _ps_get_childitem(args: list[str]) -> list[str]:
    target = _ps_quote(args[0] if args else ".")
    return [_POWERSHELL, *_PS_BASE_ARGS, f"Get-ChildItem -Path '{target}' -Force"]


def _ps_get_content(args: list[str]) -> list[str]:
    if not args:
        raise ValueError("'cat' requires exactly one file path argument")
    target = _ps_quote(args[0])
    return [_POWERSHELL, *_PS_BASE_ARGS, f"Get-Content -Path '{target}' -Raw"]


# Git flags that are safe to forward for read-only inspection: no
# --format/--pretty (placeholder execution risk via %x00 tricks is low but
# unnecessary to allow), no -O/--output, no --ext-diff, no -c/config
# overrides, no pager/exec-adjacent flags. Only plain, bounded flags.
_GIT_SAFE_FLAGS = {
    "git-status": {"-s", "--short", "-b", "--branch"},
    "git-log": {"--oneline", "-n", "--graph", "--all"},
    "git-diff": {"--stat", "--name-only", "--cached"},
}


# pytest flags safe to forward: verbosity/selection/stop-on-first-failure
# only. No -p (arbitrary plugin loading), no --co-together with exec
# plugins, no -k combined with shell-adjacent injection (it's a plain
# string match expression, passed as a single argv token, never
# shell-interpreted). No coverage/report-writing flags that could write
# files outside the normal test-output flow.
_PYTEST_SAFE_FLAGS = {"-v", "-vv", "-q", "-x", "-k", "--tb=short", "--tb=long", "--tb=no"}


def _pytest(args: list[str]) -> list[str]:
    i = 0
    while i < len(args):
        arg = args[i]
        if arg.startswith("-"):
            if arg not in _PYTEST_SAFE_FLAGS:
                raise ValueError(
                    f"Flag '{arg}' is not permitted for 'pytest'. "
                    f"Allowed flags: {', '.join(sorted(_PYTEST_SAFE_FLAGS))}."
                )
            if arg == "-k":
                i += 1  # -k takes a value; the value itself is not a flag
        i += 1
    # Runs under the same interpreter JARVIS itself is running under, so
    # it always uses this project's own venv and installed pytest - no
    # PATH ambiguity, no risk of picking up a different environment.
    return [sys.executable, "-m", "pytest", *args]


# Wildcards/whole-tree selectors that must never be accepted as a "path"
# argument to git-add - each would stage far more than the caller can see
# was staged, defeating the "explicit paths only" safety property.
_GIT_ADD_FORBIDDEN_PATH_TOKENS = {".", "*", "**", "./", ":/", "--all", "-A"}

# Flags never permitted for git-add: -p/--patch opens an interactive
# prompt (would hang until the timeout), -f/--force bypasses .gitignore
# (could stage a secret like a stray .env), -A/--all/-u stage broadly
# rather than the explicit paths the caller gave.
_GIT_ADD_FORBIDDEN_FLAGS = {"-p", "--patch", "-f", "--force", "-A", "--all", "-u", "--update"}


def _git_add(args: list[str]) -> list[str]:
    paths = [a for a in args if not a.startswith("-")]
    flags = [a for a in args if a.startswith("-")]

    if not paths:
        raise ValueError("'git-add' requires at least one explicit file path argument.")

    for flag in flags:
        if flag in _GIT_ADD_FORBIDDEN_FLAGS:
            raise ValueError(f"Flag '{flag}' is not permitted for 'git-add'.")
        raise ValueError(
            f"Flag '{flag}' is not permitted for 'git-add'. No flags are allowed - "
            "pass explicit file paths only."
        )

    for path_arg in paths:
        if path_arg in _GIT_ADD_FORBIDDEN_PATH_TOKENS:
            raise ValueError(
                f"'{path_arg}' is not permitted for 'git-add' - stage explicit file "
                "paths individually, not the whole tree."
            )

    return ["git", "add", "--", *paths]


# Flags never permitted for git-commit: --amend rewrites/destroys a prior
# commit (meaningfully higher risk than a fresh commit, deserves its own
# separate decision if ever added), -a/--all auto-stages everything
# modified (defeats the intentional stage-then-commit flow git-add
# provides), --no-verify bypasses any hooks the target repo has, -p/-e
# open an interactive prompt/editor that would hang until the timeout.
_GIT_COMMIT_FORBIDDEN_FLAGS = {
    "--amend",
    "-a",
    "--all",
    "--no-verify",
    "-n",
    "-p",
    "--patch",
    "-e",
    "--edit",
}


def _git_commit(args: list[str]) -> list[str]:
    if "-m" not in args:
        raise ValueError(
            "'git-commit' requires a commit message via -m \"<message>\". "
            "A bare 'git commit' would open an interactive editor and hang "
            "until the timeout, so this is not permitted."
        )

    m_index = args.index("-m")
    if m_index + 1 >= len(args):
        raise ValueError("'-m' requires a commit message argument immediately after it.")
    message = args[m_index + 1]
    if not message.strip():
        raise ValueError("Commit message must not be empty.")

    # Anything besides exactly '-m <message>' is rejected outright - no
    # extra flags of any kind, known-forbidden or not, keeps this to the
    # single supported shape rather than growing an ad hoc safe-flags list
    # for a command this consequential.
    remaining = [a for i, a in enumerate(args) if i not in (m_index, m_index + 1)]
    if remaining:
        forbidden_found = [a for a in remaining if a in _GIT_COMMIT_FORBIDDEN_FLAGS]
        if forbidden_found:
            raise ValueError(
                f"Flag(s) {forbidden_found} are not permitted for 'git-commit'."
            )
        raise ValueError(
            f"Unexpected argument(s) {remaining} for 'git-commit'. Only "
            '-m "<message>" is supported.'
        )

    return ["git", "commit", "-m", message]


def _python_run(args: list[str]) -> list[str]:
    if not args:
        raise ValueError(
            "'python' requires a script file path as its first argument. "
            "Arbitrary inline code (-c) and module execution (-m) are not permitted."
        )

    script = args[0]
    if script.startswith("-"):
        raise ValueError(
            f"'{script}' looks like a flag, not a script path. Only "
            "'python <file>.py [args...]' is supported - no -c, no -m, no "
            "other interpreter flags."
        )
    if not script.endswith(".py"):
        raise ValueError(
            f"'{script}' does not look like a .py file. Only running a "
            "specific Python script file is permitted."
        )

    # Runs under the same interpreter JARVIS itself is running under (its
    # own venv), with cwd pinned to JARVIS_ROOT by the caller - so the
    # script executes inside the target project, using JARVIS's Python
    # installation. Remaining args are passed through to the script
    # unchanged; any that look like paths are already sandbox-checked by
    # _validate_path_args before this function ever runs.
    return [sys.executable, script, *args[1:]]


def _git_init(args: list[str]) -> list[str]:
    if args:
        raise ValueError(
            f"'git-init' does not accept any arguments (got {args}). Only a "
            "plain, default 'git init' in the project directory is permitted "
            "- no branch-name flag, template directory, or bare-repo option."
        )

    git_dir = JARVIS_ROOT / ".git"
    if git_dir.exists():
        raise ValueError(
            f"{JARVIS_ROOT} is already a git repository (.git exists). "
            "Refusing to re-run git-init."
        )

    return ["git", "init"]


def _git_readonly(logical_name: str, subcommand: str):
    safe_flags = _GIT_SAFE_FLAGS[logical_name]

    def build(args: list[str]) -> list[str]:
        for arg in args:
            if arg.startswith("-") and arg not in safe_flags:
                raise ValueError(
                    f"Flag '{arg}' is not permitted for '{logical_name}'. "
                    f"Allowed flags: {', '.join(sorted(safe_flags)) or '(none)'}."
                )
        return ["git", subcommand, *args]

    return build


# The allowlist. Keys are the logical command name the LLM must request.
# v0.1 is deliberately read-only: nothing here writes, deletes, installs,
# or pushes. Extend only with commands that cannot mutate state.
ALLOWED_COMMANDS: dict[str, AllowedCommand] = {
    "ls": AllowedCommand(
        description="List directory contents (PowerShell Get-ChildItem)",
        build_argv=_ps_get_childitem,
    ),
    "cat": AllowedCommand(
        description="Print a file's contents (PowerShell Get-Content)",
        build_argv=_ps_get_content,
    ),
    "git-status": AllowedCommand(
        description="git status",
        build_argv=_git_readonly("git-status", "status"),
    ),
    "git-log": AllowedCommand(
        description="git log",
        build_argv=_git_readonly("git-log", "log"),
    ),
    "git-diff": AllowedCommand(
        description="git diff",
        build_argv=_git_readonly("git-diff", "diff"),
    ),
    "pytest": AllowedCommand(
        description="Run this project's own test suite (python -m pytest)",
        build_argv=_pytest,
    ),
    "git-add": AllowedCommand(
        description=(
            "git add - stage explicit file paths only. No -A/--all/wildcards, "
            "no -p/--patch, no -f/--force. Never commits or pushes."
        ),
        build_argv=_git_add,
    ),
    "git-commit": AllowedCommand(
        description=(
            'git commit -m "<message>" - commits currently staged changes. '
            "Requires an explicit message. No --amend, no -a/--all, no "
            "--no-verify. Never pushes."
        ),
        build_argv=_git_commit,
    ),
    "python": AllowedCommand(
        description=(
            "Run a single .py script file with its own arguments. No -c "
            "(inline code) and no -m (arbitrary module execution) - only "
            "a specific project script file is permitted."
        ),
        build_argv=_python_run,
    ),
    "git-init": AllowedCommand(
        description=(
            "git init - initialize the JARVIS project directory as a git "
            "repository. No arguments accepted. Refuses if it's already a "
            "git repository."
        ),
        build_argv=_git_init,
    ),
}


def _validate_path_args(args: list[str]) -> ToolResult | None:
    """Check every non-flag argument as a potential path against the
    sandbox. Returns a denial ToolResult if any argument resolves outside
    JARVIS_ROOT, else None. Flags (leading '-') are never treated as paths.
    """
    for arg in args:
        if arg.startswith("-"):
            continue
        decision = check(arg)
        if not decision.inside_sandbox:
            return ToolResult(
                ok=False,
                output=(
                    f"Denied: argument '{arg}' resolves to '{decision.path}', "
                    f"which is outside the JARVIS sandbox ({JARVIS_ROOT}). "
                    "The shell tool does not support one-time exceptions for "
                    "arguments; use read_file/write_file if you need approved "
                    "out-of-sandbox access."
                ),
            )
    return None


class ShellTool(Tool):
    name = "run_command"
    description = (
        "Run a strictly allowlisted command inside the JARVIS project "
        "directory. Only logical command names in the allowlist are "
        "accepted (see 'command' enum); arbitrary shell strings are never "
        "executed. Every invocation requires explicit user approval and is "
        "subject to a timeout. Most allowlisted commands are read-only "
        "inspection; 'pytest' runs this project's own test suite; "
        "'python' runs a single .py script file (no -c, no -m); "
        "'git-init' initializes the project directory as a git repository "
        "(no arguments, refuses if already a repo); 'git-add' stages "
        'explicit file paths; \'git-commit\' commits currently staged '
        'changes with a required -m "<message>" (no --amend, no -a/--all, '
        "no --no-verify). No git command ever pushes."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "Logical command name to run.",
                "enum": sorted(ALLOWED_COMMANDS.keys()),
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Arguments for the command. Any argument that looks like a "
                    "path must resolve inside the JARVIS project directory."
                ),
            },
            "timeout_seconds": {
                "type": "integer",
                "description": (
                    f"Optional timeout override, capped at {MAX_TIMEOUT_SECONDS}s. "
                    f"Defaults to {DEFAULT_TIMEOUT_SECONDS}s."
                ),
            },
        },
        "required": ["command"],
    }

    def run(
        self,
        *,
        command: str,
        args: list[str] | None = None,
        timeout_seconds: int | None = None,
    ) -> ToolResult:
        args = args or []

        allowed = ALLOWED_COMMANDS.get(command)
        if allowed is None:
            return ToolResult(
                ok=False,
                output=(
                    f"Denied: '{command}' is not on the shell tool allowlist. "
                    f"Allowed commands: {', '.join(sorted(ALLOWED_COMMANDS))}."
                ),
            )

        # git-commit's only argument is a free-text commit message, not a
        # filesystem path - running it through the generic path-boundary
        # check would be both meaningless and fragile (an unusual but
        # legitimate message could spuriously look path-like). _git_commit
        # itself fully controls and validates the args shape.
        if command != "git-commit":
            path_denial = _validate_path_args(args)
            if path_denial is not None:
                return path_denial

        try:
            argv = allowed.build_argv(args)
        except ValueError as e:
            return ToolResult(ok=False, output=f"Denied: {e}")

        effective_timeout = min(timeout_seconds or DEFAULT_TIMEOUT_SECONDS, MAX_TIMEOUT_SECONDS)
        effective_timeout = max(effective_timeout, 1)

        description = f"run allowlisted command '{command}' ({allowed.description}) with args {args}"
        # KeyboardInterrupt during this approval prompt is intentionally not
        # caught here - see fs.py tools for the same rationale: it must
        # propagate so the agent loop aborts the turn rather than treating
        # the interrupt as a normal denial or, worse, an approval.
        if not confirm_side_effect(description):
            return ToolResult(ok=False, output="Denied by user.")

        try:
            completed = subprocess.run(
                argv,
                shell=False,
                cwd=JARVIS_ROOT,
                capture_output=True,
                text=True,
                timeout=effective_timeout,
            )
        except subprocess.TimeoutExpired:
            return ToolResult(
                ok=False,
                output=f"Command timed out after {effective_timeout}s: {command}",
            )
        except OSError as e:
            return ToolResult(ok=False, output=f"Failed to execute command: {e}")

        output = completed.stdout or ""
        if completed.returncode != 0:
            output += f"\n[stderr]\n{completed.stderr or ''}"
            output += f"\n[exit code: {completed.returncode}]"

        truncated = output[:MAX_OUTPUT_CHARS]
        if len(output) > MAX_OUTPUT_CHARS:
            truncated += f"\n[output truncated at {MAX_OUTPUT_CHARS} chars]"

        return ToolResult(ok=completed.returncode == 0, output=truncated)
