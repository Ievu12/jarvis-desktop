"""Tests for jarvis.reel_generator.storyboard.generate_storyboard(): the
isolated, tool-free LLM call breaking an approved ReelScript into
scenes. Mocked LLMClient throughout. Confirms: a valid response with
multiple VALUE scenes is parsed correctly, HOOK/CTA must each appear
exactly grounded within their own segment's time range, a scene outside
its own segment's range is rejected, missing hook/value/cta coverage is
rejected, too many scenes is rejected, malformed JSON returns None
rather than raising, and the call never offers tools."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.script import ReelScript, ScriptSegment
from jarvis.reel_generator.storyboard import generate_storyboard


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


def _script() -> ReelScript:
    return ReelScript(segments=(
        ScriptSegment(kind="hook", start_seconds=0, end_seconds=4, text="Still hitting snooze?"),
        ScriptSegment(kind="value", start_seconds=4, end_seconds=15, text="First stretch. Second breathe. Third move."),
        ScriptSegment(kind="cta", start_seconds=15, end_seconds=20, text="Save this Reel."),
    ))


def _valid_scenes() -> dict:
    return {
        "scenes": [
            {"segment_kind": "hook", "start_seconds": 0, "end_seconds": 4, "voice_text": "Still hitting snooze?", "on_screen_text": "Still hitting snooze?", "visual_description": "person waking up"},
            {"segment_kind": "value", "start_seconds": 4, "end_seconds": 8, "voice_text": "First stretch.", "on_screen_text": "1. Stretch", "visual_description": "stretching"},
            {"segment_kind": "value", "start_seconds": 8, "end_seconds": 11, "voice_text": "Second breathe.", "on_screen_text": "2. Breathe", "visual_description": "breathing"},
            {"segment_kind": "value", "start_seconds": 11, "end_seconds": 15, "voice_text": "Third move.", "on_screen_text": "3. Move", "visual_description": "moving"},
            {"segment_kind": "cta", "start_seconds": 15, "end_seconds": 20, "voice_text": "Save this Reel.", "on_screen_text": "Save this!", "visual_description": "calm ending"},
        ]
    }


def test_valid_response_produces_multiple_value_scenes():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_scenes())
    storyboard = generate_storyboard(llm, _brief(), _script())
    assert storyboard is not None
    assert len(storyboard.scenes) == 5
    assert storyboard.scenes[0].segment_kind == "hook"
    assert storyboard.scenes[-1].segment_kind == "cta"
    value_scenes = [s for s in storyboard.scenes if s.segment_kind == "value"]
    assert len(value_scenes) == 3


def test_scenes_are_numbered_in_order():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_scenes())
    storyboard = generate_storyboard(llm, _brief(), _script())
    assert storyboard is not None
    assert [s.number for s in storyboard.scenes] == [1, 2, 3, 4, 5]


def test_on_screen_text_is_short_not_full_voice_text():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_scenes())
    storyboard = generate_storyboard(llm, _brief(), _script())
    assert storyboard is not None
    assert storyboard.scenes[1].on_screen_text == "1. Stretch"
    assert storyboard.scenes[1].on_screen_text != storyboard.scenes[1].voice_text


def test_scene_duration_property():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_scenes())
    storyboard = generate_storyboard(llm, _brief(), _script())
    assert storyboard is not None
    assert storyboard.scenes[0].duration_seconds == 4.0


def test_scene_outside_its_own_segment_range_returns_none():
    llm = MagicMock()
    bad = _valid_scenes()
    bad["scenes"][0]["end_seconds"] = 10  # hook segment only goes to 4
    llm.send.return_value = _json_response(bad)
    assert generate_storyboard(llm, _brief(), _script()) is None


def test_missing_hook_coverage_returns_none():
    llm = MagicMock()
    bad = _valid_scenes()
    bad["scenes"] = bad["scenes"][1:]  # drop the hook scene
    llm.send.return_value = _json_response(bad)
    assert generate_storyboard(llm, _brief(), _script()) is None


def test_missing_cta_coverage_returns_none():
    llm = MagicMock()
    bad = _valid_scenes()
    bad["scenes"] = bad["scenes"][:-1]  # drop the cta scene
    llm.send.return_value = _json_response(bad)
    assert generate_storyboard(llm, _brief(), _script()) is None


def test_too_many_scenes_returns_none():
    llm = MagicMock()
    bad = {"scenes": _valid_scenes()["scenes"] * 3}  # way over _MAX_SCENES
    llm.send.return_value = _json_response(bad)
    assert generate_storyboard(llm, _brief(), _script()) is None


def test_empty_on_screen_text_returns_none():
    llm = MagicMock()
    bad = _valid_scenes()
    bad["scenes"][0]["on_screen_text"] = "   "
    llm.send.return_value = _json_response(bad)
    assert generate_storyboard(llm, _brief(), _script()) is None


# --- hashtag leak fix (real, reported bug: "hashtags appear in the final Reel video") -------


def test_hashtag_in_on_screen_text_is_stripped_not_rejected():
    llm = MagicMock()
    scenes = _valid_scenes()
    scenes["scenes"][-1]["on_screen_text"] = "Follow for more! #reels #viral"
    llm.send.return_value = _json_response(scenes)
    storyboard = generate_storyboard(llm, _brief(), _script())
    assert storyboard is not None
    cta_scene = storyboard.scenes[-1]
    assert "#" not in cta_scene.on_screen_text
    assert "Follow for more!" in cta_scene.on_screen_text


def test_on_screen_text_that_is_only_a_hashtag_returns_none():
    # Stripping every "#word" out of "#reels" leaves nothing meaningful
    # - this must be rejected/retried like any other empty on_screen_text,
    # never silently rendered as a blank caption card.
    llm = MagicMock()
    scenes = _valid_scenes()
    scenes["scenes"][-1]["on_screen_text"] = "#reels"
    llm.send.return_value = _json_response(scenes)
    assert generate_storyboard(llm, _brief(), _script()) is None


def test_on_screen_text_without_hashtag_is_unaffected():
    # Confirms the fix never touches ordinary text with no "#" at all.
    llm = MagicMock()
    scenes = _valid_scenes()
    scenes["scenes"][-1]["on_screen_text"] = "Save this Reel!"
    llm.send.return_value = _json_response(scenes)
    storyboard = generate_storyboard(llm, _brief(), _script())
    assert storyboard is not None
    assert storyboard.scenes[-1].on_screen_text == "Save this Reel!"


def test_invalid_segment_kind_returns_none():
    llm = MagicMock()
    bad = _valid_scenes()
    bad["scenes"][0]["segment_kind"] = "outro"
    llm.send.return_value = _json_response(bad)
    assert generate_storyboard(llm, _brief(), _script()) is None


def test_malformed_json_returns_none():
    llm = MagicMock()
    llm.send.return_value = _text_response("not json at all")
    assert generate_storyboard(llm, _brief(), _script()) is None


def test_llm_exception_returns_none_not_raised():
    llm = MagicMock()
    llm.send.side_effect = RuntimeError("network down")
    assert generate_storyboard(llm, _brief(), _script()) is None


def test_call_never_offers_tools():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_scenes())
    generate_storyboard(llm, _brief(), _script())
    call_args = llm.send.call_args
    assert call_args[0][1] == []


def test_single_scene_value_segment_is_accepted():
    llm = MagicMock()
    single = {
        "scenes": [
            {"segment_kind": "hook", "start_seconds": 0, "end_seconds": 4, "voice_text": "hi", "on_screen_text": "Hi", "visual_description": "x"},
            {"segment_kind": "value", "start_seconds": 4, "end_seconds": 15, "voice_text": "value text", "on_screen_text": "The point", "visual_description": "x"},
            {"segment_kind": "cta", "start_seconds": 15, "end_seconds": 20, "voice_text": "cta text", "on_screen_text": "Save!", "visual_description": "x"},
        ]
    }
    llm.send.return_value = _json_response(single)
    storyboard = generate_storyboard(llm, _brief(), _script())
    assert storyboard is not None
    assert len(storyboard.scenes) == 3


def test_strip_hashtags_removes_hashtag_tokens_and_collapses_spacing():
    from jarvis.reel_generator.storyboard import strip_hashtags

    assert strip_hashtags("Follow for more! #reels #viral") == "Follow for more!"
    assert strip_hashtags("#reels") == ""
    assert strip_hashtags("no hashtags here") == "no hashtags here"
    assert strip_hashtags("") == ""
