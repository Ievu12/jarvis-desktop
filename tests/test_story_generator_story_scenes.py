"""Tests for jarvis.story_generator.story_scenes.generate_story_scenes():
the isolated, tool-free LLM call splitting an approved StoryStructure
into 5-10 Scenes (reusing jarvis.reel_generator.storyboard.Scene/
Storyboard as-is) AND their matching VisualPlan (one ScenePlan per
scene - visual type, supporting visuals, text cues, sticker, motion,
emotion/pacing/lighting). Mocked LLMClient throughout. Confirms: a
valid response is parsed into ordered Scenes with mood/transition set
AND a matching VisualPlan, scene count outside [5, 10] is rejected, a
beat left uncovered is rejected, malformed JSON/an LLM exception
returns None rather than raising, scene timings are assembled
sequentially from each scene's own duration, and a malformed
individual visual-plan FIELD falls back to a safe default rather than
failing the whole generation (only a malformed SCENE fails the whole
call)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from jarvis.story_generator.story_scenes import generate_story_scenes
from jarvis.story_generator.structure import BEAT_NAMES, StoryBeat, StoryStructure
from jarvis.story_generator.visual_plan import DEFAULT_MOTION, DEFAULT_VISUAL_TYPE


def _text_response(text: str):
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    response = MagicMock()
    response.content = [text_block]
    return response


def _json_response(value) -> MagicMock:
    return _text_response(json.dumps(value))


def _structure() -> StoryStructure:
    beats = tuple(StoryBeat(kind=kind, text=f"{kind} text.") for kind in BEAT_NAMES)
    return StoryStructure(story_type="personal", language="en", beats=beats)


def _valid_visual_plan(**overrides) -> dict:
    plan = {
        "visual_type": "photo_style", "main_visual_prompt": "a detailed scene description",
        "supporting_visuals": [], "text_cues": [
            {"text": "hook line", "position": "top", "start_seconds": 0, "end_seconds": 1.5, "animation": "fade_in"},
        ],
        "sticker": "none", "sticker_start_seconds": 0.5, "motion": "zoom_in",
        "emotion": "hopeful", "pacing": "medium", "lighting": "warm",
    }
    plan.update(overrides)
    return plan


def _valid_scenes(count: int = 8) -> dict:
    scenes = []
    for i in range(count):
        beat_kind = BEAT_NAMES[min(i, len(BEAT_NAMES) - 1)]
        scenes.append({
            "beat_kind": beat_kind, "duration_seconds": 3.0,
            "voice_text": f"Voice line {i}.", "on_screen_text": f"Text {i}",
            "visual_description": "a quiet room", "mood": "calm",
            "transition": "fade" if i < count - 1 else "end",
            "visual_plan": _valid_visual_plan(),
        })
    # ensure every beat is covered at least once
    for i, kind in enumerate(BEAT_NAMES):
        scenes[i]["beat_kind"] = kind
    return {"scenes": scenes}


def test_valid_response_produces_ordered_scenes_covering_all_beats():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_scenes(8))
    result = generate_story_scenes(llm, _structure())
    assert result is not None
    storyboard, visual_plan = result
    assert len(storyboard.scenes) == 8
    assert [s.number for s in storyboard.scenes] == list(range(1, 9))
    assert {s.segment_kind for s in storyboard.scenes} == set(BEAT_NAMES)
    assert len(visual_plan.scenes) == 8
    assert [p.scene_number for p in visual_plan.scenes] == list(range(1, 9))


def test_mood_and_transition_are_populated():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_scenes(8))
    result = generate_story_scenes(llm, _structure())
    assert result is not None
    storyboard, _ = result
    assert all(s.mood == "calm" for s in storyboard.scenes)
    assert storyboard.scenes[-1].transition == "end"


def test_scene_timings_are_sequential():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_scenes(8))
    result = generate_story_scenes(llm, _structure())
    assert result is not None
    storyboard, _ = result
    cursor = 0.0
    for scene in storyboard.scenes:
        assert scene.start_seconds == cursor
        cursor = scene.end_seconds
        assert scene.duration_seconds == 3.0


def test_too_few_scenes_returns_none():
    llm = MagicMock()
    bad = _valid_scenes(8)
    bad["scenes"] = bad["scenes"][:4]
    llm.send.return_value = _json_response(bad)
    assert generate_story_scenes(llm, _structure()) is None


def test_too_many_scenes_returns_none():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_scenes(11))
    assert generate_story_scenes(llm, _structure()) is None


def test_uncovered_beat_returns_none():
    llm = MagicMock()
    bad = _valid_scenes(8)
    # Overwrite every scene's beat_kind to the same one, leaving 7 beats uncovered.
    for scene in bad["scenes"]:
        scene["beat_kind"] = "hook"
    llm.send.return_value = _json_response(bad)
    assert generate_story_scenes(llm, _structure()) is None


def test_missing_mood_returns_none():
    llm = MagicMock()
    bad = _valid_scenes(8)
    del bad["scenes"][0]["mood"]
    llm.send.return_value = _json_response(bad)
    assert generate_story_scenes(llm, _structure()) is None


def test_missing_transition_returns_none():
    llm = MagicMock()
    bad = _valid_scenes(8)
    del bad["scenes"][0]["transition"]
    llm.send.return_value = _json_response(bad)
    assert generate_story_scenes(llm, _structure()) is None


def test_empty_on_screen_text_returns_none():
    llm = MagicMock()
    bad = _valid_scenes(8)
    bad["scenes"][0]["on_screen_text"] = "   "
    llm.send.return_value = _json_response(bad)
    assert generate_story_scenes(llm, _structure()) is None


def test_invalid_duration_falls_back_to_default():
    llm = MagicMock()
    bad = _valid_scenes(8)
    bad["scenes"][0]["duration_seconds"] = -5
    llm.send.return_value = _json_response(bad)
    result = generate_story_scenes(llm, _structure())
    assert result is not None
    storyboard, _ = result
    assert storyboard.scenes[0].duration_seconds == 4.0


def test_malformed_json_returns_none():
    llm = MagicMock()
    llm.send.return_value = _text_response("not json at all")
    assert generate_story_scenes(llm, _structure()) is None


def test_llm_exception_returns_none_not_raised():
    llm = MagicMock()
    llm.send.side_effect = RuntimeError("network down")
    assert generate_story_scenes(llm, _structure()) is None


def test_call_never_offers_tools():
    llm = MagicMock()
    llm.send.return_value = _json_response(_valid_scenes(8))
    generate_story_scenes(llm, _structure())
    call_args = llm.send.call_args
    assert call_args[0][1] == []


# --- visual plan field validation ----------------------------------------------------------


def test_visual_plan_fields_are_parsed_correctly():
    llm = MagicMock()
    bad = _valid_scenes(8)
    bad["scenes"][0]["visual_plan"] = _valid_visual_plan(
        visual_type="product_shot", sticker="thinking", motion="pan_left",
        supporting_visuals=["arrow", "before/after split"],
    )
    llm.send.return_value = _json_response(bad)
    result = generate_story_scenes(llm, _structure())
    assert result is not None
    _, visual_plan = result
    plan = visual_plan.scenes[0]
    assert plan.visual_type == "product_shot"
    assert plan.sticker == "thinking"
    assert plan.sticker_glyph == "\U0001F914"
    assert plan.motion == "pan_left"
    assert plan.supporting_visuals == ("arrow", "before/after split")
    assert len(plan.text_cues) == 1
    assert plan.text_cues[0].text == "hook line"


def test_missing_visual_plan_object_falls_back_to_defaults_not_none():
    # A malformed/missing "visual_plan" for an otherwise-valid scene
    # must not fail the whole story - it degrades to safe defaults
    # (this module's own established "enrichment never sinks the whole
    # batch" principle - see _validate_visual_plan()'s own docstring).
    llm = MagicMock()
    bad = _valid_scenes(8)
    del bad["scenes"][0]["visual_plan"]
    llm.send.return_value = _json_response(bad)
    result = generate_story_scenes(llm, _structure())
    assert result is not None
    storyboard, visual_plan = result
    assert len(storyboard.scenes) == 8  # the scene itself still succeeds
    plan = visual_plan.scenes[0]
    assert plan.visual_type == DEFAULT_VISUAL_TYPE
    assert plan.motion == DEFAULT_MOTION
    assert plan.sticker == "none"
    assert plan.text_cues == ()


def test_invalid_visual_type_falls_back_to_default():
    llm = MagicMock()
    bad = _valid_scenes(8)
    bad["scenes"][0]["visual_plan"] = _valid_visual_plan(visual_type="not-a-real-type")
    llm.send.return_value = _json_response(bad)
    result = generate_story_scenes(llm, _structure())
    assert result is not None
    _, visual_plan = result
    assert visual_plan.scenes[0].visual_type == DEFAULT_VISUAL_TYPE


def test_invalid_sticker_falls_back_to_none():
    llm = MagicMock()
    bad = _valid_scenes(8)
    bad["scenes"][0]["visual_plan"] = _valid_visual_plan(sticker="not-a-real-sticker")
    llm.send.return_value = _json_response(bad)
    result = generate_story_scenes(llm, _structure())
    assert result is not None
    _, visual_plan = result
    assert visual_plan.scenes[0].sticker == "none"
    assert visual_plan.scenes[0].sticker_glyph == ""


def test_invalid_motion_falls_back_to_static():
    llm = MagicMock()
    bad = _valid_scenes(8)
    bad["scenes"][0]["visual_plan"] = _valid_visual_plan(motion="warp_speed")
    llm.send.return_value = _json_response(bad)
    result = generate_story_scenes(llm, _structure())
    assert result is not None
    _, visual_plan = result
    assert visual_plan.scenes[0].motion == DEFAULT_MOTION


def test_supporting_visuals_capped_at_three():
    llm = MagicMock()
    bad = _valid_scenes(8)
    bad["scenes"][0]["visual_plan"] = _valid_visual_plan(
        supporting_visuals=["a", "b", "c", "d", "e"],
    )
    llm.send.return_value = _json_response(bad)
    result = generate_story_scenes(llm, _structure())
    assert result is not None
    _, visual_plan = result
    assert len(visual_plan.scenes[0].supporting_visuals) == 3


def test_text_cue_times_clamped_to_scene_duration():
    llm = MagicMock()
    bad = _valid_scenes(8)
    bad["scenes"][0]["duration_seconds"] = 3.0
    bad["scenes"][0]["visual_plan"] = _valid_visual_plan(
        text_cues=[{"text": "too long", "position": "top", "start_seconds": -5, "end_seconds": 100, "animation": "pop"}],
    )
    llm.send.return_value = _json_response(bad)
    result = generate_story_scenes(llm, _structure())
    assert result is not None
    _, visual_plan = result
    cue = visual_plan.scenes[0].text_cues[0]
    assert cue.start_seconds == 0.0
    assert cue.end_seconds == 3.0


def test_malformed_text_cue_is_dropped_not_fatal():
    llm = MagicMock()
    bad = _valid_scenes(8)
    bad["scenes"][0]["visual_plan"] = _valid_visual_plan(text_cues=[{"text": "   "}])
    llm.send.return_value = _json_response(bad)
    result = generate_story_scenes(llm, _structure())
    assert result is not None
    _, visual_plan = result
    assert visual_plan.scenes[0].text_cues == ()
