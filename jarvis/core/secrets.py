"""Secret-handling safeguards: masking for any output path, a startup
check that .env is actually gitignored before JARVIS trusts it as a safe
place for the user to keep an API key, and ensuring JARVIS's own state
directory (.jarvis/ - session history, audit log) is excluded from git in
whatever project JARVIS is pointed at, not just its own.

Nothing in this module ever logs or prints an unmasked secret.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def mask_secret(value: str | None, *, visible: int = 4) -> str:
    """Return a display-safe representation of a secret. Never returns the
    original value. Used anywhere a key might otherwise end up in debug or
    verbose output.
    """
    if not value:
        return "<not set>"
    if len(value) <= visible:
        return "*" * len(value)
    return f"{'*' * (len(value) - visible)}{value[-visible:]}"


def redact_secret(text: str, *, secret: str | None = None) -> str:
    """Scrub a known secret value out of an arbitrary string (e.g. an
    exception message from a third-party SDK) before it is printed or
    logged. Defense in depth on top of mask_secret(): this handles the
    case where a raw key value ends up embedded in error text rather than
    being displayed directly.

    Defaults to redacting the configured ANTHROPIC_API_KEY if `secret` is
    not given.
    """
    if secret is None:
        from jarvis.config import ANTHROPIC_API_KEY as secret  # noqa: PLC0415

    if not secret:
        return text
    return text.replace(secret, mask_secret(secret))


def check_env_gitignored(project_root: Path) -> list[str]:
    """Verify that a .env file, if present, would actually be ignored by
    git. Returns a list of human-readable warning strings - empty means
    "no problem found". Never raises for expected conditions (no .env, no
    git repo, git not installed) - those are just different warning
    messages, not exceptions, so a broken git toolchain never blocks
    startup.
    """
    warnings: list[str] = []
    env_file = project_root / ".env"

    if not env_file.exists():
        return warnings  # nothing to protect yet

    git_dir = project_root / ".git"
    if not git_dir.exists():
        warnings.append(
            f".env exists at {env_file} but this directory is not a git "
            "repository yet. If you run 'git init' later, make sure "
            ".gitignore excludes .env BEFORE your first commit."
        )
        return warnings

    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(env_file)],
            cwd=project_root,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        warnings.append(f"Could not run 'git check-ignore' to verify .env is ignored: {e}")
        return warnings

    # git check-ignore exit codes: 0 = ignored, 1 = not ignored, >1 = error
    if result.returncode == 1:
        warnings.append(
            f".env exists at {env_file} and is NOT covered by .gitignore. "
            "Add '.env' to .gitignore before storing any secrets in it."
        )
    elif result.returncode > 1:
        warnings.append(
            "'git check-ignore' failed unexpectedly while checking .env; "
            "could not confirm it is protected from commits."
        )

    return warnings


def ensure_jarvis_dir_gitignored(project_root: Path) -> str | None:
    """If project_root is a git repository and .jarvis/ is not already
    ignored, append a '.jarvis/' entry to its .gitignore (creating the
    file if it doesn't exist yet) so JARVIS's own session/audit state is
    never accidentally tracked into the target project's history.

    Never overwrites or truncates an existing .gitignore - only appends.
    Returns a human-readable notice string describing what was done, or
    None if nothing needed to change. Never raises for expected conditions
    (no .git, git not installed, .jarvis/ doesn't exist yet) - a broken
    git toolchain or a project that isn't a repo yet just means no action
    is taken, not a startup failure.
    """
    git_dir = project_root / ".git"
    if not git_dir.exists():
        return None  # not a git repo (yet) - nothing to protect

    jarvis_dir = project_root / ".jarvis"
    gitignore_path = project_root / ".gitignore"

    try:
        # A trailing slash tells git to treat this as a directory query
        # regardless of whether .jarvis/ exists on disk yet - without it,
        # git check-ignore treats a non-existent, slash-less path as
        # ambiguous (could be a file or a directory) and directory-only
        # patterns (e.g. a broad '.*/' rule) won't match it, even though
        # they would once the directory actually exists.
        result = subprocess.run(
            ["git", "check-ignore", "-q", str(jarvis_dir) + "/"],
            cwd=project_root,
            capture_output=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None  # can't verify - don't guess, don't write

    if result.returncode == 0:
        return None  # already ignored, nothing to do

    entry = ".jarvis/"
    try:
        if gitignore_path.exists():
            existing = gitignore_path.read_text(encoding="utf-8")
            # Re-check textually in case check-ignore's exit code above
            # was ambiguous (returncode > 1) rather than a clean "not
            # ignored" - avoid appending a duplicate entry either way.
            if any(line.strip() == entry.rstrip("/") or line.strip() == entry
                   for line in existing.splitlines()):
                return None
            separator = "" if existing.endswith("\n") or not existing else "\n"
            gitignore_path.write_text(existing + separator + entry + "\n", encoding="utf-8")
        else:
            gitignore_path.write_text(entry + "\n", encoding="utf-8")
    except OSError as e:
        return f"Could not update {gitignore_path} to exclude .jarvis/: {e}"

    return (
        f"Added '.jarvis/' to {gitignore_path} so JARVIS's own session and "
        "audit files are never accidentally committed to this project."
    )
