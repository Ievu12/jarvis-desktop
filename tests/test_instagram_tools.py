"""Tests for jarvis.tools.instagram_tools: the eleven agent-facing Tool
wrappers around InstagramConnector. Confirms each tool delegates
correctly, none requires approval (all READ_ONLY), and all degrade
gracefully with a clear message when Instagram isn't configured - never
attempting a network call or crashing. InstagramConnector itself is
mocked; no real HTTP access, no real OS keychain access."""

from __future__ import annotations

from unittest.mock import patch

from jarvis.integrations.base import CredentialError, ExternalActionResult
from jarvis.tools.instagram_tools import (
    AnalyzeInstagramInsightsTool,
    CompareInstagramHistoryTool,
    GetInstagramAccountInsightsTool,
    GetInstagramDailyReportTool,
    GetInstagramMediaDetailsTool,
    GetInstagramMediaInsightsTool,
    GetInstagramProfileTool,
    ListInstagramCommentsTool,
    ListRecentInstagramMediaTool,
    ListRecentInstagramMessagesTool,
    RecordInstagramDailySnapshotTool,
)


# --- not configured: clear message, no network attempt -----------------------------


def test_get_profile_not_configured_returns_clear_message():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = GetInstagramProfileTool().run()
    assert result.ok is False
    assert "not configured" in result.output.lower()
    mock_connector.execute.assert_not_called()


def test_list_recent_media_not_configured():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = ListRecentInstagramMediaTool().run()
    assert result.ok is False
    assert "not configured" in result.output.lower()


def test_get_media_details_not_configured():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = GetInstagramMediaDetailsTool().run(media_id="m1")
    assert result.ok is False
    assert "not configured" in result.output.lower()


def test_get_media_insights_not_configured():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = GetInstagramMediaInsightsTool().run(media_id="m1")
    assert result.ok is False
    assert "not configured" in result.output.lower()


def test_get_account_insights_not_configured():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = GetInstagramAccountInsightsTool().run()
    assert result.ok is False
    assert "not configured" in result.output.lower()
    mock_connector.execute.assert_not_called()


def test_analyze_insights_not_configured():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = AnalyzeInstagramInsightsTool().run()
    assert result.ok is False
    assert "not configured" in result.output.lower()
    mock_connector.execute.assert_not_called()


def test_daily_report_not_configured():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = GetInstagramDailyReportTool().run()
    assert result.ok is False
    assert "not configured" in result.output.lower()
    mock_connector.execute.assert_not_called()


def test_record_daily_snapshot_not_configured():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = RecordInstagramDailySnapshotTool().run()
    assert result.ok is False
    assert "not configured" in result.output.lower()
    mock_connector.execute.assert_not_called()


def test_compare_history_not_configured():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = CompareInstagramHistoryTool().run()
    assert result.ok is False
    assert "not configured" in result.output.lower()
    mock_connector.execute.assert_not_called()


def test_list_comments_not_configured():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = ListInstagramCommentsTool().run(media_id="m1")
    assert result.ok is False
    assert "not configured" in result.output.lower()


def test_list_recent_messages_not_configured():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = ListRecentInstagramMessagesTool().run()
    assert result.ok is False
    assert "not configured" in result.output.lower()


def test_not_configured_message_explains_oauth_is_the_only_path_not_a_password():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = False
        result = GetInstagramProfileTool().run()
    lowered = result.output.lower()
    assert "oauth" in lowered
    assert "set your password" not in lowered
    assert "enter your password" not in lowered


# --- successful delegation to the connector -----------------------------------------


def test_get_profile_delegates_to_connector():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="username: x", service="instagram", action="get_profile"
        )
        result = GetInstagramProfileTool().run()
    assert result.ok is True
    assert result.output == "username: x"
    mock_connector.execute.assert_called_once_with("get_profile")


def test_list_recent_media_delegates_with_limit():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="list", service="instagram", action="list_recent_media"
        )
        ListRecentInstagramMediaTool().run(limit=5)
    mock_connector.execute.assert_called_once_with("list_recent_media", limit=5)


def test_list_recent_media_defaults_to_ten():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="list", service="instagram", action="list_recent_media"
        )
        ListRecentInstagramMediaTool().run()
    mock_connector.execute.assert_called_once_with("list_recent_media", limit=10)


def test_get_media_details_delegates_with_media_id():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="id: m1", service="instagram", action="get_media_details"
        )
        result = GetInstagramMediaDetailsTool().run(media_id="m1")
    assert result.ok is True
    mock_connector.execute.assert_called_once_with("get_media_details", media_id="m1")


def test_get_media_insights_delegates_with_media_id():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="likes: 100", service="instagram", action="get_media_insights"
        )
        GetInstagramMediaInsightsTool().run(media_id="m1")
    mock_connector.execute.assert_called_once_with("get_media_insights", media_id="m1")


def test_get_account_insights_delegates_with_period():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="reach: 100", service="instagram", action="get_account_insights"
        )
        result = GetInstagramAccountInsightsTool().run(period="week")
    assert result.ok is True
    mock_connector.execute.assert_called_once_with("get_account_insights", period="week")


def test_get_account_insights_defaults_to_day():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="reach: 100", service="instagram", action="get_account_insights"
        )
        GetInstagramAccountInsightsTool().run()
    mock_connector.execute.assert_called_once_with("get_account_insights", period="day")


def test_analyze_insights_delegates_with_compare_media_count():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="reach: ...", service="instagram", action="analyze_insights"
        )
        result = AnalyzeInstagramInsightsTool().run(compare_media_count=3)
    assert result.ok is True
    mock_connector.execute.assert_called_once_with("analyze_insights", compare_media_count=3)


def test_analyze_insights_defaults_to_five():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="reach: ...", service="instagram", action="analyze_insights"
        )
        AnalyzeInstagramInsightsTool().run()
    mock_connector.execute.assert_called_once_with("analyze_insights", compare_media_count=5)


def test_daily_report_delegates_to_connector():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="reach: ...", service="instagram", action="daily_report"
        )
        result = GetInstagramDailyReportTool().run()
    assert result.ok is True
    mock_connector.execute.assert_called_once_with("daily_report")


def test_record_daily_snapshot_delegates_to_connector():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="saved", service="instagram", action="record_daily_snapshot"
        )
        result = RecordInstagramDailySnapshotTool().run()
    assert result.ok is True
    mock_connector.execute.assert_called_once_with("record_daily_snapshot")


def test_compare_history_delegates_to_connector():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="compared", service="instagram", action="compare_history"
        )
        result = CompareInstagramHistoryTool().run()
    assert result.ok is True
    mock_connector.execute.assert_called_once_with("compare_history")


def test_list_comments_delegates_with_media_id_and_limit():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="list", service="instagram", action="list_comments"
        )
        ListInstagramCommentsTool().run(media_id="m1", limit=7)
    mock_connector.execute.assert_called_once_with("list_comments", media_id="m1", limit=7)


def test_list_recent_messages_delegates_with_limit():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="list", service="instagram", action="list_recent_messages"
        )
        ListRecentInstagramMessagesTool().run(limit=3)
    mock_connector.execute.assert_called_once_with("list_recent_messages", limit=3)


# --- CredentialError raised by execute() is caught, not propagated -----------------


def test_credential_error_from_execute_is_caught_gracefully():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.side_effect = CredentialError("tokens expired")
        result = GetInstagramProfileTool().run()
    assert result.ok is False
    assert "tokens expired" in result.output


# --- no approval required for any of the eleven (all READ_ONLY) -----------------------


def test_none_of_the_eleven_tools_calls_confirm_side_effect():
    with patch("jarvis.tools.instagram_tools._connector") as mock_connector:
        mock_connector.is_configured.return_value = True
        mock_connector.execute.return_value = ExternalActionResult(
            ok=True, output="ok", service="instagram", action="x"
        )
        with patch("builtins.input") as mock_input:
            GetInstagramProfileTool().run()
            ListRecentInstagramMediaTool().run()
            GetInstagramMediaDetailsTool().run(media_id="1")
            GetInstagramMediaInsightsTool().run(media_id="1")
            GetInstagramAccountInsightsTool().run()
            AnalyzeInstagramInsightsTool().run()
            GetInstagramDailyReportTool().run()
            RecordInstagramDailySnapshotTool().run()
            CompareInstagramHistoryTool().run()
            ListInstagramCommentsTool().run(media_id="1")
            ListRecentInstagramMessagesTool().run()
    mock_input.assert_not_called()


# --- tool metadata -----------------------------------------------------------------------


def test_all_eleven_tools_have_distinct_names():
    names = {
        GetInstagramProfileTool().name,
        ListRecentInstagramMediaTool().name,
        GetInstagramMediaDetailsTool().name,
        GetInstagramMediaInsightsTool().name,
        GetInstagramAccountInsightsTool().name,
        AnalyzeInstagramInsightsTool().name,
        GetInstagramDailyReportTool().name,
        RecordInstagramDailySnapshotTool().name,
        CompareInstagramHistoryTool().name,
        ListInstagramCommentsTool().name,
        ListRecentInstagramMessagesTool().name,
    }
    assert names == {
        "get_instagram_profile", "list_recent_instagram_media",
        "get_instagram_media_details", "get_instagram_media_insights",
        "get_instagram_account_insights", "analyze_instagram_insights",
        "get_instagram_daily_report",
        "record_instagram_daily_snapshot", "compare_instagram_history",
        "list_instagram_comments", "list_recent_instagram_messages",
    }


def test_get_media_details_requires_media_id_in_schema():
    schema = GetInstagramMediaDetailsTool().input_schema
    assert schema["required"] == ["media_id"]


def test_get_media_insights_requires_media_id_in_schema():
    schema = GetInstagramMediaInsightsTool().input_schema
    assert schema["required"] == ["media_id"]


def test_list_comments_requires_media_id_in_schema():
    schema = ListInstagramCommentsTool().input_schema
    assert schema["required"] == ["media_id"]


def test_other_tools_have_no_required_fields():
    for tool in (
        GetInstagramProfileTool(), ListRecentInstagramMediaTool(),
        ListRecentInstagramMessagesTool(), GetInstagramAccountInsightsTool(),
        AnalyzeInstagramInsightsTool(), GetInstagramDailyReportTool(),
        RecordInstagramDailySnapshotTool(), CompareInstagramHistoryTool(),
    ):
        assert "required" not in tool.input_schema or tool.input_schema.get("required") == []


def test_no_tool_named_after_a_write_action():
    names = {
        GetInstagramProfileTool().name,
        ListRecentInstagramMediaTool().name,
        GetInstagramMediaDetailsTool().name,
        GetInstagramMediaInsightsTool().name,
        GetInstagramAccountInsightsTool().name,
        AnalyzeInstagramInsightsTool().name,
        GetInstagramDailyReportTool().name,
        RecordInstagramDailySnapshotTool().name,
        CompareInstagramHistoryTool().name,
        ListInstagramCommentsTool().name,
        ListRecentInstagramMessagesTool().name,
    }
    for forbidden in (
        "publish_instagram_media", "reply_to_instagram_comment", "delete_instagram_comment",
        "send_instagram_message", "create_instagram_story", "create_instagram_reel",
    ):
        assert forbidden not in names


def test_analyze_insights_description_tells_agent_to_write_the_analysis_itself():
    description = AnalyzeInstagramInsightsTool().description.lower()
    assert "does not" in description or "not write" in description


def test_daily_report_description_tells_agent_to_write_recommendations_itself():
    description = GetInstagramDailyReportTool().description.lower()
    assert "does not" in description or "not write" in description
    assert "3" in description


def test_record_daily_snapshot_description_clarifies_only_local_file_changes():
    description = RecordInstagramDailySnapshotTool().description.lower()
    assert "local" in description
    assert "no change to the instagram account" in description or "makes no change" in description or "no " in description


def test_compare_history_description_says_never_guesses_missing_data():
    description = CompareInstagramHistoryTool().description.lower()
    assert "never" in description
    assert "interpolated" in description or "guessed" in description


def test_get_instagram_profile_never_claims_a_following_count_field():
    # "following"/follows_count is confirmed unavailable from Instagram's
    # API for this connector - the tool's own description must never
    # claim it is provided, so the model never expects a field that will
    # never be returned.
    description = GetInstagramProfileTool().description.lower()
    assert "following" not in description
    assert "follows_count" not in description


def test_instagram_tools_use_a_separate_connector_instance_from_gmail_and_calendar():
    from jarvis.integrations.connectors.instagram import InstagramConnector
    from jarvis.tools.instagram_tools import _connector as instagram_connector

    assert isinstance(instagram_connector, InstagramConnector)
    assert instagram_connector.service_name == "instagram"
