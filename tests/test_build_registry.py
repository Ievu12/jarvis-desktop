"""Tests for jarvis.cli.main.build_registry(): confirms every tool the
agent should have access to is actually registered, including
get_workflow_state (bridging the agent to the deterministic
scan/plan/work/review/precommit workflow), the four read-only email
tools (bridging the agent to jarvis.integrations.connectors.email
.EmailConnector), the four read-only Stripe tools (bridging the agent to
jarvis.integrations.connectors.stripe.StripeConnector), the five
read-only Gmail tools (bridging the agent to jarvis.integrations
.connectors.gmail.GmailConnector), the three read-only Google Calendar
tools (bridging the agent to jarvis.integrations.connectors
.google_calendar.GoogleCalendarConnector), and the eleven read-only
Instagram tools (bridging the agent to jarvis.integrations.connectors
.instagram.InstagramConnector). A tool missing from this registry would
be silently unavailable to the model even if fully implemented and
tested elsewhere."""

from __future__ import annotations

from jarvis.cli.main import build_registry


def test_registry_includes_workflow_state_tool():
    registry = build_registry()
    assert registry.get("get_workflow_state") is not None


def test_registry_includes_all_expected_tool_names():
    registry = build_registry()
    names = {t.name for t in registry.all()}
    assert names == {
        "read_file",
        "write_file",
        "append_to_file",
        "list_directory",
        "delete_file",
        "create_directory",
        "move_file",
        "search_files",
        "replace_in_file",
        "edit_file_lines",
        "create_plan",
        "manage_tasks",
        "run_command",
        "get_workflow_state",
        "check_email_connection",
        "get_email_account_info",
        "list_recent_emails",
        "read_email",
        "get_stripe_balance",
        "list_recent_charges",
        "get_charge_status",
        "list_recent_payment_intents",
        "list_recent_gmail_messages",
        "list_unread_gmail_messages",
        "search_gmail_messages",
        "get_gmail_message",
        "get_gmail_message_content",
        "list_todays_calendar_events",
        "list_upcoming_calendar_events",
        "get_calendar_event",
        "get_instagram_profile",
        "list_recent_instagram_media",
        "get_instagram_media_details",
        "get_instagram_media_insights",
        "get_instagram_account_insights",
        "analyze_instagram_insights",
        "get_instagram_daily_report",
        "record_instagram_daily_snapshot",
        "compare_instagram_history",
        "list_instagram_comments",
        "list_recent_instagram_messages",
    }


def test_workflow_state_tool_schema_is_present_in_agent_schemas():
    registry = build_registry()
    schemas = registry.schemas()
    workflow_schemas = [s for s in schemas if s["name"] == "get_workflow_state"]
    assert len(workflow_schemas) == 1
    assert "stage" in workflow_schemas[0]["input_schema"]["properties"]


def test_registry_includes_all_four_email_tools():
    registry = build_registry()
    for name in (
        "check_email_connection", "get_email_account_info",
        "list_recent_emails", "read_email",
    ):
        assert registry.get(name) is not None


def test_registry_includes_all_four_stripe_tools():
    registry = build_registry()
    for name in (
        "get_stripe_balance", "list_recent_charges",
        "get_charge_status", "list_recent_payment_intents",
    ):
        assert registry.get(name) is not None


def test_registry_includes_all_five_gmail_tools():
    registry = build_registry()
    for name in (
        "list_recent_gmail_messages", "list_unread_gmail_messages",
        "search_gmail_messages", "get_gmail_message", "get_gmail_message_content",
    ):
        assert registry.get(name) is not None


def test_registry_includes_all_three_calendar_tools():
    registry = build_registry()
    for name in (
        "list_todays_calendar_events", "list_upcoming_calendar_events", "get_calendar_event",
    ):
        assert registry.get(name) is not None


def test_registry_includes_all_eleven_instagram_tools():
    registry = build_registry()
    for name in (
        "get_instagram_profile", "list_recent_instagram_media",
        "get_instagram_media_details", "get_instagram_media_insights",
        "get_instagram_account_insights", "analyze_instagram_insights",
        "get_instagram_daily_report",
        "record_instagram_daily_snapshot", "compare_instagram_history",
        "list_instagram_comments", "list_recent_instagram_messages",
    ):
        assert registry.get(name) is not None
