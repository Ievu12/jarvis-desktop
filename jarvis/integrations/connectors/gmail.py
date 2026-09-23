"""Gmail connector (REST API, OAuth-only, read-only v1): the fourth real
implementation of jarvis.integrations.base.Connector, alongside
jarvis.integrations.connectors.email.EmailConnector, .stripe
.StripeConnector, and .google_calendar.GoogleCalendarConnector.

Deliberately a separate connector from EmailConnector (IMAP), not an
extension of it: EmailConnector is a provider-agnostic IMAP client (works
against Gmail, Outlook, or any IMAP server, optionally via OAuth
XOAUTH2); this connector instead talks to Gmail's own REST API directly,
giving access to Gmail-specific query syntax (e.g. `is:unread`, `from:`,
`subject:`) that a generic IMAP SEARCH cannot express as precisely.
Architected exactly like GoogleCalendarConnector: OAuth-only (no
password/API-key fallback path), authenticating with a Bearer access
token obtained from jarvis.integrations.oauth.TokenStore - never an
environment variable.

Every action here is RiskLevel.READ_ONLY: listing recent messages,
listing unread messages, searching by sender/subject/arbitrary Gmail
query syntax, reading one specific message's headers/snippet, and
reading one specific message's full decoded body text
(get_message_full_content). No write action (send, delete/trash,
archive, modify labels - including marking a message as read, create a
draft, ...) exists anywhere in this module. Adding one later means
adding a new ExternalAction with RiskLevel.REVERSIBLE_WRITE or
IRREVERSIBLE_WRITE and going through jarvis.integrations.approval
.confirm_external_action() - it cannot happen by accident, since no code
path here can currently do it, and this module never calls anything from
Gmail's API beyond the fixed GET endpoints below.

Note on the current OAuth scope (gmail.readonly, see
jarvis.integrations.oauth.GOOGLE_OAUTH_PROVIDER): even if a write action
were added to this module by mistake, Gmail's API would reject it at the
server with a 403 - the granted token itself has no write permission.
Composing a reply is therefore handled entirely as LLM-generated text in
the conversation (see jarvis.core.llm.BASE_SYSTEM_PROMPT's Gmail
section), never as a tool call here - there is no "create a draft" or
"send a message" action, and adding one would first require a separate,
deliberate OAuth re-consent with a broader scope, which this module does
not attempt.

Authentication: a Bearer access token in the Authorization header
(Gmail API's documented scheme for OAuth), obtained from
jarvis.integrations.oauth.TokenStore using GOOGLE_OAUTH_PROVIDER's
gmail.readonly scope (jarvis.integrations.oauth.GOOGLE_OAUTH_PROVIDER -
already scoped read-only, so even a bug in this module could not reach a
write endpoint the granted token has no permission for). Exactly like
GoogleCalendarConnector's OAuth path, an expired stored token is refused
with a clear CredentialError rather than attempting a refresh, since
jarvis.integrations.oauth.refresh_access_token() remains deliberately
unimplemented (no real OAuth token exchange or refresh happens anywhere
in this codebase yet) - meaning no code path in JARVIS can currently
complete a real OAuth connection to a live Gmail account until that is
implemented as its own, separate, reviewed step.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from jarvis.integrations.base import (
    Connector,
    CredentialError,
    ExternalAction,
    ExternalActionResult,
    RiskLevel,
)
from jarvis.integrations.oauth import TokenStore, is_token_expired

SERVICE_NAME = "gmail"

_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
_REQUEST_TIMEOUT_SECONDS = 15
_DEFAULT_MESSAGE_LIMIT = 10
_MAX_MESSAGE_LIMIT = 50


def _redact_token(text: str, access_token: str | None) -> str:
    """Scrub a known access token value out of arbitrary text (e.g. an
    exception message from urllib) before it can reach a result or log
    line. Mirrors jarvis.integrations.connectors.google_calendar
    ._redact_token()'s approach, applied to this connector's own OAuth
    access token."""
    if not access_token:
        return text
    return text.replace(access_token, "****")


class GmailConnector(Connector):
    service_name = SERVICE_NAME

    def __init__(self) -> None:
        # OAuth-only: no environment-variable credential path exists for
        # this connector at all, unlike EmailConnector's password/OAuth
        # hybrid. is_configured() below checks ONLY the token store.
        self._token_store = TokenStore(SERVICE_NAME)
        self.actions = [
            ExternalAction(
                name="list_recent_messages",
                description=(
                    "List recent Gmail messages in the inbox (id, from, "
                    "subject, date, snippet) - most recent first. Use this "
                    "to answer 'show me recent emails' or 'did I get any "
                    "new emails today' (each message includes its date - "
                    "compare against today's date yourself). Read-only."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": f"Max messages to list (default {_DEFAULT_MESSAGE_LIMIT}, max {_MAX_MESSAGE_LIMIT}).",
                        }
                    },
                },
            ),
            ExternalAction(
                name="list_unread_messages",
                description=(
                    "List unread Gmail messages in the inbox (id, from, "
                    "subject, date, snippet) - most recent first. Read-only."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": f"Max messages to list (default {_DEFAULT_MESSAGE_LIMIT}, max {_MAX_MESSAGE_LIMIT}).",
                        }
                    },
                },
            ),
            ExternalAction(
                name="search_messages",
                description=(
                    "Search Gmail messages using Gmail's own query syntax "
                    "(id, from, subject, date, snippet) - most recent first. "
                    "Use 'from:someone@example.com' to search by sender, or "
                    "'subject:some topic' to search by subject - Gmail's "
                    "query syntax also supports plain keyword terms for a "
                    "general topic search. Read-only."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": (
                                "Gmail search query, e.g. 'from:alice@example.com', "
                                "'subject:invoice', or a plain keyword."
                            ),
                        },
                        "limit": {
                            "type": "integer",
                            "description": f"Max messages to list (default {_DEFAULT_MESSAGE_LIMIT}, max {_MAX_MESSAGE_LIMIT}).",
                        },
                    },
                    "required": ["query"],
                },
            ),
            ExternalAction(
                name="get_message",
                description=(
                    "Get one specific Gmail message's headers and snippet "
                    "by its id (as returned by list_recent_messages/"
                    "list_unread_messages/search_messages) - use this for a "
                    "quick preview. For the full message text (needed to "
                    "summarize or draft a reply), use "
                    "get_message_full_content instead. Read-only."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "message_id": {
                            "type": "string",
                            "description": "The message id from a list/search result.",
                        },
                    },
                    "required": ["message_id"],
                },
            ),
            ExternalAction(
                name="get_message_full_content",
                description=(
                    "Get one specific Gmail message's headers and FULL "
                    "decoded body text (not just the short snippet "
                    "get_message returns) by its id. Use this before "
                    "summarizing a message in detail or drafting a reply - "
                    "the returned text is the actual message body, decoded "
                    "and ready to read. Read-only - this only reads the "
                    "message, it never sends, saves, or modifies anything."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "message_id": {
                            "type": "string",
                            "description": "The message id from a list/search result.",
                        },
                    },
                    "required": ["message_id"],
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
        """Perform a single, read-only GET request against the Gmail API.
        Never called for anything but the actions below - there is no
        generic "call any Gmail endpoint" method for a future write
        action to accidentally reuse unsafely.
        """
        access_token = self._access_token()

        url = f"{_API_BASE_URL}{path}"
        if params:
            # urlencode (not the hand-joined "&".join() style
            # jarvis.integrations.connectors.stripe/.google_calendar use
            # for their simple numeric params) because a Gmail search
            # query can contain characters (spaces, ':', '@') that must be
            # percent-encoded to form a valid URL. doseq=True so a
            # list-valued param (metadataHeaders) is encoded as repeated
            # 'key=value' pairs, as Gmail's API expects, rather than a
            # single stringified Python list.
            url = f"{url}?{urllib.parse.urlencode(params, doseq=True)}"

        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {access_token}"}, method="GET"
        )

        try:
            with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            raise CredentialError(
                _redact_token(f"Gmail API request failed ({e.code}): {error_body}", access_token)
            ) from None
        except urllib.error.URLError as e:
            raise CredentialError(
                _redact_token(f"Gmail API connection error: {e.reason}", access_token)
            ) from None
        except TimeoutError:
            raise CredentialError(
                f"Gmail API request timed out after {_REQUEST_TIMEOUT_SECONDS}s."
            ) from None

        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise CredentialError(
                _redact_token(f"Gmail API returned invalid JSON: {e}", access_token)
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

    def _format_messages(self, messages: list[dict[str, Any]]) -> str:
        """Shared formatting for every action that lists several
        messages (list_recent_messages/list_unread_messages/
        search_messages) - each already-fetched message summary
        (id/from/subject/date/snippet, as returned by
        _fetch_message_summary) becomes one line, most-recent-first order
        exactly as Gmail's API returned it (no re-sorting logic here to
        duplicate)."""
        if not messages:
            return "No messages found."
        lines = []
        for msg in messages:
            lines.append(
                f"id={msg['id']} | from={msg['from']} | subject={msg['subject']} | "
                f"date={msg['date']} | snippet={msg['snippet']}"
            )
        return "\n".join(lines)

    def _fetch_message_summary(self, message_id: str) -> dict[str, str]:
        """Fetch one message with only the headers/snippet needed for a
        list line - uses Gmail's format=metadata to avoid downloading a
        full message body for every entry in a list."""
        data = self._get(
            f"/messages/{message_id}",
            params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
        )
        return self._extract_summary(data)

    def _extract_summary(self, data: dict[str, Any]) -> dict[str, str]:
        headers = {
            h.get("name", ""): h.get("value", "")
            for h in data.get("payload", {}).get("headers", [])
        }
        return {
            "id": data.get("id", "?"),
            "from": headers.get("From", "?"),
            "subject": headers.get("Subject", "(no subject)"),
            "date": headers.get("Date", "?"),
            "snippet": data.get("snippet", ""),
        }

    def _list_message_ids(self, *, params: dict[str, Any]) -> list[str]:
        data = self._get("/messages", params=params)
        return [m["id"] for m in data.get("messages", [])]

    def _do_list_recent_messages(self, limit: int = _DEFAULT_MESSAGE_LIMIT) -> ExternalActionResult:
        limit = max(1, min(limit, _MAX_MESSAGE_LIMIT))
        message_ids = self._list_message_ids(params={"maxResults": limit})
        messages = [self._fetch_message_summary(mid) for mid in message_ids]
        return ExternalActionResult(
            ok=True, output=self._format_messages(messages), service=self.service_name,
            action="list_recent_messages",
        )

    def _do_list_unread_messages(self, limit: int = _DEFAULT_MESSAGE_LIMIT) -> ExternalActionResult:
        limit = max(1, min(limit, _MAX_MESSAGE_LIMIT))
        message_ids = self._list_message_ids(params={"maxResults": limit, "q": "is:unread"})
        messages = [self._fetch_message_summary(mid) for mid in message_ids]
        return ExternalActionResult(
            ok=True, output=self._format_messages(messages), service=self.service_name,
            action="list_unread_messages",
        )

    def _do_search_messages(self, query: str, limit: int = _DEFAULT_MESSAGE_LIMIT) -> ExternalActionResult:
        if not query or not query.strip():
            return ExternalActionResult(
                ok=False,
                output="A search query is required.",
                service=self.service_name,
                action="search_messages",
            )
        limit = max(1, min(limit, _MAX_MESSAGE_LIMIT))
        message_ids = self._list_message_ids(params={"maxResults": limit, "q": query.strip()})
        messages = [self._fetch_message_summary(mid) for mid in message_ids]
        return ExternalActionResult(
            ok=True, output=self._format_messages(messages), service=self.service_name,
            action="search_messages",
        )

    def _do_get_message(self, message_id: str) -> ExternalActionResult:
        if not message_id or not message_id.strip():
            return ExternalActionResult(
                ok=False,
                output="A message_id is required.",
                service=self.service_name,
                action="get_message",
            )
        data = self._get(
            f"/messages/{message_id.strip()}",
            params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
        )
        summary = self._extract_summary(data)
        output = (
            f"From: {summary['from']}\n"
            f"Subject: {summary['subject']}\n"
            f"Date: {summary['date']}\n"
            f"Snippet: {summary['snippet']}"
        )
        return ExternalActionResult(
            ok=True, output=output, service=self.service_name, action="get_message"
        )

    def _decode_body_data(self, data: str) -> str:
        """Decode one Gmail API MessagePart.body.data value: base64url,
        no padding (Gmail's documented encoding for this field). Returns
        an empty string, never raises, if the value is malformed - a
        single unparseable part should not fail the whole message read."""
        try:
            padded = data + "=" * (-len(data) % 4)
            return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
        except (ValueError, TypeError):
            return ""

    def _extract_plain_text_body(self, payload: dict[str, Any]) -> str:
        """Walk a Gmail API MessagePart tree (payload, or one of its
        payload.parts, recursively - a multipart message nests parts
        inside parts, e.g. multipart/alternative inside multipart/mixed)
        and return the first text/plain part's decoded body. Falls back
        to the first text/html part (with tags left as-is - this is a
        plain-text reader, not an HTML renderer) if no text/plain part
        exists, since some messages are HTML-only. Returns an empty
        string if the message has no readable text part at all (e.g.
        purely an attachment)."""
        html_fallback = ""

        def _walk(part: dict[str, Any]) -> str | None:
            nonlocal html_fallback
            mime_type = part.get("mimeType", "")
            body_data = part.get("body", {}).get("data")
            if mime_type == "text/plain" and body_data:
                return self._decode_body_data(body_data)
            if mime_type == "text/html" and body_data and not html_fallback:
                html_fallback = self._decode_body_data(body_data)
            for sub_part in part.get("parts", []):
                found = _walk(sub_part)
                if found:
                    return found
            return None

        plain_text = _walk(payload)
        if plain_text:
            return plain_text
        return html_fallback

    def _do_get_message_full_content(self, message_id: str) -> ExternalActionResult:
        if not message_id or not message_id.strip():
            return ExternalActionResult(
                ok=False,
                output="A message_id is required.",
                service=self.service_name,
                action="get_message_full_content",
            )
        data = self._get(
            f"/messages/{message_id.strip()}",
            params={"format": "full"},
        )
        summary = self._extract_summary(data)
        body = self._extract_plain_text_body(data.get("payload", {}))
        if not body:
            body = summary["snippet"] or "(no readable text content in this message)"

        output = (
            f"From: {summary['from']}\n"
            f"Subject: {summary['subject']}\n"
            f"Date: {summary['date']}\n"
            f"\n{body}"
        )
        return ExternalActionResult(
            ok=True, output=output, service=self.service_name, action="get_message_full_content"
        )

    def disconnect(self) -> bool:
        """Clear locally-stored OAuth tokens. Makes no network call - this
        does not revoke the token with Google, only removes what JARVIS
        holds locally. Returns whatever TokenStore.clear() returns."""
        return self._token_store.clear()
