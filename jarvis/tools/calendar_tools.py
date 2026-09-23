"""Agent-facing tool wrappers around
jarvis.integrations.connectors.google_calendar.GoogleCalendarConnector:
the bridge between the LLM-driven agent and the Google Calendar
connector, exactly the shape jarvis.tools.gmail_tools/stripe_tools/
email_tools use to bridge the agent to their respective connectors. No
business logic lives here - each tool's run() only delegates to the
connector.

All three tools wrap READ_ONLY actions (today's events, upcoming events,
one event's details), so none of them requires confirm_side_effect() or
confirm_external_action() - there is nothing to approve when nothing
external is being changed, mirroring how the Gmail/Stripe/email tools
and read_file/get_workflow_state skip approval today. There is currently
no write action (create, update, delete an event, respond to an invite,
...) to wrap, so no approval-gated Calendar tool exists yet - see
GoogleCalendarConnector's own module docstring for what adding one would
require.

If Google Calendar isn't configured (no OAuth tokens stored via
jarvis.integrations.oauth.TokenStore, under its own "google_calendar"
service name - entirely separate from Gmail's stored tokens), every tool
here returns a clear, non-crashing ToolResult(ok=False, ...) instead of
attempting a network call - the same "fail closed with a clear message"
pattern used throughout jarvis.tools.
"""

from __future__ import annotations

from jarvis.integrations.base import CredentialError
from jarvis.integrations.connectors.google_calendar import GoogleCalendarConnector
from jarvis.tools.base import Tool, ToolResult

_connector = GoogleCalendarConnector()


def _run_calendar_action(action_name: str, **kwargs) -> ToolResult:
    if not _connector.is_configured():
        return ToolResult(
            ok=False,
            output=(
                "Google Calendar is not configured. No OAuth tokens are "
                "stored - connect a Google account to enable Calendar "
                "tools. There is no password or API key path for Google "
                "Calendar; only OAuth is supported."
            ),
        )
    try:
        result = _connector.execute(action_name, **kwargs)
    except CredentialError as e:
        return ToolResult(ok=False, output=str(e))
    return ToolResult(ok=result.ok, output=result.output)


class ListTodaysCalendarEventsTool(Tool):
    name = "list_todays_calendar_events"
    description = (
        "List today's events on the primary Google Calendar (id, summary, "
        "start/end time) - earliest first, bounded to the current local "
        "day. Use this to answer 'what's on my calendar today' or 'do I "
        "have any meetings today'. Read-only, no approval required."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max events to list (default 10, max 50).",
            }
        },
    }

    def run(self, *, limit: int = 10) -> ToolResult:
        return _run_calendar_action("list_todays_events", limit=limit)


class ListUpcomingCalendarEventsTool(Tool):
    name = "list_upcoming_calendar_events"
    description = (
        "List upcoming events on the primary Google Calendar (id, "
        "summary, start/end time) - soonest first, not bounded to today. "
        "Use this to answer 'what's coming up' or 'what are my next "
        "events'. Read-only, no approval required."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max events to list (default 10, max 50).",
            }
        },
    }

    def run(self, *, limit: int = 10) -> ToolResult:
        return _run_calendar_action("list_upcoming_events", limit=limit)


class GetCalendarEventTool(Tool):
    name = "get_calendar_event"
    description = (
        "Get one specific Google Calendar event's details by its id (as "
        "returned by list_todays_calendar_events/"
        "list_upcoming_calendar_events). Read-only, no approval required."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "event_id": {
                "type": "string",
                "description": "The event id from a list result.",
            }
        },
        "required": ["event_id"],
    }

    def run(self, *, event_id: str) -> ToolResult:
        return _run_calendar_action("get_event", event_id=event_id)
