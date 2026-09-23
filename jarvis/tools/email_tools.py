"""Agent-facing tool wrappers around jarvis.integrations.connectors.email
.EmailConnector: the bridge between the LLM-driven agent and the email
connector, exactly the shape jarvis.tools.workflow_state.WorkflowStateTool
uses to bridge the agent to the deterministic scan/plan/... pipeline. No
business logic lives here - each tool's run() only delegates to the
connector.

All four tools wrap READ_ONLY actions (checking the connection, reading
account info, listing message headers, reading one message), so none of
them requires confirm_side_effect() or confirm_external_action() - there
is nothing to approve when nothing external is being changed, mirroring
how read_file/get_workflow_state skip approval today. There is currently
no write action (send/delete/move/mark-read) to wrap, so no approval-
gated email tool exists yet.

If the email connector isn't configured (no EMAIL_IMAP_HOST/ADDRESS/
PASSWORD in the environment), every tool here returns a clear,
non-crashing ToolResult(ok=False, ...) instead of attempting a network
call - the same "fail closed with a clear message" pattern used
throughout jarvis.tools.
"""

from __future__ import annotations

from jarvis.integrations.base import CredentialError
from jarvis.integrations.connectors.email import EmailConnector
from jarvis.tools.base import Tool, ToolResult

_connector = EmailConnector()


def _run_email_action(action_name: str, **kwargs) -> ToolResult:
    if not _connector.is_configured():
        return ToolResult(
            ok=False,
            output=(
                "Email is not configured. Set EMAIL_IMAP_HOST, EMAIL_ADDRESS, "
                "and EMAIL_PASSWORD in the environment to enable email tools."
            ),
        )
    try:
        result = _connector.execute(action_name, **kwargs)
    except CredentialError as e:
        return ToolResult(ok=False, output=str(e))
    return ToolResult(ok=result.ok, output=result.output)


class CheckEmailConnectionTool(Tool):
    name = "check_email_connection"
    description = (
        "Verify the configured email (IMAP) credentials work, without "
        "reading any messages. Read-only, no approval required. Returns a "
        "clear message if email is not configured rather than failing."
    )
    input_schema = {"type": "object", "properties": {}}

    def run(self) -> ToolResult:
        return _run_email_action("check_connection")


class GetEmailAccountInfoTool(Tool):
    name = "get_email_account_info"
    description = (
        "Get basic email account info: the configured address, available "
        "mailboxes, and the message count in a mailbox (default INBOX). "
        "Read-only, no approval required."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "mailbox": {
                "type": "string",
                "description": "Mailbox to check the count for. Defaults to INBOX.",
            }
        },
    }

    def run(self, *, mailbox: str = "INBOX") -> ToolResult:
        return _run_email_action("get_account_info", mailbox=mailbox)


class ListRecentEmailsTool(Tool):
    name = "list_recent_emails"
    description = (
        "List recent email headers (id, from, subject, date) in a mailbox "
        "- never message bodies. Most recent first. Read-only, no approval "
        "required. Use read_email with the returned id to see a message's "
        "content."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "mailbox": {
                "type": "string",
                "description": "Mailbox to list. Defaults to INBOX.",
            },
            "limit": {
                "type": "integer",
                "description": "Max messages to list (default 10, max 50).",
            },
        },
    }

    def run(self, *, mailbox: str = "INBOX", limit: int = 10) -> ToolResult:
        return _run_email_action("list_recent_emails", mailbox=mailbox, limit=limit)


class ReadEmailTool(Tool):
    name = "read_email"
    description = (
        "Read one specific email's headers and text content by its id (as "
        "returned by list_recent_emails). Read-only, no approval required."
    )
    input_schema = {
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
    }

    def run(self, *, message_id: str, mailbox: str = "INBOX") -> ToolResult:
        return _run_email_action("read_email", message_id=message_id, mailbox=mailbox)
