"""The sandbox boundary gate.

This module is the ONLY place path-boundary decisions get made. Tools must
never call open()/os functions on a user-supplied path directly - they must
route through resolve_in_sandbox() or request_outside_access() first.

Fails closed: any ambiguity or error results in denial, never silent access.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from jarvis.config import JARVIS_ROOT


class SandboxViolation(Exception):
    """Raised when a path resolves outside JARVIS_ROOT and was not approved."""

    def __init__(self, requested: str, resolved: Path):
        self.requested = requested
        self.resolved = resolved
        super().__init__(
            f"Path '{requested}' resolves to '{resolved}', which is outside "
            f"the JARVIS sandbox ({JARVIS_ROOT})."
        )


@dataclass
class PathDecision:
    path: Path
    inside_sandbox: bool


def _resolve(path: str | Path) -> Path:
    """Resolve a path to its canonical absolute form, following symlinks,
    without requiring the path to exist yet (needed for write targets)."""
    p = Path(path)
    if not p.is_absolute():
        p = JARVIS_ROOT / p
    # resolve() with strict=False still normalizes '..' and symlinks for the
    # portion of the path that does exist.
    return p.resolve(strict=False)


def check(path: str | Path) -> PathDecision:
    """Resolve a path and report whether it falls inside the sandbox.
    Performs no side effects and raises nothing."""
    resolved = _resolve(path)
    try:
        inside = resolved.is_relative_to(JARVIS_ROOT)
    except AttributeError:  # pragma: no cover - py<3.9 fallback
        inside = str(resolved).startswith(str(JARVIS_ROOT))
    return PathDecision(path=resolved, inside_sandbox=inside)


def resolve_in_sandbox(path: str | Path) -> Path:
    """Resolve a path and enforce that it is inside JARVIS_ROOT.

    Raises SandboxViolation if it is not. Callers that want to offer the
    user an approval prompt for out-of-sandbox access should use
    request_outside_access() instead of catching this exception silently.
    """
    decision = check(path)
    if not decision.inside_sandbox:
        raise SandboxViolation(str(path), decision.path)
    return decision.path


# Type for the approval callback the CLI layer supplies: given the resolved
# out-of-sandbox path, return True to allow this one operation, False to deny.
ApprovalCallback = Callable[[Path], bool]


def request_outside_access(
    path: str | Path,
    approval_callback: ApprovalCallback,
) -> Path:
    """Resolve a path; if it's inside the sandbox, return it immediately.
    If it's outside, invoke approval_callback to ask the human. Denial
    raises SandboxViolation - there is no silent fallback.
    """
    decision = check(path)
    if decision.inside_sandbox:
        return decision.path

    approved = approval_callback(decision.path)
    if not approved:
        raise SandboxViolation(str(path), decision.path)
    return decision.path
