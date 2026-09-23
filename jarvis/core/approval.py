"""Human-in-the-loop approval prompts.

Two kinds of approval are distinct on purpose:
  - confirm_side_effect: "this tool is about to write/delete inside the
    sandbox, ok to proceed?"
  - confirm_outside_sandbox: "this path is OUTSIDE the JARVIS project
    directory, ok to proceed just this once?" - always defaults to No.

Both are called directly by name from jarvis.tools.fs/shell (e.g. "from
jarvis.core.approval import confirm_side_effect"), so swapping their
BEHAVIOR for the desktop GUI (jarvis.gui) cannot be done by reassigning
those names after import - Python binds the function object at import
time, not a lookup back into this module. Instead, each public function
here delegates to a swappable module-level handler (a plain function
reference), defaulting to the original input()-based terminal prompt.
jarvis.gui.app calls set_side_effect_handler()/set_outside_sandbox_handler()
once at GUI startup to redirect these to a GUI dialog; the terminal REPL
(jarvis.cli.main) never calls either setter, so its behavior is
completely unchanged - this module's default IS the terminal behavior
that existed before the GUI existed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from jarvis.core.audit import log_event


def _terminal_confirm_side_effect(description: str) -> bool:
    # Re-raises KeyboardInterrupt (after printing a notice) rather than
    # catching it - confirm_side_effect() below is the single place that
    # logs the interrupted-as-denied outcome, so the caller (agent loop /
    # CLI) still unwinds the current turn instead of silently continuing
    # as if nothing happened.
    try:
        answer = input(f"\n[JARVIS] About to: {description}\nProceed? [y/N] ").strip().lower()
    except KeyboardInterrupt:
        print("\n[JARVIS] Interrupted - treating as denied.")
        raise
    return answer == "y"


def _terminal_confirm_outside_sandbox(resolved_path: Path) -> bool:
    try:
        answer = input(
            f"\n[JARVIS] Requested path is OUTSIDE the JARVIS project directory:\n"
            f"  {resolved_path}\n"
            f"This is a one-time exception, not remembered for the session.\n"
            f"Allow this single access? [y/N] "
        ).strip().lower()
    except KeyboardInterrupt:
        print("\n[JARVIS] Interrupted - treating as denied.")
        raise
    return answer == "y"


_side_effect_handler: Callable[[str], bool] = _terminal_confirm_side_effect
_outside_sandbox_handler: Callable[[Path], bool] = _terminal_confirm_outside_sandbox


def set_side_effect_handler(handler: Callable[[str], bool] | None) -> None:
    """Redirects confirm_side_effect()'s prompt to `handler` (e.g. a GUI
    dialog) instead of terminal input(). Pass None to restore the
    default terminal behavior. jarvis.gui.app is the only caller of
    this outside tests."""
    global _side_effect_handler
    _side_effect_handler = handler or _terminal_confirm_side_effect


def set_outside_sandbox_handler(handler: Callable[[Path], bool] | None) -> None:
    """Same as set_side_effect_handler(), for confirm_outside_sandbox()."""
    global _outside_sandbox_handler
    _outside_sandbox_handler = handler or _terminal_confirm_outside_sandbox


def confirm_side_effect(description: str) -> bool:
    try:
        approved = _side_effect_handler(description)
    except KeyboardInterrupt:
        log_event(
            "side_effect_confirmation",
            description=description,
            approved=False,
            interrupted=True,
        )
        raise
    log_event("side_effect_confirmation", description=description, approved=approved)
    return approved


def confirm_outside_sandbox(resolved_path: Path) -> bool:
    try:
        approved = _outside_sandbox_handler(resolved_path)
    except KeyboardInterrupt:
        log_event(
            "outside_sandbox_request",
            path=str(resolved_path),
            approved=False,
            interrupted=True,
        )
        raise
    log_event(
        "outside_sandbox_request",
        path=str(resolved_path),
        approved=approved,
    )
    return approved
