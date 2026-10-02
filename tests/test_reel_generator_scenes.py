"""Tests for jarvis.reel_generator.scenes: per-scene visual generation.
Uses the real jarvis.design_studio.render.render_design() (Pillow, no
external service) against a per-test tmp_path - no mocking of the
rendering pipeline itself, since it's fast, deterministic, local
compositing. Confirms: a plain scene renders a text-card image at
1080x1920, an uploaded image is composited in (source="uploaded_image")
rather than a text card being drawn, one scene's failure doesn't stop
the others, output files are named by scene number, and
style_transform (the Brand Kit hook) is applied to each scene's style."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from PIL import Image

from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.scenes import render_all_scenes, render_scene_visual
from jarvis.reel_generator.storyboard import Scene


def _brief(**overrides: Any) -> ReelBrief:
    defaults: dict[str, Any] = dict(
        topic="morning yoga", audience="wellness beginners", objective="educate", tone="calm",
        cta="Save this Reel", style="yoga", duration_seconds=20, language="en",
    )
    defaults.update(overrides)
    return ReelBrief(**defaults)


def _scene(number: int = 1, **overrides: Any) -> Scene:
    defaults: dict[str, Any] = dict(
        number=number, start_seconds=0, end_seconds=4, segment_kind="hook",
        voice_text="Still hitting snooze?", on_screen_text="Still hitting snooze?",
        visual_description="person waking up",
    )
    defaults.update(overrides)
    return Scene(**defaults)


def test_render_scene_visual_produces_text_card(tmp_path):
    visual = render_scene_visual(_brief(), _scene(), output_path=tmp_path / "scene_01.jpg")
    assert visual.error is None
    assert visual.source == "text_card"
    assert visual.image_path is not None
    assert visual.image_path.is_file()
    with Image.open(visual.image_path) as img:
        assert img.size == (1080, 1920)


def test_render_scene_visual_with_uploaded_image(tmp_path):
    source_image = tmp_path / "upload.png"
    Image.new("RGB", (400, 300), (255, 0, 0)).save(source_image)

    visual = render_scene_visual(
        _brief(), _scene(), output_path=tmp_path / "scene_01.jpg", uploaded_image_path=source_image,
    )
    assert visual.error is None
    assert visual.source == "uploaded_image"
    assert visual.image_path is not None
    assert visual.image_path.is_file()


def test_render_all_scenes_names_files_by_scene_number(tmp_path):
    scenes = (
        _scene(number=1, on_screen_text="Hook"),
        _scene(number=2, start_seconds=4, end_seconds=8, segment_kind="value", on_screen_text="1. Stretch"),
        _scene(number=3, start_seconds=8, end_seconds=12, segment_kind="cta", on_screen_text="Save!"),
    )
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path)
    assert len(visuals) == 3
    for v in visuals:
        assert v.error is None
        assert v.image_path is not None
        assert v.image_path.name == f"scene_{v.scene_number:02d}.jpg"


def test_render_all_scenes_uses_uploaded_image_only_for_mapped_scene(tmp_path):
    source_image = tmp_path / "upload.png"
    Image.new("RGB", (400, 300), (0, 255, 0)).save(source_image)
    scenes = (
        _scene(number=1, on_screen_text="Hook"),
        _scene(number=2, start_seconds=4, end_seconds=8, segment_kind="value", on_screen_text="Point"),
    )
    visuals = render_all_scenes(
        _brief(), scenes, output_dir=tmp_path, uploaded_images_by_scene={2: source_image},
    )
    by_number = {v.scene_number: v for v in visuals}
    assert by_number[1].source == "text_card"
    assert by_number[2].source == "uploaded_image"


def test_one_scene_failure_does_not_block_the_others(tmp_path):
    scenes = (
        _scene(number=1, on_screen_text="Hook"),
        _scene(number=2, start_seconds=4, end_seconds=8, segment_kind="value", on_screen_text="Point"),
    )

    from jarvis.reel_generator import scenes as scenes_mod

    original_render_design = scenes_mod.render_design
    call_count = {"n": 0}

    def _flaky(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise scenes_mod.RenderError("simulated failure")
        return original_render_design(*args, **kwargs)

    with patch.object(scenes_mod, "render_design", side_effect=_flaky):
        visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path)

    assert len(visuals) == 2
    failed = [v for v in visuals if v.error is not None]
    succeeded = [v for v in visuals if v.error is None]
    assert len(failed) == 1
    assert len(succeeded) == 1
    error_message = failed[0].error
    assert error_message is not None
    assert "simulated failure" in error_message


def test_style_transform_is_applied_per_scene(tmp_path):
    from jarvis.design_studio.styles import resolve_style

    applied_styles = []

    def _record_and_pass_through(style):
        applied_styles.append(style)
        return style

    render_all_scenes(_brief(), (_scene(),), output_dir=tmp_path, style_transform=_record_and_pass_through)
    assert len(applied_styles) == 1
    assert applied_styles[0] == resolve_style("yoga")


# --- textless mode (Stage D) --------------------------------------------------------------


def test_render_scene_visual_textless_has_baked_in_text_false(tmp_path):
    visual = render_scene_visual(_brief(), _scene(), output_path=tmp_path / "scene_01.jpg", render_textless=True)
    assert visual.error is None
    assert visual.has_baked_in_text is False


def test_render_scene_visual_default_has_baked_in_text_true(tmp_path):
    visual = render_scene_visual(_brief(), _scene(), output_path=tmp_path / "scene_01.jpg")
    assert visual.has_baked_in_text is True


def test_render_scene_visual_textless_produces_different_pixels_than_baked_in(tmp_path):
    textless_path = tmp_path / "textless.jpg"
    baked_in_path = tmp_path / "baked_in.jpg"
    render_scene_visual(_brief(), _scene(), output_path=textless_path, render_textless=True)
    render_scene_visual(_brief(), _scene(), output_path=baked_in_path, render_textless=False)
    assert textless_path.read_bytes() != baked_in_path.read_bytes()


def test_render_all_scenes_textless_applies_to_every_scene(tmp_path):
    scenes = (_scene(number=1), _scene(number=2))
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path, render_textless=True)
    assert len(visuals) == 2
    assert all(v.has_baked_in_text is False for v in visuals)


def test_render_all_scenes_default_still_bakes_in_text(tmp_path):
    scenes = (_scene(number=1), _scene(number=2))
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path)
    assert all(v.has_baked_in_text is True for v in visuals)


def test_render_scene_visual_textless_with_uploaded_image_still_reports_flag_correctly(tmp_path):
    photo_path = tmp_path / "photo.jpg"
    Image.new("RGB", (600, 800), (100, 150, 200)).save(photo_path, quality=90)
    visual = render_scene_visual(
        _brief(), _scene(), output_path=tmp_path / "scene_01.jpg",
        uploaded_image_path=photo_path, render_textless=True,
    )
    assert visual.error is None
    assert visual.source == "uploaded_image"
    assert visual.has_baked_in_text is False


# --- hashtag leak fix (real, reported bug: "hashtags appear in the final Reel video") -------


def test_on_screen_text_with_hashtag_renders_identically_to_pre_stripped_text(tmp_path):
    # Confirms the hashtag is actually removed BEFORE rendering (this
    # is the ORIGINAL, pre-Smart-Visual-Director path - the module
    # brief's own "don't break the existing renderer" requirement means
    # this needs the exact same fix as jarvis.reel_generator.scene_render
    # got, applied here too).
    import hashlib

    with_hashtag_path = tmp_path / "with_hashtag.jpg"
    pre_stripped_path = tmp_path / "pre_stripped.jpg"
    render_scene_visual(_brief(), _scene(on_screen_text="Follow for more! #reels #viral"), output_path=with_hashtag_path)
    render_scene_visual(_brief(), _scene(on_screen_text="Follow for more!"), output_path=pre_stripped_path)
    with Image.open(with_hashtag_path) as a, Image.open(pre_stripped_path) as b:
        a_bytes = a.convert("RGB").tobytes()
        b_bytes = b.convert("RGB").tobytes()
    assert hashlib.sha256(a_bytes).digest() == hashlib.sha256(b_bytes).digest()


# --- SceneMotionClip (NATURAL MOTION / HYBRID mode's own sibling dataclass) -----------------
# Plain construction only, no logic here yet - behavior arrives via
# jarvis.reel_generator.motion_engine. Confirms SceneMotionClip is a
# genuinely separate dataclass, correlated to a SceneVisual only by
# scene_number, and that SceneVisual's own closed Literal("text_card",
# "uploaded_image") was NOT widened by this feature.


def test_scene_motion_clip_constructs_with_all_fields(tmp_path):
    from jarvis.reel_generator.scenes import SceneMotionClip

    clip_path = tmp_path / "clip_01.mp4"
    source_path = tmp_path / "scene_01.jpg"
    clip = SceneMotionClip(
        scene_number=1, video_path=clip_path, source_image_path=source_path,
        error=None, provider_task_id="task-123", duration_seconds=5.0,
    )
    assert clip.scene_number == 1
    assert clip.video_path == clip_path
    assert clip.source_image_path == source_path
    assert clip.error is None
    assert clip.provider_task_id == "task-123"
    assert clip.duration_seconds == 5.0


def test_scene_motion_clip_represents_a_failure_with_no_video_path(tmp_path):
    from jarvis.reel_generator.scenes import SceneMotionClip

    source_path = tmp_path / "scene_02.jpg"
    clip = SceneMotionClip(
        scene_number=2, video_path=None, source_image_path=source_path,
        error="RUNWAY_API_KEY is not configured", provider_task_id=None, duration_seconds=5.0,
    )
    assert clip.video_path is None
    assert clip.error == "RUNWAY_API_KEY is not configured"


def test_scene_motion_clip_is_independent_of_scene_visual_and_does_not_widen_its_literal():
    import typing

    from jarvis.reel_generator.scenes import SceneMotionClip, SceneVisual

    assert SceneMotionClip is not SceneVisual
    source_literal_args = typing.get_args(typing.get_type_hints(SceneVisual)["source"])
    assert source_literal_args == ("text_card", "uploaded_image")
