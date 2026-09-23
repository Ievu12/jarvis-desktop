"""Formats a combined diff + test-run summary for the CLI's 'precommit'
command: the single checkpoint that answers both "what changed" and "does
it still work" before committing. Reuses review_view's diff summary and
the shell tool's own pytest argv-building logic (jarvis.tools.shell._pytest)
so the test invocation is identical to what the model's 'pytest' shell
command would run - no second, divergent way of running tests.

Like review_view, this runs directly (not through the approval-gated
shell tool) since it's a human-initiated CLI convenience with nothing to
approve - the user is the one asking to see it, not the model acting on
their own initiative. It is therefore not written to the audit log, which
is specifically for approval decisions (see jarvis.core.audit); there is
none here, consistent with review_view's precedent.

Test suites can themselves have side effects (writing temp files, hitting
a local database, etc.) if the project's own tests do that - this is the
same trust boundary already implicit in the shell tool's 'pytest' command,
not a new one introduced here.
"""

from __future__ import annotations

import subprocess

from jarvis.config import JARVIS_ROOT
from jarvis.core.review_view import format_review
from jarvis.tools.shell import DEFAULT_TIMEOUT_SECONDS, MAX_OUTPUT_CHARS, _pytest


def _run_tests() -> str:
    argv = _pytest([])
    try:
        completed = subprocess.run(
            argv,
            shell=False,
            cwd=JARVIS_ROOT,
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return f"Test run timed out after {DEFAULT_TIMEOUT_SECONDS}s."
    except OSError as e:
        return f"Failed to run tests: {e}"

    output = completed.stdout or ""
    if completed.returncode != 0:
        output += f"\n[stderr]\n{completed.stderr or ''}"

    truncated = output[:MAX_OUTPUT_CHARS]
    if len(output) > MAX_OUTPUT_CHARS:
        truncated += f"\n[output truncated at {MAX_OUTPUT_CHARS} chars]"

    status = "PASSED" if completed.returncode == 0 else f"FAILED (exit code {completed.returncode})"
    return f"Test run: {status}\n\n{truncated.strip()}"


def format_precommit() -> str:
    """Return the diff summary (same as 'review') followed by a test run
    and pass/fail summary. Never raises - test failures and git errors are
    both reported as ordinary output text, not exceptions.
    """
    diff_summary = format_review()
    test_summary = _run_tests()
    return f"{diff_summary}\n\n{'-' * 40}\n\n{test_summary}"
