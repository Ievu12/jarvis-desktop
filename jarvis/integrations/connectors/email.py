"""Email connector (IMAP, read-only v1): the first real implementation of
jarvis.integrations.base.Connector. Deliberately IMAP-based rather than a
provider-specific API (Gmail API, Graph API, ...) - IMAP works uniformly
across providers with just a host/address plus either a password (an
app-specific password for providers requiring one, e.g. Gmail) or, since
this stage, an OAuth token (jarvis.integrations.oauth) via IMAP's
standard XOAUTH2 SASL mechanism - avoiding OAuth complexity in the
original v1 while still allowing it now without changing the connector's
action set or risk levels.

All four actions here are RiskLevel.READ_ONLY: checking the connection,
reading account/mailbox info, listing recent message headers, and reading
one specific message by id. None of them can be reached without going
through IMAP4_SSL's own authentication, but none of them requires
confirm_external_action() either, mirroring how local read-only tools
(read_file, get_workflow_state) skip confirm_side_effect() - there is
nothing to approve when nothing is being changed.

No write action (send, delete, move, mark as read, ...) exists in this
module. Adding one later means adding a new ExternalAction with
RiskLevel.REVERSIBLE_WRITE or IRREVERSIBLE_WRITE and going through
confirm_external_action() - it cannot happen by accident, since there is
currently no code path here that could perform one.

Password and OAuth are two independent, coexisting ways to authenticate:
is_configured() is true if EITHER is set up, and _connect() prefers OAuth
tokens when present (falling back to the password path otherwise) - the
original env-var/password flow from before this stage is completely
unchanged for anyone still using it.
"""

from __future__ import annotations

import email as email_lib
import imaplib
from email.header import decode_header
from typing import Any

from jarvis.integrations.base import (
    Connector,
    CredentialError,
    ExternalAction,
    ExternalActionResult,
    RiskLevel,
)
from jarvis.integrations.credentials import get_credential, register_connector_env_vars
from jarvis.integrations.oauth import TokenStore, is_token_expired

SERVICE_NAME = "email"

_ENV_HOST = "EMAIL_IMAP_HOST"
_ENV_ADDRESS = "EMAIL_ADDRESS"
_ENV_PASSWORD = "EMAIL_PASSWORD"

register_connector_env_vars(SERVICE_NAME, [_ENV_HOST, _ENV_ADDRESS, _ENV_PASSWORD])

_IMAP_TIMEOUT_SECONDS = 15
_DEFAULT_LIST_LIMIT = 10
_MAX_LIST_LIMIT = 50


def _build_xoauth2_string(address: str, access_token: str) -> str:
    """Build the SASL XOAUTH2 initial-response string IMAP's AUTHENTICATE
    command expects: 'user=<address>\\x01auth=Bearer <token>\\x01\\x01'.
    Pure string construction - documented, standard format (RFC-adjacent,
    used by Gmail/Outlook IMAP), not something specific to this codebase.
    """
    return f"user={address}\x01auth=Bearer {access_token}\x01\x01"


def _decode_header_value(raw: str | None) -> str:
    """Decode a raw IMAP header value (which may contain RFC 2047
    encoded-word segments, e.g. '=?UTF-8?B?...?=') into a plain string.
    Never raises - falls back to the raw value if decoding fails."""
    if not raw:
        return ""
    try:
        parts = decode_header(raw)
    except Exception:
        return raw
    decoded = []
    for text, charset in parts:
        if isinstance(text, bytes):
            try:
                decoded.append(text.decode(charset or "utf-8", errors="replace"))
            except (LookupError, TypeError):
                decoded.append(text.decode("utf-8", errors="replace"))
        else:
            decoded.append(text)
    return "".join(decoded)


class EmailConnector(Connector):
    service_name = SERVICE_NAME

    def __init__(self) -> None:
        # Independent of credential lookup for the password path
        # (jarvis.integrations.credentials) - OAuth tokens live in the OS
        # keychain, not the environment. Constructed unconditionally; it's
        # a cheap object and load()/is_available() only touch the
        # keychain when actually asked to.
        self._token_store = TokenStore(SERVICE_NAME)
        self.actions = [
            ExternalAction(
                name="check_connection",
                description=(
                    "Verify the configured IMAP credentials work by connecting "
                    "and authenticating. Reads nothing beyond mailbox names."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={"type": "object", "properties": {}},
            ),
            ExternalAction(
                name="get_account_info",
                description=(
                    "Get basic account info: the configured email address, "
                    "available mailboxes/folders, and the message count in "
                    "the selected mailbox (default INBOX)."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "mailbox": {
                            "type": "string",
                            "description": "Mailbox to check the count for. Defaults to INBOX.",
                        }
                    },
                },
            ),
            ExternalAction(
                name="list_recent_emails",
                description=(
                    "List recent message headers (id, from, subject, date) in "
                    "a mailbox - never message bodies. Most recent first."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "mailbox": {
                            "type": "string",
                            "description": "Mailbox to list. Defaults to INBOX.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": f"Max messages to list (default {_DEFAULT_LIST_LIMIT}, max {_MAX_LIST_LIMIT}).",
                        },
                    },
                },
            ),
            ExternalAction(
                name="read_email",
                description=(
                    "Read one specific message's headers and text content by "
                    "its id (as returned by list_recent_emails)."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "message_id": {
                            "type": "string",
                            "description": "The message id from list_recent_emails.",
                        },
                        "mailbox": {
                            "type": "string",
                            "description": "Mailbox the message is in. Defaults to INBOX.",
                        },
                    },
                    "required": ["message_id"],
                },
            ),
        ]

    def _has_password_configured(self) -> bool:
        return all(
            get_credential(SERVICE_NAME, var) for var in (_ENV_HOST, _ENV_ADDRESS, _ENV_PASSWORD)
        )

    def _has_oauth_configured(self) -> bool:
        return self._token_store.load() is not None

    def is_configured(self) -> bool:
        """True if EITHER a password (EMAIL_IMAP_HOST/ADDRESS/PASSWORD)
        or stored OAuth tokens are available. The two are independent -
        having one does not require the other, and both can coexist."""
        return self._has_password_configured() or self._has_oauth_configured()

    def _connect(self) -> imaplib.IMAP4_SSL:
        """Connect and authenticate, preferring OAuth over a password
        when both happen to be configured - OAuth is the more capable,
        revocable credential, so it's the natural default once set up.
        Falls through to the password path otherwise, completely
        unchanged from before OAuth support existed.
        """
        host = get_credential(SERVICE_NAME, _ENV_HOST)

        tokens = self._token_store.load()
        if tokens is not None:
            address = get_credential(SERVICE_NAME, _ENV_ADDRESS)
            if not (host and address):
                raise CredentialError(
                    f"OAuth tokens are stored for '{SERVICE_NAME}', but {_ENV_HOST} and "
                    f"{_ENV_ADDRESS} must still be set in the environment to know which "
                    "server/mailbox to connect to."
                )
            if is_token_expired(tokens):
                # Refreshing requires a real token-endpoint HTTP call,
                # which jarvis.integrations.oauth.refresh_access_token()
                # deliberately does not implement yet (see its
                # docstring) - surfaced as a clear, actionable error
                # rather than silently trying and failing deeper in the
                # IMAP handshake.
                raise CredentialError(
                    f"Stored OAuth tokens for '{SERVICE_NAME}' have expired and automatic "
                    "refresh is not yet implemented - reconnect the account to get new tokens."
                )

            connection = imaplib.IMAP4_SSL(host, timeout=_IMAP_TIMEOUT_SECONDS)
            xoauth2_string = _build_xoauth2_string(address, tokens.access_token)
            try:
                connection.authenticate("XOAUTH2", lambda _challenge: xoauth2_string.encode())
            except imaplib.IMAP4.error as e:
                connection.logout()
                raise CredentialError(f"IMAP OAuth authentication failed for {address}@{host}: {e}") from e
            return connection

        address = get_credential(SERVICE_NAME, _ENV_ADDRESS)
        password = get_credential(SERVICE_NAME, _ENV_PASSWORD)
        if not (host and address and password):
            raise CredentialError(
                f"'{SERVICE_NAME}' is not configured - set {_ENV_HOST}, {_ENV_ADDRESS}, "
                f"and {_ENV_PASSWORD} in the environment, or connect an OAuth account."
            )

        connection = imaplib.IMAP4_SSL(host, timeout=_IMAP_TIMEOUT_SECONDS)
        try:
            connection.login(address, password)
        except imaplib.IMAP4.error as e:
            connection.logout()
            raise CredentialError(f"IMAP login failed for {address}@{host}: {e}") from e
        return connection

    def disconnect(self) -> bool:
        """Clear locally-stored OAuth tokens (jarvis.integrations.oauth
        .TokenStore), if any. Never touches the EMAIL_IMAP_HOST/ADDRESS/
        PASSWORD environment variables - those are the user's own shell
        state, not something a connector clears on its own initiative;
        "disconnecting" the password path just means removing those
        variables yourself. Makes no network call - this does not revoke
        the token with the OAuth provider, only removes what JARVIS holds
        locally. Returns whatever TokenStore.clear() returns: True if a
        stored entry was actually removed, False if there was nothing
        stored or the keychain is unavailable.
        """
        return self._token_store.clear()

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
        except CredentialError:
            raise
        except (imaplib.IMAP4.error, OSError, TimeoutError) as e:
            return ExternalActionResult(
                ok=False,
                output=f"Email connection error: {e}",
                service=self.service_name,
                action=action_name,
            )

    def _do_check_connection(self) -> ExternalActionResult:
        connection = self._connect()
        try:
            status, mailboxes = connection.list()
        finally:
            connection.logout()

        if status != "OK":
            return ExternalActionResult(
                ok=False,
                output="Connected and authenticated, but could not list mailboxes.",
                service=self.service_name,
                action="check_connection",
            )
        count = len(mailboxes) if mailboxes else 0
        return ExternalActionResult(
            ok=True,
            output=f"Connection OK. Authenticated successfully. {count} mailbox(es) visible.",
            service=self.service_name,
            action="check_connection",
        )

    def _do_get_account_info(self, mailbox: str = "INBOX") -> ExternalActionResult:
        address = get_credential(SERVICE_NAME, _ENV_ADDRESS)
        connection = self._connect()
        try:
            status, raw_mailboxes = connection.list()
            mailbox_names = []
            if status == "OK" and raw_mailboxes:
                for raw in raw_mailboxes:
                    if isinstance(raw, bytes):
                        text = raw.decode("utf-8", errors="replace")
                        mailbox_names.append(text.rsplit(' "/" ', 1)[-1].strip('"'))

            select_status, select_data = connection.select(mailbox, readonly=True)
            message_count = int(select_data[0]) if select_status == "OK" and select_data[0] else 0
        finally:
            connection.logout()

        lines = [
            f"Account: {address}",
            f"Mailboxes: {', '.join(mailbox_names) if mailbox_names else '(none listed)'}",
            f"Messages in {mailbox}: {message_count}",
        ]
        return ExternalActionResult(
            ok=True, output="\n".join(lines), service=self.service_name, action="get_account_info"
        )

    def _do_list_recent_emails(
        self, mailbox: str = "INBOX", limit: int = _DEFAULT_LIST_LIMIT
    ) -> ExternalActionResult:
        limit = max(1, min(limit, _MAX_LIST_LIMIT))

        connection = self._connect()
        try:
            select_status, _ = connection.select(mailbox, readonly=True)
            if select_status != "OK":
                return ExternalActionResult(
                    ok=False,
                    output=f"Could not open mailbox '{mailbox}'.",
                    service=self.service_name,
                    action="list_recent_emails",
                )

            search_status, ids_data = connection.search(None, "ALL")
            if search_status != "OK" or not ids_data or not ids_data[0]:
                return ExternalActionResult(
                    ok=True,
                    output=f"No messages in {mailbox}.",
                    service=self.service_name,
                    action="list_recent_emails",
                )

            all_ids = ids_data[0].split()
            recent_ids = list(reversed(all_ids))[:limit]

            lines = []
            for msg_id in recent_ids:
                fetch_status, msg_data = connection.fetch(
                    msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])"
                )
                if fetch_status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                    continue
                header_text = msg_data[0][1].decode("utf-8", errors="replace")
                parsed = email_lib.message_from_string(header_text)
                sender = _decode_header_value(parsed.get("From"))
                subject = _decode_header_value(parsed.get("Subject"))
                date = parsed.get("Date", "")
                lines.append(
                    f"id={msg_id.decode()} | from={sender} | subject={subject} | date={date}"
                )
        finally:
            connection.logout()

        output = "\n".join(lines) if lines else f"No messages in {mailbox}."
        return ExternalActionResult(
            ok=True, output=output, service=self.service_name, action="list_recent_emails"
        )

    def _do_read_email(self, message_id: str, mailbox: str = "INBOX") -> ExternalActionResult:
        connection = self._connect()
        try:
            select_status, _ = connection.select(mailbox, readonly=True)
            if select_status != "OK":
                return ExternalActionResult(
                    ok=False,
                    output=f"Could not open mailbox '{mailbox}'.",
                    service=self.service_name,
                    action="read_email",
                )

            fetch_status, msg_data = connection.fetch(message_id, "(BODY.PEEK[])")
            if fetch_status != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                return ExternalActionResult(
                    ok=False,
                    output=f"Message id '{message_id}' not found in {mailbox}.",
                    service=self.service_name,
                    action="read_email",
                )

            raw_message = msg_data[0][1]
            parsed = email_lib.message_from_bytes(raw_message)
        finally:
            connection.logout()

        sender = _decode_header_value(parsed.get("From"))
        subject = _decode_header_value(parsed.get("Subject"))
        date = parsed.get("Date", "")

        body = ""
        if parsed.is_multipart():
            for part in parsed.walk():
                if part.get_content_type() == "text/plain" and not part.get_filename():
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        charset = part.get_content_charset() or "utf-8"
                        body = payload.decode(charset, errors="replace")
                        break
        else:
            payload = parsed.get_payload(decode=True)
            if isinstance(payload, bytes):
                charset = parsed.get_content_charset() or "utf-8"
                body = payload.decode(charset, errors="replace")
            elif isinstance(payload, str):
                body = payload

        lines = [f"From: {sender}", f"Subject: {subject}", f"Date: {date}", "", body]
        return ExternalActionResult(
            ok=True, output="\n".join(lines), service=self.service_name, action="read_email"
        )
