"""A settable "please navigate the GUI to this Reel project" signal -
same settable-global-handler pattern as jarvis.core.approval's own
set_side_effect_handler()/set_outside_sandbox_handler() (see that
module's own docstring for the identical reasoning), used here because
jarvis.tools.base.Tool.run() has no return channel back to the GUI
shell beyond its own plain ToolResult text (jarvis.core.agent.Agent
.step() itself returns only a plain string - extending that shared,
CLI-and-GUI-used return shape just for one GUI-only behavior would be
far more invasive than this small, additive side channel).

jarvis.tools.reel_chat.CreateReelDraftTool (the only tool that ever
calls request_navigation()) sets this after successfully creating a new
jarvis.reel_generator project; jarvis.gui.app._handle_result() checks
and clears it via consume_navigation_request() right after every
AgentStepResult, then calls self._navigate("reel_generator",
open_project_id=...) - the EXACT SAME mechanism
jarvis.content_studio's own "Preview / Continue" hand-off already uses
(see jarvis.gui.app._navigate()'s own docstring), just triggered from a
chat command instead of a button click.

Deliberately NOT a queue (a single most-recent request is always
sufficient - there is no scenario where two navigation requests need to
queue up within one agent step) and deliberately NOT tool-specific
beyond this one field, so a second Reel Generator project id case never
turns this into a growing pile of ad-hoc globals - if a future tool
needs the same "signal the GUI to navigate somewhere" capability for a
different view, it should get its own equally small, equally
documented module, not a shared "generic event bus" that would invite
every future tool to route GUI side effects through here."""

from __future__ import annotations

import threading

_lock = threading.Lock()
_pending_project_id: str | None = None


def request_navigation(project_id: str) -> None:
    """Called by CreateReelDraftTool.run() after it has ACTUALLY created
    a new jarvis.reel_generator project (never before - this is not a
    speculative/optimistic signal). Overwrites any previous unconsumed
    request - only the most recent navigation matters within one agent
    step."""
    global _pending_project_id
    with _lock:
        _pending_project_id = project_id


def consume_navigation_request() -> str | None:
    """Returns the pending project id (if any) and clears it - called by
    jarvis.gui.app._handle_result() after every AgentStepResult, whether
    or not a navigation was actually requested this turn. Returns None
    (a no-op) when nothing is pending, which is the common case for
    every chat turn that isn't "create a Reel"."""
    global _pending_project_id
    with _lock:
        project_id = _pending_project_id
        _pending_project_id = None
    return project_id
