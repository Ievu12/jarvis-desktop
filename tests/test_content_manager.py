"""Tests for jarvis.content_manager: builds the text prompt sent through
Agent.step() for a content brief, tracks recently-suggested topics in a
local JSON file, and formats the morning greeting. Generates NO content
itself - confirmed by checking the prompt-building functions never call
an LLM or any Instagram connector. The topics history file is redirected
to a per-test tmp_path file - no test touches the real
.jarvis/content_topics_history.json."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from jarvis import content_manager


@pytest.fixture(autouse=True)
def _isolated_topics_history(tmp_path, monkeypatch):
    history_file = tmp_path / "content_topics_history.json"
    monkeypatch.setattr(content_manager, "CONTENT_TOPICS_HISTORY_FILE", history_file)
    return history_file


# --- load_topics_history(): missing/empty/corrupted file ----------------------------


def test_load_topics_history_missing_file_returns_empty_no_warning():
    result = content_manager.load_topics_history()
    assert result.entries == {}
    assert result.warning is None


def test_load_topics_history_corrupted_json_returns_empty_with_warning(_isolated_topics_history):
    _isolated_topics_history.parent.mkdir(parents=True, exist_ok=True)
    _isolated_topics_history.write_text("{not valid json", encoding="utf-8")
    result = content_manager.load_topics_history()
    assert result.entries == {}
    assert result.warning is not None
    assert "corrupted" in result.warning.lower()


def test_load_topics_history_wrong_shape_returns_empty_with_warning(_isolated_topics_history):
    _isolated_topics_history.parent.mkdir(parents=True, exist_ok=True)
    _isolated_topics_history.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    result = content_manager.load_topics_history()
    assert result.entries == {}
    assert result.warning is not None


def test_load_topics_history_skips_malformed_entries(_isolated_topics_history):
    _isolated_topics_history.parent.mkdir(parents=True, exist_ok=True)
    _isolated_topics_history.write_text(
        json.dumps({"2026-09-20": ["Reel apie jogą"], "2026-09-19": "not a list"}),
        encoding="utf-8",
    )
    result = content_manager.load_topics_history()
    assert result.entries == {"2026-09-20": ["Reel apie jogą"]}


# --- record_used_topics() / upsert semantics -----------------------------------------


def test_record_used_topics_round_trips():
    content_manager.record_used_topics(["Reel apie jogą", "Story apie kremą"], on_date="2026-09-20")
    result = content_manager.load_topics_history()
    assert result.entries == {"2026-09-20": ["Reel apie jogą", "Story apie kremą"]}


def test_record_used_topics_same_date_overwrites_not_appends():
    content_manager.record_used_topics(["A"], on_date="2026-09-20")
    content_manager.record_used_topics(["B", "C"], on_date="2026-09-20")
    result = content_manager.load_topics_history()
    assert result.entries == {"2026-09-20": ["B", "C"]}


def test_record_used_topics_empty_list_is_a_noop():
    content_manager.record_used_topics([], on_date="2026-09-20")
    result = content_manager.load_topics_history()
    assert result.entries == {}


def test_record_used_topics_preserves_other_dates():
    content_manager.record_used_topics(["A"], on_date="2026-09-19")
    content_manager.record_used_topics(["B"], on_date="2026-09-20")
    result = content_manager.load_topics_history()
    assert result.entries == {"2026-09-19": ["A"], "2026-09-20": ["B"]}


# --- recent_topics(): window filtering, de-duplication, ordering --------------------


def test_recent_topics_empty_history_returns_empty_list():
    assert content_manager.recent_topics() == []


def test_recent_topics_includes_topics_within_window():
    today = datetime.now(timezone.utc).date()
    yesterday = (today - timedelta(days=1)).isoformat()
    content_manager.record_used_topics(["Reel apie jogą"], on_date=yesterday)
    assert "Reel apie jogą" in content_manager.recent_topics()


def test_recent_topics_excludes_topics_outside_window():
    today = datetime.now(timezone.utc).date()
    long_ago = (today - timedelta(days=30)).isoformat()
    content_manager.record_used_topics(["Senas Reel"], on_date=long_ago)
    assert "Senas Reel" not in content_manager.recent_topics(window_days=14)


def test_recent_topics_deduplicates_across_dates():
    today = datetime.now(timezone.utc).date()
    content_manager.record_used_topics(["Reel apie jogą"], on_date=(today - timedelta(days=1)).isoformat())
    content_manager.record_used_topics(["Reel apie jogą"], on_date=today.isoformat())
    assert content_manager.recent_topics().count("Reel apie jogą") == 1


def test_recent_topics_ignores_malformed_date_keys(_isolated_topics_history):
    _isolated_topics_history.parent.mkdir(parents=True, exist_ok=True)
    _isolated_topics_history.write_text(
        json.dumps({"not-a-date": ["X"]}), encoding="utf-8"
    )
    assert content_manager.recent_topics() == []  # must not raise


# --- build_content_brief_prompt(): shapes the request, generates no content ---------


def test_prompt_includes_all_content_directions():
    prompt = content_manager.build_content_brief_prompt()
    for direction in content_manager.CONTENT_DIRECTIONS:
        assert direction in prompt


def test_prompt_requests_exact_output_structure():
    prompt = content_manager.build_content_brief_prompt()
    assert "1 Reel" in prompt
    assert "3-5 Story" in prompt or "3–5 Story" in prompt
    assert "1 Carousel" in prompt


def test_prompt_requests_hook_main_idea_text_and_cta():
    prompt = content_manager.build_content_brief_prompt()
    assert "Hook" in prompt
    assert "Pagrindinė mintis" in prompt
    assert "CTA" in prompt


def test_prompt_includes_todays_date_and_weekday():
    now = datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc)  # a Wednesday
    prompt = content_manager.build_content_brief_prompt(now=now)
    assert "2026-09-23" in prompt
    assert "trečiadienis" in prompt


def test_prompt_includes_instagram_context_when_given():
    prompt = content_manager.build_content_brief_prompt(instagram_context="reach: 150 (+20%)")
    assert "reach: 150 (+20%)" in prompt


def test_prompt_states_no_data_when_instagram_context_missing():
    prompt = content_manager.build_content_brief_prompt(instagram_context=None)
    assert "nesukonfigūruota" in prompt.lower() or "nėra prieinama" in prompt.lower()


def test_prompt_asks_to_avoid_recent_topics_when_history_exists():
    content_manager.record_used_topics(["Reel apie jogos pratimą"], on_date=datetime.now(timezone.utc).date().isoformat())
    prompt = content_manager.build_content_brief_prompt()
    assert "Reel apie jogos pratimą" in prompt
    assert "nekartok" in prompt.lower()


def test_prompt_states_no_history_when_none_recorded():
    prompt = content_manager.build_content_brief_prompt()
    assert "istorijos dar nėra" in prompt.lower()


def test_prompt_forbids_publishing_or_commenting():
    prompt = content_manager.build_content_brief_prompt()
    lowered = prompt.lower()
    assert "nepublikuok" in lowered
    assert "nekomentuok" in lowered


def test_build_content_brief_prompt_never_calls_any_llm_or_connector():
    # A pure string-builder - confirmed by checking the module never
    # imports anything capable of network I/O or talking to a
    # connector (docstring prose mentioning these names by way of
    # explanation is fine; an actual import is not).
    module_globals = vars(content_manager)
    assert "InstagramConnector" not in module_globals
    assert "anthropic" not in module_globals
    assert "urllib" not in module_globals


# --- extract_topic_summaries(): best-effort, never raises ---------------------------


def test_extract_topic_summaries_finds_reel_story_carousel_lines():
    text = (
        "Reel: kaip pradėti rytą su joga\n"
        "Story 1: klausk sekėjų apie jų rytinę rutiną\n"
        "Carousel: 5 kosmetikos patarimai\n"
        "Kažkoks kitas tekstas be žymos\n"
    )
    topics = content_manager.extract_topic_summaries(text)
    assert any("Reel" in t for t in topics)
    assert any("Story" in t for t in topics)
    assert any("Carousel" in t for t in topics)


def test_extract_topic_summaries_empty_text_returns_empty_list():
    assert content_manager.extract_topic_summaries("") == []


def test_extract_topic_summaries_never_raises_on_arbitrary_text():
    # Garbage/unexpected input must not crash - worst case, zero topics
    # extracted, never an exception propagating to the caller.
    content_manager.extract_topic_summaries("\n\n---\n###\n" * 50)  # must not raise


# --- format_greeting(): fixed opening line -------------------------------------------


def test_format_greeting_includes_labas_rytas():
    assert "Labas rytas" in content_manager.format_greeting()


def test_format_greeting_includes_weekday_in_lithuanian():
    now = datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc)  # Wednesday
    greeting = content_manager.format_greeting(now=now)
    assert "trečiadienis" in greeting


def test_format_greeting_includes_human_readable_date():
    now = datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc)
    greeting = content_manager.format_greeting(now=now)
    assert "2026" in greeting
    assert "rugsėjo" in greeting
    assert "23" in greeting


def test_format_greeting_all_weekdays_are_valid_lithuanian_words():
    expected = {
        "pirmadienis", "antradienis", "trečiadienis", "ketvirtadienis",
        "penktadienis", "šeštadienis", "sekmadienis",
    }
    for day_offset in range(7):
        now = datetime(2026, 9, 21 + day_offset, 8, 0, tzinfo=timezone.utc)  # Mon..Sun
        greeting = content_manager.format_greeting(now=now)
        assert any(day in greeting for day in expected)


# --- never touches Gmail/Calendar/Instagram connectors directly ---------------------


def test_module_does_not_import_any_connector():
    module_globals = vars(content_manager)
    for forbidden in (
        "InstagramConnector", "GmailConnector", "GoogleCalendarConnector",
        "EmailConnector", "StripeConnector",
    ):
        assert forbidden not in module_globals


def test_module_does_not_import_voice_or_agent():
    module_globals = vars(content_manager)
    assert "Agent" not in module_globals
    assert "speak" not in module_globals
    assert "listen_once" not in module_globals
