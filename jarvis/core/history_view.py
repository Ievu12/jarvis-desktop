"""Formats the audit log (jarvis.core.audit) into a human-readable summary
for the CLI's 'history' command. Read-only - never writes to the log."""

from __future__ import annotations

import json

from jarvis.config import AUDIT_LOG_FILE

DEFAULT_ENTRY_LIMIT = 20

_EVENT_LABELS = {
    "side_effect_confirmation": "side effect",
    "outside_sandbox_request": "outside-sandbox access",
    "tool_call": "tool call",
    "external_action_confirmation": "external action",
}


def _format_external_action_entry(ts: str, entry: dict) -> str:
    # Distinct label from "side effect" even though the APPROVED/DENIED
    # vocabulary is the same shape - so external actions (which can be
    # irreversible outside the project, unlike a local file write) are
    # visibly identifiable in the log on their own, not lumped in with
    # ordinary local side effects.
    approved = entry.get("approved")
    interrupted = entry.get("interrupted", False)
    if approved is True:
        status = "APPROVED"
    elif approved is False:
        status = "INTERRUPTED (denied)" if interrupted else "DENIED"
    else:
        status = "?"

    service = entry.get("service", "?")
    action = entry.get("action", "?")
    risk_level = entry.get("risk_level", "?")
    return f"[{ts}] {status:<20} external action: {service}.{action} (risk: {risk_level})"


def _format_tool_call_entry(ts: str, entry: dict) -> str:
    # Distinct from the approval events above: a tool_call entry records
    # every tool the agent decided to invoke, not just ones that went
    # through a y/N approval prompt (read-only tools like
    # get_workflow_state have no approval step, but are still worth
    # seeing here) - so it uses its own status vocabulary (OK/ERROR)
    # rather than APPROVED/DENIED, which would misleadingly imply a human
    # was asked.
    ok = entry.get("ok")
    status = "OK" if ok else "ERROR"
    tool = entry.get("tool", "?")
    tool_input = entry.get("input")
    detail = f"{tool}({tool_input})" if tool_input else tool
    return f"[{ts}] {status:<20} tool call: {detail}"


def _format_entry(entry: dict) -> str:
    ts = entry.get("ts", "?")
    event = entry.get("event", "?")

    if event == "tool_call":
        return _format_tool_call_entry(ts, entry)
    if event == "external_action_confirmation":
        return _format_external_action_entry(ts, entry)

    label = _EVENT_LABELS.get(event, event)
    approved = entry.get("approved")
    interrupted = entry.get("interrupted", False)

    if approved is True:
        status = "APPROVED"
    elif approved is False:
        status = "INTERRUPTED (denied)" if interrupted else "DENIED"
    else:
        status = "?"

    detail = entry.get("description") or entry.get("path") or ""

    return f"[{ts}] {status:<20} {label}: {detail}"


def format_history(limit: int = DEFAULT_ENTRY_LIMIT) -> str:
    """Read the audit log and return a human-readable summary of the most
    recent `limit` entries. This log spans all runs of JARVIS in this
    project directory, not just the current process - the output says so
    explicitly rather than implying a session boundary that doesn't exist
    in the underlying data. It only reads the current (post-rotation) log
    file; older entries that have been rotated into a timestamped archive
    (see jarvis.core.audit) are not included here.
    """
    if not AUDIT_LOG_FILE.exists():
        return "No audit history yet - nothing has been approved or denied in this project."

    entries: list[dict] = []
    with open(AUDIT_LOG_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # skip any malformed line rather than failing the whole view

    if not entries:
        return "No audit history yet - nothing has been approved or denied in this project."

    total = len(entries)
    shown = entries[-limit:]

    lines = [
        f"Showing {len(shown)} of {total} entries in the current audit log "
        "(spans all sessions, not archived/rotated history):",
        "",
    ]
    lines.extend(_format_entry(e) for e in shown)

    if total > limit:
        lines.append("")
        lines.append(f"... {total - limit} earlier entries not shown. See {AUDIT_LOG_FILE} for the full log.")

    return "\n".join(lines)
