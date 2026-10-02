"""Tests for jarvis.content_studio.plan.generate_content_plan(): the
isolated, tool-free LLM call generating a per-content-type angle/
objective/CTA plan from a single topic. Mocked LLMClient throughout (no
real API calls) - same pattern as
tests/test_instagram_ai_manager_ai_services.py's own weekly-plan tests
(the precedent this module's shape was copied from). Confirms: a valid
response is parsed into one ContentPlanItem per content type, a
partially-malformed response still returns the valid items (partial
success), an entirely-malformed response returns None, malformed JSON
returns None rather than raising, an empty topic never reaches the LLM,
and the call never offers tools."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from jarvis.content_studio.plan import CONTENT_TYPES, generate_content_plan


def _text_response(text: str):
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    response = MagicMock()
    response.content = [text_block]
    return response


def _json_response(value) -> MagicMock:
    return _text_response(json.dumps(value))


def _valid_items() -> dict:
    return {
        "items": [
            {"content_type": ct, "angle": f"{ct} angle", "objective": f"{ct} objective", "cta": f"{ct} cta"}
            for ct in CONTENT_TYPES
        ]
    }


def test_empty_topic_returns_none_without_calling_llm():
    llm = MagicMock()
    assert generate_content_plan(llm, "") is None
    assert generate_content_plan(llm, "   ") is None
    llm.send.assert_not_called()


def test_valid_response_produces_one_item_per_content_type():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_items())
    plan = generate_content_plan(llm, "5 minute morning yoga")
    assert plan is not None
    assert plan.topic == "5 minute morning yoga"
    assert len(plan.items) == len(CONTENT_TYPES)
    assert {i.content_type for i in plan.items} == set(CONTENT_TYPES)


def test_item_for_returns_the_matching_item():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_items())
    plan = generate_content_plan(llm, "some topic")
    assert plan is not None
    reel_item = plan.item_for("reel")
    assert reel_item is not None
    assert reel_item.angle == "reel angle"
    assert plan.item_for("not_a_real_type") is None


def test_item_label_property():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_items())
    plan = generate_content_plan(llm, "some topic")
    assert plan is not None
    pdf_item = plan.item_for("pdf")
    assert pdf_item is not None
    assert pdf_item.label == "PDF"


def test_partial_malformed_response_keeps_the_valid_items():
    llm = MagicMock()
    data = _valid_items()
    del data["items"][0]["cta"]  # drop a required field from the first item
    llm.send.return_value = _json_response(data)
    plan = generate_content_plan(llm, "some topic")
    assert plan is not None
    assert len(plan.items) == len(CONTENT_TYPES) - 1


def test_entirely_malformed_response_returns_none():
    llm = MagicMock()
    llm.send.return_value = _json_response({"items": [{"content_type": "reel"}]})  # missing required fields
    assert generate_content_plan(llm, "some topic") is None


def test_unknown_content_type_is_dropped():
    llm = MagicMock()
    data = _valid_items()
    data["items"].append({"content_type": "not_a_real_type", "angle": "x", "objective": "y", "cta": "z"})
    llm.send.return_value = _json_response(data)
    plan = generate_content_plan(llm, "some topic")
    assert plan is not None
    assert "not_a_real_type" not in {i.content_type for i in plan.items}


def test_malformed_json_returns_none():
    llm = MagicMock()
    llm.send.return_value = _text_response("not json at all")
    assert generate_content_plan(llm, "some topic") is None


def test_llm_exception_returns_none_not_raised():
    llm = MagicMock()
    llm.send.side_effect = RuntimeError("network down")
    assert generate_content_plan(llm, "some topic") is None


def test_call_never_offers_tools():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_items())
    generate_content_plan(llm, "some topic")
    call_args = llm.send.call_args
    assert call_args[0][1] == []


def test_missing_items_key_returns_none():
    llm = MagicMock()
    llm.send.return_value = _json_response({"not_items": []})
    assert generate_content_plan(llm, "some topic") is None


def test_items_not_a_list_returns_none():
    llm = MagicMock()
    llm.send.return_value = _json_response({"items": "not a list"})
    assert generate_content_plan(llm, "some topic") is None
