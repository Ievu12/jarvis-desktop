"""Agent-facing tool wrappers around
jarvis.integrations.connectors.instagram.InstagramConnector: the bridge
between the LLM-driven agent and the Instagram connector, exactly the
shape jarvis.tools.gmail_tools/calendar_tools/stripe_tools use to bridge
the agent to their respective connectors. No business logic lives here -
each tool's run() only delegates to the connector.

All eleven tools wrap READ_ONLY actions (profile, recent media, one
media item's details/insights, account-wide insights, a data-only
recent-performance analysis, a "how did today go" daily report, saving
today's numbers to a local history file, comparing previously recorded
history, comments, recent conversations), so none of them requires
confirm_side_effect() or
confirm_external_action() - there is nothing to approve when nothing
external is being changed, mirroring how the Gmail/Calendar/Stripe/email
tools and read_file/get_workflow_state skip approval today. There is
currently no write action (publish media, reply to/delete/hide a
comment, send a message, ...) to wrap - this is Stage 1A only. See
InstagramConnector's own module docstring for what adding one would
require.

If Instagram isn't configured (no OAuth tokens stored via
jarvis.integrations.oauth.TokenStore, under its own "instagram" service
name - entirely separate from Gmail's/Calendar's stored tokens), every
tool here returns a clear, non-crashing ToolResult(ok=False, ...)
instead of attempting a network call - the same "fail closed with a
clear message" pattern used throughout jarvis.tools.
"""

from __future__ import annotations

from jarvis.integrations.base import CredentialError
from jarvis.integrations.connectors.instagram import InstagramConnector
from jarvis.tools.base import Tool, ToolResult

_connector = InstagramConnector()


def _run_instagram_action(action_name: str, **kwargs) -> ToolResult:
    if not _connector.is_configured():
        return ToolResult(
            ok=False,
            output=(
                "Instagram is not configured. No OAuth tokens are stored - "
                "connect an Instagram Business/Creator account (via a "
                "linked Facebook Page) to enable Instagram tools. There is "
                "no password or API key path for Instagram; only OAuth is "
                "supported."
            ),
        )
    try:
        result = _connector.execute(action_name, **kwargs)
    except CredentialError as e:
        return ToolResult(ok=False, output=str(e))
    return ToolResult(ok=result.ok, output=result.output)


class GetInstagramProfileTool(Tool):
    name = "get_instagram_profile"
    description = (
        "Get the connected Instagram Business/Creator account's profile "
        "info (username, name, follower count, media count, account "
        "type). Read-only, no approval required."
    )
    input_schema = {"type": "object", "properties": {}}

    def run(self) -> ToolResult:
        return _run_instagram_action("get_profile")


class ListRecentInstagramMediaTool(Tool):
    name = "list_recent_instagram_media"
    description = (
        "List recent posts on the connected Instagram account (id, media "
        "type, caption, permalink, timestamp) - most recent first. "
        "Read-only, no approval required."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max posts to list (default 10, max 50).",
            }
        },
    }

    def run(self, *, limit: int = 10) -> ToolResult:
        return _run_instagram_action("list_recent_media", limit=limit)


class GetInstagramMediaDetailsTool(Tool):
    name = "get_instagram_media_details"
    description = (
        "Get one specific Instagram post's full details by its media id "
        "(as returned by list_recent_instagram_media) - caption, media "
        "type, permalink, timestamp, like/comment counts. Read-only, no "
        "approval required."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "media_id": {
                "type": "string",
                "description": "The media id from list_recent_instagram_media.",
            }
        },
        "required": ["media_id"],
    }

    def run(self, *, media_id: str) -> ToolResult:
        return _run_instagram_action("get_media_details", media_id=media_id)


class GetInstagramMediaInsightsTool(Tool):
    name = "get_instagram_media_insights"
    description = (
        "Get one specific Instagram post's performance insights (likes, "
        "comments, shares, saved, total_interactions, reach) by its "
        "media id - only available for Business/Creator accounts. For "
        "account-wide statistics not tied to one post, use "
        "get_instagram_account_insights instead. Read-only, no approval "
        "required."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "media_id": {
                "type": "string",
                "description": "The media id from list_recent_instagram_media.",
            }
        },
        "required": ["media_id"],
    }

    def run(self, *, media_id: str) -> ToolResult:
        return _run_instagram_action("get_media_insights", media_id=media_id)


class GetInstagramAccountInsightsTool(Tool):
    name = "get_instagram_account_insights"
    description = (
        "Get account-wide performance statistics (profile views, reach) "
        "for the connected Instagram account over a given period - NOT "
        "tied to a single post. For one specific post's performance, use "
        "get_instagram_media_insights instead. Only available for "
        "Business/Creator accounts; may return no data for a period with "
        "no recorded activity yet. Read-only, no approval required."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "period": {
                "type": "string",
                "description": (
                    "Aggregation period (default 'day'). One of Meta's "
                    "supported period values, e.g. 'day', 'week', 'days_28'."
                ),
            }
        },
    }

    def run(self, *, period: str = "day") -> ToolResult:
        return _run_instagram_action("get_account_insights", period=period)


class AnalyzeInstagramInsightsTool(Tool):
    name = "analyze_instagram_insights"
    description = (
        "Analyze recent Instagram performance: compares the latest "
        "account-level insights (reach, profile views) day-over-day, and "
        "compares the most recent post's insights (likes, comments, "
        "shares, saved, total_interactions, reach) against the average of "
        "the preceding posts - returning percent change and direction "
        "(up/down/flat) per metric, in Lithuanian, as a plain data "
        "summary. This tool does NOT write the analysis or content "
        "suggestions itself - after calling it, read the numbers it "
        "returns and write a short Lithuanian analysis plus content ideas "
        "based only on those numbers; never invent or assume a metric "
        "(e.g. following count) that isn't in the summary. Read-only, no "
        "approval required."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "compare_media_count": {
                "type": "integer",
                "description": (
                    "How many preceding posts to average for the media "
                    "comparison baseline (default 5)."
                ),
            }
        },
    }

    def run(self, *, compare_media_count: int = 5) -> ToolResult:
        return _run_instagram_action(
            "analyze_insights", compare_media_count=compare_media_count
        )


class GetInstagramDailyReportTool(Tool):
    name = "get_instagram_daily_report"
    description = (
        "Answer 'how did my Instagram do today' (Lithuanian: 'Kaip sekėsi "
        "mano Instagram šiandien?'): reach change vs the prior day, plus "
        "totals (likes, comments, shares, saved, total_interactions) "
        "summed across only the posts published today (UTC calendar "
        "date), the best-performing post published today, and what "
        "improved/declined. If nothing was published today, that is "
        "stated plainly and per-post figures are reported as unavailable "
        "- never estimated from older posts. This tool does NOT write "
        "recommendations itself - after calling it, write exactly 3 "
        "concrete Lithuanian content recommendations for tomorrow based "
        "only on the numbers it returns; never invent or assume a metric "
        "not present in the summary. Read-only, no approval required."
    )
    input_schema = {"type": "object", "properties": {}}

    def run(self) -> ToolResult:
        return _run_instagram_action("daily_report")


class RecordInstagramDailySnapshotTool(Tool):
    name = "record_instagram_daily_snapshot"
    description = (
        "Read today's real Instagram Insights (reach, likes, comments, "
        "shares, saved, total_interactions, profile views where "
        "available) and save them to JARVIS's own local history file, "
        "keyed by today's date - needed because Meta's API does not keep "
        "insights history indefinitely. A metric with no data today is "
        "simply not recorded, never invented. Safe to run more than once "
        "per day. This changes ONLY a local JARVIS file - it makes NO "
        "change to the Instagram account itself. Call this once per day "
        "(or whenever asked to 'save today's stats'/'išsaugok šiandienos "
        "statistiką') so compare_instagram_history has data to compare "
        "against later. Read-only with respect to Instagram, no approval "
        "required."
    )
    input_schema = {"type": "object", "properties": {}}

    def run(self) -> ToolResult:
        return _run_instagram_action("record_daily_snapshot")


class CompareInstagramHistoryTool(Tool):
    name = "compare_instagram_history"
    description = (
        "Compare PREVIOUSLY RECORDED Instagram Insights snapshots (saved "
        "via record_instagram_daily_snapshot) - never a live API call. "
        "Three comparisons: today vs yesterday, today vs 7 days ago, and "
        "the last 7 recorded days (summed) vs the 7 recorded days before "
        "that. Any date not previously recorded is reported plainly as "
        "unavailable for that comparison - never interpolated or "
        "guessed. Requires record_instagram_daily_snapshot to have been "
        "run on the relevant days beforehand; a sparse history will "
        "report most comparisons as unavailable, which is correct, not "
        "an error - say so plainly rather than filling in a number. "
        "Read-only, no approval required."
    )
    input_schema = {"type": "object", "properties": {}}

    def run(self) -> ToolResult:
        return _run_instagram_action("compare_history")


class ListInstagramCommentsTool(Tool):
    name = "list_instagram_comments"
    description = (
        "List comments on one specific Instagram post by its media id "
        "(id, username, text, timestamp). Read-only - there is no way to "
        "reply to, hide, or delete a comment yet. No approval required."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "media_id": {
                "type": "string",
                "description": "The media id from list_recent_instagram_media.",
            },
            "limit": {
                "type": "integer",
                "description": "Max comments to list (default 10, max 50).",
            },
        },
        "required": ["media_id"],
    }

    def run(self, *, media_id: str, limit: int = 10) -> ToolResult:
        return _run_instagram_action("list_comments", media_id=media_id, limit=limit)


class ListRecentInstagramMessagesTool(Tool):
    name = "list_recent_instagram_messages"
    description = (
        "List recent Instagram Direct conversations and their most "
        "recent message (conversation id, participant, latest message "
        "snippet, timestamp). Read-only - there is no way to send a "
        "message yet. No approval required."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Max conversations to list (default 10, max 25).",
            }
        },
    }

    def run(self, *, limit: int = 10) -> ToolResult:
        return _run_instagram_action("list_recent_messages", limit=limit)
