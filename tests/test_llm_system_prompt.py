"""Tests for jarvis.core.llm.BASE_SYSTEM_PROMPT's Stripe and Gmail
guidance: the system prompt is the only thing that changed (beyond the
new connector/tools modules themselves) to teach the LLM-driven agent how
to answer natural-language (including Lithuanian) questions about Stripe
payments/orders and Gmail messages using the already-existing, read-only
Stripe/Gmail tools (jarvis.tools.stripe_tools, jarvis.tools.gmail_tools) -
no change to approval/TaskRunner/DryRunExecutor. These tests verify the
prompt text itself (pure string content, no API calls) rather than actual
model behavior, which would require a real Claude API call."""

from __future__ import annotations

from jarvis.core.llm import BASE_SYSTEM_PROMPT, SYSTEM_PROMPT


# --- all four existing Stripe tools are named -----------------------------------------------------


def test_prompt_mentions_get_stripe_balance():
    assert "get_stripe_balance" in BASE_SYSTEM_PROMPT


def test_prompt_mentions_list_recent_charges():
    assert "list_recent_charges" in BASE_SYSTEM_PROMPT


def test_prompt_mentions_get_charge_status():
    assert "get_charge_status" in BASE_SYSTEM_PROMPT


def test_prompt_mentions_list_recent_payment_intents():
    assert "list_recent_payment_intents" in BASE_SYSTEM_PROMPT


# --- explicit read-only / no-write guidance -----------------------------------------------------


def test_prompt_states_no_charge_creation():
    assert "create a charge" in BASE_SYSTEM_PROMPT.lower()


def test_prompt_states_no_refund():
    assert "refund" in BASE_SYSTEM_PROMPT.lower()


def test_prompt_states_no_payout():
    assert "payout" in BASE_SYSTEM_PROMPT.lower()


def test_prompt_instructs_against_workarounds_for_writes():
    lowered = BASE_SYSTEM_PROMPT.lower()
    stripe_section_start = lowered.index("stripe")
    stripe_section = lowered[stripe_section_start:stripe_section_start + 2000]
    assert "workaround" in stripe_section


# --- guidance for date/aggregation reasoning (e.g. "today", counts, sums) --------------------


def test_prompt_explains_created_field_is_unix_timestamp():
    assert "created" in BASE_SYSTEM_PROMPT
    assert "unix timestamp" in BASE_SYSTEM_PROMPT.lower()


def test_prompt_tells_model_tool_does_not_filter_by_date():
    assert "unfiltered by date" in BASE_SYSTEM_PROMPT.lower()


def test_prompt_warns_about_incomplete_results_under_default_limit():
    assert "partial" in BASE_SYSTEM_PROMPT.lower() or "incomplete" in BASE_SYSTEM_PROMPT.lower()


# --- credential safety guidance -----------------------------------------------------


def test_prompt_instructs_never_to_print_the_stripe_key():
    lowered = BASE_SYSTEM_PROMPT.lower()
    assert "never print" in lowered or "never" in lowered and "api key" in lowered


def test_prompt_mentions_stripe_api_key_env_var():
    assert "STRIPE_API_KEY" in BASE_SYSTEM_PROMPT


# --- not-configured handling mirrors the email section's pattern --------------------


def test_prompt_says_not_configured_should_be_told_plainly():
    lowered = BASE_SYSTEM_PROMPT.lower()
    stripe_section_start = lowered.index("stripe")
    stripe_section = lowered[stripe_section_start:stripe_section_start + 2000]
    assert "not configured" in stripe_section or "isn't configured" in stripe_section


# --- Lithuanian example phrasings present, for discoverability -----------------------------------


def test_prompt_includes_lithuanian_example_phrasings():
    assert "šiandien" in BASE_SYSTEM_PROMPT
    assert "mokėjim" in BASE_SYSTEM_PROMPT  # covers mokėjimų/mokėjimas/mokėjimo


# --- backward-compatible alias unaffected -----------------------------------------------------


def test_system_prompt_alias_still_matches_base_prompt():
    assert SYSTEM_PROMPT == BASE_SYSTEM_PROMPT


# --- nothing here mentions a write action as available --------------------------------------------


def test_prompt_never_lists_a_stripe_write_tool_name():
    forbidden_tool_names = (
        "create_charge", "refund_charge", "create_refund",
        "create_payout", "capture_charge", "update_customer",
    )
    for name in forbidden_tool_names:
        assert name not in BASE_SYSTEM_PROMPT


# ==================================================================================
# Gmail guidance
# ==================================================================================


# --- all four existing Gmail tools are named -----------------------------------------------------


def test_prompt_mentions_list_recent_gmail_messages():
    assert "list_recent_gmail_messages" in BASE_SYSTEM_PROMPT


def test_prompt_mentions_list_unread_gmail_messages():
    assert "list_unread_gmail_messages" in BASE_SYSTEM_PROMPT


def test_prompt_mentions_search_gmail_messages():
    assert "search_gmail_messages" in BASE_SYSTEM_PROMPT


def test_prompt_mentions_get_gmail_message():
    assert "get_gmail_message" in BASE_SYSTEM_PROMPT


# --- explicit read-only / no-write guidance -----------------------------------------------------


def test_prompt_states_no_send():
    lowered = BASE_SYSTEM_PROMPT.lower()
    gmail_section_start = lowered.index("gmail")
    gmail_section = lowered[gmail_section_start:gmail_section_start + 4000]
    assert "send" in gmail_section


def test_prompt_states_no_delete_or_trash():
    lowered = BASE_SYSTEM_PROMPT.lower()
    gmail_section_start = lowered.index("gmail")
    gmail_section = lowered[gmail_section_start:gmail_section_start + 4000]
    assert "delete" in gmail_section or "trash" in gmail_section


def test_prompt_states_no_archive():
    lowered = BASE_SYSTEM_PROMPT.lower()
    gmail_section_start = lowered.index("gmail")
    gmail_section = lowered[gmail_section_start:gmail_section_start + 4000]
    assert "archive" in gmail_section


def test_prompt_states_no_label_modification_including_mark_as_read():
    lowered = BASE_SYSTEM_PROMPT.lower()
    gmail_section_start = lowered.index("gmail")
    gmail_section = lowered[gmail_section_start:gmail_section_start + 4000]
    assert "label" in gmail_section
    assert "marking" in gmail_section or "mark" in gmail_section


def test_prompt_instructs_against_workarounds_for_gmail_writes():
    lowered = BASE_SYSTEM_PROMPT.lower()
    gmail_section_start = lowered.index("gmail")
    gmail_section = lowered[gmail_section_start:gmail_section_start + 4000]
    assert "workaround" in gmail_section


# --- credential safety guidance: OAuth, never a password or pasted token -----------------------


def test_prompt_instructs_never_to_ask_for_gmail_password():
    lowered = BASE_SYSTEM_PROMPT.lower()
    gmail_section_start = lowered.index("gmail")
    gmail_section = lowered[gmail_section_start:gmail_section_start + 4000]
    assert "password" in gmail_section
    assert "never" in gmail_section


def test_prompt_instructs_never_to_accept_a_pasted_oauth_token():
    lowered = BASE_SYSTEM_PROMPT.lower()
    gmail_section_start = lowered.index("gmail")
    gmail_section = lowered[gmail_section_start:gmail_section_start + 4000]
    assert "token" in gmail_section
    assert "never" in gmail_section


def test_prompt_mentions_oauth_for_gmail():
    lowered = BASE_SYSTEM_PROMPT.lower()
    gmail_section_start = lowered.index("gmail")
    gmail_section = lowered[gmail_section_start:gmail_section_start + 4000]
    assert "oauth" in gmail_section


# --- not-configured handling mirrors the Stripe/email section's pattern --------------------


def test_prompt_says_gmail_not_configured_should_be_told_plainly():
    lowered = BASE_SYSTEM_PROMPT.lower()
    gmail_section_start = lowered.index("gmail")
    gmail_section = lowered[gmail_section_start:gmail_section_start + 4000]
    assert "not configured" in gmail_section or "isn't configured" in gmail_section


# --- Lithuanian example phrasings from the six target commands present -----------------------------------


def test_prompt_includes_lithuanian_gmail_example_phrasings():
    assert "naujausius laiškus" in BASE_SYSTEM_PROMPT
    assert "neperskaitytus laiškus" in BASE_SYSTEM_PROMPT
    assert "apibendrink" in BASE_SYSTEM_PROMPT.lower()


# --- nothing here mentions a Gmail write action as available --------------------------------------------


def test_prompt_never_lists_a_gmail_write_tool_name_as_something_to_call():
    # "send_message"/"create_draft" now legitimately appear in the prompt's
    # explicit "there is no such tool" disclaimer (e.g. "There is no
    # create_draft or send_message tool, and none should ever be
    # invented...") - that is the correct, safe usage. What must never
    # happen is one of these names appearing as an instruction to actually
    # call it (e.g. "call send_message" or "use create_draft to ...").
    forbidden_call_phrasings = (
        "call send_message", "call create_draft", "call delete_message",
        "call archive_message", "call modify_labels", "call trash_message",
        "use send_message", "use create_draft",
    )
    lowered = BASE_SYSTEM_PROMPT.lower()
    for phrase in forbidden_call_phrasings:
        assert phrase not in lowered


def test_prompt_explicitly_disclaims_the_nonexistent_draft_and_send_tools():
    lowered = BASE_SYSTEM_PROMPT.lower()
    assert "no create_draft or send_message tool" in lowered


# ==================================================================================
# Gmail: full-content reading, summarization, importance triage, reply drafting
# ==================================================================================


def test_prompt_mentions_get_gmail_message_content():
    assert "get_gmail_message_content" in BASE_SYSTEM_PROMPT


def test_prompt_distinguishes_snippet_tool_from_full_content_tool():
    lowered = BASE_SYSTEM_PROMPT.lower()
    gmail_section_start = lowered.index("gmail")
    gmail_section = lowered[gmail_section_start:gmail_section_start + 4000]
    assert "get_gmail_message_content" in gmail_section
    assert "snippet" in gmail_section


def test_prompt_explains_how_to_judge_importance_without_a_dedicated_tool():
    lowered = BASE_SYSTEM_PROMPT.lower()
    gmail_section_start = lowered.index("gmail")
    gmail_section = lowered[gmail_section_start:gmail_section_start + 4000]
    assert "svarbiausi" in gmail_section or "importance" in gmail_section


def test_prompt_includes_lithuanian_summarize_this_email_phrasing():
    assert "apibendrink šį laišką lietuviškai" in BASE_SYSTEM_PROMPT


def test_prompt_includes_lithuanian_important_new_emails_phrasing():
    assert "kurie nauji laiškai svarbiausi" in BASE_SYSTEM_PROMPT


def test_prompt_includes_lithuanian_draft_reply_phrasing():
    assert "paruošk atsakymą į šį laišką" in BASE_SYSTEM_PROMPT


# --- reply drafting: text-only, never a tool call, never claims sending/saving --------------


def test_prompt_instructs_reply_is_written_as_plain_text_not_a_tool_call():
    lowered = BASE_SYSTEM_PROMPT.lower()
    gmail_section_start = lowered.index("gmail")
    gmail_section = lowered[gmail_section_start:gmail_section_start + 4000]
    assert "never as a tool call" in gmail_section


def test_prompt_instructs_reply_must_state_it_is_a_draft_not_sent():
    lowered = BASE_SYSTEM_PROMPT.lower()
    gmail_section_start = lowered.index("gmail")
    gmail_section = lowered[gmail_section_start:gmail_section_start + 4000]
    assert "draft text only" in gmail_section
    assert "not been saved" in gmail_section or "not saved" in gmail_section


def test_prompt_forbids_claiming_a_reply_was_sent_or_saved():
    lowered = BASE_SYSTEM_PROMPT.lower()
    gmail_section_start = lowered.index("gmail")
    gmail_section = lowered[gmail_section_start:gmail_section_start + 4000]
    assert "'sent'" in gmail_section or '"sent"' in gmail_section
    assert "never say or imply" in gmail_section


def test_prompt_forbids_a_create_draft_tool_being_invented():
    lowered = BASE_SYSTEM_PROMPT.lower()
    gmail_section_start = lowered.index("gmail")
    gmail_section = lowered[gmail_section_start:gmail_section_start + 4000]
    assert "no create_draft or send_message tool" in gmail_section
    assert "never" in gmail_section and "invented" in gmail_section


def test_prompt_instructs_to_decline_if_user_insists_on_sending():
    lowered = BASE_SYSTEM_PROMPT.lower()
    gmail_section_start = lowered.index("gmail")
    gmail_section = lowered[gmail_section_start:gmail_section_start + 4000]
    assert "sending isn't supported" in gmail_section


def test_prompt_states_no_draft_saving_in_gmail_itself():
    lowered = BASE_SYSTEM_PROMPT.lower()
    gmail_section_start = lowered.index("gmail")
    gmail_section = lowered[gmail_section_start:gmail_section_start + 4000]
    assert "no way to save a draft in gmail itself" in gmail_section


def test_prompt_never_lists_get_message_full_content_as_a_write_tool_by_mistake():
    # Sanity check: the new read-only tool name itself must never appear
    # anywhere near forbidden write-tool phrasing in a way that could
    # confuse it for one - it is explicitly documented as read-only.
    idx = BASE_SYSTEM_PROMPT.index("get_gmail_message_content")
    nearby = BASE_SYSTEM_PROMPT[max(0, idx - 300):idx + 300].lower()
    assert "never sends, saves, or modifies" in nearby or "read-only" in nearby


# ==================================================================================
# Google Calendar guidance
# ==================================================================================


def _calendar_section() -> str:
    lowered = BASE_SYSTEM_PROMPT.lower()
    idx = lowered.index("google calendar")
    return lowered[idx:idx + 3000]


# --- all three existing Calendar tools are named -----------------------------------------------------


def test_prompt_mentions_list_todays_calendar_events():
    assert "list_todays_calendar_events" in BASE_SYSTEM_PROMPT


def test_prompt_mentions_list_upcoming_calendar_events():
    assert "list_upcoming_calendar_events" in BASE_SYSTEM_PROMPT


def test_prompt_mentions_get_calendar_event():
    assert "get_calendar_event" in BASE_SYSTEM_PROMPT


# --- explicit read-only / no-write guidance -----------------------------------------------------


def test_prompt_states_no_create_update_delete_event():
    section = _calendar_section()
    assert "create" in section
    assert "update" in section
    assert "delete" in section


def test_prompt_states_no_respond_to_invite():
    section = _calendar_section()
    assert "respond to an invite" in section


def test_prompt_instructs_against_workarounds_for_calendar_writes():
    section = _calendar_section()
    assert "workaround" in section


# --- credential safety guidance: OAuth, never a password or pasted token -----------------------


def test_prompt_instructs_never_to_ask_for_google_password():
    section = _calendar_section()
    assert "password" in section
    assert "never" in section


def test_prompt_instructs_never_to_accept_a_pasted_calendar_oauth_token():
    section = _calendar_section()
    assert "token" in section
    assert "never" in section


def test_prompt_mentions_oauth_for_calendar():
    section = _calendar_section()
    assert "oauth" in section


def test_prompt_says_calendar_not_configured_should_be_told_plainly():
    section = _calendar_section()
    assert "not configured" in section or "isn't configured" in section


# --- Calendar is configured separately from Gmail - the prompt must say so ------------------------


def test_prompt_states_calendar_and_gmail_are_configured_separately():
    section = _calendar_section()
    assert "separately from" in section or "separate" in section


def test_prompt_mentions_google_calendar_service_name_for_token_store():
    assert "google_calendar" in BASE_SYSTEM_PROMPT


# --- today's events tool is already date-bounded, unlike Stripe/Gmail listing tools --------------


def test_prompt_explains_todays_events_tool_is_already_date_bounded():
    section = _calendar_section()
    assert "already bounded to" in section or "bounded to the current local day" in section


# --- Lithuanian example phrasings present, for discoverability -----------------------------------


def test_prompt_includes_lithuanian_calendar_example_phrasings():
    assert "kas šiandien mano kalendoriuje" in BASE_SYSTEM_PROMPT
    assert "artimiausi įvykiai" in BASE_SYSTEM_PROMPT


# --- nothing here mentions a Calendar write action as available --------------------------------------------


def test_prompt_never_instructs_calling_a_calendar_write_tool():
    forbidden_call_phrasings = (
        "call create_event", "call update_event", "call delete_event",
        "use create_event", "use delete_event",
    )
    lowered = BASE_SYSTEM_PROMPT.lower()
    for phrase in forbidden_call_phrasings:
        assert phrase not in lowered


def test_prompt_never_lists_a_calendar_write_tool_name_as_a_real_tool():
    forbidden_tool_names = (
        "create_calendar_event", "delete_calendar_event", "update_calendar_event",
        "respond_to_calendar_invite",
    )
    for name in forbidden_tool_names:
        assert name not in BASE_SYSTEM_PROMPT


# ==================================================================================
# Instagram guidance
# ==================================================================================


def _instagram_section() -> str:
    lowered = BASE_SYSTEM_PROMPT.lower()
    idx = lowered.index("instagram")
    return lowered[idx:idx + 6500]


# --- all six existing Instagram tools are named -----------------------------------------------------


def test_prompt_mentions_get_instagram_profile():
    assert "get_instagram_profile" in BASE_SYSTEM_PROMPT


def test_prompt_mentions_list_recent_instagram_media():
    assert "list_recent_instagram_media" in BASE_SYSTEM_PROMPT


def test_prompt_mentions_get_instagram_media_details():
    assert "get_instagram_media_details" in BASE_SYSTEM_PROMPT


def test_prompt_mentions_get_instagram_media_insights():
    assert "get_instagram_media_insights" in BASE_SYSTEM_PROMPT


def test_prompt_mentions_get_instagram_account_insights():
    assert "get_instagram_account_insights" in BASE_SYSTEM_PROMPT


def test_prompt_mentions_analyze_instagram_insights():
    assert "analyze_instagram_insights" in BASE_SYSTEM_PROMPT


def test_prompt_mentions_get_instagram_daily_report():
    assert "get_instagram_daily_report" in BASE_SYSTEM_PROMPT


def test_prompt_mentions_record_instagram_daily_snapshot():
    assert "record_instagram_daily_snapshot" in BASE_SYSTEM_PROMPT


def test_prompt_mentions_compare_instagram_history():
    assert "compare_instagram_history" in BASE_SYSTEM_PROMPT


def test_prompt_mentions_list_instagram_comments():
    assert "list_instagram_comments" in BASE_SYSTEM_PROMPT


def test_prompt_mentions_list_recent_instagram_messages():
    assert "list_recent_instagram_messages" in BASE_SYSTEM_PROMPT


# --- explicit read-only / no-write guidance (Stage 1A) -----------------------------------------------


def test_prompt_states_no_publish():
    section = _instagram_section()
    assert "publish" in section


def test_prompt_states_no_reply_or_delete_comment():
    section = _instagram_section()
    assert "reply to" in section or "delete" in section


def test_prompt_never_claims_following_count_is_available():
    section = _instagram_section()
    assert "following" not in section or "not available" in section


def test_prompt_tells_agent_to_write_the_analysis_itself_not_the_tool():
    section = _instagram_section()
    assert "analyze_instagram_insights" in section
    assert "write the actual short" in section or "does not write the analysis" in section


def test_prompt_tells_agent_to_write_exactly_three_daily_recommendations():
    section = _instagram_section()
    assert "get_instagram_daily_report" in section
    assert "exactly 3" in section


def test_prompt_explains_snapshot_changes_only_a_local_file():
    section = _instagram_section()
    assert "record_instagram_daily_snapshot" in section
    assert "only a local file" in section


def test_prompt_says_compare_history_never_estimates_missing_days():
    section = _instagram_section()
    assert "compare_instagram_history" in section
    assert "never estimate or interpolate" in section


def test_prompt_states_no_send_message():
    section = _instagram_section()
    assert "send a message" in section


def test_prompt_instructs_against_workarounds_for_instagram_writes():
    section = _instagram_section()
    assert "workaround" in section


def test_prompt_does_not_imply_a_future_publish_stage_unprompted():
    section = _instagram_section()
    assert "do not imply" in section


# --- credential safety guidance: OAuth, never a password or pasted token -----------------------


def test_prompt_instructs_never_to_ask_for_meta_password():
    section = _instagram_section()
    assert "password" in section
    assert "never" in section


def test_prompt_instructs_never_to_accept_a_pasted_instagram_oauth_token():
    section = _instagram_section()
    assert "token" in section
    assert "never" in section


def test_prompt_mentions_oauth_for_instagram():
    section = _instagram_section()
    assert "oauth" in section


def test_prompt_says_instagram_not_configured_should_be_told_plainly():
    section = _instagram_section()
    assert "not configured" in section or "isn't configured" in section


# --- Instagram is configured separately from Gmail/Calendar - the prompt must say so ------------------------


def test_prompt_states_instagram_and_gmail_calendar_are_configured_separately():
    section = _instagram_section()
    assert "separately from" in section or "separate" in section


def test_prompt_mentions_instagram_service_name_for_token_store():
    assert "'instagram'" in BASE_SYSTEM_PROMPT


# --- Lithuanian example phrasings present, for discoverability -----------------------------------


def test_prompt_includes_lithuanian_instagram_example_phrasings():
    assert "kokia mano Instagram profilio informacija" in BASE_SYSTEM_PROMPT
    assert "parodyk paskutinius įrašus" in BASE_SYSTEM_PROMPT


# --- nothing here mentions an Instagram write action as available --------------------------------------------


def test_prompt_never_instructs_calling_an_instagram_write_tool():
    forbidden_call_phrasings = (
        "call publish_media", "call reply_to_comment", "call send_message",
        "use publish_media", "use send_message",
    )
    lowered = BASE_SYSTEM_PROMPT.lower()
    for phrase in forbidden_call_phrasings:
        assert phrase not in lowered


def test_prompt_never_lists_an_instagram_write_tool_name_as_a_real_tool():
    forbidden_tool_names = (
        "publish_instagram_media", "reply_to_instagram_comment", "delete_instagram_comment",
        "send_instagram_message", "create_instagram_story", "create_instagram_reel",
    )
    for name in forbidden_tool_names:
        assert name not in BASE_SYSTEM_PROMPT
