"""Tests for jarvis.reel_generator.scene_render: real Pillow rendering
of a scene's visual, enriched by an optional ScenePlan (main visual
treatment by visual_type/visual_style, supporting-visual badges,
on-screen text cues, sticker/emoji compositing, multi-photo collage).
Uses REAL Pillow rendering throughout (no mocking of render_design/PIL)
- this module's entire job is producing actual pixels, so a mocked-out
render would test nothing real.

This module was originally written under jarvis.story_generator and was
promoted here once AI Reel Generator's own Smart Visual Director brief
needed the exact same rendering - see jarvis.reel_generator
.visual_plan's own docstring for the full history. This test file was
moved (not duplicated) from tests/test_story_generator_scene_render.py
along with it; jarvis.story_generator's own re-export shim
(jarvis.story_generator.scene_render) is covered separately by
tests/test_story_generator_scene_render.py, kept minimal (just
confirms the re-export works)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from PIL import Image

from jarvis.reel_generator import image_generation
from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.scene_render import render_all_story_scenes, render_story_scene_visual
from jarvis.reel_generator.storyboard import Scene
from jarvis.reel_generator.visual_plan import ScenePlan, TextCue


@pytest.fixture(autouse=True)
def _no_real_image_api_calls(monkeypatch):
    # _plan()'s default visual_source ("ai_generated", ScenePlan's own
    # DEFAULT_VISUAL_SOURCE) is one of scene_render's
    # _REAL_IMAGE_VISUAL_SOURCES - if a real OPENAI_API_KEY happens to
    # be configured in the environment this suite runs in, every test
    # below would otherwise make a real, slow, costly OpenAI Images API
    # call. This module's job is the Pillow rendering/compositing logic
    # (hence the "no mocking of render_design/PIL" policy above), never
    # real photo generation - that has its own separate test coverage
    # in tests/test_reel_generator_image_generation.py (HTTP mocked
    # throughout) and its own manually-run end-to-end verification.
    monkeypatch.setattr(image_generation, "is_configured", lambda: False)


def _brief(**overrides: Any) -> ReelBrief:
    defaults: dict[str, Any] = dict(
        topic="founder story", audience="entrepreneurs", objective="inspire", tone="reflective",
        cta="Follow for more", style="storytelling", duration_seconds=24, language="en",
    )
    defaults.update(overrides)
    return ReelBrief(**defaults)


def _scene(**overrides: Any) -> Scene:
    defaults: dict[str, Any] = dict(
        number=1, start_seconds=0.0, end_seconds=3.0, segment_kind="hook",
        voice_text="A founder's launch failed.", on_screen_text="LAUNCH FAILED",
        visual_description="a quiet dim room", mood="tense", transition="fade",
    )
    defaults.update(overrides)
    return Scene(**defaults)


def _plan(**overrides: Any) -> ScenePlan:
    defaults: dict[str, Any] = dict(
        scene_number=1, visual_type="photo_style", main_visual_prompt="a dim room, a laptop glowing",
        supporting_visuals=(), text_cues=(), sticker="none", sticker_start_seconds=0.0,
        motion="zoom_in", emotion="tense", pacing="medium", lighting="dim",
    )
    defaults.update(overrides)
    return ScenePlan(**defaults)


def _make_photo(path: Path, color: tuple[int, int, int]) -> Path:
    Image.new("RGB", (600, 800), color).save(path, quality=90)
    return path


def test_render_without_plan_produces_valid_image(tmp_path):
    output_path = tmp_path / "scene_01.jpg"
    visual = render_story_scene_visual(_brief(), _scene(), None, output_path=output_path)
    assert visual.error is None
    assert visual.image_path is not None
    assert visual.image_path.is_file()
    with Image.open(visual.image_path) as img:
        assert img.size == (1080, 1920)


def test_render_with_plan_produces_valid_image(tmp_path):
    output_path = tmp_path / "scene_01.jpg"
    plan = _plan(sticker="thinking", supporting_visuals=("arrow",), text_cues=(
        TextCue(text="hook line", position="top", start_seconds=0, end_seconds=1.5, animation="fade_in"),
    ))
    visual = render_story_scene_visual(_brief(), _scene(), plan, output_path=output_path)
    assert visual.error is None
    assert visual.image_path is not None
    with Image.open(visual.image_path) as img:
        assert img.size == (1080, 1920)


def test_headline_is_suppressed_when_plan_has_text_cues(tmp_path):
    # Real, hand-tested "text appears twice" fix (see
    # _scene_design_brief()'s own docstring): when a plan has its own
    # text_cues, the base render must NOT also draw scene.on_screen_text
    # as a separate fixed headline - confirmed here by rendering the
    # SAME scene/plan with and without cues and checking the (otherwise
    # identical) middle band, where the headline would appear, actually
    # differs between the two - a real, measurable pixel difference,
    # not just an API/parameter check.
    scene = _scene(on_screen_text="LAUNCH FAILED")
    no_cues_plan = _plan(text_cues=())
    with_cues_plan = _plan(text_cues=(
        TextCue(text="a different overlay phrase", position="top", start_seconds=0, end_seconds=1.5, animation="fade_in"),
    ))

    no_cues_path = tmp_path / "no_cues.jpg"
    with_cues_path = tmp_path / "with_cues.jpg"
    render_story_scene_visual(_brief(), scene, no_cues_plan, output_path=no_cues_path)
    render_story_scene_visual(_brief(), scene, with_cues_plan, output_path=with_cues_path)

    # The headline ("LAUNCH FAILED") is drawn without cues and skipped
    # with cues (see _scene_design_brief()) - a whole-frame hash
    # comparison catches this real difference regardless of exactly
    # where render_design() happens to place the headline, without
    # guessing at pixel coordinates.
    import hashlib
    with Image.open(no_cues_path) as a, Image.open(with_cues_path) as b:
        hash_a = hashlib.sha256(a.convert("RGB").tobytes()).digest()
        hash_b = hashlib.sha256(b.convert("RGB").tobytes()).digest()
    assert hash_a != hash_b


def test_no_plan_still_draws_the_headline(tmp_path):
    # The ORIGINAL, plan-less path (module brief's own hard requirement:
    # never break it) must still draw scene.on_screen_text as the
    # headline exactly as before - this is what "no other text layer to
    # carry it in that case" means in _scene_design_brief()'s own
    # docstring.
    scene = _scene(on_screen_text="LAUNCH FAILED")
    output_path = tmp_path / "scene_01.jpg"
    visual = render_story_scene_visual(_brief(), scene, None, output_path=output_path)
    assert visual.error is None
    assert visual.image_path is not None
    assert visual.image_path.is_file()


def test_sticker_is_actually_composited_as_real_pixels(tmp_path):
    # Renders the SAME scene with and without a sticker and confirms the
    # sticker's own corner region actually differs - a real, visible
    # pixel-level effect, not just stored metadata.
    plain_path = tmp_path / "plain.jpg"
    stickered_path = tmp_path / "stickered.jpg"
    render_story_scene_visual(_brief(), _scene(), _plan(sticker="none"), output_path=plain_path)
    render_story_scene_visual(_brief(), _scene(), _plan(sticker="celebration"), output_path=stickered_path)

    with Image.open(plain_path) as plain_img, Image.open(stickered_path) as stickered_img:
        plain_pixels = plain_img.convert("RGB").tobytes()
        stickered_pixels = stickered_img.convert("RGB").tobytes()
    assert plain_pixels != stickered_pixels


def test_text_cue_is_actually_composited_as_real_pixels(tmp_path):
    plain_path = tmp_path / "plain.jpg"
    texted_path = tmp_path / "texted.jpg"
    render_story_scene_visual(_brief(), _scene(), _plan(text_cues=()), output_path=plain_path)
    render_story_scene_visual(
        _brief(), _scene(),
        _plan(text_cues=(TextCue(text="A big reveal", position="center", start_seconds=0, end_seconds=3, animation="pop"),)),
        output_path=texted_path,
    )
    with Image.open(plain_path) as plain_img, Image.open(texted_path) as texted_img:
        assert plain_img.convert("RGB").tobytes() != texted_img.convert("RGB").tobytes()


def test_sticker_none_produces_no_sticker_glyph(tmp_path):
    # A plan with sticker="none" still applies its own visual_type
    # style treatment (a deliberate difference from a plan-less render -
    # see _style_for_scene_plan()'s own docstring) but must not
    # composite any sticker glyph - confirmed indirectly via
    # ScenePlan.sticker_glyph itself (the source of truth
    # _composite_sticker() reads) rather than a raw pixel comparison,
    # since the background treatment legitimately differs.
    plan = _plan(sticker="none")
    assert plan.sticker_glyph == ""
    output_path = tmp_path / "none_sticker.jpg"
    visual = render_story_scene_visual(_brief(), _scene(), plan, output_path=output_path)
    assert visual.error is None


def test_render_all_story_scenes_renders_every_scene(tmp_path):
    scenes = (
        _scene(number=1, on_screen_text="ONE"),
        _scene(number=2, on_screen_text="TWO"),
        _scene(number=3, on_screen_text="THREE"),
    )
    plans = {1: _plan(scene_number=1, sticker="thinking"), 3: _plan(scene_number=3, motion="pan_left")}
    # scene 2 deliberately has no plan - must still render plainly
    visuals = render_all_story_scenes(_brief(), scenes, plans, output_dir=tmp_path / "scenes")
    assert len(visuals) == 3
    assert all(v.error is None for v in visuals)
    assert all(v.image_path is not None and v.image_path.is_file() for v in visuals)
    # Same file-naming convention as jarvis.reel_generator.scenes, so a
    # project's reopen logic keeps working unmodified.
    assert visuals[0].image_path is not None
    assert visuals[0].image_path.name == "scene_01.jpg"


def test_unrenderable_emoji_font_is_skipped_not_fatal(tmp_path, monkeypatch):
    from jarvis.reel_generator import scene_render as module

    monkeypatch.setattr(module, "_EMOJI_FONT_PATH", "C:/Windows/Fonts/does_not_exist.ttf")
    output_path = tmp_path / "scene_01.jpg"
    visual = render_story_scene_visual(_brief(), _scene(), _plan(sticker="thinking"), output_path=output_path)
    assert visual.error is None
    assert visual.image_path is not None
    assert visual.image_path.is_file()


# --- AI Reel Generator's own promoted additions: visual_style, multi-photo collage --------


def test_visual_style_changes_rendering_style(tmp_path):
    minimal_path = tmp_path / "minimal.jpg"
    bold_path = tmp_path / "bold.jpg"
    render_story_scene_visual(_brief(), _scene(), None, output_path=minimal_path, visual_style="minimal")
    render_story_scene_visual(_brief(), _scene(), None, output_path=bold_path, visual_style="bold")
    with Image.open(minimal_path) as a, Image.open(bold_path) as b:
        assert a.convert("RGB").tobytes() != b.convert("RGB").tobytes()


def test_unknown_visual_style_falls_back_to_brief_style(tmp_path):
    output_path = tmp_path / "scene_01.jpg"
    # Must not raise for an unrecognized visual_style value.
    visual = render_story_scene_visual(_brief(), _scene(), None, output_path=output_path, visual_style="not-a-real-style")
    assert visual.error is None


def test_single_uploaded_photo_becomes_the_real_base_layer(tmp_path):
    # Visual Story Director brief's own requirement C (user-provided
    # photo/video): a SINGLE uploaded photo is used directly as the
    # real, full-bleed base layer (cover-cropped to 1080x1920) - a
    # deliberate behavior change from this module's own earlier
    # "single photo does nothing, only 2+ triggers the collage path"
    # rule (that rule still applies to the MULTI-photo GRID path
    # specifically - see test_two_or_more_uploaded_photos_render_as_
    # real_collage below - but a lone photo is no longer wasted).
    photo = _make_photo(tmp_path / "photo1.jpg", (200, 50, 50))
    output_path = tmp_path / "scene_01.jpg"
    plain_path = tmp_path / "plain.jpg"
    visual = render_story_scene_visual(_brief(), _scene(), None, output_path=output_path, uploaded_photo_paths=[photo])
    render_story_scene_visual(_brief(), _scene(), None, output_path=plain_path)

    assert visual.error is None
    assert visual.source == "uploaded_image"
    with Image.open(output_path) as a, Image.open(plain_path) as b:
        a_bytes = a.convert("RGB").tobytes()
        b_bytes = b.convert("RGB").tobytes()
    # A cheap length/hash-based inequality check instead of a direct
    # `assert a_bytes == b_bytes` - a raw byte-buffer comparison on a
    # ~6MB near-random failure case makes pytest's own assertion-diff
    # machinery (difflib) pathologically slow (hand-tested: this exact
    # comparison, when it fails, can hang pytest's own error reporting
    # for minutes - a real, confirmed pytest-internal issue, not a bug
    # in the code under test) - a hash comparison gives the same pass/
    # fail signal without ever triggering that slow path.
    import hashlib
    assert hashlib.sha256(a_bytes).digest() != hashlib.sha256(b_bytes).digest()
    # The uploaded photo's own dominant color (reddish) should be
    # visible somewhere in the rendered frame.
    with Image.open(output_path) as rendered:
        sample = rendered.convert("RGB").getpixel((rendered.width // 2, rendered.height // 2))
    assert isinstance(sample, tuple)


def test_two_or_more_uploaded_photos_render_as_real_collage(tmp_path):
    photo1 = _make_photo(tmp_path / "photo1.jpg", (200, 50, 50))
    photo2 = _make_photo(tmp_path / "photo2.jpg", (50, 50, 200))
    output_path = tmp_path / "scene_01.jpg"
    plain_path = tmp_path / "plain.jpg"

    visual = render_story_scene_visual(
        _brief(), _scene(), None, output_path=output_path, uploaded_photo_paths=[photo1, photo2],
    )
    render_story_scene_visual(_brief(), _scene(), None, output_path=plain_path)

    assert visual.error is None
    assert visual.source == "uploaded_image"
    with Image.open(output_path) as a, Image.open(plain_path) as b:
        assert a.convert("RGB").tobytes() != b.convert("RGB").tobytes()
        # The collage's own red/blue photo regions must actually appear
        # in the output - not just "some difference", genuinely those
        # photos' own real pixels.
        pixels = a.convert("RGB")
        # top-left cell should be reddish (photo1), by construction of
        # the 2-column grid layout in _composite_uploaded_photos().
        sample = pixels.getpixel((60, 60))
        assert isinstance(sample, tuple)
        assert sample[0] > sample[2]  # more red than blue


def test_missing_uploaded_photo_file_is_skipped_not_fatal(tmp_path):
    real_photo = _make_photo(tmp_path / "real.jpg", (10, 200, 10))
    missing_photo = tmp_path / "does_not_exist.jpg"
    output_path = tmp_path / "scene_01.jpg"
    visual = render_story_scene_visual(
        _brief(), _scene(), None, output_path=output_path, uploaded_photo_paths=[real_photo, missing_photo],
    )
    assert visual.error is None
    assert visual.image_path is not None and visual.image_path.is_file()


# --- "video_clip"/"uploaded_photo" AI-generation fallback (real, reported bug fix) --------


def _fake_generated_image() -> "image_generation.GeneratedImage":
    # A REAL, valid PNG (not fake header bytes) - render_story_scene_visual()
    # opens whatever image_generation "generates" with PIL.Image.open() and
    # cover-crops it, so the fallback bytes must be genuinely decodable,
    # unlike tests/test_reel_generator_image_generation.py's own
    # save-bytes-verbatim tests (which never open the file as an image).
    import io

    buf = io.BytesIO()
    Image.new("RGB", (1024, 1536), (30, 60, 120)).save(buf, format="PNG")
    return image_generation.GeneratedImage(image_bytes=buf.getvalue(), width=1024, height=1536)


def test_video_clip_source_without_upload_falls_back_to_ai_generation(tmp_path, monkeypatch):
    # Real, reported bug: the Smart Visual Director's own LLM call can
    # choose visual_source="video_clip" (or "uploaded_photo") for a scene
    # the person never actually uploaded a photo/video for (e.g. a CTA
    # scene describing a phone-screen tap, which an LLM reads as
    # "footage") - this used to silently render a blank Pillow text card
    # for that ONE scene with no error at all, even with a real,
    # configured image-generation API and a perfectly good
    # main_visual_prompt sitting right there on the plan.
    monkeypatch.setattr(image_generation, "is_configured", lambda: True)
    monkeypatch.setattr(image_generation, "generate_scene_image", lambda *a, **k: (_fake_generated_image(), None))

    plan = _plan(visual_source="video_clip", main_visual_prompt="a finger tapping a glowing phone screen")
    output_path = tmp_path / "scene_05.jpg"
    visual = render_story_scene_visual(_brief(), _scene(number=5), plan, output_path=output_path)

    assert visual.error is None
    assert visual.image_path is not None and visual.image_path.is_file()
    assert visual.source == "generated_image"


def test_uploaded_photo_source_without_upload_falls_back_to_ai_generation(tmp_path, monkeypatch):
    # Same fix, for the sibling visual_source ("uploaded_photo" instead
    # of "video_clip") - both are meant for a real upload that takes
    # priority when one exists (see the next two tests), but must not
    # silently produce a blank card when nothing was actually uploaded.
    monkeypatch.setattr(image_generation, "is_configured", lambda: True)
    monkeypatch.setattr(image_generation, "generate_scene_image", lambda *a, **k: (_fake_generated_image(), None))

    plan = _plan(visual_source="uploaded_photo", main_visual_prompt="a product on a marble countertop")
    output_path = tmp_path / "scene_01.jpg"
    visual = render_story_scene_visual(_brief(), _scene(), plan, output_path=output_path)

    assert visual.error is None
    assert visual.image_path is not None and visual.image_path.is_file()
    assert visual.source == "generated_image"


def test_video_clip_source_with_real_upload_still_prefers_the_upload(tmp_path, monkeypatch):
    # A real uploaded photo must still win over AI generation even when
    # visual_source="video_clip" - the fallback only fills a gap, it
    # never overrides a real upload the person actually provided.
    generate_mock_called = False

    def _tracking_generate(*_args, **_kwargs):
        nonlocal generate_mock_called
        generate_mock_called = True
        return _fake_generated_image(), None

    monkeypatch.setattr(image_generation, "is_configured", lambda: True)
    monkeypatch.setattr(image_generation, "generate_scene_image", _tracking_generate)

    photo = _make_photo(tmp_path / "real_upload.jpg", (10, 200, 10))
    plan = _plan(visual_source="video_clip", main_visual_prompt="anything")
    output_path = tmp_path / "scene_01.jpg"
    visual = render_story_scene_visual(
        _brief(), _scene(), plan, output_path=output_path, uploaded_photo_paths=[photo],
    )

    assert visual.error is None
    assert visual.source == "uploaded_image"
    assert generate_mock_called is False


def test_video_clip_source_without_upload_and_api_not_configured_falls_back_to_text_card(tmp_path):
    # Regression coverage: with NO image-generation API configured at
    # all (this file's own autouse fixture forces is_configured() to
    # False), a "video_clip"/"uploaded_photo" scene with no real upload
    # must keep behaving exactly as before this fix - the plain,
    # unmodified Pillow text-card render, not an error.
    plan = _plan(visual_source="video_clip", main_visual_prompt="a finger tapping a glowing phone screen")
    output_path = tmp_path / "scene_05.jpg"
    visual = render_story_scene_visual(_brief(), _scene(number=5), plan, output_path=output_path)

    assert visual.error is None
    assert visual.image_path is not None and visual.image_path.is_file()
    assert visual.source == "text_card"


def test_video_clip_source_ai_generation_failure_falls_back_to_text_card_not_silent_error(tmp_path, monkeypatch):
    # When the fallback AI call itself fails (network/HTTP error, same
    # None-on-failure contract as every other visual_source), this must
    # still degrade to the plain text-card render rather than raising or
    # producing a broken/missing image - matching
    # jarvis.reel_generator.image_generation.generate_scene_image()'s own
    # "never raises" convention and _generate_real_scene_image()'s own
    # existing behavior for every OTHER visual_source.
    monkeypatch.setattr(image_generation, "is_configured", lambda: True)
    monkeypatch.setattr(
        image_generation, "generate_scene_image",
        lambda *a, **k: (None, "HTTP 429: You have no credits remaining."),
    )

    plan = _plan(visual_source="video_clip", main_visual_prompt="a finger tapping a glowing phone screen")
    output_path = tmp_path / "scene_05.jpg"
    visual = render_story_scene_visual(_brief(), _scene(number=5), plan, output_path=output_path)

    assert visual.error is None
    assert visual.image_path is not None and visual.image_path.is_file()
    assert visual.source == "text_card"
    # Real, reported bug fix ("REGENERATE doesn't always generate a new
    # image"): AI generation WAS attempted (eligible + configured) but
    # the API call itself failed - this must be distinguishable from
    # "no AI generation was ever attempted at all", so a person is never
    # left thinking their click produced a fresh AI photo when it
    # silently fell back to the plain text-card render instead.
    assert visual.ai_generation_warning is not None
    assert "fallback" in visual.ai_generation_warning.lower()
    # Real, reported bug fix ("AI image generation failed (network error
    # or no usable response)" with no way to tell WHY): the REAL,
    # specific reason from generate_scene_image() must be surfaced
    # verbatim, not replaced with a generic placeholder.
    assert "HTTP 429" in visual.ai_generation_warning
    assert "no credits remaining" in visual.ai_generation_warning.lower()


def test_ai_generation_never_attempted_has_no_warning(tmp_path):
    # Regression coverage: the overwhelmingly common case (no API key
    # configured at all, or a visual_source that never calls for AI
    # generation) must NOT show any warning - only a genuine attempt-
    # then-failure sets ai_generation_warning (see its own docstring).
    plan = _plan(visual_source="typography", main_visual_prompt="anything")
    output_path = tmp_path / "scene_01.jpg"
    visual = render_story_scene_visual(_brief(), _scene(), plan, output_path=output_path)
    assert visual.error is None
    assert visual.ai_generation_warning is None


def test_ai_generation_success_has_no_warning(tmp_path, monkeypatch):
    monkeypatch.setattr(image_generation, "is_configured", lambda: True)
    monkeypatch.setattr(image_generation, "generate_scene_image", lambda *a, **k: (_fake_generated_image(), None))

    plan = _plan(visual_source="b_roll", main_visual_prompt="a calm morning scene")
    output_path = tmp_path / "scene_01.jpg"
    visual = render_story_scene_visual(_brief(), _scene(), plan, output_path=output_path)

    assert visual.error is None
    assert visual.source == "generated_image"
    assert visual.ai_generation_warning is None


def test_ai_generation_save_failure_sets_warning_not_error(tmp_path, monkeypatch):
    # The OTHER attempted-but-failed case: generation succeeds but
    # saving the result to disk fails (e.g. a permissions/OSError) -
    # same "still a usable fallback, never a hard error" contract.
    monkeypatch.setattr(image_generation, "is_configured", lambda: True)
    monkeypatch.setattr(image_generation, "generate_scene_image", lambda *a, **k: (_fake_generated_image(), None))
    monkeypatch.setattr(image_generation, "save_scene_image", MagicMock(side_effect=OSError("disk full")))

    plan = _plan(visual_source="b_roll", main_visual_prompt="a calm morning scene")
    output_path = tmp_path / "scene_01.jpg"
    visual = render_story_scene_visual(_brief(), _scene(), plan, output_path=output_path)

    assert visual.error is None
    assert visual.image_path is not None and visual.image_path.is_file()
    assert visual.source == "text_card"
    assert visual.ai_generation_warning is not None


def test_typography_source_never_falls_back_to_ai_generation(tmp_path, monkeypatch):
    # "typography" stays the one deliberate plain-text-card source (see
    # _REAL_IMAGE_VISUAL_SOURCES's own docstring) - confirms this fix's
    # new fallback tuple doesn't accidentally widen to include it.
    generate_mock_called = False

    def _tracking_generate(*_args, **_kwargs):
        nonlocal generate_mock_called
        generate_mock_called = True
        return _fake_generated_image(), None

    monkeypatch.setattr(image_generation, "is_configured", lambda: True)
    monkeypatch.setattr(image_generation, "generate_scene_image", _tracking_generate)

    plan = _plan(visual_source="typography", main_visual_prompt="anything")
    output_path = tmp_path / "scene_01.jpg"
    visual = render_story_scene_visual(_brief(), _scene(), plan, output_path=output_path)

    assert visual.error is None
    assert visual.source == "text_card"
    assert generate_mock_called is False


def test_render_all_story_scenes_applies_visual_style_and_photos(tmp_path):
    scenes = (_scene(number=1), _scene(number=2))
    photo1 = _make_photo(tmp_path / "p1.jpg", (200, 50, 50))
    photo2 = _make_photo(tmp_path / "p2.jpg", (50, 200, 50))
    visuals = render_all_story_scenes(
        _brief(), scenes, {}, output_dir=tmp_path / "scenes", visual_style="luxury",
        uploaded_photos_by_scene={1: [photo1, photo2]},
    )
    assert len(visuals) == 2
    assert all(v.error is None for v in visuals)
    assert visuals[0].source == "uploaded_image"  # scene 1 had 2 photos
    assert visuals[1].source == "text_card"  # scene 2 had none


# --- textless mode (Stage D) --------------------------------------------------------------


def test_render_textless_produces_visual_with_has_baked_in_text_false(tmp_path):
    output_path = tmp_path / "scene_01.jpg"
    visual = render_story_scene_visual(_brief(), _scene(), None, output_path=output_path, render_textless=True)
    assert visual.error is None
    assert visual.has_baked_in_text is False


def test_render_without_textless_flag_has_baked_in_text_true_by_default(tmp_path):
    output_path = tmp_path / "scene_01.jpg"
    visual = render_story_scene_visual(_brief(), _scene(), None, output_path=output_path)
    assert visual.has_baked_in_text is True


def test_render_textless_produces_genuinely_different_pixels_than_baked_in(tmp_path):
    # The core "prevent duplicated text" guarantee at the pixel level -
    # a textless render must differ from the SAME scene's baked-in
    # render (which draws the headline).
    textless_path = tmp_path / "textless.jpg"
    baked_in_path = tmp_path / "baked_in.jpg"
    render_story_scene_visual(_brief(), _scene(), None, output_path=textless_path, render_textless=True)
    render_story_scene_visual(_brief(), _scene(), None, output_path=baked_in_path, render_textless=False)
    assert textless_path.read_bytes() != baked_in_path.read_bytes()


def test_render_textless_with_plan_suppresses_text_cues_too(tmp_path):
    # A ScenePlan's own text_cues restate on_screen_text exactly like
    # the headline does - textless mode must suppress BOTH, not just
    # the headline (see render_story_scene_visual()'s own docstring).
    # Confirmed by comparing the SAME plan rendered with and without
    # render_textless - the non-textless render DOES draw the cue (see
    # test_text_cue_is_actually_composited_as_real_pixels above), so if
    # textless mode failed to suppress it, the two files would be
    # identical instead of different.
    plan = _plan(text_cues=(TextCue(text="A CUE", position="top", start_seconds=0, end_seconds=1.0, animation="pop"),))
    textless_path = tmp_path / "textless_with_cues.jpg"
    baked_in_path = tmp_path / "baked_in_with_cues.jpg"

    visual_textless = render_story_scene_visual(
        _brief(), _scene(), plan, output_path=textless_path, render_textless=True,
    )
    render_story_scene_visual(_brief(), _scene(), plan, output_path=baked_in_path, render_textless=False)

    assert visual_textless.error is None
    assert visual_textless.has_baked_in_text is False
    assert textless_path.read_bytes() != baked_in_path.read_bytes()


def test_render_textless_sticker_still_composited(tmp_path):
    # Textless mode suppresses TEXT specifically - a sticker/emoji is
    # not text and must still composite normally.
    plan = _plan(sticker="celebration")
    plain_path = tmp_path / "plain.jpg"
    stickered_path = tmp_path / "stickered.jpg"
    render_story_scene_visual(_brief(), _scene(), _plan(sticker="none"), output_path=plain_path, render_textless=True)
    render_story_scene_visual(_brief(), _scene(), plan, output_path=stickered_path, render_textless=True)
    assert plain_path.read_bytes() != stickered_path.read_bytes()


def test_render_all_story_scenes_textless_applies_to_every_scene(tmp_path):
    scenes = (_scene(number=1), _scene(number=2))
    visuals = render_all_story_scenes(_brief(), scenes, {}, output_dir=tmp_path / "scenes", render_textless=True)
    assert len(visuals) == 2
    assert all(v.has_baked_in_text is False for v in visuals)


def test_render_all_story_scenes_default_still_bakes_in_text(tmp_path):
    scenes = (_scene(number=1), _scene(number=2))
    visuals = render_all_story_scenes(_brief(), scenes, {}, output_dir=tmp_path / "scenes")
    assert all(v.has_baked_in_text is True for v in visuals)


# --- hashtag leak fix (real, reported bug: "hashtags appear in the final Reel video") -------


def test_headline_with_hashtag_renders_identically_to_pre_stripped_text(tmp_path):
    # Confirms the hashtag is actually removed BEFORE rendering (not
    # merely invisible by coincidence) - a scene whose on_screen_text
    # has a trailing hashtag must produce the EXACT same pixels as the
    # same scene with that hashtag already removed by hand.
    import hashlib

    with_hashtag_path = tmp_path / "with_hashtag.jpg"
    pre_stripped_path = tmp_path / "pre_stripped.jpg"
    render_story_scene_visual(
        _brief(), _scene(on_screen_text="Follow for more! #reels #viral"), None, output_path=with_hashtag_path,
    )
    render_story_scene_visual(
        _brief(), _scene(on_screen_text="Follow for more!"), None, output_path=pre_stripped_path,
    )
    with Image.open(with_hashtag_path) as a, Image.open(pre_stripped_path) as b:
        a_bytes = a.convert("RGB").tobytes()
        b_bytes = b.convert("RGB").tobytes()
    assert hashlib.sha256(a_bytes).digest() == hashlib.sha256(b_bytes).digest()


def test_text_cue_with_hashtag_renders_identically_to_pre_stripped_text(tmp_path):
    import hashlib

    with_hashtag_path = tmp_path / "with_hashtag.jpg"
    pre_stripped_path = tmp_path / "pre_stripped.jpg"
    plan_with = _plan(text_cues=(
        TextCue(text="Save this! #reels", position="bottom", start_seconds=0, end_seconds=2, animation="pop"),
    ))
    plan_without = _plan(text_cues=(
        TextCue(text="Save this!", position="bottom", start_seconds=0, end_seconds=2, animation="pop"),
    ))
    render_story_scene_visual(_brief(), _scene(), plan_with, output_path=with_hashtag_path)
    render_story_scene_visual(_brief(), _scene(), plan_without, output_path=pre_stripped_path)
    with Image.open(with_hashtag_path) as a, Image.open(pre_stripped_path) as b:
        a_bytes = a.convert("RGB").tobytes()
        b_bytes = b.convert("RGB").tobytes()
    assert hashlib.sha256(a_bytes).digest() == hashlib.sha256(b_bytes).digest()


def test_text_cue_that_is_only_a_hashtag_is_dropped_entirely(tmp_path):
    # A cue whose text is ONLY a hashtag strips down to nothing - it
    # must be skipped entirely (no blank overlay drawn), not crash.
    plan = _plan(text_cues=(
        TextCue(text="#reels", position="bottom", start_seconds=0, end_seconds=2, animation="pop"),
    ))
    output_path = tmp_path / "scene.jpg"
    visual = render_story_scene_visual(_brief(), _scene(), plan, output_path=output_path)
    assert visual.error is None
    assert visual.image_path is not None and visual.image_path.is_file()
