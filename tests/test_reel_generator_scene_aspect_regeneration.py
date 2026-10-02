"""Tests for jarvis.reel_generator.storyboard's Storyboard Creative
Controls stage: regenerate_scene_camera()/regenerate_scene_style()/
regenerate_scene_visual() - the single-scene, single-aspect targeted
LLM regeneration functions behind CHANGE CAMERA/CHANGE STYLE/CHANGE
VISUAL. Mocked LLMClient throughout, matching every other
generate_*()/regenerate_*() test file in this package.

Confirms: each function calls the LLM with a request scoped to ONE
scene, returns an updated ScenePlan matched to that scene_number, the
fields each is explicitly NOT supposed to touch are structurally
preserved (dataclasses.replace()'d back from the original plan - a real
guarantee, not just a prompt instruction), the returned plan carries a
freshly recomputed visual_quality_score, and every attempt failing
returns None without raising."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

from jarvis.reel_generator.storyboard import (
    Scene,
    regenerate_scene_camera,
    regenerate_scene_style,
    regenerate_scene_visual,
)
from jarvis.reel_generator.visual_plan import ScenePlan


def _text_response(text: str):
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    response = MagicMock()
    response.content = [text_block]
    return response


def _json_response(value) -> MagicMock:
    return _text_response(json.dumps(value))


def _scene(**overrides: Any) -> Scene:
    defaults: dict[str, Any] = dict(
        number=1, start_seconds=0.0, end_seconds=3.0, segment_kind="hook",
        voice_text="Three ways to start your day.", on_screen_text="3 ways to start",
        visual_description="morning light",
    )
    defaults.update(overrides)
    return Scene(**defaults)


def _plan(**overrides: Any) -> ScenePlan:
    defaults: dict[str, Any] = dict(
        scene_number=1, visual_type="photo_style",
        main_visual_prompt="A woman standing in a bright bathroom looking into the mirror, warm morning light",
        supporting_visuals=(), text_cues=(), sticker="none", sticker_start_seconds=0.0,
        motion="static", emotion="hopeful", pacing="medium", lighting="warm",
        environment="bright bathroom with a large mirror", subject_action="looking into the mirror",
        camera_shot="medium_shot", camera_movement="static", visual_source="ai_generated",
        voiceover="Three ways to start your day.",
    )
    defaults.update(overrides)
    return ScenePlan(**defaults)


# --- regenerate_scene_camera() -------------------------------------------------------------


def test_regenerate_scene_camera_returns_updated_plan_for_the_right_scene():
    llm = MagicMock()
    scene = _scene()
    plan = _plan()
    response = {
        "scene_number": 1, "camera_shot": "close_up", "camera_movement": "handheld",
        "subject_action": "leaning closer to the mirror", "main_visual_prompt": "close-up shot of her face",
    }
    llm.send.return_value = _json_response(response)
    new_plan = regenerate_scene_camera(llm, scene=scene, plan=plan)
    assert new_plan is not None
    assert new_plan.scene_number == 1
    assert new_plan.camera_shot == "close_up"
    assert new_plan.camera_movement == "handheld"


def test_regenerate_scene_camera_preserves_environment_and_visual_source():
    llm = MagicMock()
    scene = _scene()
    plan = _plan(environment="a specific cozy bedroom", visual_source="b_roll")
    response = {
        "scene_number": 1, "camera_shot": "wide_shot", "camera_movement": "pan",
        "subject_action": "walking across the room", "main_visual_prompt": "wide shot of the room",
        # A misbehaving model tries to change these too - must be
        # structurally overridden back to the original values.
        "environment": "a completely different location", "visual_source": "typography",
    }
    llm.send.return_value = _json_response(response)
    new_plan = regenerate_scene_camera(llm, scene=scene, plan=plan)
    assert new_plan is not None
    assert new_plan.environment == "a specific cozy bedroom"
    assert new_plan.visual_source == "b_roll"


def test_regenerate_scene_camera_with_explicit_shot_uses_it_exactly():
    llm = MagicMock()
    scene = _scene()
    plan = _plan()
    response = {
        "scene_number": 1, "camera_shot": "macro", "camera_movement": "static",
        "subject_action": "applying cream", "main_visual_prompt": "macro shot of skin",
    }
    llm.send.return_value = _json_response(response)
    regenerate_scene_camera(llm, scene=scene, plan=plan, camera_shot="macro")
    system_prompt = llm.send.call_args.kwargs["system"]
    assert 'Use EXACTLY this camera shot: "macro"' in system_prompt


def test_regenerate_scene_camera_without_explicit_shot_asks_for_a_different_one():
    llm = MagicMock()
    scene = _scene()
    plan = _plan(camera_shot="medium_shot")
    llm.send.return_value = _json_response(
        {"scene_number": 1, "camera_shot": "wide_shot", "camera_movement": "static",
         "subject_action": "x", "main_visual_prompt": "y"}
    )
    regenerate_scene_camera(llm, scene=scene, plan=plan)
    system_prompt = llm.send.call_args.kwargs["system"]
    assert "medium_shot" in system_prompt
    assert "DIFFERENT camera_shot" in system_prompt


def test_regenerate_scene_camera_recomputes_quality_score():
    llm = MagicMock()
    scene = _scene()
    plan = _plan()
    llm.send.return_value = _json_response(
        {"scene_number": 1, "camera_shot": "close_up", "camera_movement": "handheld",
         "subject_action": "x", "main_visual_prompt": "a"}  # too short -> lower score
    )
    new_plan = regenerate_scene_camera(llm, scene=scene, plan=plan)
    assert new_plan is not None
    assert new_plan.visual_quality_score < 10


def test_regenerate_scene_camera_all_attempts_fail_returns_none():
    llm = MagicMock()
    llm.send.return_value = _text_response("not json")
    result = regenerate_scene_camera(llm, scene=_scene(), plan=_plan())
    assert result is None


def test_regenerate_scene_camera_llm_exception_returns_none():
    llm = MagicMock()
    llm.send.side_effect = RuntimeError("network down")
    result = regenerate_scene_camera(llm, scene=_scene(), plan=_plan())
    assert result is None


# --- regenerate_scene_style() --------------------------------------------------------------


def test_regenerate_scene_style_returns_updated_plan():
    llm = MagicMock()
    scene = _scene()
    plan = _plan()
    response = {
        "scene_number": 1, "main_visual_prompt": "cinematic, dramatic lighting version",
        "lighting": "dramatic",
    }
    llm.send.return_value = _json_response(response)
    new_plan = regenerate_scene_style(llm, scene=scene, plan=plan, visual_style="cinematic")
    assert new_plan is not None
    assert new_plan.lighting == "dramatic"
    assert "cinematic" in new_plan.main_visual_prompt.lower()


def test_regenerate_scene_style_preserves_camera_and_environment():
    llm = MagicMock()
    scene = _scene()
    plan = _plan(camera_shot="close_up", camera_movement="handheld", environment="a specific kitchen")
    response = {
        "scene_number": 1, "main_visual_prompt": "restyled",
        "lighting": "bright",
        # misbehaving model tries to change these - must be overridden back
        "camera_shot": "wide_shot", "camera_movement": "static", "environment": "somewhere else",
    }
    llm.send.return_value = _json_response(response)
    new_plan = regenerate_scene_style(llm, scene=scene, plan=plan, visual_style="minimal")
    assert new_plan is not None
    assert new_plan.camera_shot == "close_up"
    assert new_plan.camera_movement == "handheld"
    assert new_plan.environment == "a specific kitchen"


def test_regenerate_scene_style_invalid_style_falls_back_to_default():
    llm = MagicMock()
    scene = _scene()
    plan = _plan()
    llm.send.return_value = _json_response({"scene_number": 1, "main_visual_prompt": "x", "lighting": "warm"})
    regenerate_scene_style(llm, scene=scene, plan=plan, visual_style="not-a-real-style")
    system_prompt = llm.send.call_args.kwargs["system"]
    from jarvis.reel_generator.visual_plan import DEFAULT_VISUAL_STYLE
    assert DEFAULT_VISUAL_STYLE in system_prompt


def test_regenerate_scene_style_all_attempts_fail_returns_none():
    llm = MagicMock()
    llm.send.return_value = _text_response("not json")
    result = regenerate_scene_style(llm, scene=_scene(), plan=_plan(), visual_style="luxury")
    assert result is None


# --- regenerate_scene_visual() -------------------------------------------------------------


def test_regenerate_scene_visual_returns_a_different_interpretation():
    llm = MagicMock()
    scene = _scene()
    plan = _plan(environment="a bathroom", subject_action="looking in the mirror")
    response = {
        "scene_number": 1, "environment": "a sunny kitchen", "subject_action": "pouring coffee",
        "main_visual_prompt": "a completely different scene", "camera_shot": "wide_shot",
        "camera_movement": "pan", "visual_source": "b_roll",
    }
    llm.send.return_value = _json_response(response)
    new_plan = regenerate_scene_visual(llm, scene=scene, plan=plan)
    assert new_plan is not None
    assert new_plan.environment == "a sunny kitchen"
    assert new_plan.subject_action == "pouring coffee"
    assert new_plan.visual_source == "b_roll"


def test_regenerate_scene_visual_preserves_sticker_and_motion():
    llm = MagicMock()
    scene = _scene()
    plan = _plan(sticker="celebration", motion="zoom_in", transition="fade")
    response = {
        "scene_number": 1, "environment": "x", "subject_action": "y", "main_visual_prompt": "z",
        "camera_shot": "macro", "camera_movement": "static", "visual_source": "ai_generated",
    }
    llm.send.return_value = _json_response(response)
    new_plan = regenerate_scene_visual(llm, scene=scene, plan=plan)
    assert new_plan is not None
    assert new_plan.sticker == "celebration"
    assert new_plan.motion == "zoom_in"
    assert new_plan.transition == "fade"


def test_regenerate_scene_visual_never_touches_scene_on_screen_text():
    # regenerate_scene_visual() takes `scene` read-only - it never
    # returns or mutates a Scene at all, only a ScenePlan; this test
    # confirms the function's own return type carries no on_screen_text
    # field to accidentally diverge from the real Scene.
    llm = MagicMock()
    scene = _scene(on_screen_text="ORIGINAL TEXT")
    plan = _plan()
    response = {
        "scene_number": 1, "environment": "x", "subject_action": "y", "main_visual_prompt": "z",
        "camera_shot": "macro", "camera_movement": "static", "visual_source": "ai_generated",
    }
    llm.send.return_value = _json_response(response)
    new_plan = regenerate_scene_visual(llm, scene=scene, plan=plan)
    assert new_plan is not None
    assert not hasattr(new_plan, "on_screen_text")
    assert scene.on_screen_text == "ORIGINAL TEXT"  # the real Scene object is untouched


def test_regenerate_scene_visual_all_attempts_fail_returns_none():
    llm = MagicMock()
    llm.send.return_value = _text_response("not json")
    result = regenerate_scene_visual(llm, scene=_scene(), plan=_plan())
    assert result is None


def test_regenerate_scene_visual_llm_exception_returns_none():
    llm = MagicMock()
    llm.send.side_effect = RuntimeError("network down")
    result = regenerate_scene_visual(llm, scene=_scene(), plan=_plan())
    assert result is None


# --- shared behavior across all three ------------------------------------------------------


def test_all_three_retry_on_malformed_json_then_succeed():
    for fn, kwargs in (
        (regenerate_scene_camera, {}),
        (regenerate_scene_style, {"visual_style": "cinematic"}),
        (regenerate_scene_visual, {}),
    ):
        llm = MagicMock()
        good_response = {
            "scene_number": 1, "camera_shot": "close_up", "camera_movement": "static",
            "subject_action": "x", "main_visual_prompt": "a detailed enough description of the scene",
            "environment": "somewhere", "visual_source": "ai_generated", "lighting": "warm",
        }
        llm.send.side_effect = [_text_response("not valid json"), _json_response(good_response)]
        result = fn(llm, scene=_scene(), plan=_plan(), **kwargs)
        assert result is not None, f"{fn.__name__} did not retry/succeed"
        assert llm.send.call_count == 2
