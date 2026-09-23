"""Agent-facing tool wrappers around
jarvis.integrations.connectors.gmail.GmailConnector: the bridge between
the LLM-driven agent and the Gmail connector, exactly the shape
jarvis.tools.stripe_tools/email_tools use to bridge the agent to their
respective connectors. No business logic lives here - each tool's run()
only delegates to the connector.

All five tools wrap READ_ONLY actions (recent messages, unread messages,
search by sender/subject/query, one message's short preview, one
message's full decoded body), so none of them requires
confirm_side_effect() or confirm_external_action() - there is nothing to
approve when nothing external is being changed, mirroring how the
Stripe/email tools and read_file/get_workflow_state skip approval today.
There is currently no write action (send, delete/trash, archive, modify
labels - including marking as read, create a draft, ...) to wrap, so no
approval-gated Gmail tool exists yet - see GmailConnector's own module
docstring for what adding one would require. A reply is composed as
plain LLM-generated text in the conversation (see jarvis.core.llm
.BASE_SYSTEM_PROMPT's Gmail section) - there is deliberately no
"create draft" or "send" tool here to call.

If Gmail isn't configured (no OAuth tokens stored via
jarvis.integrations.oauth.TokenStore), every tool here returns a clear,
non-crashing ToolResult(ok=False, ...) instead of attempting a network
call - the same "fail closed with a clear message" pattern used
throughout jarvis.tools.
"""

from __future__ import annotations

from jarvis.integrations.base import CredentialError
from jarvis.integrations.connectors.gmail import GmailConnector
from jarvis.tools.base import Tool, ToolResult

_connector = GmailConnector()


def _run_gmail_action(action_name: str, **kwargs) -> ToolResult:
    if not _connector.is_configured():
        return ToolResult(
            ok=False,
            output=(
                "Gmail is not configured. No OAuth tokens are stored - "
                "connect a Google account to enable Gmail tools. There is "
                "no password or API key path for Gmail; only OAuth is "
                "supported."
            ),
        )
    try:
        result = _connector.execute(action_name, **kwargs)
    except CredentialError as e:
        return ToolResult(ok=False, output=str(e))
    return ToolResult(ok=result.ok, output=result.output)


class ListRecentGmailMessagesTool(Tool):
    name = "list_recent_gmail_messages"
    description = (
        "List recent Gmail messages in the inbox (id, from, subject, date, "
        "snippet) - most recent first. Use this to answer 'show me my "
        "recent emails' or 'did I get any new emails today' (each message "
        "includes its date - compare against today's date yourself, the "
        "tool does not pre-filter by date). Read-only, no approval "
        "required."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max messages to list (default 10, max 50).",
            }
        },
    }

    def run(self, *, limit: int = 10) -> ToolResult:
        return _run_gmail_action("list_recent_messages", limit=limit)


class ListUnreadGmailMessagesTool(Tool):
    name = "list_unread_gmail_messages"
    description = (
        "List unread Gmail messages in the inbox (id, from, subject, date, "
        "snippet) - most recent first. Use this to answer 'show me unread "
        "emails'. Read-only, no approval required."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max messages to list (default 10, max 50).",
            }
        },
    }

    def run(self, *, limit: int = 10) -> ToolResult:
        return _run_gmail_action("list_unread_messages", limit=limit)


class SearchGmailMessagesTool(Tool):
    name = "search_gmail_messages"
    description = (
        "Search Gmail messages using Gmail's own query syntax (id, from, "
        "subject, date, snippet) - most recent first. Use "
        "'from:someone@example.com' to answer 'find emails from X', or "
        "'subject:some topic' (or a plain keyword) to answer 'find emails "
        "about Y'. Read-only, no approval required."
    )
    input_schema = {
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
                "description": "Max messages to list (default 10, max 50).",
            },
        },
        "required": ["query"],
    }

    def run(self, *, query: str, limit: int = 10) -> ToolResult:
        return _run_gmail_action("search_messages", query=query, limit=limit)


class GetGmailMessageTool(Tool):
    name = "get_gmail_message"
    description = (
        "Get one specific Gmail message's headers and short snippet by "
        "its id (as returned by list_recent_gmail_messages/"
        "list_unread_gmail_messages/search_gmail_messages). Use this for "
        "a quick preview. For the full message text - needed to summarize "
        "in detail or draft a reply - use get_gmail_message_content "
        "instead. Read-only, no approval required."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "message_id": {
                "type": "string",
                "description": "The message id from a list/search result.",
            }
        },
        "required": ["message_id"],
    }

    def run(self, *, message_id: str) -> ToolResult:
        return _run_gmail_action("get_message", message_id=message_id)


class GetGmailMessageContentTool(Tool):
    name = "get_gmail_message_content"
    description = (
        "Get one specific Gmail message's headers and FULL decoded body "
        "text (not just the short snippet get_gmail_message returns) by "
        "its id. Use this before summarizing a message in detail or "
        "drafting a reply in Lithuanian. Read-only - this only reads the "
        "message; it never sends, saves, or modifies anything, and there "
        "is no tool that does. No approval required."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "message_id": {
                "type": "string",
                "description": "The message id from a list/search result.",
            }
        },
        "required": ["message_id"],
    }

    def run(self, *, message_id: str) -> ToolResult:
        return _run_gmail_action("get_message_full_content", message_id=message_id)
