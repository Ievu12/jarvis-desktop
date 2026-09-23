"""Read-only data aggregator for the desktop dashboard's panels (Home,
Tasks, Activity, Integrations, Automations). This module is the ONE
place the new dashboard views read JARVIS state from - it contains NO
new business logic of its own, only thin wrappers that call existing,
unmodified sources and reshape their output into plain dataclasses the
UI can render:

  - get_integration_statuses() -> jarvis.integrations.manager
    .IntegrationsManager.list_statuses() (already the single source of
    truth for connector status - unchanged).
  - get_tasks() -> jarvis.session.tasks.load_tasks() (unchanged).
  - get_recent_activity() -> reads jarvis.config.AUDIT_LOG_FILE's raw
    JSON-lines, the same file jarvis.core.history_view.format_history()
    reads for the CLI - but returns structured ActivityEntry objects for
    cards/rows instead of a pre-formatted text block, since the CLI's
    formatter is display logic tied to a terminal, not a reusable data
    API. No new write path, no change to the audit log's own format.
  - get_automations() -> queries Windows Task Scheduler (via
    PowerShell's Get-ScheduledTask/Get-ScheduledTaskInfo, read-only) for
    the JARVIS-prefixed scheduled tasks created in earlier work (morning
    briefing, Instagram daily report) - there was no existing Python
    reader for these; this is the one new piece of genuinely new code
    here, and it only reads, never creates/modifies/deletes a scheduled
    task.

Every function here degrades gracefully (never raises to the caller) -
a dashboard panel showing "no data available" is always preferable to
crashing the whole window over one panel's data source being
unavailable.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone

from jarvis.cli.main import build_integration_registry
from jarvis.config import AUDIT_LOG_FILE
from jarvis.integrations.manager import IntegrationsManager, IntegrationStatusInfo
from jarvis.session.tasks import Task, load_tasks

# --- integrations --------------------------------------------------------------------


def get_integration_statuses() -> list[IntegrationStatusInfo]:
    """Every registered connector's current status - identical data to
    the 'jarvis integrations' CLI command / REPL 'integrations' keyword,
    just returned as data instead of printed. Never raises: a fresh
    IntegrationsManager is built per call (cheap - no network I/O, only
    local credential-presence checks), so a transient failure in one
    connector's is_configured() surfaces as that one connector's ERROR
    status (see IntegrationsManager.get_status()'s own docstring), not
    a crash of this whole call.
    """
    manager = IntegrationsManager(build_integration_registry())
    return manager.list_statuses()


# --- tasks -----------------------------------------------------------------------------


def get_tasks() -> list[Task]:
    """The current project task list - identical data to the CLI 'tasks'
    command. load_tasks() already degrades to an empty list on a
    missing/corrupted tasks file; nothing added here."""
    return load_tasks()


# --- activity (audit log) --------------------------------------------------------------


@dataclass(frozen=True)
class ActivityEntry:
    """One audit-log entry, reshaped for the Activity panel - a strict
    subset/relabeling of the raw JSON-lines fields jarvis.core.audit
    writes, never a new field jarvis.core.audit doesn't already record.
    `summary` is a short, human-readable one-liner (built the same way
    jarvis.core.history_view._format_entry() builds its text, but as
    plain data instead of a pre-joined string) for a card/row to display
    directly."""

    timestamp: str
    event_type: str
    summary: str
    ok: bool | None  # True/False when the entry records a clear outcome, None otherwise


_MAX_SUMMARY_LENGTH = 80


def _one_line(text: str) -> str:
    """Collapses a possibly multi-line detail (e.g. a write_file diff
    preview stored in the audit log's `description` field) into a
    single line short enough for a UI row/card - a dashboard row is not
    the place to render a full diff. The full entry remains available
    in the audit log file itself for anyone who needs it."""
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    if len(first_line) > _MAX_SUMMARY_LENGTH:
        return first_line[: _MAX_SUMMARY_LENGTH - 1] + "…"
    return first_line


def _summarize_entry(entry: dict) -> ActivityEntry:
    event = entry.get("event", "?")
    ts = entry.get("ts", "?")

    if event == "tool_call":
        ok = entry.get("ok")
        tool = entry.get("tool", "?")
        return ActivityEntry(timestamp=ts, event_type=event, summary=f"Tool: {tool}", ok=ok)

    if event == "external_action_confirmation":
        approved = entry.get("approved")
        service = entry.get("service", "?")
        action = entry.get("action", "?")
        return ActivityEntry(
            timestamp=ts, event_type=event, summary=f"{service}.{action}", ok=approved,
        )

    approved = entry.get("approved")
    detail = entry.get("description") or entry.get("path") or event
    return ActivityEntry(timestamp=ts, event_type=event, summary=_one_line(str(detail)), ok=approved)


def get_recent_activity(limit: int = 20) -> list[ActivityEntry]:
    """The most recent `limit` audit-log entries (newest first), as
    structured data. Reads the same AUDIT_LOG_FILE
    jarvis.core.history_view.format_history() reads, with the same
    graceful handling of a missing file or malformed lines (skipped,
    never raised) - this function does not open a second write path or
    change the log's format in any way."""
    if not AUDIT_LOG_FILE.exists():
        return []

    entries: list[dict] = []
    try:
        with open(AUDIT_LOG_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []

    recent = entries[-limit:]
    recent.reverse()  # newest first, for a UI feed
    return [_summarize_entry(e) for e in recent]


# --- automations (Windows Task Scheduler) -----------------------------------------------


@dataclass(frozen=True)
class AutomationInfo:
    """One JARVIS-related Windows Task Scheduler task's current status -
    read-only snapshot, never used to create/modify/delete a task."""

    name: str
    enabled: bool
    next_run: str | None
    last_run: str | None


# Every scheduled task name JARVIS's own setup work has created so far
# (see RELEASE.md / earlier project history) - a fixed, known list
# rather than "every task whose name contains JARVIS", so a person's
# unrelated scheduled task that happens to mention "jarvis" is never
# shown here as if it were one of this project's automations.
_KNOWN_AUTOMATION_TASK_NAMES = (
    "JARVIS Morning Briefing - Weekday",
    "JARVIS Morning Briefing - Weekend",
    "JARVIS Instagram Daily Report",
)

_POWERSHELL_TIMEOUT_SECONDS = 10


def _query_scheduled_task(task_name: str) -> AutomationInfo | None:
    # A single PowerShell invocation per task, read-only
    # (Get-ScheduledTask / Get-ScheduledTaskInfo - never
    # Register-ScheduledTask/Set-ScheduledTask/Unregister-ScheduledTask).
    # Returns None (never raises) if the task doesn't exist on this
    # machine or PowerShell/Task Scheduler isn't reachable - a
    # since-removed or not-yet-created automation just doesn't appear in
    # the panel, rather than crashing it.
    script = (
        f"$task = Get-ScheduledTask -TaskName '{task_name}' -ErrorAction SilentlyContinue; "
        "if ($task) { "
        f"  $info = Get-ScheduledTaskInfo -TaskName '{task_name}'; "
        "  [PSCustomObject]@{ "
        "    Enabled = ($task.State -ne 'Disabled'); "
        "    NextRun = $info.NextRunTime; "
        "    LastRun = $info.LastRunTime "
        "  } | ConvertTo-Json -Compress "
        "}"
    )
    try:
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=_POWERSHELL_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    output = result.stdout.strip()
    if not output:
        return None
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return None

    def _clean_datetime(value: object) -> str | None:
        # PowerShell's ConvertTo-Json serializes a [DateTime] as the
        # .NET "/Date(<milliseconds-since-epoch>)/" form (confirmed via
        # a live query, not assumed) - parsed here into a plain,
        # human-readable local-time string for the panel to show
        # directly, since a UI card should never display that raw
        # wire format. Returns None (never raises) for anything that
        # doesn't match, including Task Scheduler's "never run yet"
        # placeholder (epoch year 1999, i.e. 943912800000ms - the exact
        # value confirmed via a live query for a task that has never
        # run), which reads as noise in a UI rather than a real date.
        if not value or not isinstance(value, str):
            return None
        match = re.match(r"/Date\((-?\d+)\)/", value)
        if not match:
            return value  # already a plain string (e.g. a different PowerShell version) - show as-is
        epoch_ms = int(match.group(1))
        if epoch_ms < 946_684_800_000:  # before 2000-01-01 - Task Scheduler's "never run" sentinel
            return None
        local_dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).astimezone()
        return local_dt.strftime("%Y-%m-%d %H:%M")

    return AutomationInfo(
        name=task_name,
        enabled=bool(data.get("Enabled", False)),
        next_run=_clean_datetime(data.get("NextRun")),
        last_run=_clean_datetime(data.get("LastRun")),
    )


def get_automations() -> list[AutomationInfo]:
    """Status of every known JARVIS Windows Task Scheduler automation.
    Never raises: a task that can't be queried (removed, or
    PowerShell/Task Scheduler unavailable) is simply omitted from the
    returned list rather than failing the whole call."""
    results = []
    for name in _KNOWN_AUTOMATION_TASK_NAMES:
        info = _query_scheduled_task(name)
        if info is not None:
            results.append(info)
    return results


# --- greeting (time-of-day) -------------------------------------------------------------


def time_of_day_greeting(now: datetime | None = None) -> str:
    """'Good morning' / 'Good afternoon' / 'Good evening', by local
    clock hour - display-only, no relation to
    jarvis.content_manager.format_greeting() (that one is a
    Lithuanian, date-inclusive spoken greeting for the morning voice
    briefing specifically; this one is a short English dashboard
    header string, always available even if the JARVIS.md/voice/LLM
    dependencies used by the fuller greeting aren't)."""
    hour = (now or datetime.now()).hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"
