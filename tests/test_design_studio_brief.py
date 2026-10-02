"""Tests for jarvis.design_studio.brief.generate_design_brief(): the
isolated, tool-free LLM call generating a structured DesignBrief from a
plain-language request. Mocked LLMClient throughout (no real API
calls) - same pattern as tests/test_instagram_ai_manager_ai_services.py
and tests/test_video_studio_highlights.py. Confirms: valid responses
are parsed and validated, malformed/incomplete responses return None
rather than raising, forced_format/forced_style are passed through as
hard constraints in the prompt, the call never offers tools, and an
empty request never reaches the LLM at all."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from jarvis.design_studio.brief import FORMAT_CHOICES, generate_design_brief
from jarvis.design_studio.styles import AUTO_STYLE, DESIGN_STYLES


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
    "topic": "morning yoga", "objective": "educate", "audience": "wellness beginners",
    "tone": "calm", "headline": "3 Poses To Start Your Day",
    "supporting_text": "Stretch and breathe.", "cta": "Save this",
    "format": "story", "style": "yoga",
}


def test_empty_request_returns_none_without_calling_llm():
    llm = MagicMock()
    assert generate_design_brief(llm, "") is None
    assert generate_design_brief(llm, "   ") is None
    llm.send.assert_not_called()


def test_valid_response_produces_a_complete_brief():
    llm = MagicMock()
    llm.send.return_value = _json_response(_VALID_BRIEF)
    brief = generate_design_brief(llm, "Create a Story about morning yoga.")
    assert brief is not None
    assert brief.topic == "morning yoga"
    assert brief.headline == "3 Poses To Start Your Day"
    assert brief.format == "story"
    assert brief.style == "yoga"


def test_missing_field_returns_none():
    llm = MagicMock()
    incomplete = dict(_VALID_BRIEF)
    del incomplete["cta"]
    llm.send.return_value = _json_response(incomplete)
    assert generate_design_brief(llm, "some request") is None


def test_empty_string_field_returns_none():
    llm = MagicMock()
    blank = dict(_VALID_BRIEF, headline="   ")
    llm.send.return_value = _json_response(blank)
    assert generate_design_brief(llm, "some request") is None


def test_invalid_format_returns_none():
    llm = MagicMock()
    bad = dict(_VALID_BRIEF, format="not_a_real_format")
    llm.send.return_value = _json_response(bad)
    assert generate_design_brief(llm, "some request") is None


def test_invalid_style_returns_none():
    llm = MagicMock()
    bad = dict(_VALID_BRIEF, style="not_a_real_style")
    llm.send.return_value = _json_response(bad)
    assert generate_design_brief(llm, "some request") is None


def test_malformed_json_returns_none():
    llm = MagicMock()
    llm.send.return_value = _text_response("not json at all")
    assert generate_design_brief(llm, "some request") is None


def test_llm_exception_returns_none_not_raised():
    llm = MagicMock()
    llm.send.side_effect = RuntimeError("network down")
    assert generate_design_brief(llm, "some request") is None


def test_call_never_offers_tools():
    llm = MagicMock()
    llm.send.return_value = _json_response(_VALID_BRIEF)
    generate_design_brief(llm, "some request")
    call_args = llm.send.call_args
    assert call_args[0][1] == []


def test_forced_format_appears_in_prompt():
    llm = MagicMock()
    llm.send.return_value = _json_response(_VALID_BRIEF)
    generate_design_brief(llm, "some request", forced_format="square")
    prompt = llm.send.call_args[0][0][0]["content"]
    assert "square" in prompt
    assert "MUST" in prompt


def test_forced_style_appears_in_prompt():
    llm = MagicMock()
    llm.send.return_value = _json_response(_VALID_BRIEF)
    generate_design_brief(llm, "some request", forced_style="bold")
    prompt = llm.send.call_args[0][0][0]["content"]
    assert "bold" in prompt


def test_forced_style_auto_does_not_constrain_prompt():
    llm = MagicMock()
    llm.send.return_value = _json_response(_VALID_BRIEF)
    generate_design_brief(llm, "some request", forced_style=AUTO_STYLE)
    prompt = llm.send.call_args[0][0][0]["content"]
    assert "MUST" not in prompt


def test_all_format_choices_are_accepted():
    llm = MagicMock()
    for fmt in FORMAT_CHOICES:
        llm.send.return_value = _json_response(dict(_VALID_BRIEF, format=fmt))
        brief = generate_design_brief(llm, "some request")
        assert brief is not None
        assert brief.format == fmt


def test_all_style_choices_are_accepted():
    llm = MagicMock()
    for style_name in DESIGN_STYLES:
        llm.send.return_value = _json_response(dict(_VALID_BRIEF, style=style_name))
        brief = generate_design_brief(llm, "some request")
        assert brief is not None
        assert brief.style == style_name


def test_format_label_property():
    llm = MagicMock()
    llm.send.return_value = _json_response(_VALID_BRIEF)
    brief = generate_design_brief(llm, "some request")
    assert brief is not None
    assert "1080" in brief.format_label
