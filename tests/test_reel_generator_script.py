"""Tests for jarvis.reel_generator.script.generate_reel_script(): the
isolated, tool-free LLM call generating a 3-segment (hook/value/cta)
ReelScript timed to fit a ReelBrief's duration_seconds. Mocked
LLMClient throughout. Confirms: a valid, well-timed response is parsed
into exactly 3 ordered ScriptSegments, a script whose total exceeds
the requested duration is rejected (module brief section 3: "Do not
create scripts that are too long for the selected duration."), out-of-
order/wrong-kind segments are rejected, overlapping segments are
rejected, malformed JSON returns None rather than raising, estimated
speaking duration is computed locally from word count (not trusted
from the model), and the call never offers tools."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.script import ScriptSegment, generate_reel_script


def _text_response(text: str):
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    response = MagicMock()
    response.content = [text_block]
    return response


def _json_response(value) -> MagicMock:
    return _text_response(json.dumps(value))


def _brief(**overrides: Any) -> ReelBrief:
    defaults: dict[str, Any] = dict(
        topic="morning yoga", audience="wellness beginners", objective="educate", tone="calm",
        cta="Save this Reel", style="yoga", duration_seconds=20, language="en",
    )
    defaults.update(overrides)
    return ReelBrief(**defaults)


def _valid_segments(duration_seconds: int = 20) -> dict:
    third = duration_seconds / 3
    return {
        "segments": [
            {"kind": "hook", "start_seconds": 0, "end_seconds": third, "text": "Three ways to start your morning right."},
            {"kind": "value", "start_seconds": third, "end_seconds": 2 * third, "text": "First, stretch. Second, breathe. Third, move slowly."},
            {"kind": "cta", "start_seconds": 2 * third, "end_seconds": duration_seconds, "text": "Save this Reel for tomorrow."},
        ]
    }


def test_valid_response_produces_three_ordered_segments():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_segments())
    script = generate_reel_script(llm, _brief())
    assert script is not None
    assert len(script.segments) == 3
    assert [s.kind for s in script.segments] == ["hook", "value", "cta"]
    assert script.segments[0].label == "HOOK"
    assert script.segments[2].label == "CTA"


def test_full_text_joins_all_segments():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_segments())
    script = generate_reel_script(llm, _brief())
    assert script is not None
    assert "Save this Reel" in script.full_text


def test_estimated_speaking_seconds_is_computed_locally_from_word_count():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_segments())
    script = generate_reel_script(llm, _brief())
    assert script is not None
    word_count = len(script.full_text.split())
    assert script.estimated_speaking_seconds == round(word_count / 2.5, 1)


def test_segment_duration_property():
    segment = ScriptSegment(kind="hook", start_seconds=0, end_seconds=3.5, text="hi")
    assert segment.duration_seconds == 3.5


def test_script_too_long_for_duration_returns_none():
    llm = MagicMock()
    too_long = _valid_segments(duration_seconds=20)
    too_long["segments"][2]["end_seconds"] = 40  # blows way past the 20-second brief
    llm.send.return_value = _json_response(too_long)
    assert generate_reel_script(llm, _brief(duration_seconds=20)) is None


def test_wrong_number_of_segments_returns_none():
    llm = MagicMock()
    bad = _valid_segments()
    bad["segments"] = bad["segments"][:2]
    llm.send.return_value = _json_response(bad)
    assert generate_reel_script(llm, _brief()) is None


def test_wrong_segment_order_returns_none():
    llm = MagicMock()
    bad = _valid_segments()
    bad["segments"][0]["kind"] = "value"
    bad["segments"][1]["kind"] = "hook"
    llm.send.return_value = _json_response(bad)
    assert generate_reel_script(llm, _brief()) is None


def test_overlapping_segments_return_none():
    llm = MagicMock()
    bad = _valid_segments()
    bad["segments"][1]["start_seconds"] = 0  # overlaps the hook by a lot
    llm.send.return_value = _json_response(bad)
    assert generate_reel_script(llm, _brief()) is None


def test_empty_segment_text_returns_none():
    llm = MagicMock()
    bad = _valid_segments()
    bad["segments"][0]["text"] = "   "
    llm.send.return_value = _json_response(bad)
    assert generate_reel_script(llm, _brief()) is None


def test_end_before_start_returns_none():
    llm = MagicMock()
    bad = _valid_segments()
    bad["segments"][0]["end_seconds"] = -1
    llm.send.return_value = _json_response(bad)
    assert generate_reel_script(llm, _brief()) is None


def test_malformed_json_returns_none():
    llm = MagicMock()
    llm.send.return_value = _text_response("not json at all")
    assert generate_reel_script(llm, _brief()) is None


def test_llm_exception_returns_none_not_raised():
    llm = MagicMock()
    llm.send.side_effect = RuntimeError("network down")
    assert generate_reel_script(llm, _brief()) is None


def test_call_never_offers_tools():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_segments())
    generate_reel_script(llm, _brief())
    call_args = llm.send.call_args
    assert call_args[0][1] == []


def test_duration_appears_in_prompt():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_segments(duration_seconds=45))
    generate_reel_script(llm, _brief(duration_seconds=45))
    system_prompt = llm.send.call_args.kwargs["system"]
    assert "45" in system_prompt


def test_small_seam_drift_is_tolerated():
    # A model reporting start_seconds a fraction of a second before the
    # previous segment's end (floating point / rounding drift) should
    # not be rejected outright - only a MEANINGFUL overlap should be.
    llm = MagicMock()
    slightly_off = _valid_segments()
    slightly_off["segments"][1]["start_seconds"] = slightly_off["segments"][0]["end_seconds"] - 0.2
    llm.send.return_value = _json_response(slightly_off)
    script = generate_reel_script(llm, _brief())
    assert script is not None
