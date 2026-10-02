"""Tests for jarvis.reel_generator.brief.generate_reel_brief(): the
isolated, tool-free LLM call generating a structured ReelBrief from a
plain-language Reel idea. Mocked LLMClient throughout (no real API
calls) - same pattern as tests/test_design_studio_brief.py. Confirms:
valid responses are parsed and validated, malformed/incomplete
responses return None rather than raising, forced_style is passed
through as a hard constraint in the prompt, duration/language are
UI-selected settings stored as-is (never inferred), invalid
duration/language reject before calling the LLM, the call never offers
tools, and an empty request never reaches the LLM at all."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from jarvis.reel_generator.brief import (
    DURATION_CHOICES,
    LANGUAGE_ENGLISH,
    LANGUAGE_LITHUANIAN,
    STYLE_CHOICES,
    generate_reel_brief,
)


def _text_response(text: str):
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    response = MagicMock()
    response.content = [text_block]
    return response


def _json_response(value) -> MagicMock:
    return _text_response(json.dumps(value))


_VALID_BRIEF = {
    "topic": "morning yoga", "audience": "wellness beginners", "objective": "educate",
    "tone": "calm", "cta": "Save this Reel", "style": "yoga",
}


def test_empty_request_returns_none_without_calling_llm():
    llm = MagicMock()
    assert generate_reel_brief(llm, "") is None
    assert generate_reel_brief(llm, "   ") is None
    llm.send.assert_not_called()


def test_invalid_duration_returns_none_without_calling_llm():
    llm = MagicMock()
    assert generate_reel_brief(llm, "some idea", duration_seconds=17) is None
    llm.send.assert_not_called()


def test_invalid_language_returns_none_without_calling_llm():
    llm = MagicMock()
    assert generate_reel_brief(llm, "some idea", language="fr") is None
    llm.send.assert_not_called()


def test_valid_response_produces_a_complete_brief():
    llm = MagicMock()
    llm.send.return_value = _json_response(_VALID_BRIEF)
    brief = generate_reel_brief(llm, "Create a Reel about morning yoga.", duration_seconds=20)
    assert brief is not None
    assert brief.topic == "morning yoga"
    assert brief.style == "yoga"
    assert brief.duration_seconds == 20
    assert brief.language == LANGUAGE_ENGLISH


def test_duration_and_language_are_stored_as_given_not_inferred():
    llm = MagicMock()
    llm.send.return_value = _json_response(_VALID_BRIEF)
    brief = generate_reel_brief(llm, "some idea", duration_seconds=45, language=LANGUAGE_LITHUANIAN)
    assert brief is not None
    assert brief.duration_seconds == 45
    assert brief.language == LANGUAGE_LITHUANIAN
    assert brief.language_label == "Lithuanian"


def test_missing_field_returns_none():
    llm = MagicMock()
    incomplete = dict(_VALID_BRIEF)
    del incomplete["cta"]
    llm.send.return_value = _json_response(incomplete)
    assert generate_reel_brief(llm, "some idea") is None


def test_empty_string_field_returns_none():
    llm = MagicMock()
    blank = dict(_VALID_BRIEF, topic="   ")
    llm.send.return_value = _json_response(blank)
    assert generate_reel_brief(llm, "some idea") is None


def test_invalid_style_returns_none():
    llm = MagicMock()
    bad = dict(_VALID_BRIEF, style="not_a_real_style")
    llm.send.return_value = _json_response(bad)
    assert generate_reel_brief(llm, "some idea") is None


def test_malformed_json_returns_none():
    llm = MagicMock()
    llm.send.return_value = _text_response("not json at all")
    assert generate_reel_brief(llm, "some idea") is None


def test_llm_exception_returns_none_not_raised():
    llm = MagicMock()
    llm.send.side_effect = RuntimeError("network down")
    assert generate_reel_brief(llm, "some idea") is None


def test_call_never_offers_tools():
    llm = MagicMock()
    llm.send.return_value = _json_response(_VALID_BRIEF)
    generate_reel_brief(llm, "some idea")
    call_args = llm.send.call_args
    assert call_args[0][1] == []


def test_forced_style_appears_in_prompt():
    llm = MagicMock()
    llm.send.return_value = _json_response(_VALID_BRIEF)
    generate_reel_brief(llm, "some idea", forced_style="bold" if "bold" in STYLE_CHOICES else STYLE_CHOICES[0])
    prompt = llm.send.call_args[0][0][0]["content"]
    assert "MUST" in prompt


def test_all_style_choices_are_accepted():
    llm = MagicMock()
    for style_name in STYLE_CHOICES:
        llm.send.return_value = _json_response(dict(_VALID_BRIEF, style=style_name))
        brief = generate_reel_brief(llm, "some idea")
        assert brief is not None
        assert brief.style == style_name


def test_all_duration_choices_are_accepted():
    llm = MagicMock()
    llm.send.return_value = _json_response(_VALID_BRIEF)
    for duration in DURATION_CHOICES:
        brief = generate_reel_brief(llm, "some idea", duration_seconds=duration)
        assert brief is not None
        assert brief.duration_seconds == duration
