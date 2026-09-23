"""Instagram connector (Meta Graph API, OAuth-only, read-only Stage 1A):
the fifth real implementation of jarvis.integrations.base.Connector,
alongside jarvis.integrations.connectors.email.EmailConnector, .stripe
.StripeConnector, .google_calendar.GoogleCalendarConnector, and
.gmail.GmailConnector.

Architected exactly like GmailConnector/GoogleCalendarConnector:
OAuth-only (no password/API-key fallback path), authenticating with a
Bearer access token obtained from jarvis.integrations.oauth.TokenStore -
never an environment variable. This module does not modify or extend
GmailConnector or GoogleCalendarConnector in any way; it is a fully
separate connector with its own TokenStore("instagram") entry (a
distinct OS keychain record from "gmail"/"google_calendar" - see
jarvis.integrations.oauth.TokenStore).

Every action here is RiskLevel.READ_ONLY: profile info, listing recent
media, one media item's insights, one media item's details, account-wide
insights, a data-only analysis of recent performance (analyze_insights -
percent change/direction, no interpretation or suggestions - see
_do_analyze_insights()), a "how did today go" daily report (daily_report
- today's reach change plus totals/best-performer for posts published
today only, see _do_daily_report()), saving today's real numbers to a
local history file (record_daily_snapshot - see _do_record_daily_snapshot()
and jarvis.integrations.instagram_history), comparing previously
recorded snapshots (compare_history - see _do_compare_history(); never a
live API call), listing comments on a media item, and listing recent
conversation messages. NO write action against the Instagram account
itself (publish media, reply to a comment, delete/hide a comment, send a
message, ...) exists anywhere in this module - this is Stage 1A only.
record_daily_snapshot writes to a local JARVIS file only (see
jarvis.integrations.instagram_history), never to Instagram, which is why
it remains READ_ONLY with respect to this connector's risk model - the
same way jarvis.session.store persisting conversation history isn't an
"external write" either. Adding an actual Instagram write action later
means adding a new ExternalAction with RiskLevel.REVERSIBLE_WRITE or
IRREVERSIBLE_WRITE and going through jarvis.integrations.approval
.confirm_external_action() - it cannot happen by accident, since no code
path here can currently do it, and this module never calls anything from
the Graph API beyond the fixed GET endpoints below (analyze_insights,
daily_report, and record_daily_snapshot included - all three only
compose those same GET endpoints, never a new one; compare_history makes
no Graph API call at all).

Authentication and account resolution: this connector targets the
current Instagram API with Instagram Login (NOT the older Facebook Login
for Business flow - see jarvis.integrations.oauth
.META_INSTAGRAM_OAUTH_PROVIDER and meta_instagram_oauth_setup.py, which
perform the authorization-code exchange, resolve the Instagram Business
Account id, and exchange the short-lived token for a long-lived one
(~60 days) via graph.instagram.com/access_token - Meta does not issue a
standard OAuth refresh_token from the authorization-code grant the way
Google does). All of that happens entirely in the standalone setup
script at connect time - never inside this connector's execute() path,
and never automatically.

IMPORTANT: a token issued by the Instagram Login flow is only valid
against graph.instagram.com endpoints - it is NOT interchangeable with
graph.facebook.com (the older Facebook Login for Business API surface).
Using the wrong base URL here produces Meta error code 190 ("Invalid
OAuth access token - Cannot parse access token") even though the token
itself is valid and correctly stored. _API_BASE_URL below must stay
graph.instagram.com to match the token meta_instagram_oauth_setup.py
actually issues. Exactly like GmailConnector/GoogleCalendarConnector, an
expired stored token is refused with a clear CredentialError rather than
attempting a refresh, since jarvis.integrations.oauth
.refresh_access_token() remains deliberately unimplemented.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from jarvis.integrations import instagram_history
from jarvis.integrations.base import (
    Connector,
    CredentialError,
    ExternalAction,
    ExternalActionResult,
    RiskLevel,
)
from jarvis.integrations.oauth import TokenStore, is_token_expired

_ONE_DAY = timedelta(days=1)

SERVICE_NAME = "instagram"

_API_BASE_URL = "https://graph.instagram.com"
_REQUEST_TIMEOUT_SECONDS = 15
_DEFAULT_MEDIA_LIMIT = 10
_MAX_MEDIA_LIMIT = 50
_DEFAULT_COMMENT_LIMIT = 10
_MAX_COMMENT_LIMIT = 50
_DEFAULT_CONVERSATION_LIMIT = 10
_MAX_CONVERSATION_LIMIT = 25

# Business/Creator accounts only - insights are unavailable for personal
# Instagram accounts, and Meta's API simply omits them rather than
# erroring, so no special-casing is needed here beyond what the
# formatting functions already do (report "no data" plainly).
#
# Confirmed against the live Media Insights API (graph.instagram.com/
# {media-id}/insights) for a VIDEO post: "impressions" and "engagement"
# are NOT in Meta's currently accepted metric list for this media
# product type (Meta's error response names the accepted set) -
# "engagement" was renamed to "total_interactions". This set - likes,
# comments, shares, saved, total_interactions, reach - was verified to
# return data in a single real (read-only) request.
_MEDIA_INSIGHTS_METRICS = "likes,comments,shares,saved,total_interactions,reach"

# Account-level insights (jarvis.integrations.connectors.instagram
# .InstagramConnector._do_get_account_insights) - distinct from
# per-media insights above. "profile_visits" (the metric name product
# docs commonly use) is NOT accepted by the Media Insights API at all
# ("does not support the profile_visits metric for this media product
# type" - confirmed via a live request) - it is an ACCOUNT-level metric,
# named "profile_views" at this endpoint, reachable only via
# /{account_id}/insights, never /{media_id}/insights. "reach" is
# included here too since it is also meaningful at the account level
# (confirmed to return real data via a live request), separate from a
# single media item's reach.
_ACCOUNT_INSIGHTS_METRICS = "profile_views,reach"
_DEFAULT_ACCOUNT_INSIGHTS_PERIOD = "day"

# analyze_insights (InstagramConnector._do_analyze_insights) - a pure
# aggregation over the three read-only actions above (get_account_insights,
# list_recent_media, get_media_insights). It makes no new Graph API call of
# its own; it only calls existing methods and does arithmetic on what they
# already return. Account-level comparison uses the last two time buckets
# from get_account_insights(period="day") (yesterday vs the day before).
# Media-level comparison has no time series available from the API at all
# (get_media_insights returns only a current snapshot per post, never
# historical values) - so it compares the single most recent post against
# the average of the _ANALYZE_COMPARISON_MEDIA_COUNT posts before it, per
# jarvis product decision (see conversation), not a Meta API limitation.
_ANALYZE_COMPARISON_MEDIA_COUNT = 5
_ANALYZE_MEDIA_METRIC_NAMES = ("likes", "comments", "shares", "saved", "total_interactions", "reach")

# daily_report (InstagramConnector._do_daily_report) - like
# analyze_insights, a pure aggregation over existing read-only calls; it
# adds no new Graph API endpoint. It answers "how did my Instagram do
# TODAY" specifically: reach change vs the prior day (reusing
# _fetch_account_insights_series exactly as analyze_insights does), and
# per-metric TOTALS summed only across posts whose "timestamp" field
# falls on today's UTC calendar date (Meta returns timestamps as ISO 8601
# with a numeric UTC offset - see _is_today() below). If zero posts were
# published today, every per-post figure is reported as unavailable
# rather than guessed or substituted with older posts' numbers - this
# module never estimates a metric it cannot read from the API. Checking
# "today" requires scanning some number of recent posts (there is no
# Graph API filter for "posts from today"), bounded by
# _DAILY_REPORT_MEDIA_SCAN_LIMIT so a quiet account doesn't walk its
# entire history on every report.
_DAILY_REPORT_MEDIA_SCAN_LIMIT = 20


def _redact_token(text: str, access_token: str | None) -> str:
    """Scrub a known access token value out of arbitrary text (e.g. an
    exception message from urllib) before it can reach a result or log
    line. Mirrors jarvis.integrations.connectors.gmail._redact_token()'s
    approach, applied to this connector's own OAuth access token."""
    if not access_token:
        return text
    return text.replace(access_token, "****")


class InstagramConnector(Connector):
    service_name = SERVICE_NAME

    def __init__(self) -> None:
        # OAuth-only: no environment-variable credential path exists for
        # this connector at all, unlike EmailConnector's password/OAuth
        # hybrid. is_configured() below checks ONLY the token store.
        self._token_store = TokenStore(SERVICE_NAME)
        self.actions = [
            ExternalAction(
                name="get_profile",
                description=(
                    "Get the connected Instagram Business/Creator "
                    "account's profile info (username, name, follower "
                    "count, media count, account type). Read-only."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={"type": "object", "properties": {}},
            ),
            ExternalAction(
                name="list_recent_media",
                description=(
                    "List recent posts on the connected Instagram account "
                    "(id, media type, caption, permalink, timestamp) - "
                    "most recent first. Read-only."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": f"Max posts to list (default {_DEFAULT_MEDIA_LIMIT}, max {_MAX_MEDIA_LIMIT}).",
                        }
                    },
                },
            ),
            ExternalAction(
                name="get_media_details",
                description=(
                    "Get one specific post's full details by its media id "
                    "(as returned by list_recent_media) - caption, media "
                    "type, permalink, timestamp, like/comment counts. "
                    "Read-only."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "media_id": {
                            "type": "string",
                            "description": "The media id from list_recent_media.",
                        },
                    },
                    "required": ["media_id"],
                },
            ),
            ExternalAction(
                name="get_media_insights",
                description=(
                    "Get one specific post's performance insights (likes, "
                    "comments, shares, saved, total_interactions, reach) "
                    "by its media id - only available for Business/"
                    "Creator accounts; some metrics may be unavailable "
                    "depending on media age/type. For account-wide "
                    "statistics (not tied to one post), use "
                    "get_account_insights instead. Read-only."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "media_id": {
                            "type": "string",
                            "description": "The media id from list_recent_media.",
                        },
                    },
                    "required": ["media_id"],
                },
            ),
            ExternalAction(
                name="get_account_insights",
                description=(
                    "Get account-wide performance statistics (profile "
                    "views, reach) for the connected Instagram account, "
                    "over a given period - NOT tied to a single post. For "
                    "one specific post's performance, use "
                    "get_media_insights instead. Only available for "
                    "Business/Creator accounts; may return no data for a "
                    "period with no recorded activity yet. Read-only."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "period": {
                            "type": "string",
                            "description": (
                                f"Aggregation period (default "
                                f"'{_DEFAULT_ACCOUNT_INSIGHTS_PERIOD}'). One of "
                                "Meta's supported period values, e.g. 'day', 'week', 'days_28'."
                            ),
                        },
                    },
                },
            ),
            ExternalAction(
                name="analyze_insights",
                description=(
                    "Analyze recent Instagram performance: compares the "
                    "latest account-level insights (reach, profile views) "
                    "day-over-day, and compares the most recent post's "
                    "insights (likes, comments, shares, saved, "
                    "total_interactions, reach) against the average of the "
                    "preceding posts - reporting percent change and "
                    "direction (up/down/flat) for each metric where a prior "
                    "value exists. Returns a plain data summary in "
                    "Lithuanian for the agent to turn into a short analysis "
                    "and content suggestions - no analysis or suggestions "
                    "are generated here. Read-only; makes no new Instagram "
                    "API call beyond get_account_insights/list_recent_media/"
                    "get_media_insights."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "compare_media_count": {
                            "type": "integer",
                            "description": (
                                f"How many preceding posts to average for the "
                                f"media comparison baseline (default "
                                f"{_ANALYZE_COMPARISON_MEDIA_COUNT})."
                            ),
                        },
                    },
                },
            ),
            ExternalAction(
                name="daily_report",
                description=(
                    "Answer 'how did my Instagram do today': reach change "
                    "vs the prior day, and totals (likes, comments, shares, "
                    "saved, total_interactions) summed across only the "
                    "posts published today (UTC calendar date) - plus the "
                    "best-performing post published today (by "
                    "total_interactions) and what improved/declined vs the "
                    "prior day. If no posts were published today, that is "
                    "stated plainly and per-post totals are reported as "
                    "unavailable, never estimated from older posts. Returns "
                    "a plain data summary in Lithuanian - the agent must "
                    "write the 3 content recommendations itself from these "
                    "numbers, never invent a metric this summary doesn't "
                    "contain. Read-only; makes no new Instagram API call "
                    "beyond get_account_insights/list_recent_media/"
                    "get_media_insights."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={"type": "object", "properties": {}},
            ),
            ExternalAction(
                name="record_daily_snapshot",
                description=(
                    "Read today's real Instagram Insights (reach, likes, "
                    "comments, shares, saved, total_interactions, profile "
                    "views where available) from the API and save them to "
                    "JARVIS's own local history file, keyed by today's date "
                    "- needed because Meta's API does not keep insights "
                    "history indefinitely. A metric with no data today is "
                    "simply not recorded, never invented. Safe to run more "
                    "than once per day (overwrites today's entry with the "
                    "latest numbers). This changes ONLY a local JARVIS file "
                    "- it makes no change to the Instagram account itself. "
                    "Read-only with respect to Instagram; call this once per "
                    "day (or whenever asked to 'save today's stats') so "
                    "compare_history has data to compare against later."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={"type": "object", "properties": {}},
            ),
            ExternalAction(
                name="compare_history",
                description=(
                    "Compare PREVIOUSLY RECORDED Instagram Insights snapshots "
                    "(via record_daily_snapshot) - never a live API call. "
                    "Three comparisons: today vs yesterday, today vs 7 days "
                    "ago, and the last 7 recorded days (summed) vs the 7 "
                    "recorded days before that. Any date not previously "
                    "recorded is reported plainly as unavailable for that "
                    "comparison - never interpolated or guessed. Requires "
                    "record_daily_snapshot to have been run on the relevant "
                    "days beforehand; a fresh or sparse history will report "
                    "most comparisons as unavailable, which is correct "
                    "behavior, not an error."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={"type": "object", "properties": {}},
            ),
            ExternalAction(
                name="list_comments",
                description=(
                    "List comments on one specific post by its media id "
                    "(id, username, text, timestamp) - read-only. There is "
                    "no way to reply to, hide, or delete a comment in this "
                    "stage."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "media_id": {
                            "type": "string",
                            "description": "The media id from list_recent_media.",
                        },
                        "limit": {
                            "type": "integer",
                            "description": f"Max comments to list (default {_DEFAULT_COMMENT_LIMIT}, max {_MAX_COMMENT_LIMIT}).",
                        },
                    },
                    "required": ["media_id"],
                },
            ),
            ExternalAction(
                name="list_recent_messages",
                description=(
                    "List recent Instagram Direct conversations and their "
                    "most recent message (conversation id, participant, "
                    "latest message snippet, timestamp) - read-only. There "
                    "is no way to send a message in this stage. Requires "
                    "the instagram_manage_messages permission to be "
                    "granted."
                ),
                risk_level=RiskLevel.READ_ONLY,
                input_schema={
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": f"Max conversations to list (default {_DEFAULT_CONVERSATION_LIMIT}, max {_MAX_CONVERSATION_LIMIT}).",
                        }
                    },
                },
            ),
        ]

    def is_configured(self) -> bool:
        """True only if OAuth tokens (and the resolved Instagram Business
        Account id - see _account_id()) are stored. There is no other
        credential path for this connector."""
        return self._token_store.load() is not None

    def _access_token(self) -> str:
        tokens = self._token_store.load()
        if tokens is None:
            raise CredentialError(
                f"'{SERVICE_NAME}' is not configured - no OAuth tokens are stored. "
                "Connect an Instagram Business/Creator account (via a linked "
                "Facebook Page) to enable this integration."
            )
        if is_token_expired(tokens):
            # Refreshing requires a real token-endpoint HTTP call, which
            # jarvis.integrations.oauth.refresh_access_token() deliberately
            # does not implement yet (see its docstring) - surfaced as a
            # clear, actionable error rather than silently trying and
            # failing deeper in the request. Meta's long-lived tokens last
            # ~60 days; reconnecting means re-running the setup script.
            raise CredentialError(
                f"Stored OAuth tokens for '{SERVICE_NAME}' have expired and automatic "
                "refresh is not yet implemented - reconnect the account to get new tokens."
            )
        return tokens.access_token

    def _account_id(self) -> str:
        """The Instagram Business Account id (distinct from the access
        token) - resolved once at connect time by the standalone setup
        script and stored as the OAuthTokens.scope field's suffix (see
        that script's docstring for the exact encoding), since
        jarvis.integrations.oauth.OAuthTokens has no dedicated field for
        a provider-specific account identifier and adding one would mean
        changing the shared, connector-agnostic oauth.py module for a
        single connector's need."""
        tokens = self._token_store.load()
        if tokens is None:
            raise CredentialError(
                f"'{SERVICE_NAME}' is not configured - no OAuth tokens are stored."
            )
        # OAuthTokens.scope is repurposed here to carry
        # "<granted_scope>|<ig_business_account_id>" - a plain separator
        # encoding chosen to avoid touching oauth.py's OAuthTokens
        # dataclass just for this one connector's extra identifier. See
        # meta_instagram_oauth_setup.py for where this is assembled.
        _, _, account_id = tokens.scope.partition("|")
        if not account_id:
            raise CredentialError(
                f"'{SERVICE_NAME}' tokens are stored but no Instagram Business "
                "Account id was recorded - reconnect the account using the "
                "setup script to re-resolve it."
            )
        return account_id

    def _get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Perform a single, read-only GET request against the Graph API.
        Never called for anything but the actions below - there is no
        generic "call any Graph API endpoint" method for a future write
        action to accidentally reuse unsafely.
        """
        access_token = self._access_token()

        query_params = dict(params or {})
        query_params["access_token"] = access_token

        url = f"{_API_BASE_URL}{path}?{urllib.parse.urlencode(query_params)}"

        request = urllib.request.Request(url, method="GET")

        try:
            with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace")
            if e.code == 429:
                raise CredentialError(
                    "Instagram API rate limit reached (HTTP 429) - this is a "
                    "temporary limit, not a configuration problem. Wait before "
                    "retrying."
                ) from None
            raise CredentialError(
                _redact_token(f"Instagram API request failed ({e.code}): {error_body}", access_token)
            ) from None
        except urllib.error.URLError as e:
            raise CredentialError(
                _redact_token(f"Instagram API connection error: {e.reason}", access_token)
            ) from None
        except TimeoutError:
            raise CredentialError(
                f"Instagram API request timed out after {_REQUEST_TIMEOUT_SECONDS}s."
            ) from None

        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            raise CredentialError(
                _redact_token(f"Instagram API returned invalid JSON: {e}", access_token)
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

    def _do_get_profile(self) -> ExternalActionResult:
        account_id = self._account_id()
        data = self._get(
            f"/{account_id}",
            params={"fields": "username,name,followers_count,media_count,account_type"},
        )

        lines = [
            f"username: {data.get('username', '?')}",
            f"name: {data.get('name', '?')}",
            f"followers_count: {data.get('followers_count', '?')}",
            f"media_count: {data.get('media_count', '?')}",
            f"account_type: {data.get('account_type', '?')}",
        ]
        return ExternalActionResult(
            ok=True, output="\n".join(lines), service=self.service_name, action="get_profile"
        )

    def _do_list_recent_media(self, limit: int = _DEFAULT_MEDIA_LIMIT) -> ExternalActionResult:
        limit = max(1, min(limit, _MAX_MEDIA_LIMIT))
        account_id = self._account_id()
        data = self._get(
            f"/{account_id}/media",
            params={
                "fields": "id,caption,media_type,permalink,timestamp",
                "limit": limit,
            },
        )

        items = data.get("data", [])
        if not items:
            return ExternalActionResult(
                ok=True, output="No posts found.", service=self.service_name,
                action="list_recent_media",
            )

        lines = []
        for item in items:
            media_id = item.get("id", "?")
            media_type = item.get("media_type", "?")
            caption = (item.get("caption") or "")[:80]
            timestamp = item.get("timestamp", "?")
            lines.append(
                f"id={media_id} | type={media_type} | timestamp={timestamp} | caption={caption}"
            )

        return ExternalActionResult(
            ok=True, output="\n".join(lines), service=self.service_name, action="list_recent_media"
        )

    def _do_get_media_details(self, media_id: str) -> ExternalActionResult:
        if not media_id or not media_id.strip():
            return ExternalActionResult(
                ok=False, output="A media_id is required.", service=self.service_name,
                action="get_media_details",
            )
        data = self._get(
            f"/{media_id.strip()}",
            params={
                "fields": "id,caption,media_type,permalink,timestamp,like_count,comments_count",
            },
        )

        lines = [
            f"id: {data.get('id', '?')}",
            f"media_type: {data.get('media_type', '?')}",
            f"timestamp: {data.get('timestamp', '?')}",
            f"permalink: {data.get('permalink', '?')}",
            f"like_count: {data.get('like_count', '?')}",
            f"comments_count: {data.get('comments_count', '?')}",
            f"caption: {data.get('caption', '')}",
        ]
        return ExternalActionResult(
            ok=True, output="\n".join(lines), service=self.service_name, action="get_media_details"
        )

    def _do_get_media_insights(self, media_id: str) -> ExternalActionResult:
        if not media_id or not media_id.strip():
            return ExternalActionResult(
                ok=False, output="A media_id is required.", service=self.service_name,
                action="get_media_insights",
            )
        data = self._get(
            f"/{media_id.strip()}/insights",
            params={"metric": _MEDIA_INSIGHTS_METRICS},
        )

        entries = data.get("data", [])
        if not entries:
            return ExternalActionResult(
                ok=True,
                output=(
                    "No insights data available for this post (may not be a "
                    "Business/Creator account post, or the metric isn't "
                    "supported for this media type)."
                ),
                service=self.service_name,
                action="get_media_insights",
            )

        lines = []
        for entry in entries:
            name = entry.get("name", "?")
            values = entry.get("values", [{}])
            value = values[0].get("value", "?") if values else "?"
            lines.append(f"{name}: {value}")

        return ExternalActionResult(
            ok=True, output="\n".join(lines), service=self.service_name, action="get_media_insights"
        )

    def _do_get_account_insights(self, period: str = _DEFAULT_ACCOUNT_INSIGHTS_PERIOD) -> ExternalActionResult:
        period = period.strip() if period and period.strip() else _DEFAULT_ACCOUNT_INSIGHTS_PERIOD
        account_id = self._account_id()
        data = self._get(
            f"/{account_id}/insights",
            params={"metric": _ACCOUNT_INSIGHTS_METRICS, "period": period},
        )

        entries = data.get("data", [])
        if not entries:
            return ExternalActionResult(
                ok=True,
                output=(
                    f"No account insights data available for period '{period}' "
                    "(may not be a Business/Creator account, or nothing has "
                    "been recorded for this period yet)."
                ),
                service=self.service_name,
                action="get_account_insights",
            )

        # Unlike per-media insights (one value per metric), account-level
        # insights return one value PER TIME BUCKET within the period
        # (e.g. one entry per day for period='day') - each is reported on
        # its own line with its end_time, rather than only the first
        # value, so the caller sees the actual time series Meta returned.
        lines = []
        for entry in entries:
            name = entry.get("name", "?")
            values = entry.get("values", [])
            if not values:
                lines.append(f"{name}: (no data)")
                continue
            for point in values:
                value = point.get("value", "?")
                end_time = point.get("end_time", "?")
                lines.append(f"{name}: {value} (as of {end_time})")

        return ExternalActionResult(
            ok=True, output="\n".join(lines), service=self.service_name, action="get_account_insights"
        )

    def _fetch_account_insights_series(
        self, period: str = _DEFAULT_ACCOUNT_INSIGHTS_PERIOD
    ) -> dict[str, list[dict[str, Any]]]:
        """Raw (metric name -> list of {value, end_time} points, oldest
        first as Meta returns them) for account-level insights - the same
        underlying request as _do_get_account_insights(), but returning
        structured data instead of a formatted string, for
        _do_analyze_insights() to do arithmetic on. Not exposed as its own
        action; _do_get_account_insights() keeps its own independent
        request/formatting so this helper's existence never changes that
        action's behavior."""
        account_id = self._account_id()
        data = self._get(
            f"/{account_id}/insights",
            params={"metric": _ACCOUNT_INSIGHTS_METRICS, "period": period},
        )
        series: dict[str, list[dict[str, Any]]] = {}
        for entry in data.get("data", []):
            name = entry.get("name")
            if not name:
                continue
            series[name] = entry.get("values", [])
        return series

    def _fetch_media_insights_values(self, media_id: str) -> dict[str, float]:
        """Raw {metric name: numeric value} for one post's insights - the
        same underlying request as _do_get_media_insights(), but returning
        numbers instead of a formatted string. Non-numeric or missing
        values are simply omitted (never raise) since a media item can
        legitimately lack a metric (e.g. a very new post)."""
        data = self._get(
            f"/{media_id}/insights",
            params={"metric": _MEDIA_INSIGHTS_METRICS},
        )
        values: dict[str, float] = {}
        for entry in data.get("data", []):
            name = entry.get("name")
            entry_values = entry.get("values", [])
            if not name or not entry_values:
                continue
            raw_value = entry_values[0].get("value")
            if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
                values[name] = float(raw_value)
        return values

    @staticmethod
    def _percent_change(previous: float, current: float) -> str:
        """Format a percent-change + direction marker for one metric.
        previous == 0 is reported as a fresh/new value rather than a
        (mathematically undefined) percent change, since a jump from 0 is
        not meaningfully "infinite percent" to a reader."""
        if previous == 0:
            if current == 0:
                return "0 -> 0 (be pokyčio)"
            return f"0 -> {current:g} (naujas rezultatas, palyginimo bazė buvo 0)"
        change = ((current - previous) / previous) * 100
        if abs(change) < 0.5:
            direction = "→ nepakito"
        elif change > 0:
            direction = "↑ augo"
        else:
            direction = "↓ mažėjo"
        return f"{previous:g} -> {current:g} ({change:+.1f}%) {direction}"

    def _do_analyze_insights(
        self, compare_media_count: int = _ANALYZE_COMPARISON_MEDIA_COUNT
    ) -> ExternalActionResult:
        compare_media_count = max(1, min(compare_media_count, _MAX_MEDIA_LIMIT - 1))
        sections: list[str] = []

        # --- Account-level: latest day vs the day before it ------------
        sections.append("=== Paskyros rodikliai (diena prieš dieną) ===")
        try:
            account_series = self._fetch_account_insights_series(period="day")
        except CredentialError:
            raise
        account_lines: list[str] = []
        for metric_name, points in account_series.items():
            numeric_points = [
                p for p in points
                if isinstance(p.get("value"), (int, float)) and not isinstance(p.get("value"), bool)
            ]
            if len(numeric_points) < 2:
                if len(numeric_points) == 1:
                    only = numeric_points[0]
                    account_lines.append(
                        f"{metric_name}: {only.get('value'):g} "
                        f"(as of {only.get('end_time', '?')}) - nepakanka duomenų palyginimui"
                    )
                else:
                    account_lines.append(f"{metric_name}: nėra duomenų")
                continue
            previous_point, latest_point = numeric_points[-2], numeric_points[-1]
            comparison = self._percent_change(
                float(previous_point["value"]), float(latest_point["value"])
            )
            account_lines.append(
                f"{metric_name} ({previous_point.get('end_time', '?')} -> "
                f"{latest_point.get('end_time', '?')}): {comparison}"
            )
        if not account_lines:
            account_lines.append("Nėra prieinamų paskyros lygio insights duomenų.")
        sections.extend(account_lines)

        # --- Media-level: latest post vs average of the N before it ----
        sections.append("")
        sections.append(
            f"=== Naujausio įrašo rodikliai vs paskutinių {compare_media_count} "
            "ankstesnių įrašų vidurkis ==="
        )
        account_id = self._account_id()
        media_data = self._get(
            f"/{account_id}/media",
            params={"fields": "id,timestamp", "limit": compare_media_count + 1},
        )
        media_items = media_data.get("data", [])
        if len(media_items) < 2:
            sections.append(
                "Nepakanka įrašų palyginimui (reikia bent 2 paskelbtų įrašų)."
            )
        else:
            latest_id = media_items[0]["id"]
            comparison_ids = [item["id"] for item in media_items[1:]]

            latest_values = self._fetch_media_insights_values(latest_id)
            comparison_values_list = [
                self._fetch_media_insights_values(mid) for mid in comparison_ids
            ]

            media_lines: list[str] = []
            for metric_name in _ANALYZE_MEDIA_METRIC_NAMES:
                latest_value = latest_values.get(metric_name)
                prior_values = [
                    v[metric_name] for v in comparison_values_list if metric_name in v
                ]
                if latest_value is None:
                    media_lines.append(f"{metric_name}: naujausiam įrašui nėra duomenų")
                    continue
                if not prior_values:
                    media_lines.append(
                        f"{metric_name}: {latest_value:g} - nėra ankstesnių įrašų "
                        "duomenų palyginimui"
                    )
                    continue
                average_prior = sum(prior_values) / len(prior_values)
                comparison = self._percent_change(average_prior, latest_value)
                media_lines.append(f"{metric_name}: {comparison}")
            sections.extend(media_lines)

        return ExternalActionResult(
            ok=True, output="\n".join(sections), service=self.service_name, action="analyze_insights"
        )

    @staticmethod
    def _is_today(timestamp: str | None) -> bool:
        """True if a Graph API "timestamp" field (ISO 8601 with a numeric
        UTC offset, e.g. "2026-09-22T10:38:28+0000") falls on today's UTC
        calendar date. Returns False (never raises) for a missing or
        unparseable timestamp, so a malformed field just excludes that
        post from "today" rather than crashing the report."""
        if not timestamp:
            return False
        try:
            # Python's fromisoformat wants "+00:00", not Meta's "+0000".
            normalized = timestamp
            if len(normalized) >= 5 and normalized[-5] in "+-" and ":" not in normalized[-5:]:
                normalized = normalized[:-2] + ":" + normalized[-2:]
            parsed = datetime.fromisoformat(normalized)
        except ValueError:
            return False
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).date() == datetime.now(timezone.utc).date()

    def _do_daily_report(self) -> ExternalActionResult:
        sections: list[str] = []

        # --- Reach: today vs the prior day (reuses the same series as
        # analyze_insights's account-level comparison) -------------------
        sections.append("=== Reach pokytis (šiandien vs vakar) ===")
        account_series = self._fetch_account_insights_series(period="day")
        reach_points = [
            p for p in account_series.get("reach", [])
            if isinstance(p.get("value"), (int, float)) and not isinstance(p.get("value"), bool)
        ]
        if len(reach_points) >= 2:
            previous_point, latest_point = reach_points[-2], reach_points[-1]
            sections.append(
                f"reach ({previous_point.get('end_time', '?')} -> "
                f"{latest_point.get('end_time', '?')}): "
                f"{self._percent_change(float(previous_point['value']), float(latest_point['value']))}"
            )
        elif len(reach_points) == 1:
            only = reach_points[0]
            sections.append(
                f"reach: {only.get('value'):g} (as of {only.get('end_time', '?')}) "
                "- nepakanka duomenų palyginimui su ankstesne diena"
            )
        else:
            sections.append("reach: duomenų nėra.")

        # --- Today's posts: totals + best performer ----------------------
        account_id = self._account_id()
        media_data = self._get(
            f"/{account_id}/media",
            params={"fields": "id,timestamp", "limit": _DAILY_REPORT_MEDIA_SCAN_LIMIT},
        )
        todays_media_ids = [
            item["id"] for item in media_data.get("data", [])
            if self._is_today(item.get("timestamp"))
        ]

        sections.append("")
        sections.append("=== Šiandien paskelbtų įrašų rodikliai ===")
        if not todays_media_ids:
            sections.append(
                "Šiandien (UTC data) nepaskelbta nė vieno įrašo - rodiklių "
                "suvestinės ir geriausiai pasirodžiusio turinio pateikti "
                "negalima."
            )
            for metric_name in _ANALYZE_MEDIA_METRIC_NAMES:
                if metric_name == "reach":
                    continue
                sections.append(f"{metric_name}: neprieinama (šiandien nepaskelbta įrašų)")
        else:
            per_media_values = {
                media_id: self._fetch_media_insights_values(media_id)
                for media_id in todays_media_ids
            }
            totals: dict[str, float] = {}
            for metric_name in _ANALYZE_MEDIA_METRIC_NAMES:
                if metric_name == "reach":
                    continue
                values_for_metric = [
                    v[metric_name] for v in per_media_values.values() if metric_name in v
                ]
                if values_for_metric:
                    totals[metric_name] = sum(values_for_metric)
            for metric_name in _ANALYZE_MEDIA_METRIC_NAMES:
                if metric_name == "reach":
                    continue
                if metric_name in totals:
                    sections.append(f"{metric_name}: {totals[metric_name]:g} (iš viso, {len(todays_media_ids)} įrašas(-ai))")
                else:
                    sections.append(f"{metric_name}: duomenų nėra")

            sections.append("")
            sections.append("=== Geriausiai pasirodęs šiandienos turinys ===")
            best_media_id = None
            best_score = None
            for media_id, values in per_media_values.items():
                score = values.get("total_interactions")
                if score is None:
                    continue
                if best_score is None or score > best_score:
                    best_score = score
                    best_media_id = media_id
            if best_media_id is None:
                sections.append(
                    "Negalima nustatyti - nė vienam šiandienos įrašui nėra "
                    "total_interactions duomenų."
                )
            else:
                best_values = per_media_values[best_media_id]
                metric_summary = ", ".join(
                    f"{name}={best_values[name]:g}"
                    for name in _ANALYZE_MEDIA_METRIC_NAMES
                    if name in best_values
                )
                sections.append(f"media_id={best_media_id} | {metric_summary}")

        return ExternalActionResult(
            ok=True, output="\n".join(sections), service=self.service_name, action="daily_report"
        )

    def _fetch_todays_metrics(self) -> dict[str, float]:
        """Raw {metric name: number} for today (UTC calendar date) only -
        reach's latest account-level value (if available) plus totals
        summed across today's posts (if any were published) - the same
        underlying data _do_daily_report() formats into its Lithuanian
        report, returned here as plain numbers for record_daily_snapshot()
        to persist. A metric with no data today is simply absent from the
        returned dict - never defaulted to 0."""
        metrics: dict[str, float] = {}

        account_series = self._fetch_account_insights_series(period="day")
        reach_points = [
            p for p in account_series.get("reach", [])
            if isinstance(p.get("value"), (int, float)) and not isinstance(p.get("value"), bool)
        ]
        if reach_points:
            # The latest point in the series is "today" per Meta - the
            # same point _do_daily_report() reports as the current value.
            metrics["reach"] = float(reach_points[-1]["value"])

        profile_views_points = [
            p for p in account_series.get("profile_views", [])
            if isinstance(p.get("value"), (int, float)) and not isinstance(p.get("value"), bool)
        ]
        if profile_views_points:
            metrics["profile_views"] = float(profile_views_points[-1]["value"])

        account_id = self._account_id()
        media_data = self._get(
            f"/{account_id}/media",
            params={"fields": "id,timestamp", "limit": _DAILY_REPORT_MEDIA_SCAN_LIMIT},
        )
        todays_media_ids = [
            item["id"] for item in media_data.get("data", [])
            if self._is_today(item.get("timestamp"))
        ]
        if todays_media_ids:
            per_media_values = [
                self._fetch_media_insights_values(media_id) for media_id in todays_media_ids
            ]
            for metric_name in _ANALYZE_MEDIA_METRIC_NAMES:
                if metric_name == "reach":
                    continue  # reach is an account-level figure above, not summed per-post
                values_for_metric = [v[metric_name] for v in per_media_values if metric_name in v]
                if values_for_metric:
                    metrics[metric_name] = sum(values_for_metric)

        return metrics

    def _do_record_daily_snapshot(self) -> ExternalActionResult:
        """Read today's real metrics from the Instagram API and persist
        them to jarvis.integrations.instagram_history (a local JSON file
        - see that module's docstring), keyed by today's UTC calendar
        date. Makes no change to the Instagram account itself - the only
        write is to JARVIS's own local file, exactly like
        jarvis.session.store persisting conversation history. Safe to run
        more than once per day: re-running overwrites today's entry with
        the latest real numbers rather than duplicating it."""
        today_str = datetime.now(timezone.utc).date().isoformat()
        metrics = self._fetch_todays_metrics()

        if not metrics:
            return ExternalActionResult(
                ok=True,
                output=(
                    f"Nėra jokių realių Instagram Insights duomenų šiandienai "
                    f"({today_str}) - nieko neįrašyta į istoriją. Jokie reikšmės "
                    "nebuvo sugalvotos ar apytiksliai apskaičiuotos."
                ),
                service=self.service_name,
                action="record_daily_snapshot",
            )

        instagram_history.upsert_snapshot(today_str, metrics)

        recorded_lines = [f"{name}: {value:g}" for name, value in sorted(metrics.items())]
        return ExternalActionResult(
            ok=True,
            output=(
                f"Išsaugota {today_str} suvestinė ({len(metrics)} rodiklis(-iai)):\n"
                + "\n".join(recorded_lines)
            ),
            service=self.service_name,
            action="record_daily_snapshot",
        )

    def _do_compare_history(self) -> ExternalActionResult:
        """Compare previously RECORDED snapshots only (via
        jarvis.integrations.instagram_history.load_history()) - never a
        live API call. Three fixed comparisons: today vs yesterday, today
        vs 7 days ago, and the last 7 recorded days (summed) vs the 7
        recorded days before that. Any date or metric missing from the
        stored history is reported plainly as unavailable - never
        interpolated or estimated. Depends entirely on record_daily_snapshot
        having been run on the relevant days; a fresh or sparse history
        will report most or all comparisons as unavailable, which is
        correct, not a bug."""
        history = instagram_history.load_history().entries
        today = datetime.now(timezone.utc).date()
        sections: list[str] = []

        def _fmt_date(d) -> str:
            return d.isoformat()

        def _compare_two_dates(label: str, date_a, date_b) -> list[str]:
            lines = [f"=== {label} ==="]
            snapshot_a = history.get(_fmt_date(date_a))
            snapshot_b = history.get(_fmt_date(date_b))
            if snapshot_a is None or snapshot_b is None:
                missing = _fmt_date(date_a) if snapshot_a is None else _fmt_date(date_b)
                lines.append(
                    f"Nėra išsaugotų duomenų datai {missing} - palyginimas "
                    "negalimas. Naudok record_daily_snapshot kiekvieną dieną, "
                    "kad ateityje šis palyginimas būtų prieinamas."
                )
                return lines
            metric_names = sorted(set(snapshot_a) | set(snapshot_b))
            if not metric_names:
                lines.append("Nėra jokių rodiklių, kuriuos būtų galima palyginti.")
                return lines
            for metric_name in metric_names:
                if metric_name not in snapshot_a or metric_name not in snapshot_b:
                    lines.append(f"{metric_name}: duomenų nėra vienai iš dienų - praleista")
                    continue
                lines.append(
                    f"{metric_name}: {self._percent_change(float(snapshot_a[metric_name]), float(snapshot_b[metric_name]))}"
                )
            return lines

        # --- šiandien vs vakar -------------------------------------------
        yesterday = today - _ONE_DAY
        sections.extend(_compare_two_dates("Šiandien vs vakar", yesterday, today))
        sections.append("")

        # --- šiandien vs prieš 7 dienas -----------------------------------
        seven_days_ago = today - (_ONE_DAY * 7)
        sections.extend(_compare_two_dates("Šiandien vs prieš 7 dienas", seven_days_ago, today))
        sections.append("")

        # --- paskutinės 7 dienos vs ankstesnės 7 dienos --------------------
        sections.append("=== Paskutinės 7 dienos vs ankstesnės 7 dienos ===")
        last_7_dates = [today - (_ONE_DAY * i) for i in range(0, 7)]
        prior_7_dates = [today - (_ONE_DAY * i) for i in range(7, 14)]
        last_7_snapshots = [history[_fmt_date(d)] for d in last_7_dates if _fmt_date(d) in history]
        prior_7_snapshots = [history[_fmt_date(d)] for d in prior_7_dates if _fmt_date(d) in history]
        if not last_7_snapshots or not prior_7_snapshots:
            sections.append(
                f"Nepakanka išsaugotų duomenų (rasta {len(last_7_snapshots)} iš "
                f"paskutinių 7 dienų, {len(prior_7_snapshots)} iš ankstesnių 7 "
                "dienų) - reikia bent po vieną išsaugotą dieną iš kiekvieno "
                "7 dienų laikotarpio."
            )
        else:
            metric_names = sorted(
                {name for snapshot in last_7_snapshots + prior_7_snapshots for name in snapshot}
            )
            for metric_name in metric_names:
                last_values = [s[metric_name] for s in last_7_snapshots if metric_name in s]
                prior_values = [s[metric_name] for s in prior_7_snapshots if metric_name in s]
                if not last_values or not prior_values:
                    sections.append(f"{metric_name}: duomenų nėra viename iš laikotarpių - praleista")
                    continue
                last_sum = sum(last_values)
                prior_sum = sum(prior_values)
                sections.append(
                    f"{metric_name} (suma, {len(prior_values)} d. -> {len(last_values)} d.): "
                    f"{self._percent_change(prior_sum, last_sum)}"
                )

        return ExternalActionResult(
            ok=True, output="\n".join(sections), service=self.service_name, action="compare_history"
        )

    def _do_list_comments(self, media_id: str, limit: int = _DEFAULT_COMMENT_LIMIT) -> ExternalActionResult:
        if not media_id or not media_id.strip():
            return ExternalActionResult(
                ok=False, output="A media_id is required.", service=self.service_name,
                action="list_comments",
            )
        limit = max(1, min(limit, _MAX_COMMENT_LIMIT))
        data = self._get(
            f"/{media_id.strip()}/comments",
            params={"fields": "id,username,text,timestamp", "limit": limit},
        )

        items = data.get("data", [])
        if not items:
            return ExternalActionResult(
                ok=True, output="No comments found.", service=self.service_name,
                action="list_comments",
            )

        lines = []
        for item in items:
            comment_id = item.get("id", "?")
            username = item.get("username", "?")
            text = item.get("text", "")
            timestamp = item.get("timestamp", "?")
            lines.append(f"id={comment_id} | from={username} | timestamp={timestamp} | text={text}")

        return ExternalActionResult(
            ok=True, output="\n".join(lines), service=self.service_name, action="list_comments"
        )

    def _do_list_recent_messages(self, limit: int = _DEFAULT_CONVERSATION_LIMIT) -> ExternalActionResult:
        limit = max(1, min(limit, _MAX_CONVERSATION_LIMIT))
        account_id = self._account_id()
        data = self._get(
            f"/{account_id}/conversations",
            params={
                "platform": "instagram",
                "fields": "participants,messages.limit(1){message,created_time}",
                "limit": limit,
            },
        )

        items = data.get("data", [])
        if not items:
            return ExternalActionResult(
                ok=True, output="No conversations found.", service=self.service_name,
                action="list_recent_messages",
            )

        lines = []
        for item in items:
            conversation_id = item.get("id", "?")
            participants = item.get("participants", {}).get("data", [])
            participant_names = ", ".join(p.get("username", p.get("id", "?")) for p in participants)
            messages = item.get("messages", {}).get("data", [])
            latest = messages[0] if messages else {}
            snippet = (latest.get("message") or "")[:80]
            created_time = latest.get("created_time", "?")
            lines.append(
                f"id={conversation_id} | with={participant_names} | "
                f"latest={snippet} | timestamp={created_time}"
            )

        return ExternalActionResult(
            ok=True, output="\n".join(lines), service=self.service_name, action="list_recent_messages"
        )

    def disconnect(self) -> bool:
        """Clear locally-stored OAuth tokens. Makes no network call - this
        does not revoke the token with Meta, only removes what JARVIS
        holds locally. Returns whatever TokenStore.clear() returns."""
        return self._token_store.clear()
