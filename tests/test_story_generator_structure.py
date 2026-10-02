"""Tests for jarvis.story_generator.structure.generate_story_structure():
the isolated, tool-free LLM call generating a complete 8-beat
StoryStructure (Hook/Setup/Conflict/Emotional development/Turning
point/Transformation/Conclusion/CTA) from a plain-language story idea.
Mocked LLMClient throughout. Confirms: a valid response is parsed into
exactly 8 ordered StoryBeats in the fixed order, wrong beat count/order/
kind is rejected, empty beat text is rejected, malformed JSON/an LLM
exception returns None rather than raising, an empty idea short-
circuits without calling the LLM, and the call never offers tools."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from jarvis.story_generator.structure import BEAT_NAMES, StoryBeat, generate_story_structure


def _text_response(text: str):
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    response = MagicMock()
    response.content = [text_block]
    return response


def _json_response(value) -> MagicMock:
    return _text_response(json.dumps(value))


def _valid_beats() -> dict:
    return {"beats": [{"kind": kind, "text": f"Some {kind} text."} for kind in BEAT_NAMES]}


def test_valid_response_produces_eight_ordered_beats():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_beats())
    structure = generate_story_structure(llm, "A founder's first failed product launch.")
    assert structure is not None
    assert len(structure.beats) == 8
    assert [b.kind for b in structure.beats] == list(BEAT_NAMES)


def test_beat_labels_are_human_readable():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_beats())
    structure = generate_story_structure(llm, "idea")
    assert structure is not None
    assert structure.beat("conflict").label == "Conflict/problem"  # type: ignore[union-attr]
    assert structure.beat("emotional_development").label == "Emotional development"  # type: ignore[union-attr]


def test_full_text_joins_all_beats():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_beats())
    structure = generate_story_structure(llm, "idea")
    assert structure is not None
    assert "hook" in structure.full_text


def test_beat_lookup_returns_none_for_unknown_kind():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_beats())
    structure = generate_story_structure(llm, "idea")
    assert structure is not None
    assert structure.beat("not_a_real_beat") is None


def test_wrong_number_of_beats_returns_none():
    llm = MagicMock()
    bad = _valid_beats()
    bad["beats"] = bad["beats"][:5]
    llm.send.return_value = _json_response(bad)
    assert generate_story_structure(llm, "idea") is None


def test_wrong_beat_order_returns_none():
    llm = MagicMock()
    bad = _valid_beats()
    bad["beats"][0], bad["beats"][1] = bad["beats"][1], bad["beats"][0]
    llm.send.return_value = _json_response(bad)
    assert generate_story_structure(llm, "idea") is None


def test_empty_beat_text_returns_none():
    llm = MagicMock()
    bad = _valid_beats()
    bad["beats"][3]["text"] = "   "
    llm.send.return_value = _json_response(bad)
    assert generate_story_structure(llm, "idea") is None


def test_malformed_json_returns_none():
    llm = MagicMock()
    llm.send.return_value = _text_response("not json at all")
    assert generate_story_structure(llm, "idea") is None


def test_llm_exception_returns_none_not_raised():
    llm = MagicMock()
    llm.send.side_effect = RuntimeError("network down")
    assert generate_story_structure(llm, "idea") is None


def test_empty_idea_returns_none_without_calling_llm():
    llm = MagicMock()
    assert generate_story_structure(llm, "   ") is None
    llm.send.assert_not_called()


def test_call_never_offers_tools():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_beats())
    generate_story_structure(llm, "idea")
    call_args = llm.send.call_args
    assert call_args[0][1] == []


def test_invalid_story_type_falls_back_to_default():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_beats())
    structure = generate_story_structure(llm, "idea", story_type="not-a-real-type")
    assert structure is not None
    assert structure.story_type == "personal"


def test_invalid_language_falls_back_to_english():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_beats())
    structure = generate_story_structure(llm, "idea", language="fr")
    assert structure is not None
    assert structure.language == "en"


def test_story_beat_dataclass_label_fallback_for_unknown_kind():
    beat = StoryBeat(kind="custom_kind", text="x")
    assert beat.label == "Custom Kind"
