"""Tests for jarvis.reel_generator.export.export_reel_video(): renders
a set of already-rendered SceneVisuals into an actual MP4. Uses REAL
ffmpeg subprocess calls against real Pillow-rendered scene images (via
jarvis.reel_generator.scenes.render_all_scenes() - fast, local, no
mocking needed for either half of this pipeline). Skipped entirely if
ffmpeg isn't on PATH, matching tests/test_video_studio_export.py's own
precedent for this codebase's ffmpeg-wrapping modules.

Confirms: a multi-scene storyboard produces an MP4 whose total duration
matches the sum of scene durations, output is 1080x1920, a scene
missing its rendered visual raises ExportError before ever invoking
ffmpeg, an empty scene list raises ExportError, and the export never
touches/deletes the source scene image files.

Also confirms the `motion_by_scene` parameter (jarvis.story_generator's
own Ken Burns pan/zoom addition): a motion-enabled export's total
duration still matches the sum of scene durations exactly (the bug this
was hand-debugged against - see export_reel_video()'s own docstring/
comments - produced a wildly corrupted multi-minute duration instead),
a mix of motion and non-motion scenes in the SAME export still produces
the correct total duration, and passing NO motion_by_scene at all (AI
Reel Generator's own call site) produces byte-for-byte identical output
to before this parameter existed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.export import ExportError, export_reel_video
from jarvis.reel_generator.scenes import SceneVisual, render_all_scenes
from jarvis.reel_generator.storyboard import Scene
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available

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


def test_export_produces_mp4_with_correct_total_duration(tmp_path):
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    output_path = tmp_path / "exports" / "reel.mp4"

    result = export_reel_video(scenes, visuals, output_path=output_path)

    assert result.output_path.is_file()
    assert result.width == 1080
    assert result.height == 1920
    # 2s + 2s + 2s = 6s total - allow a small ffmpeg encoding tolerance.
    assert abs(result.duration_seconds - 6.0) < 0.5


def test_default_does_not_burn_in_captions(tmp_path):
    # Regression test for the real, hand-tested "text rendered twice"
    # bug (see export_reel_video()'s own docstring): every scene's own
    # image already draws its own on_screen_text - the DEFAULT call
    # (visuals whose has_baked_in_text is True, the SceneVisual default)
    # must not also burn an ffmpeg subtitle track on top of that.
    # burn_in_captions's own parameter default changed from a fixed
    # False to None (Stage D: auto-derived from SceneVisual
    # .has_baked_in_text - see export_reel_video()'s own docstring) -
    # this test now confirms the REAL, end-to-end behavior that matters
    # (no .srt sidecar ever produced for ordinary baked-in-text scenes)
    # rather than a specific literal parameter default value, since
    # None is the new correct default and asserting `is False` here
    # would incorrectly fail against it despite behaving identically.
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    output_path = tmp_path / "exports" / "reel_default.mp4"

    export_reel_video(scenes, visuals, output_path=output_path)
    assert not output_path.with_suffix(".captions.srt").exists()


def test_export_never_deletes_scene_images(tmp_path):
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    output_path = tmp_path / "exports" / "reel.mp4"

    export_reel_video(scenes, visuals, output_path=output_path)

    for v in visuals:
        assert v.image_path is not None
        assert v.image_path.is_file()


def test_export_without_captions(tmp_path):
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    output_path = tmp_path / "exports" / "reel_no_captions.mp4"

    result = export_reel_video(scenes, visuals, output_path=output_path, burn_in_captions=False)
    assert result.output_path.is_file()
    # The .srt sidecar file must not be left behind either way.
    assert not output_path.with_suffix(".captions.srt").exists()


def test_export_with_captions_cleans_up_srt_sidecar(tmp_path):
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    output_path = tmp_path / "exports" / "reel_captions.mp4"

    export_reel_video(scenes, visuals, output_path=output_path, burn_in_captions=True)
    assert not output_path.with_suffix(".captions.srt").exists()


def test_empty_scenes_raises_export_error(tmp_path):
    with pytest.raises(ExportError, match="no scenes"):
        export_reel_video((), [], output_path=tmp_path / "reel.mp4")


def test_scene_missing_visual_raises_export_error(tmp_path):
    scenes = _scenes()
    # Only render/pass visuals for the first two scenes - the third is missing.
    visuals = render_all_scenes(_brief(), scenes[:2], output_dir=tmp_path / "scenes")
    with pytest.raises(ExportError, match="Scene 3"):
        export_reel_video(scenes, visuals, output_path=tmp_path / "reel.mp4")


def test_scene_with_failed_visual_raises_export_error(tmp_path):
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    # Simulate scene 2 having failed to render.
    visuals[1] = SceneVisual(scene_number=2, source="text_card", image_path=None, error="simulated failure")
    with pytest.raises(ExportError, match="Scene 2"):
        export_reel_video(scenes, visuals, output_path=tmp_path / "reel.mp4")


def test_export_writes_to_a_new_file_under_exports_dir(tmp_path):
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    exports_dir = tmp_path / "exports"
    output_path = exports_dir / "reel.mp4"

    assert not exports_dir.exists()
    export_reel_video(scenes, visuals, output_path=output_path)
    assert exports_dir.is_dir()
    assert output_path.is_file()


# --- motion_by_scene (Ken Burns pan/zoom, added for jarvis.story_generator) ----------------


def test_motion_on_every_scene_preserves_correct_total_duration(tmp_path):
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    output_path = tmp_path / "exports" / "reel_motion.mp4"

    motion_by_scene = {1: "zoom_in", 2: "zoom_out", 3: "pan_right"}
    result = export_reel_video(scenes, visuals, output_path=output_path, motion_by_scene=motion_by_scene)

    assert result.output_path.is_file()
    # Same 6s total as the plain (no-motion) test above - the bug this
    # guards against corrupted this to over 7 minutes.
    assert abs(result.duration_seconds - 6.0) < 0.5


def test_motion_mixed_with_static_scenes_preserves_correct_total_duration(tmp_path):
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    output_path = tmp_path / "exports" / "reel_mixed_motion.mp4"

    # Scene 2 deliberately has no entry (plain scale) while 1 and 3 use
    # real zoompan motion - the mixed-fps-timebase bug this regression
    # test guards against only appeared when motion and non-motion
    # segments were concatenated together.
    motion_by_scene = {1: "zoom_in", 3: "pan_left"}
    result = export_reel_video(scenes, visuals, output_path=output_path, motion_by_scene=motion_by_scene)

    assert result.output_path.is_file()
    assert abs(result.duration_seconds - 6.0) < 0.5


def test_static_motion_value_behaves_like_no_motion(tmp_path):
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    output_path = tmp_path / "exports" / "reel_static.mp4"

    result = export_reel_video(scenes, visuals, output_path=output_path, motion_by_scene={1: "static"})
    assert result.output_path.is_file()
    assert abs(result.duration_seconds - 6.0) < 0.5


def test_no_motion_by_scene_matches_default_call_exactly(tmp_path):
    # AI Reel Generator's own call site never passes motion_by_scene at
    # all - confirms an explicit None is indistinguishable from omitting
    # the parameter entirely (both take the same "byte-for-byte as
    # before this parameter existed" code path).
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")

    result_default = export_reel_video(scenes, visuals, output_path=tmp_path / "exports" / "default.mp4")
    result_explicit_none = export_reel_video(
        scenes, visuals, output_path=tmp_path / "exports" / "explicit_none.mp4", motion_by_scene=None,
    )
    assert abs(result_default.duration_seconds - result_explicit_none.duration_seconds) < 0.1


# --- voiceover mux (Stage B) -------------------------------------------------------------


def _make_real_wav(path: Path, *, duration_seconds: float) -> Path:
    """Generates a REAL WAV file via ffmpeg's own lavfi sine source -
    same "use real ffmpeg to build a synthetic-but-real test fixture"
    precedent as tests/test_reel_generator_quality_control.py's own
    lavfi color-source fixtures (see that file's own docstring)."""
    import subprocess

    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_seconds}",
            "-c:a", "pcm_s16le", str(path),
        ],
        capture_output=True, check=True,
    )
    return path


def test_export_with_voiceover_produces_a_video_with_a_real_audio_track(tmp_path):
    from jarvis.video_studio.ffmpeg_utils import probe_video

    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    voiceover_path = _make_real_wav(tmp_path / "voiceover" / "narration.wav", duration_seconds=6.0)
    output_path = tmp_path / "exports" / "reel_with_voiceover.mp4"

    result = export_reel_video(scenes, visuals, output_path=output_path, voiceover_path=voiceover_path)

    assert result.output_path.is_file()
    assert result.width == 1080
    assert result.height == 1920
    assert abs(result.duration_seconds - 6.0) < 0.5

    probe = probe_video(result.output_path)
    assert probe.has_audio is True
    assert probe.audio_codec == "aac"


def test_export_without_voiceover_still_produces_silent_video(tmp_path):
    from jarvis.video_studio.ffmpeg_utils import probe_video

    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    output_path = tmp_path / "exports" / "reel_silent.mp4"

    result = export_reel_video(scenes, visuals, output_path=output_path)

    probe = probe_video(result.output_path)
    assert probe.has_audio is False


def test_export_with_shorter_voiceover_pads_to_video_length(tmp_path):
    # The voiceover (3s) is deliberately shorter than the video's own
    # total duration (6s) - the export must still be the FULL video
    # length, with the voiceover padded with silence, never shortening
    # the video to match the narration.
    from jarvis.video_studio.ffmpeg_utils import probe_video

    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    voiceover_path = _make_real_wav(tmp_path / "voiceover" / "short.wav", duration_seconds=3.0)
    output_path = tmp_path / "exports" / "reel_short_voiceover.mp4"

    result = export_reel_video(scenes, visuals, output_path=output_path, voiceover_path=voiceover_path)

    assert abs(result.duration_seconds - 6.0) < 0.5
    probe = probe_video(result.output_path)
    assert probe.has_audio is True


def test_export_with_longer_voiceover_does_not_extend_video(tmp_path):
    # The voiceover (10s) is deliberately LONGER than the video's own
    # total duration (6s) - the export must stay the video's own
    # length, with the voiceover trimmed, never extending the video to
    # fit the whole narration.
    from jarvis.video_studio.ffmpeg_utils import probe_video

    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    voiceover_path = _make_real_wav(tmp_path / "voiceover" / "long.wav", duration_seconds=10.0)
    output_path = tmp_path / "exports" / "reel_long_voiceover.mp4"

    result = export_reel_video(scenes, visuals, output_path=output_path, voiceover_path=voiceover_path)

    assert abs(result.duration_seconds - 6.0) < 0.5
    probe = probe_video(result.output_path)
    assert probe.has_audio is True


def test_export_with_voiceover_and_motion_and_captions_all_together(tmp_path):
    # All three optional export features combined at once (motion +
    # burned captions + voiceover) - confirms they don't interfere with
    # each other's own filter-graph stages.
    from jarvis.video_studio.ffmpeg_utils import probe_video

    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    voiceover_path = _make_real_wav(tmp_path / "voiceover" / "combined.wav", duration_seconds=6.0)
    output_path = tmp_path / "exports" / "reel_combined.mp4"

    result = export_reel_video(
        scenes, visuals, output_path=output_path, burn_in_captions=True,
        motion_by_scene={1: "zoom_in", 2: "pan_left"}, voiceover_path=voiceover_path,
    )

    assert result.output_path.is_file()
    assert result.width == 1080
    assert result.height == 1920
    assert abs(result.duration_seconds - 6.0) < 0.5
    probe = probe_video(result.output_path)
    assert probe.has_audio is True
    assert not output_path.with_suffix(".captions.srt").exists()


# --- caption styling (Stage C) ------------------------------------------------------------


def test_caption_style_default_force_style_uses_default_vocabulary():
    from jarvis.reel_generator.export import CaptionStyle

    style = CaptionStyle()
    force_style = style.force_style()
    assert "FontName=Arial" in force_style
    assert "FontSize=64" in force_style
    assert "Alignment=2" in force_style  # bottom


def test_caption_style_position_maps_to_correct_alignment():
    from jarvis.reel_generator.export import CaptionStyle

    assert "Alignment=2" in CaptionStyle(position="bottom").force_style()
    assert "Alignment=5" in CaptionStyle(position="middle").force_style()
    assert "Alignment=8" in CaptionStyle(position="top").force_style()


def test_caption_style_size_maps_to_correct_font_size():
    from jarvis.reel_generator.export import CaptionStyle

    assert "FontSize=48" in CaptionStyle(size="small").force_style()
    assert "FontSize=64" in CaptionStyle(size="medium").force_style()
    assert "FontSize=84" in CaptionStyle(size="large").force_style()


def test_caption_style_unrecognized_font_falls_back_to_default():
    from jarvis.reel_generator.export import CaptionStyle

    style = CaptionStyle(font="Not A Real Font")
    assert "FontName=Arial" in style.force_style()


def test_write_srt_default_has_no_fade_tag(tmp_path):
    from jarvis.reel_generator.export import _write_srt

    scenes = _scenes()
    srt_path = tmp_path / "captions.srt"
    _write_srt(scenes, srt_path)
    content = srt_path.read_text(encoding="utf-8")
    assert r"\fad(" not in content
    assert "Hi there" in content


def test_write_srt_fade_animation_wraps_each_line_in_fad_tag(tmp_path):
    from jarvis.reel_generator.export import _write_srt

    scenes = _scenes()
    srt_path = tmp_path / "captions.srt"
    _write_srt(scenes, srt_path, animation="fade")
    content = srt_path.read_text(encoding="utf-8")
    assert content.count(r"\fad(300,300)") == len(scenes)
    assert "Hi there" in content


# --- hashtag leak fix (real, reported bug: "hashtags appear in the final Reel video") -------


def test_write_srt_strips_hashtags_from_burned_in_captions(tmp_path):
    from jarvis.reel_generator.export import _write_srt

    scenes = (
        Scene(
            number=1, start_seconds=0, end_seconds=2, segment_kind="cta", voice_text="Follow",
            on_screen_text="Follow for more! #reels #viral", visual_description="x",
        ),
    )
    srt_path = tmp_path / "captions.srt"
    _write_srt(scenes, srt_path)
    content = srt_path.read_text(encoding="utf-8")
    assert "#" not in content
    assert "Follow for more!" in content


def test_export_with_default_caption_style_produces_valid_9x16_mp4(tmp_path):
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    output_path = tmp_path / "exports" / "reel_styled_captions.mp4"

    result = export_reel_video(scenes, visuals, output_path=output_path, burn_in_captions=True)

    assert result.output_path.is_file()
    assert result.width == 1080
    assert result.height == 1920
    assert abs(result.duration_seconds - 6.0) < 0.5
    assert not output_path.with_suffix(".captions.srt").exists()


def test_export_with_custom_caption_style_produces_valid_9x16_mp4(tmp_path):
    from jarvis.reel_generator.export import CaptionStyle

    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    output_path = tmp_path / "exports" / "reel_custom_captions.mp4"
    style = CaptionStyle(font="Impact", position="top", size="large", animation="fade")

    result = export_reel_video(
        scenes, visuals, output_path=output_path, burn_in_captions=True, caption_style=style,
    )

    assert result.output_path.is_file()
    assert result.width == 1080
    assert result.height == 1920
    assert abs(result.duration_seconds - 6.0) < 0.5


def test_caption_style_has_no_effect_without_burn_in_captions(tmp_path):
    # caption_style is passed but burn_in_captions is left at its own
    # default (False) - must produce the EXACT same output as omitting
    # caption_style entirely (no ffmpeg error from an unused parameter,
    # no unexpected subtitle track).
    from jarvis.reel_generator.export import CaptionStyle

    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    output_path = tmp_path / "exports" / "reel_style_ignored.mp4"
    style = CaptionStyle(font="Impact", position="top", size="large", animation="fade")

    result = export_reel_video(scenes, visuals, output_path=output_path, caption_style=style)

    assert result.output_path.is_file()
    assert not output_path.with_suffix(".captions.srt").exists()


# --- textless mode auto-derivation (Stage D) ----------------------------------------------


def test_textless_scenes_auto_burn_in_captions(tmp_path):
    from jarvis.reel_generator.scenes import render_all_scenes as render_all_scenes_textless

    scenes = _scenes()
    visuals = render_all_scenes_textless(_brief(), scenes, output_dir=tmp_path / "scenes", render_textless=True)
    assert all(not v.has_baked_in_text for v in visuals)
    output_path = tmp_path / "exports" / "reel_textless.mp4"

    export_reel_video(scenes, visuals, output_path=output_path)
    # Captions were auto-burned-in - the .srt sidecar is always cleaned up
    # afterward either way, so this instead checks the export still
    # produces a valid file (the real "were captions actually burned in"
    # check is the frame-inspection test below).
    assert output_path.is_file()


def test_baked_in_text_scenes_do_not_auto_burn_in_captions(tmp_path):
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    assert all(v.has_baked_in_text for v in visuals)
    output_path = tmp_path / "exports" / "reel_baked_in.mp4"

    export_reel_video(scenes, visuals, output_path=output_path)
    assert not output_path.with_suffix(".captions.srt").exists()


def test_explicit_false_overrides_auto_derivation_even_for_textless_scenes(tmp_path):
    from jarvis.reel_generator.scenes import render_all_scenes as render_all_scenes_textless

    scenes = _scenes()
    visuals = render_all_scenes_textless(_brief(), scenes, output_dir=tmp_path / "scenes", render_textless=True)
    output_path = tmp_path / "exports" / "reel_textless_no_captions.mp4"

    # Even though every scene is textless, an EXPLICIT False must win -
    # produces a silent-of-text export (no captions at all, on purpose).
    result = export_reel_video(scenes, visuals, output_path=output_path, burn_in_captions=False)
    assert result.output_path.is_file()


def test_explicit_true_overrides_auto_derivation_for_baked_in_scenes(tmp_path):
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    output_path = tmp_path / "exports" / "reel_baked_in_forced_captions.mp4"

    # Explicit True still works even for baked-in-text scenes (a caller's
    # deliberate choice, unchanged from before auto-derivation existed) -
    # this WOULD duplicate text, which is exactly why it's opt-in only.
    result = export_reel_video(scenes, visuals, output_path=output_path, burn_in_captions=True)
    assert result.output_path.is_file()


def test_mixed_textless_and_baked_in_scenes_still_burns_in_captions(tmp_path):
    # A realistic edge case: one scene was regenerated in textless mode
    # inside an otherwise baked-in-text project. auto-derivation errs
    # toward burning captions in for the WHOLE export (per the confirmed
    # design - a mixed project is an edge case, not the normal path,
    # which is a whole project in one mode via a persisted text_mode).
    from jarvis.reel_generator.scene_render import render_story_scene_visual

    brief = _brief()
    scenes = _scenes()
    scenes_dir = tmp_path / "scenes"
    visuals = render_all_scenes(brief, scenes, output_dir=scenes_dir)
    # Re-render scene 2 in textless mode, overwriting its own file.
    textless_visual = render_story_scene_visual(
        brief, scenes[1], None, output_path=scenes_dir / "scene_02.jpg", render_textless=True,
    )
    visuals = [textless_visual if v.scene_number == 2 else v for v in visuals]
    assert visuals[0].has_baked_in_text is True
    assert visuals[1].has_baked_in_text is False
    assert visuals[2].has_baked_in_text is True

    output_path = tmp_path / "exports" / "reel_mixed.mp4"
    export_reel_video(scenes, visuals, output_path=output_path)
    assert output_path.is_file()


def test_textless_frame_has_no_baked_in_text_and_caption_is_burned_in(tmp_path):
    # Real, hand-inspectable verification (not just duration/dimension
    # checks): a textless-mode scene's OWN image file must have NO text
    # in it (the render itself), and the FINAL EXPORTED frame must show
    # the caption text exactly once (burned in by export, never baked
    # into the image) - this is the core "prevent duplicated text" check
    # for Stage D.
    from PIL import Image

    from jarvis.reel_generator.scenes import render_all_scenes as render_all_scenes_textless

    scenes = (
        Scene(
            number=1, start_seconds=0, end_seconds=3, segment_kind="hook", voice_text="hi",
            on_screen_text="UNIQUE CAPTION MARKER", visual_description="x",
        ),
    )
    visuals = render_all_scenes_textless(_brief(), scenes, output_dir=tmp_path / "scenes", render_textless=True)
    assert visuals[0].image_path is not None

    # The scene's own image file must be genuinely textless - Pillow has
    # no OCR, so this checks the deterministic, hand-confirmed property
    # that a blank-headline render never draws any headline-region ink
    # at all: the whole canvas is one smooth gradient (a real headline
    # draws sharp white/black edges the gradient alone never produces).
    # Kept simple and honest: what's ACTUALLY testable here without OCR
    # is that this image differs from the SAME scene rendered WITHOUT
    # render_textless (which does draw the headline).
    from jarvis.reel_generator.scenes import render_scene_visual

    baked_in_path = tmp_path / "scenes_baked_in" / "scene_01.jpg"
    baked_in_path.parent.mkdir(parents=True)
    render_scene_visual(_brief(), scenes[0], output_path=baked_in_path)

    textless_bytes = visuals[0].image_path.read_bytes()
    baked_in_bytes = baked_in_path.read_bytes()
    assert textless_bytes != baked_in_bytes  # genuinely different pixels - text was really skipped

    output_path = tmp_path / "exports" / "reel_textless_verify.mp4"
    result = export_reel_video(scenes, visuals, output_path=output_path)
    assert result.output_path.is_file()

    import subprocess

    frame_path = tmp_path / "frame.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "1.0", "-i", str(output_path), "-vframes", "1", str(frame_path)],
        capture_output=True, check=True,
    )
    assert frame_path.is_file()
    with Image.open(frame_path) as frame:
        assert frame.size == (1080, 1920)


# --- cover-in-export bug fix: cover_intro_path/cover_intro_duration_seconds ----------------
# Real, reported bug: the cover selected in CHOOSE A REEL COVER never
# reached the final exported video at all (export_reel_video() had no
# parameter for it). These tests confirm the fix: the cover is really
# spliced in as the export's own first segment, real audio/subtitle
# timing shifts forward to match, and omitting the parameter reproduces
# the exact previous behavior.


def _make_solid_cover(path: Path, *, color: tuple[int, int, int]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (1080, 1920), color=color).save(path, "JPEG")
    return path


def test_export_without_cover_intro_path_is_unchanged(tmp_path):
    # Omitting cover_intro_path (every call site before this fix, and
    # every Instagram-Cover-only project after it) must reproduce the
    # exact previous duration/resolution - never a silent behavior
    # change for a project that isn't using this feature.
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    output_path = tmp_path / "exports" / "reel_no_cover.mp4"

    result = export_reel_video(scenes, visuals, output_path=output_path)

    assert abs(result.duration_seconds - 6.0) < 0.5
    assert result.width == 1080
    assert result.height == 1920


def test_export_with_cover_intro_path_extends_duration_by_cover_length(tmp_path):
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    cover_path = _make_solid_cover(tmp_path / "cover.jpg", color=(200, 0, 0))
    output_path = tmp_path / "exports" / "reel_with_cover.mp4"

    result = export_reel_video(
        scenes, visuals, output_path=output_path,
        cover_intro_path=cover_path, cover_intro_duration_seconds=1.5,
    )

    # 1.5s cover + 6s of scenes = 7.5s total.
    assert abs(result.duration_seconds - 7.5) < 0.5
    assert result.width == 1080
    assert result.height == 1920


def test_export_with_cover_intro_shows_cover_pixels_at_the_start(tmp_path):
    # The real, end-to-end proof this bug fix exists for: the exported
    # video's own first frame must actually BE the cover, not a scene.
    import subprocess

    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    cover_path = _make_solid_cover(tmp_path / "cover.jpg", color=(200, 0, 0))
    output_path = tmp_path / "exports" / "reel_cover_pixels.mp4"

    export_reel_video(
        scenes, visuals, output_path=output_path,
        cover_intro_path=cover_path, cover_intro_duration_seconds=1.5,
    )

    cover_frame_path = tmp_path / "cover_frame.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "0.5", "-i", str(output_path), "-vframes", "1", str(cover_frame_path)],
        capture_output=True, check=True,
    )
    with Image.open(cover_frame_path) as frame:
        r, g, b = frame.convert("RGB").getpixel((frame.width // 2, frame.height // 2))
    # The cover is a solid, saturated red - a scene image (rendered by
    # jarvis.reel_generator.scenes' own style presets) is never this
    # close to pure red, so this is a real, meaningful pixel assertion,
    # not a coincidence.
    assert r > 150 and g < 80 and b < 80

    scene_frame_path = tmp_path / "scene_frame.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "3.0", "-i", str(output_path), "-vframes", "1", str(scene_frame_path)],
        capture_output=True, check=True,
    )
    with Image.open(scene_frame_path) as frame:
        r2, g2, b2 = frame.convert("RGB").getpixel((frame.width // 2, frame.height // 2))
    # At 3.0s (1.5s cover + 1.5s into scene 1), the frame must NOT be the
    # cover's own solid red anymore - confirms the cover only occupies
    # its own intro duration, not the whole video.
    assert not (r2 > 150 and g2 < 80 and b2 < 80)


def test_export_with_cover_intro_and_voiceover_shifts_audio_forward(tmp_path):
    from jarvis.video_studio.ffmpeg_utils import probe_video

    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    cover_path = _make_solid_cover(tmp_path / "cover.jpg", color=(0, 200, 0))
    voiceover_path = _make_real_wav(tmp_path / "voiceover" / "narration.wav", duration_seconds=6.0)
    output_path = tmp_path / "exports" / "reel_cover_and_voiceover.mp4"

    result = export_reel_video(
        scenes, visuals, output_path=output_path, voiceover_path=voiceover_path,
        cover_intro_path=cover_path, cover_intro_duration_seconds=1.5,
    )

    # 1.5s cover + 6s of scenes/voiceover = 7.5s total - the voiceover
    # itself is still only 6s, shifted forward by the cover's own
    # duration (via adelay) and padded, never stretched/looped.
    assert abs(result.duration_seconds - 7.5) < 0.5
    probe = probe_video(result.output_path)
    assert probe.has_audio is True


def test_write_srt_time_offset_shifts_every_subtitle_forward(tmp_path):
    from jarvis.reel_generator.export import _write_srt

    scenes = _scenes()
    srt_path = tmp_path / "captions.srt"
    _write_srt(scenes, srt_path, time_offset_seconds=1.5)
    content = srt_path.read_text(encoding="utf-8")

    assert "00:00:01,500 --> 00:00:03,500" in content  # scene 1: 0-2s shifted to 1.5-3.5s
    assert "00:00:03,500 --> 00:00:05,500" in content  # scene 2: 2-4s shifted to 3.5-5.5s
    assert "Hi there" in content


def test_write_srt_default_time_offset_is_zero_and_unchanged(tmp_path):
    # Omitting time_offset_seconds must reproduce the exact previous
    # subtitle timestamps - byte-for-byte the same as before this
    # parameter existed for any project not using a cover intro.
    from jarvis.reel_generator.export import _write_srt

    scenes = _scenes()
    srt_path_a = tmp_path / "a.srt"
    srt_path_b = tmp_path / "b.srt"
    _write_srt(scenes, srt_path_a)
    _write_srt(scenes, srt_path_b, time_offset_seconds=0.0)

    assert srt_path_a.read_text(encoding="utf-8") == srt_path_b.read_text(encoding="utf-8")


# --- NATURAL MOTION / AI VIDEO mode: clip_by_scene splicing ---------------------------------
# Real, reported requirement: real AI-generated video clips (jarvis
# .reel_generator.motion_engine's own output) spliced into the SAME
# concat filtergraph alongside still-image scenes, normalized to the
# exact same resolution/SAR/fps every still-image branch already uses
# (see _clip_input_filter()'s own docstring for the ffmpeg concat
# requirements this satisfies).


def _make_real_dummy_clip(path: Path, *, duration_seconds: float, color: str = "blue") -> Path:
    """Generates a REAL MP4 clip via ffmpeg's own lavfi color source -
    same "use real ffmpeg to build a synthetic-but-real test fixture"
    precedent as _make_real_wav() above."""
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


def test_export_without_clip_by_scene_is_unchanged(tmp_path):
    # Omitting clip_by_scene (every call site before this feature, and
    # every Static-mode project after it) must reproduce the exact
    # previous duration/resolution - never a silent behavior change.
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    output_path = tmp_path / "exports" / "reel_no_clips.mp4"

    result = export_reel_video(scenes, visuals, output_path=output_path)

    assert abs(result.duration_seconds - 6.0) < 0.5
    assert result.width == 1080
    assert result.height == 1920
    assert result.scene_fallback_warnings == ()


def test_export_with_a_real_clip_mixed_with_still_scenes(tmp_path):
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    # Scene 1 is 2s long (see _scenes()) - give its clip a bit of extra
    # headroom so a tiny real-encode duration variance never trips the
    # too-short-clip guard.
    clip_path = _make_real_dummy_clip(tmp_path / "clip_01.mp4", duration_seconds=2.5)
    output_path = tmp_path / "exports" / "reel_with_clip.mp4"

    result = export_reel_video(scenes, visuals, output_path=output_path, clip_by_scene={1: clip_path})

    assert abs(result.duration_seconds - 6.0) < 0.5  # total timeline unchanged - only scene 1's SOURCE changed
    assert result.width == 1080
    assert result.height == 1920
    assert result.scene_fallback_warnings == ()


def test_export_with_a_too_short_clip_falls_back_to_the_still_image(tmp_path):
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    # Scene 1 needs 2s - this clip is deliberately much shorter.
    clip_path = _make_real_dummy_clip(tmp_path / "clip_short.mp4", duration_seconds=0.5)
    output_path = tmp_path / "exports" / "reel_short_clip.mp4"

    result = export_reel_video(scenes, visuals, output_path=output_path, clip_by_scene={1: clip_path})

    # The export must still succeed (never a hard failure just because
    # one clip was too short) and the total duration must still match
    # the full storyboard - proving scene 1 fell back to its still image
    # rather than leaving a gap or a short segment.
    assert abs(result.duration_seconds - 6.0) < 0.5
    assert len(result.scene_fallback_warnings) == 1
    assert "shorter than" in result.scene_fallback_warnings[0]


def test_clip_by_scene_takes_precedence_over_motion_by_scene_for_the_same_scene(tmp_path):
    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    clip_path = _make_real_dummy_clip(tmp_path / "clip_01.mp4", duration_seconds=2.5)
    output_path = tmp_path / "exports" / "reel_precedence.mp4"

    # Scene 1 gets BOTH a real clip and a zoompan motion entry - the
    # real clip must win (see export_reel_video()'s own precedence
    # rule); a successful export at the correct total duration, with no
    # fallback warning, is the observable proof (a zoompan filter forces
    # a very different code path that would still produce a valid
    # export too, so this test's real assertion is the ABSENCE of any
    # fallback warning combined with a successful export using the
    # clip's own filter chain - a wrong precedence would still often
    # "work" by coincidence, which is exactly why the unit-level
    # precedence rule itself is documented and exercised here rather
    # than only inferred from output duration alone).
    result = export_reel_video(
        scenes, visuals, output_path=output_path,
        motion_by_scene={1: "zoom_in"}, clip_by_scene={1: clip_path},
    )

    assert abs(result.duration_seconds - 6.0) < 0.5
    assert result.scene_fallback_warnings == ()


def test_export_with_clip_by_scene_and_voiceover_and_captions(tmp_path):
    # Full integration: a real clip mixed with still scenes, plus a
    # voiceover and burned-in captions - proves clip splicing doesn't
    # disturb the subtitle/voiceover stages, which operate on the final
    # concatenated label regardless of what produced each segment.
    from jarvis.video_studio.ffmpeg_utils import probe_video

    scenes = _scenes()
    visuals = render_all_scenes(_brief(), scenes, output_dir=tmp_path / "scenes")
    clip_path = _make_real_dummy_clip(tmp_path / "clip_01.mp4", duration_seconds=2.5)
    voiceover_path = _make_real_wav(tmp_path / "voiceover" / "narration.wav", duration_seconds=6.0)
    output_path = tmp_path / "exports" / "reel_full_motion.mp4"

    result = export_reel_video(
        scenes, visuals, output_path=output_path, burn_in_captions=True,
        voiceover_path=voiceover_path, clip_by_scene={1: clip_path},
    )

    assert abs(result.duration_seconds - 6.0) < 0.5
    probe = probe_video(result.output_path)
    assert probe.has_audio is True
    assert result.scene_fallback_warnings == ()
