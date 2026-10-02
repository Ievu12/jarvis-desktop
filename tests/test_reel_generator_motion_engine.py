"""Tests for jarvis.reel_generator.motion_engine: NATURAL MOTION/HYBRID
mode's own settings model, motion-prompt building, and per-scene
orchestration. video_generation.* is monkeypatched throughout (no real
network call). Confirms: build_motion_prompt() composes deterministic
strings per intensity/style/toggle combination, validate_motion_settings()
rejects malformed input, and - most importantly -
generate_motion_for_scene() NEVER raises even when video_generation
itself raises an unexpected exception (requirement 11's own "a Motion
module failure must NEVER break STATIC mode", proven here at the unit
level via a deliberately injected exception)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from jarvis.reel_generator import motion_engine
from jarvis.reel_generator.ai_video_provider import GeneratedVideo
from jarvis.reel_generator.motion_engine import MotionSettings, build_motion_prompt, validate_motion_settings
from jarvis.reel_generator.scenes import SceneVisual
from jarvis.reel_generator.storyboard import Scene
from jarvis.reel_generator.visual_plan import ScenePlan
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available


def _scene(**overrides: Any) -> Scene:
    defaults: dict[str, Any] = dict(
        number=1, start_seconds=0, end_seconds=5, segment_kind="hook",
        voice_text="x", on_screen_text="Hi there", visual_description="a cozy morning scene",
    )
    defaults.update(overrides)
    return Scene(**defaults)


def _plan(**overrides: Any) -> ScenePlan:
    defaults: dict[str, Any] = dict(
        scene_number=1, visual_type="photo_style", main_visual_prompt="a woman stretching in a sunny room",
        supporting_visuals=(), text_cues=(), sticker="none", sticker_start_seconds=0.0,
        motion="static", emotion="calm", pacing="medium", lighting="neutral",
        environment="a sunny bedroom", subject_action="stretching gently", camera_movement="slow pan",
    )
    defaults.update(overrides)
    return ScenePlan(**defaults)


def _visual(**overrides: Any) -> SceneVisual:
    defaults: dict[str, Any] = dict(scene_number=1, source="text_card", image_path=None, error=None)
    defaults.update(overrides)
    return SceneVisual(**defaults)


# --- MotionSettings / validate_motion_settings() --------------------------------------------


def test_motion_settings_defaults():
    settings = MotionSettings()
    assert settings.intensity == "medium"
    assert settings.style == "natural"
    assert settings.camera_movement_enabled is True
    assert settings.people_movement_enabled is True
    assert settings.environment_animation_enabled is True
    assert settings.clip_duration_seconds == 5.0


def test_validate_motion_settings_accepts_every_documented_choice():
    for intensity in motion_engine.MOTION_INTENSITY_CHOICES:
        for style in motion_engine.MOTION_STYLE_CHOICES:
            validate_motion_settings(MotionSettings(intensity=intensity, style=style))


def test_validate_motion_settings_rejects_unknown_intensity():
    with pytest.raises(ValueError, match="intensity"):
        validate_motion_settings(MotionSettings(intensity="extreme"))


def test_validate_motion_settings_rejects_unknown_style():
    with pytest.raises(ValueError, match="style"):
        validate_motion_settings(MotionSettings(style="dramatic"))


def test_validate_motion_settings_rejects_out_of_range_duration():
    with pytest.raises(ValueError, match="clip_duration_seconds"):
        validate_motion_settings(MotionSettings(clip_duration_seconds=60.0))


# --- build_motion_prompt() -------------------------------------------------------------------


def test_build_motion_prompt_includes_subject_action_and_environment():
    prompt = build_motion_prompt(_scene(), _plan(), MotionSettings())
    assert "stretching gently" in prompt
    assert "sunny bedroom" in prompt


def test_build_motion_prompt_includes_camera_movement_when_enabled():
    prompt = build_motion_prompt(_scene(), _plan(camera_movement="slow pan"), MotionSettings(camera_movement_enabled=True))
    assert "slow pan" in prompt


def test_build_motion_prompt_omits_camera_movement_when_disabled():
    prompt = build_motion_prompt(_scene(), _plan(camera_movement="slow pan"), MotionSettings(camera_movement_enabled=False))
    assert "slow pan" not in prompt


def test_build_motion_prompt_omits_subject_action_when_people_movement_disabled():
    prompt = build_motion_prompt(_scene(), _plan(), MotionSettings(people_movement_enabled=False))
    assert "stretching gently" not in prompt


def test_build_motion_prompt_omits_environment_when_environment_animation_disabled():
    prompt = build_motion_prompt(_scene(), _plan(), MotionSettings(environment_animation_enabled=False))
    assert "sunny bedroom" not in prompt


def test_build_motion_prompt_falls_back_to_main_visual_prompt_when_all_toggles_disabled():
    settings = MotionSettings(people_movement_enabled=False, environment_animation_enabled=False)
    prompt = build_motion_prompt(_scene(), _plan(), settings)
    assert "sunny room" in prompt  # from main_visual_prompt


def test_build_motion_prompt_varies_by_style():
    natural = build_motion_prompt(_scene(), _plan(), MotionSettings(style="natural"))
    cinematic = build_motion_prompt(_scene(), _plan(), MotionSettings(style="cinematic"))
    yoga = build_motion_prompt(_scene(), _plan(), MotionSettings(style="yoga"))
    assert natural != cinematic != yoga


def test_build_motion_prompt_varies_by_intensity():
    low = build_motion_prompt(_scene(), _plan(), MotionSettings(intensity="low"))
    high = build_motion_prompt(_scene(), _plan(), MotionSettings(intensity="high"))
    assert low != high


# --- generate_motion_for_scene() (the isolation boundary) -----------------------------------


def test_generate_motion_for_scene_no_still_image_returns_clip_with_error():
    visual = _visual(image_path=None)
    clip = motion_engine.generate_motion_for_scene(_scene(), _plan(), visual, MotionSettings(), output_path=None)
    assert clip.video_path is None
    assert clip.error is not None and "no still image" in clip.error


def test_generate_motion_for_scene_visual_error_returns_clip_with_error(tmp_path):
    visual = _visual(image_path=tmp_path / "scene_01.jpg", error="render failed")
    clip = motion_engine.generate_motion_for_scene(_scene(), _plan(), visual, MotionSettings(), output_path=None)
    assert clip.video_path is None
    assert clip.error is not None


def test_generate_motion_for_scene_not_configured_returns_clip_with_specific_error(tmp_path):
    image_path = tmp_path / "scene_01.jpg"
    image_path.write_bytes(b"fake")
    visual = _visual(image_path=image_path)
    with patch.object(motion_engine.video_generation, "is_configured", return_value=False):
        clip = motion_engine.generate_motion_for_scene(_scene(), _plan(), visual, MotionSettings(), output_path=tmp_path / "clip.mp4")
    assert clip.video_path is None
    assert clip.error is not None and "RUNWAY_API_KEY" in clip.error
    assert clip.source_image_path == image_path


def test_generate_motion_for_scene_provider_error_returns_clip_with_that_reason(tmp_path):
    image_path = tmp_path / "scene_01.jpg"
    image_path.write_bytes(b"fake")
    visual = _visual(image_path=image_path)
    with patch.object(motion_engine.video_generation, "is_configured", return_value=True), \
         patch.object(motion_engine.video_generation, "generate_scene_video", return_value=(None, "Runway generation failed: unsafe content")):
        clip = motion_engine.generate_motion_for_scene(_scene(), _plan(), visual, MotionSettings(), output_path=tmp_path / "clip.mp4")
    assert clip.video_path is None
    assert clip.error == "Runway generation failed: unsafe content"


def test_generate_motion_for_scene_success_saves_and_returns_clip_path(tmp_path):
    image_path = tmp_path / "scene_01.jpg"
    image_path.write_bytes(b"fake")
    output_path = tmp_path / "clip_01.mp4"
    visual = _visual(image_path=image_path)
    video = GeneratedVideo(video_bytes=b"fake-mp4-bytes", duration_seconds=5.0, width=768, height=1280)
    with patch.object(motion_engine.video_generation, "is_configured", return_value=True), \
         patch.object(motion_engine.video_generation, "generate_scene_video", return_value=(video, None)):
        clip = motion_engine.generate_motion_for_scene(_scene(), _plan(), visual, MotionSettings(), output_path=output_path)
    assert clip.error is None
    assert clip.video_path == output_path
    assert output_path.is_file()
    assert output_path.read_bytes() == b"fake-mp4-bytes"


def test_generate_motion_for_scene_never_raises_on_injected_exception(tmp_path):
    # The direct, unit-level proof of requirement 11's "a Motion module
    # failure must NEVER break STATIC mode": even a genuinely unexpected
    # exception from video_generation itself must come back as a
    # well-formed SceneMotionClip with `error` set, never propagate.
    image_path = tmp_path / "scene_01.jpg"
    image_path.write_bytes(b"fake")
    visual = _visual(image_path=image_path)
    with patch.object(motion_engine.video_generation, "is_configured", return_value=True), \
         patch.object(motion_engine.video_generation, "generate_scene_video", side_effect=RuntimeError("boom")):
        clip = motion_engine.generate_motion_for_scene(_scene(), _plan(), visual, MotionSettings(), output_path=tmp_path / "clip.mp4")
    assert clip.video_path is None
    assert clip.error is not None and "boom" in clip.error


# --- generate_motion_for_scenes() (batch orchestration) --------------------------------------


def test_generate_motion_for_scenes_only_processes_scenes_in_settings_by_scene(tmp_path):
    scenes = (_scene(number=1), _scene(number=2), _scene(number=3))
    plans = {1: _plan(scene_number=1), 2: _plan(scene_number=2), 3: _plan(scene_number=3)}
    for i in (1, 2, 3):
        (tmp_path / f"scene_{i:02d}.jpg").write_bytes(b"fake")
    visuals = [_visual(scene_number=i, image_path=tmp_path / f"scene_{i:02d}.jpg") for i in (1, 2, 3)]
    settings_by_scene = {2: MotionSettings()}  # only scene 2 requests motion (Hybrid-style)

    with patch.object(motion_engine.video_generation, "is_configured", return_value=False):
        clips = motion_engine.generate_motion_for_scenes(scenes, plans, visuals, settings_by_scene, output_dir=tmp_path)

    assert len(clips) == 1
    assert clips[0].scene_number == 2


def test_generate_motion_for_scenes_one_failure_does_not_block_the_others(tmp_path):
    scenes = (_scene(number=1), _scene(number=2))
    plans = {1: _plan(scene_number=1), 2: _plan(scene_number=2)}
    (tmp_path / "scene_01.jpg").write_bytes(b"fake")
    (tmp_path / "scene_02.jpg").write_bytes(b"fake")
    visuals = [
        _visual(scene_number=1, image_path=tmp_path / "scene_01.jpg"),
        _visual(scene_number=2, image_path=tmp_path / "scene_02.jpg"),
    ]
    settings_by_scene = {1: MotionSettings(), 2: MotionSettings()}
    video = GeneratedVideo(video_bytes=b"ok", duration_seconds=5.0, width=768, height=1280)

    def fake_generate(source_image_path, prompt, *, duration_seconds):
        if "scene_01" in str(source_image_path):
            raise RuntimeError("boom")
        return video, None

    with patch.object(motion_engine.video_generation, "is_configured", return_value=True), \
         patch.object(motion_engine.video_generation, "generate_scene_video", side_effect=fake_generate):
        clips = motion_engine.generate_motion_for_scenes(scenes, plans, visuals, settings_by_scene, output_dir=tmp_path)

    clips_by_number = {c.scene_number: c for c in clips}
    assert clips_by_number[1].error is not None and "boom" in clips_by_number[1].error
    assert clips_by_number[2].error is None
    assert clips_by_number[2].video_path is not None


# --- quality_precheck_clip() ------------------------------------------------------------------


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_quality_precheck_clip_flags_a_black_clip(tmp_path):
    import subprocess

    clip_path = tmp_path / "black_clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=768x1280:d=2", "-c:v", "libx264", "-t", "2", str(clip_path)],
        capture_output=True, timeout=30, check=True,
    )
    issues = motion_engine.quality_precheck_clip(clip_path)
    assert any(i.check == "black_frames" for i in issues)
