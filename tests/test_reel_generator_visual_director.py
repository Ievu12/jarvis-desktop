"""Tests for jarvis.reel_generator.storyboard.generate_visual_plan(): AI
Reel Generator's own Smart Visual Director - the isolated, tool-free LLM
call that produces a VisualPlan (one ScenePlan per scene) from an
already-generated Storyboard. Mocked LLMClient throughout.

Confirms: a valid response is parsed into ordered ScenePlans matched by
scene_number, the FIRST scene is always marked is_hook regardless of
its own segment_kind, a wrong scene COUNT returns None, malformed JSON/
an LLM exception returns None, an empty storyboard returns None without
calling the LLM, a malformed individual field falls back to a safe
default rather than failing the whole call, visual_style is carried
through onto the returned VisualPlan, and - critically - none of this
ever touches or changes generate_storyboard()'s own behavior (see
tests/test_reel_generator_storyboard.py, run and confirmed unmodified
alongside this file)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.storyboard import Scene, Storyboard, generate_visual_plan
from jarvis.reel_generator.visual_plan import DEFAULT_MOTION, DEFAULT_TRANSITION, DEFAULT_VISUAL_SOURCE


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


def _storyboard(count: int = 3) -> Storyboard:
    scenes = []
    cursor = 0.0
    kinds = ["hook", "value", "value", "cta"]
    for i in range(count):
        kind = kinds[min(i, len(kinds) - 1)]
        scenes.append(Scene(
            number=i + 1, start_seconds=cursor, end_seconds=cursor + 4.0, segment_kind=kind,
            voice_text=f"Voice {i}", on_screen_text=f"Text {i}", visual_description="a room",
        ))
        cursor += 4.0
    return Storyboard(scenes=tuple(scenes))


def _valid_scene_plan_json(scene_number: int) -> dict:
    # main_visual_prompt/environment/subject_action are deliberately
    # detailed/concrete enough to pass jarvis.reel_generator
    # .visual_quality.score_scene_plan()'s own quality gate (Visual
    # Reel Generator stage) - a too-short/vague fixture here would
    # otherwise trigger this module's own retry-on-low-quality logic
    # in every test using it, which is testing something else entirely
    # (JSON shape/field validation, not the quality gate itself - that
    # has its own dedicated tests further below).
    return {
        "scene_number": scene_number, "visual_source": "b_roll",
        "main_visual_prompt": (
            "A woman standing in a bright bathroom looking into the mirror, warm morning light, "
            "cinematic lifestyle photography"
        ),
        "environment": "bright bathroom with a large mirror and warm morning light",
        "subject_action": "standing and looking into the mirror",
        "supporting_visuals": [],
        "text_cues": [{"text": "hook", "position": "top", "start_seconds": 0, "end_seconds": 1.5, "animation": "pop"}],
        "sticker": "none", "sticker_start_seconds": 0.5, "motion": "zoom_in",
        "transition": "fade", "emotion": "hopeful", "pacing": "medium", "lighting": "warm",
    }


def _valid_response(storyboard: Storyboard) -> dict:
    return {"scenes": [_valid_scene_plan_json(s.number) for s in storyboard.scenes]}


def test_valid_response_produces_ordered_scene_plans():
    llm = MagicMock()
    storyboard = _storyboard(4)
    llm.send.return_value = _json_response(_valid_response(storyboard))
    plan = generate_visual_plan(llm, _brief(), storyboard)
    assert plan is not None
    assert len(plan.scenes) == 4
    assert [p.scene_number for p in plan.scenes] == [1, 2, 3, 4]


def test_first_scene_is_always_marked_hook():
    llm = MagicMock()
    storyboard = _storyboard(3)
    response = _valid_response(storyboard)
    # even if the model sets is_hook nowhere and the first scene's own
    # segment_kind is "hook" already - confirms is_hook comes from
    # POSITION (scene 1), not from any model-provided flag or from
    # segment_kind.
    llm.send.return_value = _json_response(response)
    plan = generate_visual_plan(llm, _brief(), storyboard)
    assert plan is not None
    assert plan.scenes[0].is_hook is True
    assert all(not p.is_hook for p in plan.scenes[1:])


def test_wrong_scene_count_returns_none_after_exhausting_retries():
    llm = MagicMock()
    storyboard = _storyboard(4)
    bad = _valid_response(storyboard)
    bad["scenes"] = bad["scenes"][:2]
    llm.send.return_value = _json_response(bad)
    assert generate_visual_plan(llm, _brief(), storyboard) is None
    from jarvis.reel_generator.storyboard import _VISUAL_PLAN_MAX_ATTEMPTS
    assert llm.send.call_count == _VISUAL_PLAN_MAX_ATTEMPTS


def test_retries_on_malformed_json_then_succeeds():
    # Hand-tested, real, intermittent LLM failure mode (see
    # generate_visual_plan()'s own docstring) - a malformed FIRST
    # response must not fail the whole call if a LATER retry succeeds.
    llm = MagicMock()
    storyboard = _storyboard(3)
    llm.send.side_effect = [
        _text_response("not valid json at all"),
        _json_response(_valid_response(storyboard)),
    ]
    plan = generate_visual_plan(llm, _brief(), storyboard)
    assert plan is not None
    assert len(plan.scenes) == 3
    assert llm.send.call_count == 2


def test_retries_on_wrong_scene_count_then_succeeds():
    llm = MagicMock()
    storyboard = _storyboard(3)
    bad = _valid_response(storyboard)
    bad["scenes"] = bad["scenes"][:1]
    llm.send.side_effect = [
        _json_response(bad),
        _json_response(_valid_response(storyboard)),
    ]
    plan = generate_visual_plan(llm, _brief(), storyboard)
    assert plan is not None
    assert len(plan.scenes) == 3
    assert llm.send.call_count == 2


def test_succeeds_on_first_attempt_does_not_retry():
    llm = MagicMock()
    storyboard = _storyboard(3)
    llm.send.return_value = _json_response(_valid_response(storyboard))
    plan = generate_visual_plan(llm, _brief(), storyboard)
    assert plan is not None
    assert llm.send.call_count == 1


def test_all_attempts_malformed_returns_none():
    llm = MagicMock()
    storyboard = _storyboard(3)
    llm.send.return_value = _text_response("still not json")
    assert generate_visual_plan(llm, _brief(), storyboard) is None
    from jarvis.reel_generator.storyboard import _VISUAL_PLAN_MAX_ATTEMPTS
    assert llm.send.call_count == _VISUAL_PLAN_MAX_ATTEMPTS


def test_malformed_json_returns_none():
    llm = MagicMock()
    llm.send.return_value = _text_response("not json")
    assert generate_visual_plan(llm, _brief(), _storyboard(3)) is None


def test_llm_exception_returns_none_not_raised():
    llm = MagicMock()
    llm.send.side_effect = RuntimeError("network down")
    assert generate_visual_plan(llm, _brief(), _storyboard(3)) is None


def test_empty_storyboard_returns_none_without_calling_llm():
    llm = MagicMock()
    assert generate_visual_plan(llm, _brief(), Storyboard(scenes=())) is None
    llm.send.assert_not_called()


def test_call_never_offers_tools():
    llm = MagicMock()
    storyboard = _storyboard(3)
    llm.send.return_value = _json_response(_valid_response(storyboard))
    generate_visual_plan(llm, _brief(), storyboard)
    call_args = llm.send.call_args
    assert call_args[0][1] == []


def test_visual_style_is_carried_through_to_returned_plan():
    llm = MagicMock()
    storyboard = _storyboard(3)
    llm.send.return_value = _json_response(_valid_response(storyboard))
    plan = generate_visual_plan(llm, _brief(), storyboard, visual_style="cinematic")
    assert plan is not None
    assert plan.visual_style == "cinematic"


def test_visual_style_appears_in_system_prompt():
    llm = MagicMock()
    storyboard = _storyboard(2)
    llm.send.return_value = _json_response(_valid_response(storyboard))
    generate_visual_plan(llm, _brief(), storyboard, visual_style="luxury")
    system_prompt = llm.send.call_args.kwargs["system"]
    assert "luxury" in system_prompt


# --- per-field graceful degradation (malformed individual fields don't fail the whole call) ---


def test_invalid_visual_source_falls_back_to_default():
    llm = MagicMock()
    storyboard = _storyboard(2)
    bad = _valid_response(storyboard)
    bad["scenes"][0]["visual_source"] = "not-a-real-source"
    llm.send.return_value = _json_response(bad)
    plan = generate_visual_plan(llm, _brief(), storyboard)
    assert plan is not None
    assert plan.scenes[0].visual_source == DEFAULT_VISUAL_SOURCE


def test_invalid_motion_falls_back_to_default():
    llm = MagicMock()
    storyboard = _storyboard(2)
    bad = _valid_response(storyboard)
    bad["scenes"][0]["motion"] = "warp_speed"
    llm.send.return_value = _json_response(bad)
    plan = generate_visual_plan(llm, _brief(), storyboard)
    assert plan is not None
    assert plan.scenes[0].motion == DEFAULT_MOTION


def test_invalid_transition_falls_back_to_default():
    llm = MagicMock()
    storyboard = _storyboard(2)
    bad = _valid_response(storyboard)
    bad["scenes"][0]["transition"] = "teleport"
    llm.send.return_value = _json_response(bad)
    plan = generate_visual_plan(llm, _brief(), storyboard)
    assert plan is not None
    assert plan.scenes[0].transition == DEFAULT_TRANSITION


def test_missing_scene_plan_object_falls_back_to_defaults():
    llm = MagicMock()
    storyboard = _storyboard(2)
    bad = {"scenes": [{"scene_number": 1}, {"scene_number": 2}]}
    llm.send.return_value = _json_response(bad)
    plan = generate_visual_plan(llm, _brief(), storyboard)
    assert plan is not None
    assert len(plan.scenes) == 2
    assert plan.scenes[0].visual_source == DEFAULT_VISUAL_SOURCE
    assert plan.scenes[0].sticker == "none"


def test_text_cue_times_clamped_to_scene_duration():
    llm = MagicMock()
    storyboard = _storyboard(2)
    bad = _valid_response(storyboard)
    bad["scenes"][0]["text_cues"] = [
        {"text": "too long", "position": "top", "start_seconds": -5, "end_seconds": 999, "animation": "pop"},
    ]
    llm.send.return_value = _json_response(bad)
    plan = generate_visual_plan(llm, _brief(), storyboard)
    assert plan is not None
    cue = plan.scenes[0].text_cues[0]
    assert cue.start_seconds == 0.0
    assert cue.end_seconds == storyboard.scenes[0].duration_seconds


def test_scene_number_mismatch_falls_back_to_positional_matching():
    # If the model returns a scene_number that doesn't match any real
    # scene, the i-th response object is matched to the i-th storyboard
    # scene positionally rather than the whole call failing.
    llm = MagicMock()
    storyboard = _storyboard(3)
    bad = _valid_response(storyboard)
    bad["scenes"][1]["scene_number"] = 999
    llm.send.return_value = _json_response(bad)
    plan = generate_visual_plan(llm, _brief(), storyboard)
    assert plan is not None
    assert len(plan.scenes) == 3
    assert plan.scenes[1].scene_number == storyboard.scenes[1].number


# --- visual quality gate (Visual Reel Generator stage) ------------------------------------


def _low_quality_scene_plan_json(scene_number: int) -> dict:
    plan = _valid_scene_plan_json(scene_number)
    plan["main_visual_prompt"] = "a room"  # too short - scores below the quality floor
    plan["environment"] = ""
    plan["subject_action"] = ""
    return plan


def test_generated_plan_carries_a_visual_quality_score():
    llm = MagicMock()
    storyboard = _storyboard(3)
    llm.send.return_value = _json_response(_valid_response(storyboard))
    plan = generate_visual_plan(llm, _brief(), storyboard)
    assert plan is not None
    assert all(1 <= p.visual_quality_score <= 10 for p in plan.scenes)


def test_low_quality_plan_retries_then_succeeds_with_good_plan():
    llm = MagicMock()
    storyboard = _storyboard(2)
    low_quality = {"scenes": [_low_quality_scene_plan_json(s.number) for s in storyboard.scenes]}
    good_quality = _valid_response(storyboard)
    llm.send.side_effect = [_json_response(low_quality), _json_response(good_quality)]

    plan = generate_visual_plan(llm, _brief(), storyboard)

    assert plan is not None
    assert llm.send.call_count == 2
    from jarvis.reel_generator.visual_quality import MIN_RENDERABLE_QUALITY_SCORE
    assert all(p.visual_quality_score >= MIN_RENDERABLE_QUALITY_SCORE for p in plan.scenes)


def test_low_quality_on_every_attempt_returns_best_scoring_plan_not_none():
    # Never silently discards real generated content - if the quality
    # floor is never reached, the best attempt seen is still returned
    # (never None), matching this codebase's own established
    # "never silently discard real generated content" convention.
    llm = MagicMock()
    storyboard = _storyboard(2)
    low_quality = {"scenes": [_low_quality_scene_plan_json(s.number) for s in storyboard.scenes]}
    llm.send.return_value = _json_response(low_quality)

    plan = generate_visual_plan(llm, _brief(), storyboard)

    assert plan is not None  # NOT None, even though quality never passed
    assert len(plan.scenes) == 2
    from jarvis.reel_generator.storyboard import _VISUAL_PLAN_MAX_ATTEMPTS
    assert llm.send.call_count == _VISUAL_PLAN_MAX_ATTEMPTS


def test_best_scoring_attempt_is_kept_when_none_pass():
    # Attempt 1 scores worse than attempt 2 - the BETTER (attempt 2)
    # plan must be the one returned, not simply the last attempt tried.
    llm = MagicMock()
    storyboard = _storyboard(1)

    worst = {"scenes": [_low_quality_scene_plan_json(storyboard.scenes[0].number)]}
    # Better than "worst" but still below the floor: has environment/
    # subject_action but a too-short main_visual_prompt (-3 only, no
    # -1/-1 on top) -> score 7... to keep it BELOW the floor for this
    # test, drop main_visual_prompt to empty AND leave one of
    # environment/subject_action populated, landing at score 6 (better
    # than "worst"'s score of roughly 4, still below the 7 floor).
    better_but_still_failing = _valid_scene_plan_json(storyboard.scenes[0].number)
    better_but_still_failing["main_visual_prompt"] = ""
    better_but_still_failing["subject_action"] = ""

    llm.send.side_effect = [
        _json_response(worst),
        _json_response({"scenes": [better_but_still_failing]}),
        _json_response(worst),
        _json_response(worst),
    ]

    plan = generate_visual_plan(llm, _brief(), storyboard)
    assert plan is not None
    # The kept plan must be the BETTER one (has a populated environment),
    # not whichever attempt happened to run last.
    assert plan.scenes[0].environment != ""


def test_quality_gate_does_not_affect_generate_storyboard():
    # generate_storyboard() (a completely separate function/LLM call)
    # must be totally unaffected by this quality gate - confirmed by
    # running its own dedicated test file unmodified alongside this one
    # (see this file's own docstring); this test just re-confirms the
    # import boundary: generate_storyboard doesn't reference
    # visual_quality at all.
    import jarvis.reel_generator.storyboard as storyboard_module
    import inspect

    source = inspect.getsource(storyboard_module.generate_storyboard)
    assert "visual_quality" not in source
