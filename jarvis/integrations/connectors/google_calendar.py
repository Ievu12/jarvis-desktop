"""Google Calendar connector (REST API, OAuth-only, read-only v1): the
third real implementation of jarvis.integrations.base.Connector,
alongside jarvis.integrations.connectors.email.EmailConnector and
.stripe.StripeConnector.

Deliberately architected differently from EmailConnector: this connector
supports ONLY OAuth authentication - there is no password/API-key
fallback path, and none is planned. This is an intentional contrast with
EmailConnector's hybrid (password OR OAuth) model, so the codebase has a
clear example of each shape: a connector that accepts either credential
type, and one that accepts exactly one. Google Calendar's API has no
practical read path for a private calendar without either OAuth or a
service account; service accounts are out of scope for this v1 (they
introduce a second, heavier credential model - a downloaded JSON key
file - that would blur this connector's "OAuth-only" architecture rather
than demonstrate it cleanly).

Every action here is RiskLevel.READ_ONLY: listing today's events,
listing upcoming events, and reading one specific event's details. No
write action (create, update, delete, respond to an invite, ...) exists
anywhere in this module. Adding one later means adding a new
ExternalAction with RiskLevel.REVERSIBLE_WRITE or IRREVERSIBLE_WRITE and
going through jarvis.integrations.approval.confirm_external_action() -
it cannot happen by accident, since no code path here can currently do
it, and this module never calls anything from Google Calendar's API
beyond the fixed GET endpoints below.

Note on OAuth scope: this connector requests calendar.readonly (see
jarvis.integrations.oauth.GOOGLE_CALENDAR_OAUTH_PROVIDER), not the
broader `calendar` scope - deliberately matching the actual, read-only
action set below, the same principle jarvis.integrations.oauth
.GOOGLE_OAUTH_PROVIDER's gmail.readonly scope follows for the Gmail
connector. If a write action (create/update/delete an event) is ever
added, it would need a separate, deliberate OAuth re-consent with a
broader scope - this module does not request one preemptively.

Authentication: a Bearer access token in the Authorization header
(Google Calendar API's documented scheme for OAuth), obtained from
jarvis.integrations.oauth.TokenStore - never an environment variable.
Exactly like EmailConnector's OAuth path, an expired stored token is
refused with a clear CredentialError rather than attempting a refresh,
since jarvis.integrations.oauth.refresh_access_token() remains
deliberately unimplemented (no real OAuth token exchange or refresh
happens anywhere in this codebase yet).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, time, timedelta
from typing import Any

from jarvis.integrations.base import (
    Connector,
    CredentialError,
    ExternalAction,
    ExternalActionResult,
    RiskLevel,
)
from jarvis.integrations.oauth import TokenStore, is_token_expired

SERVICE_NAME = "google_calendar"

_API_BASE_URL = "https://www.googleapis.com/calendar/v3"
_REQUEST_TIMEOUT_SECONDS = 15
_DEFAULT_EVENT_LIMIT = 10
_MAX_EVENT_LIMIT = 50


def _redact_token(text: str, access_token: str | None) -> str:
    """Scrub a known access token value out of arbitrary text (e.g. an
    exception message from urllib) before it can reach a result or log
    line. Mirrors jarvis.integrations.connectors.stripe._redact_key()'s
    approach, applied to this connector's own OAuth access token."""
    if not access_token:
        return text
    return text.replace(access_token, "****")


class GoogleCalendarConnector(Connector):
    service_name = SERVICE_NAME

    def __init__(self) -> None:
        # OAuth-only: no environment-variable credential path exists for
        # this connector at all, unlike EmailConnector's password/OAuth
        # hybrid. is_configured() below checks ONLY the token store.
        self._token_store = TokenStore(SERVICE_NAME)
        self.actions = [
            ExternalAction(
                name="list_todays_events",
                description=(
                    "List today's events on the primary calendar (id, "
                    "summary, start/end time) - earliest first, bounded to "
                    "the current local day (midnight to midnight). "
                    "Read-only."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": f"Max events to list (default {_DEFAULT_EVENT_LIMIT}, max {_MAX_EVENT_LIMIT}).",
                        }
                    },
                },
            ),
            ExternalAction(
                name="list_upcoming_events",
                description=(
                    "List upcoming events on the primary calendar (id, "
                    "summary, start/end time) - soonest first. Read-only."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": f"Max events to list (default {_DEFAULT_EVENT_LIMIT}, max {_MAX_EVENT_LIMIT}).",
                        }
                    },
                },
            ),
            ExternalAction(
                name="get_event",
                description=(
                    "Get one specific event's details by its id (as "
                    "returned by list_upcoming_events)."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "event_id": {
                            "type": "string",
                            "description": "The event id from list_upcoming_events.",
                        },
                    },
                    "required": ["event_id"],
                },
            ),
        ]

    def is_configured(self) -> bool:
        """True only if OAuth tokens are stored. There is no other
        credential path for this connector - unlike EmailConnector,
        which is also configured via a plain password."""
        return self._token_store.load() is not None

    def _access_token(self) -> str:
        tokens = self._token_store.load()
        if tokens is None:
            raise CredentialError(
                f"'{SERVICE_NAME}' is not configured - no OAuth tokens are stored. "
                "Connect a Google account to enable this integration."
            )
        if is_token_expired(tokens):
            # Refreshing requires a real token-endpoint HTTP call, which
            # jarvis.integrations.oauth.refresh_access_token()
            # deliberately does not implement yet (see its docstring) -
            # surfaced as a clear, actionable error rather than silently
            # trying and failing deeper in the request.
            raise CredentialError(
                f"Stored OAuth tokens for '{SERVICE_NAME}' have expired and automatic "
                "refresh is not yet implemented - reconnect the account to get new tokens."
            )
        return tokens.access_token

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Perform a single, read-only GET request against the Google
        Calendar API. Never called for anything but the actions below -
        there is no generic "call any Calendar endpoint" method for a
        future write action to accidentally reuse unsafely.
        """
        access_token = self._access_token()

        url = f"{_API_BASE_URL}{path}"
        if params:
            # urlencode (not a hand-joined "&".join() string) because
            # timeMin/timeMax (list_todays_events) are RFC3339 timestamps
            # containing ':' and a '+' UTC offset, both of which must be
            # percent-encoded to form a valid URL - a '+' left as-is would
            # be interpreted as a literal space by the server.
            url = f"{url}?{urllib.parse.urlencode(params)}"

        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {access_token}"}, method="GET"
        )

        try:
            with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            raise CredentialError(
                _redact_token(f"Google Calendar API request failed ({e.code}): {error_body}", access_token)
            ) from None
        except urllib.error.URLError as e:
            raise CredentialError(
                _redact_token(f"Google Calendar API connection error: {e.reason}", access_token)
            ) from None
        except TimeoutError:
            raise CredentialError(
                f"Google Calendar API request timed out after {_REQUEST_TIMEOUT_SECONDS}s."
            ) from None

        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise CredentialError(
                _redact_token(f"Google Calendar API returned invalid JSON: {e}", access_token)
            ) from None

    def execute(self, action_name: str, **kwargs: Any) -> ExternalActionResult:
        action = self.get_action(action_name)
        if action is None:
            return ExternalActionResult(
                ok=False,
                output=f"Unknown action: {action_name}",
                service=self.service_name,
                action=action_name,
            )

        handler = getattr(self, f"_do_{action_name}")
        try:
            return handler(**kwargs)
        except CredentialError as e:
            return ExternalActionResult(
                ok=False, output=str(e), service=self.service_name, action=action_name
            )

    def _format_events(self, events: list[dict[str, Any]]) -> str:
        """Shared formatting for every action that lists several events
        (list_todays_events/list_upcoming_events) - one line per event,
        in whatever order the Google Calendar API returned them (already
        soonest-first via orderBy=startTime - no re-sorting logic here to
        duplicate)."""
        if not events:
            return "No events found."
        lines = []
        for event in events:
            event_id = event.get("id", "?")
            summary = event.get("summary", "(no title)")
            start = event.get("start", {})
            start_time = start.get("dateTime") or start.get("date", "?")
            lines.append(f"id={event_id} | summary={summary} | start={start_time}")
        return "\n".join(lines)

    def _do_list_todays_events(self, limit: int = _DEFAULT_EVENT_LIMIT) -> ExternalActionResult:
        limit = max(1, min(limit, _MAX_EVENT_LIMIT))

        # Bounded to the current local day (machine's local timezone,
        # since that's what "today" means to the person asking) -
        # midnight to the following midnight, expressed as RFC3339
        # timestamps with a UTC offset, exactly as the Google Calendar
        # API's timeMin/timeMax parameters require.
        now_local = datetime.now().astimezone()
        start_of_day = datetime.combine(now_local.date(), time.min, tzinfo=now_local.tzinfo)
        end_of_day = start_of_day + timedelta(days=1)

        data = self._get(
            "/calendars/primary/events",
            params={
                "maxResults": limit,
                "orderBy": "startTime",
                "singleEvents": "true",
                "timeMin": start_of_day.isoformat(),
                "timeMax": end_of_day.isoformat(),
            },
        )

        events = data.get("items", [])
        if not events:
            return ExternalActionResult(
                ok=True, output="No events found for today.", service=self.service_name,
                action="list_todays_events",
            )

        return ExternalActionResult(
            ok=True, output=self._format_events(events), service=self.service_name,
            action="list_todays_events",
        )

    def _do_list_upcoming_events(self, limit: int = _DEFAULT_EVENT_LIMIT) -> ExternalActionResult:
        limit = max(1, min(limit, _MAX_EVENT_LIMIT))
        data = self._get(
            "/calendars/primary/events",
            params={"maxResults": limit, "orderBy": "startTime", "singleEvents": "true"},
        )

        events = data.get("items", [])
        if not events:
            return ExternalActionResult(
                ok=True, output="No upcoming events found.", service=self.service_name,
                action="list_upcoming_events",
            )

        return ExternalActionResult(
            ok=True, output=self._format_events(events), service=self.service_name,
            action="list_upcoming_events",
        )

    def _do_get_event(self, event_id: str) -> ExternalActionResult:
        data = self._get(f"/calendars/primary/events/{event_id}")

        summary = data.get("summary", "(no title)")
        description = data.get("description", "")
        start = data.get("start", {})
        end = data.get("end", {})
        start_time = start.get("dateTime") or start.get("date", "?")
        end_time = end.get("dateTime") or end.get("date", "?")
        location = data.get("location", "")

        lines = [
            f"Summary: {summary}",
            f"Start: {start_time}",
            f"End: {end_time}",
        ]
        if location:
            lines.append(f"Location: {location}")
        if description:
            lines.append(f"Description: {description}")

        return ExternalActionResult(
            ok=True, output="\n".join(lines), service=self.service_name, action="get_event"
        )

    def disconnect(self) -> bool:
        """Clear locally-stored OAuth tokens. Makes no network call - this
        does not revoke the token with Google, only removes what JARVIS
        holds locally. Returns whatever TokenStore.clear() returns."""
        return self._token_store.clear()
