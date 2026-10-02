"""End-to-end integration tests for the NATURAL MOTION / AI VIDEO
feature: storyboard -> still visuals -> motion generation (Hybrid mix of
static + moving scenes) -> export -> probe the final output. Runway's
own network calls are mocked throughout (jarvis.reel_generator
.video_generation.generate_scene_video()); everything else - Pillow
scene rendering, ffmpeg export, ffmpeg probing - is real. Skipped
entirely if ffmpeg isn't on PATH, matching every other real-ffmpeg test
file in this codebase.

Confirms requirement 11's two hardest acceptance criteria end-to-end
(not just at the motion_engine.py unit level, see
tests/test_reel_generator_motion_engine.py for that): a Hybrid mix of
static and moving scenes combines correctly into one final export, and
a Motion Engine failure for ONE scene mid-batch never blocks the other
scenes or the final export - the failed scene simply falls back to its
own still image, exactly as designed."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from jarvis.reel_generator import motion_engine
from jarvis.reel_generator.ai_video_provider import GeneratedVideo
from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.export import export_reel_video
from jarvis.reel_generator.motion_engine import MotionSettings
from jarvis.reel_generator.scenes import render_all_scenes
from jarvis.reel_generator.storyboard import Scene
from jarvis.reel_generator.visual_plan import ScenePlan
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available, probe_video

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


def _brief(**overrides: Any) -> ReelBrief:
    defaults: dict[str, Any] = dict(
        topic="morning yoga", audience="wellness beginners", objective="educate", tone="calm",
        cta="Save this Reel", style="yoga", duration_seconds=6, language="en",
    )
    defaults.update(overrides)
    return ReelBrief(**defaults)


def _scenes() -> tuple[Scene, ...]:
    return (
        Scene(number=1, start_seconds=0, end_seconds=2, segment_kind="hook", voice_text="Hi", on_screen_text="Hi there", visual_description="x"),
        Scene(number=2, start_seconds=2, end_seconds=4, segment_kind="value", voice_text="Point", on_screen_text="The point", visual_description="x"),
        Scene(number=3, start_seconds=4, end_seconds=6, segment_kind="cta", voice_text="Save it", on_screen_text="Save this!", visual_description="x"),
    )


def _plan(scene: Scene) -> ScenePlan:
    return ScenePlan(
        scene_number=scene.number, visual_type="photo_style", main_visual_prompt=f"a scene for {scene.number}",
        supporting_visuals=(), text_cues=(), sticker="none", sticker_start_seconds=0.0,
        motion="static", emotion="calm", pacing="medium", lighting="neutral",
        environment="a sunny room", subject_action="stretching gently", camera_movement="slow pan",
    )


def _make_real_clip(path: Path, *, duration_seconds: float, color: str = "green") -> Path:
    import subprocess

    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s=768x1280:d={duration_seconds}",
            "-c:v", "libx264", "-t", str(duration_seconds), str(path),
        ],
        capture_output=True, timeout=30, check=True,
    )
    return path


def test_hybrid_mix_of_static_and_moving_scenes_combines_correctly(tmp_path):
    scenes = _scenes()
    brief = _brief()
    visuals = render_all_scenes(brief, scenes, output_dir=tmp_path / "scenes")
    assert all(v.error is None for v in visuals)

    plans = {s.number: _plan(s) for s in scenes}
    # Hybrid: only scenes 1 and 3 are marked as "moving" (moving_scene_numbers) -
    # scene 2 stays static, exactly like a Static-mode scene.
    settings_by_scene = {1: MotionSettings(), 3: MotionSettings()}

    real_video = GeneratedVideo(video_bytes=b"", duration_seconds=2.0, width=768, height=1280)

    def fake_generate_scene_video(source_image_path, prompt, *, duration_seconds):
        clip_path = _make_real_clip(tmp_path / f"clip_from_{source_image_path.stem}.mp4", duration_seconds=duration_seconds)
        return GeneratedVideo(video_bytes=clip_path.read_bytes(), duration_seconds=duration_seconds, width=768, height=1280), None

    with patch.object(motion_engine.video_generation, "is_configured", return_value=True), \
         patch.object(motion_engine.video_generation, "generate_scene_video", side_effect=fake_generate_scene_video):
        clips = motion_engine.generate_motion_for_scenes(
            scenes, plans, visuals, settings_by_scene, output_dir=tmp_path / "motion",
        )

    clips_by_number = {c.scene_number: c for c in clips}
    assert len(clips) == 2  # only scenes 1 and 3 were requested
    assert clips_by_number[1].error is None and clips_by_number[1].video_path is not None
    assert clips_by_number[3].error is None and clips_by_number[3].video_path is not None
    assert 2 not in clips_by_number  # scene 2 was never requested - stays static

    clip_by_scene = {c.scene_number: c.video_path for c in clips if c.video_path is not None}
    output_path = tmp_path / "exports" / "hybrid_reel.mp4"
    result = export_reel_video(scenes, visuals, output_path=output_path, clip_by_scene=clip_by_scene)

    assert result.output_path.is_file()
    assert abs(result.duration_seconds - 6.0) < 0.5  # total timeline unchanged
    assert result.width == 1080
    assert result.height == 1920
    assert result.scene_fallback_warnings == ()

    probe = probe_video(result.output_path)
    assert probe.duration_seconds > 0


def test_mid_batch_motion_failure_does_not_block_other_scenes_or_the_export(tmp_path):
    # Requirement 11's own hardest acceptance criterion, end-to-end:
    # Runway is configured, but the call for scene 2 raises a genuinely
    # unexpected exception mid-batch - scenes 1 and 3 must still get
    # real clips, scene 2 must fall back to its still image, and the
    # final export must still succeed with the correct total duration.
    scenes = _scenes()
    brief = _brief()
    visuals = render_all_scenes(brief, scenes, output_dir=tmp_path / "scenes")
    assert all(v.error is None for v in visuals)

    plans = {s.number: _plan(s) for s in scenes}
    settings_by_scene = {1: MotionSettings(), 2: MotionSettings(), 3: MotionSettings()}

    def fake_generate_scene_video(source_image_path, prompt, *, duration_seconds):
        if "scene_02" in str(source_image_path):
            raise RuntimeError("simulated Runway outage")
        clip_path = _make_real_clip(tmp_path / f"clip_from_{source_image_path.stem}.mp4", duration_seconds=duration_seconds)
        return GeneratedVideo(video_bytes=clip_path.read_bytes(), duration_seconds=duration_seconds, width=768, height=1280), None

    with patch.object(motion_engine.video_generation, "is_configured", return_value=True), \
         patch.object(motion_engine.video_generation, "generate_scene_video", side_effect=fake_generate_scene_video):
        clips = motion_engine.generate_motion_for_scenes(
            scenes, plans, visuals, settings_by_scene, output_dir=tmp_path / "motion",
        )

    clips_by_number = {c.scene_number: c for c in clips}
    assert len(clips) == 3  # the batch itself never aborted
    assert clips_by_number[1].error is None and clips_by_number[1].video_path is not None
    assert clips_by_number[2].error is not None and "simulated Runway outage" in clips_by_number[2].error
    assert clips_by_number[2].video_path is None
    assert clips_by_number[3].error is None and clips_by_number[3].video_path is not None

    # Scene 2's own still image must be completely untouched - it's
    # still there, ready to be used as export.py's own fallback.
    scene2_visual = next(v for v in visuals if v.scene_number == 2)
    assert scene2_visual.error is None
    assert scene2_visual.image_path is not None and scene2_visual.image_path.is_file()

    # Only scenes with a REAL clip go into clip_by_scene - scene 2 is
    # simply absent, so export.py's own existing still-image branch
    # handles it exactly as in Static mode.
    clip_by_scene = {c.scene_number: c.video_path for c in clips if c.video_path is not None}
    assert 2 not in clip_by_scene

    output_path = tmp_path / "exports" / "partial_motion_reel.mp4"
    result = export_reel_video(scenes, visuals, output_path=output_path, clip_by_scene=clip_by_scene)

    assert result.output_path.is_file()
    assert abs(result.duration_seconds - 6.0) < 0.5  # export still succeeds, full timeline intact
    assert result.scene_fallback_warnings == ()  # scene 2 was never IN clip_by_scene, so no splice-time fallback either


def test_static_mode_full_pipeline_is_completely_unaffected(tmp_path):
    # Final sanity check: the exact same still-image pipeline, with NO
    # motion generation attempted at all (the default Static-mode path)
    # - the export must be identical in shape to every pre-existing
    # AI Reel Generator export.
    scenes = _scenes()
    brief = _brief()
    visuals = render_all_scenes(brief, scenes, output_dir=tmp_path / "scenes")
    output_path = tmp_path / "exports" / "static_reel.mp4"

    result = export_reel_video(scenes, visuals, output_path=output_path)

    assert result.output_path.is_file()
    assert abs(result.duration_seconds - 6.0) < 0.5
    assert result.width == 1080
    assert result.height == 1920
    assert result.scene_fallback_warnings == ()
