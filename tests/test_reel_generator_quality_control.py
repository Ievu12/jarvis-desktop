"""Tests for jarvis.reel_generator.quality_control: pre-export/post-
export quality checks (module brief section 20). check_export_file()
uses REAL ffprobe against real, small ffmpeg-generated test files
(skipped if ffmpeg isn't on PATH) - the other checks are pure Python
logic over dataclasses, no ffmpeg needed.

Confirms: a correctly-sized 1080x1920 export passes resolution/aspect/
duration checks, a wrong-resolution or wrong-aspect export fails with a
clear message, a duration far from the target is a warning (not a
fail), a silent export gets an informational audio note (never a
fail), an unreadable/corrupt file fails gracefully rather than raising,
a scene missing its visual or with a failed visual is a fail with a
specific suggested_action naming that scene number, overly-long
on-screen text for a short scene duration is a warning, a missing/
unreadable cover and a missing caption are flagged, and
QualityReport.passed is false whenever any fail OR warning exists but
true for an info-only report."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from jarvis.reel_generator import quality_control as qc
from jarvis.reel_generator.scenes import SceneVisual
from jarvis.reel_generator.storyboard import Scene
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


@pytest.fixture(scope="module")
def correct_export(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("qc_correct")
    path = out_dir / "correct.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=1080x1920:d=15", "-c:v", "libx264", "-t", "15", str(path)],
        capture_output=True, timeout=30, check=True,
    )
    return path


@pytest.fixture(scope="module")
def wrong_resolution_export(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("qc_wrong_res")
    path = out_dir / "wrong.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=640x480:d=10", "-c:v", "libx264", "-t", "10", str(path)],
        capture_output=True, timeout=30, check=True,
    )
    return path


@pytest.fixture(scope="module")
def with_audio_export(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("qc_with_audio")
    path = out_dir / "with_audio.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=green:s=1080x1920:d=10",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
            "-c:v", "libx264", "-c:a", "aac", "-t", "10", str(path),
        ],
        capture_output=True, timeout=30, check=True,
    )
    return path


# --- check_export_file -----------------------------------------------------------------


def test_correct_export_has_no_fail_or_warning(correct_export):
    issues = qc.check_export_file(correct_export, target_duration_seconds=15)
    assert not any(i.severity == "fail" for i in issues)
    assert not any(i.severity == "warning" for i in issues)


def test_correct_export_gets_silent_audio_info_note(correct_export):
    issues = qc.check_export_file(correct_export)
    audio_issues = [i for i in issues if i.check == "audio"]
    assert len(audio_issues) == 1
    assert audio_issues[0].severity == "info"
    assert "no audio track" in audio_issues[0].message.lower()


def test_export_with_audio_gets_different_info_note(with_audio_export):
    issues = qc.check_export_file(with_audio_export)
    audio_issues = [i for i in issues if i.check == "audio"]
    assert len(audio_issues) == 1
    assert audio_issues[0].severity == "info"
    assert "not performed" in audio_issues[0].message.lower()


def test_wrong_resolution_fails(wrong_resolution_export):
    issues = qc.check_export_file(wrong_resolution_export)
    resolution_issues = [i for i in issues if i.check == "resolution"]
    assert len(resolution_issues) == 1
    assert resolution_issues[0].severity == "fail"
    assert "640x480" in resolution_issues[0].message


def test_duration_far_from_target_is_warning_not_fail(correct_export):
    issues = qc.check_export_file(correct_export, target_duration_seconds=60)
    duration_issues = [i for i in issues if i.check == "duration"]
    assert len(duration_issues) == 1
    assert duration_issues[0].severity == "warning"


def test_duration_close_to_target_has_no_duration_issue(correct_export):
    issues = qc.check_export_file(correct_export, target_duration_seconds=15)
    assert not any(i.check == "duration" for i in issues)


def test_scene_transitions_and_spelling_are_always_info(correct_export):
    issues = qc.check_export_file(correct_export)
    transition_issues = [i for i in issues if i.check == "scene_transitions"]
    spelling_issues = [i for i in issues if i.check == "spelling"]
    assert len(transition_issues) == 1 and transition_issues[0].severity == "info"
    assert len(spelling_issues) == 1 and spelling_issues[0].severity == "info"


def test_unreadable_file_fails_gracefully_not_raise(tmp_path):
    bad_file = tmp_path / "not_a_video.mp4"
    bad_file.write_text("this is not a video file")
    issues = qc.check_export_file(bad_file)
    assert len(issues) == 1
    assert issues[0].severity == "fail"
    assert issues[0].check == "file_integrity"


# --- check_scene_visuals ----------------------------------------------------------------


def _scene(number: int = 1, duration: float = 4.0, on_screen_text: str = "Short text", **overrides: Any) -> Scene:
    defaults: dict[str, Any] = dict(
        number=number, start_seconds=0, end_seconds=duration, segment_kind="hook",
        voice_text="voice", on_screen_text=on_screen_text, visual_description="x",
    )
    defaults.update(overrides)
    return Scene(**defaults)


def test_missing_scene_visual_is_a_fail_with_scene_number():
    scenes = (_scene(number=1),)
    issues = qc.check_scene_visuals(scenes, [])
    assert len(issues) == 1
    assert issues[0].severity == "fail"
    assert issues[0].check == "empty_frames"
    assert issues[0].suggested_action is not None


def test_failed_scene_visual_is_a_fail_naming_the_scene():
    scenes = (_scene(number=3),)
    visuals = [SceneVisual(scene_number=3, source="text_card", image_path=None, error="render boom")]
    issues = qc.check_scene_visuals(scenes, visuals)
    assert len(issues) == 1
    assert issues[0].severity == "fail"
    suggested_action = issues[0].suggested_action
    assert suggested_action is not None
    assert "Scene 3" in suggested_action


def test_successful_scene_with_short_text_has_no_issue(tmp_path):
    scenes = (_scene(number=1, duration=10.0, on_screen_text="Short"),)
    image_path = tmp_path / "scene_01.jpg"
    image_path.write_bytes(b"fake")
    visuals = [SceneVisual(scene_number=1, source="text_card", image_path=image_path, error=None)]
    issues = qc.check_scene_visuals(scenes, visuals)
    assert issues == []


def test_overly_long_text_for_short_duration_is_a_warning(tmp_path):
    long_text = "This is a very long piece of on screen text for a short scene duration here"
    scenes = (_scene(number=1, duration=2.0, on_screen_text=long_text),)
    image_path = tmp_path / "scene_01.jpg"
    image_path.write_bytes(b"fake")
    visuals = [SceneVisual(scene_number=1, source="text_card", image_path=image_path, error=None)]
    issues = qc.check_scene_visuals(scenes, visuals)
    assert len(issues) == 1
    assert issues[0].severity == "warning"
    assert issues[0].check == "text_readability"
    suggested_action = issues[0].suggested_action
    assert suggested_action is not None
    assert "Scene 1" in suggested_action


# --- check_no_hashtags_in_scene_text (real, reported requirement: hashtags must never --------
# appear in the final video - only in the separate written caption) -------------------------


def test_scene_with_hashtag_in_on_screen_text_is_a_fail():
    scenes = (_scene(number=2, on_screen_text="Follow for more! #reels #viral"),)
    issues = qc.check_no_hashtags_in_scene_text(scenes)
    assert len(issues) == 1
    assert issues[0].severity == "fail"
    assert issues[0].check == "hashtags_in_video"
    assert "Scene 2" in issues[0].message
    assert issues[0].suggested_action is not None


def test_scene_without_hashtag_has_no_issue():
    scenes = (_scene(number=1, on_screen_text="Save this Reel!"),)
    assert qc.check_no_hashtags_in_scene_text(scenes) == []


def test_scene_plan_text_cue_with_hashtag_is_a_fail():
    from jarvis.reel_generator.visual_plan import ScenePlan, TextCue

    scenes = (_scene(number=1, on_screen_text="Clean text"),)
    plan = ScenePlan(
        scene_number=1, visual_type="photo_style", main_visual_prompt="x", supporting_visuals=(),
        text_cues=(TextCue(text="Save this #now", position="bottom", start_seconds=0, end_seconds=1, animation="pop"),),
        sticker="none", sticker_start_seconds=0.0, motion="zoom_in", emotion="calm", pacing="medium", lighting="soft",
    )
    issues = qc.check_no_hashtags_in_scene_text(scenes, plans_by_number={1: plan})
    assert len(issues) == 1
    assert issues[0].severity == "fail"
    assert "Scene 1" in issues[0].message


def test_scene_plan_text_cue_without_hashtag_has_no_issue():
    from jarvis.reel_generator.visual_plan import ScenePlan, TextCue

    scenes = (_scene(number=1, on_screen_text="Clean text"),)
    plan = ScenePlan(
        scene_number=1, visual_type="photo_style", main_visual_prompt="x", supporting_visuals=(),
        text_cues=(TextCue(text="Save this", position="bottom", start_seconds=0, end_seconds=1, animation="pop"),),
        sticker="none", sticker_start_seconds=0.0, motion="zoom_in", emotion="calm", pacing="medium", lighting="soft",
    )
    assert qc.check_no_hashtags_in_scene_text(scenes, plans_by_number={1: plan}) == []


def test_multiple_scenes_with_hashtags_each_get_their_own_issue():
    scenes = (
        _scene(number=1, on_screen_text="Clean text"),
        _scene(number=2, on_screen_text="Follow us #now"),
        _scene(number=3, on_screen_text="Another #tag here"),
    )
    issues = qc.check_no_hashtags_in_scene_text(scenes)
    assert len(issues) == 2
    assert all(i.severity == "fail" for i in issues)


def test_no_plans_by_number_only_checks_on_screen_text():
    scenes = (_scene(number=1, on_screen_text="Clean text"),)
    assert qc.check_no_hashtags_in_scene_text(scenes, plans_by_number=None) == []


# --- check_cover_and_caption ------------------------------------------------------------


def test_no_cover_is_a_warning():
    issues = qc.check_cover_and_caption(cover_path=None, caption_text="a caption")
    cover_issues = [i for i in issues if i.check == "cover"]
    assert len(cover_issues) == 1
    assert cover_issues[0].severity == "warning"


def test_missing_cover_file_is_a_fail(tmp_path):
    issues = qc.check_cover_and_caption(cover_path=tmp_path / "does_not_exist.jpg", caption_text="a caption")
    cover_issues = [i for i in issues if i.check == "cover"]
    assert len(cover_issues) == 1
    assert cover_issues[0].severity == "fail"


def test_existing_cover_file_has_no_cover_issue(tmp_path):
    cover_path = tmp_path / "cover.jpg"
    cover_path.write_bytes(b"fake")
    issues = qc.check_cover_and_caption(cover_path=cover_path, caption_text="a caption")
    assert not any(i.check == "cover" for i in issues)


def test_no_caption_is_a_warning():
    issues = qc.check_cover_and_caption(cover_path=None, caption_text=None)
    caption_issues = [i for i in issues if i.check == "caption"]
    assert len(caption_issues) == 1
    assert caption_issues[0].severity == "warning"


def test_empty_string_caption_is_a_warning():
    issues = qc.check_cover_and_caption(cover_path=None, caption_text="   ")
    assert any(i.check == "caption" and i.severity == "warning" for i in issues)


# --- check_cover_integration (cover-in-export bug fix, requirement 7) --------------------
# Real, reported bug: the cover selected in CHOOSE A REEL COVER never
# reached the final exported video. This is a REAL, measured check
# (pixel comparison against the export's own first frame), not just
# "was a path configured" - these tests build real exports (with and
# without the cover genuinely spliced in) via jarvis.reel_generator
# .export.export_reel_video() itself, the same function this bug fix
# changed, so a regression in either module would show up here.


@pytest.fixture(scope="module")
def real_cover_path(tmp_path_factory):
    from PIL import Image

    out_dir = tmp_path_factory.mktemp("qc_cover")
    path = out_dir / "cover.jpg"
    Image.new("RGB", (1080, 1920), color=(200, 0, 0)).save(path, "JPEG")
    return path


@pytest.fixture(scope="module")
def _real_scenes_and_visuals(tmp_path_factory):
    from jarvis.reel_generator.brief import ReelBrief
    from jarvis.reel_generator.scenes import render_all_scenes

    scenes = (
        Scene(number=1, start_seconds=0, end_seconds=2, segment_kind="hook", voice_text="Hi", on_screen_text="Hi there", visual_description="x"),
        Scene(number=2, start_seconds=2, end_seconds=4, segment_kind="cta", voice_text="Save it", on_screen_text="Save this!", visual_description="x"),
    )
    brief = ReelBrief(
        topic="morning yoga", audience="beginners", objective="educate", tone="calm",
        cta="Save this Reel", style="yoga", duration_seconds=4, language="en",
    )
    visuals = render_all_scenes(brief, scenes, output_dir=tmp_path_factory.mktemp("qc_scenes"))
    return scenes, visuals


@pytest.fixture(scope="module")
def export_with_real_cover_intro(tmp_path_factory, real_cover_path, _real_scenes_and_visuals):
    from jarvis.reel_generator.export import export_reel_video

    scenes, visuals = _real_scenes_and_visuals
    output_path = tmp_path_factory.mktemp("qc_export_with_cover") / "reel.mp4"
    result = export_reel_video(
        scenes, visuals, output_path=output_path,
        cover_intro_path=real_cover_path, cover_intro_duration_seconds=1.5,
    )
    return result.output_path


@pytest.fixture(scope="module")
def export_without_cover_intro(tmp_path_factory, _real_scenes_and_visuals):
    from jarvis.reel_generator.export import export_reel_video

    scenes, visuals = _real_scenes_and_visuals
    output_path = tmp_path_factory.mktemp("qc_export_no_cover") / "reel.mp4"
    result = export_reel_video(scenes, visuals, output_path=output_path)
    return result.output_path


def test_instagram_mode_with_no_cover_at_all_does_not_fail():
    # Regression test for a real bug caught by
    # tests/test_gui_reel_generator_dashboard.py::test_reel_workflow_
    # status_progresses_through_the_state_machine: INSTAGRAM is the
    # DEFAULT mode for every project, including ones that have never
    # generated a cover at all - that's check_cover_and_caption()'s own
    # existing `warning`, never a new hard `fail` this check introduces.
    issues = qc.check_cover_integration(cover_integration_mode="instagram", cover_path=None, export_path=None)
    assert issues == []


def test_instagram_mode_passes_with_existing_cover_regardless_of_export(tmp_path, export_without_cover_intro, real_cover_path):
    # INSTAGRAM mode never splices the cover into the video - the cover
    # file existing is the whole guarantee, even against an export that
    # has no cover intro at all.
    issues = qc.check_cover_integration(
        cover_integration_mode="instagram", cover_path=real_cover_path, export_path=export_without_cover_intro,
    )
    assert issues == []


def test_intro_mode_passes_when_cover_is_genuinely_in_the_export(export_with_real_cover_intro, real_cover_path):
    issues = qc.check_cover_integration(
        cover_integration_mode="intro", cover_path=real_cover_path, export_path=export_with_real_cover_intro,
        cover_intro_duration_seconds=1.5,
    )
    assert issues == []


def test_intro_mode_fails_when_cover_was_not_actually_spliced_in(export_without_cover_intro, real_cover_path):
    # The real bug this fix targets: cover_integration_mode says
    # "intro", but the export doesn't actually contain the cover - this
    # must be a real, measured `fail`, not a silent pass.
    issues = qc.check_cover_integration(
        cover_integration_mode="intro", cover_path=real_cover_path, export_path=export_without_cover_intro,
        cover_intro_duration_seconds=1.5,
    )
    fail_issues = [i for i in issues if i.check == "cover_integration" and i.severity == "fail"]
    assert len(fail_issues) == 1
    assert "doesn't appear to be included" in fail_issues[0].message


def test_both_mode_fails_when_cover_was_not_actually_spliced_in(export_without_cover_intro, real_cover_path):
    issues = qc.check_cover_integration(
        cover_integration_mode="both", cover_path=real_cover_path, export_path=export_without_cover_intro,
        cover_intro_duration_seconds=1.5,
    )
    assert any(i.check == "cover_integration" and i.severity == "fail" for i in issues)


def test_intro_mode_with_no_cover_path_is_a_fail():
    issues = qc.check_cover_integration(cover_integration_mode="intro", cover_path=None, export_path=None)
    assert len(issues) == 1
    assert issues[0].severity == "fail"
    assert "no cover has been generated" in issues[0].message.lower()


def test_intro_mode_with_missing_cover_file_is_a_fail(tmp_path):
    issues = qc.check_cover_integration(
        cover_integration_mode="intro", cover_path=tmp_path / "gone.jpg", export_path=None,
    )
    assert len(issues) == 1
    assert issues[0].severity == "fail"
    assert "missing" in issues[0].message.lower()


def test_intro_mode_with_no_export_yet_is_a_fail(real_cover_path):
    issues = qc.check_cover_integration(cover_integration_mode="intro", cover_path=real_cover_path, export_path=None)
    assert len(issues) == 1
    assert issues[0].severity == "fail"
    assert "no exported video" in issues[0].message.lower()


def test_intro_mode_with_export_shorter_than_cover_duration_is_a_fail(correct_export, real_cover_path):
    # correct_export is only used here for its own short synthetic
    # duration relative to an intentionally huge cover_intro_duration_seconds -
    # a real, physically-impossible-to-satisfy case.
    issues = qc.check_cover_integration(
        cover_integration_mode="intro", cover_path=real_cover_path, export_path=correct_export,
        cover_intro_duration_seconds=9999.0,
    )
    assert any(i.check == "cover_integration" and i.severity == "fail" and "too short" in i.message for i in issues)


def test_run_quality_control_with_cover_integration_mode_none_skips_the_check(export_without_cover_intro):
    # Omitting cover_integration_mode (every caller before this fix, and
    # this function's own default) must not run this new check at all -
    # never a regression for a caller that doesn't pass it.
    report = qc.run_quality_control(export_path=export_without_cover_intro)
    assert not any(i.check == "cover_integration" for i in report.issues)


def test_run_quality_control_with_cover_integration_mode_runs_the_check(export_without_cover_intro, real_cover_path):
    report = qc.run_quality_control(
        export_path=export_without_cover_intro, cover_path=real_cover_path, cover_integration_mode="intro",
    )
    assert any(i.check == "cover_integration" and i.severity == "fail" for i in report.issues)


# --- QualityReport -----------------------------------------------------------------------


def test_report_passed_is_true_for_info_only_issues():
    issues = (qc.QualityIssue(check="audio", severity="info", message="x", suggested_action=None),)
    report = qc.QualityReport(issues=issues)
    assert report.passed is True
    assert report.has_failures is False
    assert report.has_warnings is False


def test_report_passed_is_false_with_a_warning():
    issues = (qc.QualityIssue(check="duration", severity="warning", message="x", suggested_action="y"),)
    report = qc.QualityReport(issues=issues)
    assert report.passed is False
    assert report.has_warnings is True
    assert report.has_failures is False


def test_report_passed_is_false_with_a_failure():
    issues = (qc.QualityIssue(check="resolution", severity="fail", message="x", suggested_action="y"),)
    report = qc.QualityReport(issues=issues)
    assert report.passed is False
    assert report.has_failures is True


# --- run_quality_control (integration) ---------------------------------------------------


def test_run_quality_control_with_only_export_path(correct_export):
    report = qc.run_quality_control(export_path=correct_export, target_duration_seconds=15)
    assert not report.has_failures


def test_run_quality_control_with_no_inputs_returns_empty_report():
    report = qc.run_quality_control(export_path=None)
    assert report.issues == ()
    assert report.passed is True


def test_run_quality_control_combines_all_applicable_checks(correct_export, tmp_path):
    scenes = (_scene(number=1),)
    image_path = tmp_path / "scene_01.jpg"
    image_path.write_bytes(b"fake")
    visuals = [SceneVisual(scene_number=1, source="text_card", image_path=image_path, error=None)]
    report = qc.run_quality_control(
        export_path=correct_export, target_duration_seconds=15, scenes=scenes, visuals=visuals,
        check_cover_and_caption_presence=True, cover_path=None, caption_text=None,
    )
    checks_seen = {i.check for i in report.issues}
    assert "cover" in checks_seen
    assert "caption" in checks_seen
    assert "audio" in checks_seen


def test_run_quality_control_skips_cover_caption_check_by_default(correct_export):
    report = qc.run_quality_control(export_path=correct_export, target_duration_seconds=15)
    checks_seen = {i.check for i in report.issues}
    assert "cover" not in checks_seen
    assert "caption" not in checks_seen


def test_run_quality_control_flags_hashtag_in_scene_text(correct_export):
    scenes = (_scene(number=1, on_screen_text="Follow for more! #reels"),)
    report = qc.run_quality_control(export_path=correct_export, target_duration_seconds=15, scenes=scenes)
    checks_seen = {i.check for i in report.issues}
    assert "hashtags_in_video" in checks_seen
    assert report.has_failures is True


def test_run_quality_control_flags_hashtag_in_scene_plan_text_cue(correct_export):
    from jarvis.reel_generator.visual_plan import ScenePlan, TextCue

    scenes = (_scene(number=1, on_screen_text="Clean text"),)
    plan = ScenePlan(
        scene_number=1, visual_type="photo_style", main_visual_prompt="x", supporting_visuals=(),
        text_cues=(TextCue(text="Save this #now", position="bottom", start_seconds=0, end_seconds=1, animation="pop"),),
        sticker="none", sticker_start_seconds=0.0, motion="zoom_in", emotion="calm", pacing="medium", lighting="soft",
    )
    report = qc.run_quality_control(
        export_path=correct_export, target_duration_seconds=15, scenes=scenes, plans_by_number={1: plan},
    )
    checks_seen = {i.check for i in report.issues}
    assert "hashtags_in_video" in checks_seen
    assert report.has_failures is True


def test_run_quality_control_no_hashtags_passes_that_check(correct_export):
    scenes = (_scene(number=1, on_screen_text="Save this Reel!"),)
    report = qc.run_quality_control(export_path=correct_export, target_duration_seconds=15, scenes=scenes)
    checks_seen = {i.check for i in report.issues}
    assert "hashtags_in_video" not in checks_seen


# --- check_exported_frames / extract_representative_frames (module brief requirement 12) --


@pytest.fixture(scope="module")
def varied_scenes_export(tmp_path_factory):
    """A real export with 4 DIFFERENT-colored 3-second segments, built
    with real ffmpeg (concat of 4 distinct lavfi color sources) - real,
    genuinely varied visual content across scenes, used to confirm
    check_exported_frames() does NOT flag a normally-varied Reel. Colors
    chosen for clearly distinct GRAYSCALE luminance specifically (not
    just distinct hue) - check_exported_frames()'s own similarity check
    compares grayscale thumbnails (see _frame_similarity()'s own
    docstring), and plain "red"/"green" are both ~76/255 in standard
    luminance despite looking obviously different in color, which was
    hand-tested to produce a false "repeated visual" positive here."""
    out_dir = tmp_path_factory.mktemp("qc_varied")
    path = out_dir / "varied.mp4"
    colors = ["0x303030", "0x707070", "0xB0B0B0", "0xF0F0F0"]
    filter_parts = []
    inputs = []
    for i, color in enumerate(colors):
        inputs += ["-f", "lavfi", "-i", f"color=c={color}:s=1080x1920:d=3"]
        filter_parts.append(f"[{i}:v]")
    filter_complex = "".join(filter_parts) + f"concat=n={len(colors)}:v=1:a=0[outv]"
    subprocess.run(
        ["ffmpeg", "-y", *inputs, "-filter_complex", filter_complex, "-map", "[outv]",
         "-c:v", "libx264", str(path)],
        capture_output=True, timeout=30, check=True,
    )
    return path


@pytest.fixture(scope="module")
def repeated_scenes_export(tmp_path_factory):
    """A real export where every 3-second segment is the SAME color -
    the real defect check_exported_frames()'s own repeated_visuals check
    is meant to catch (e.g. a Smart Visual Director plan that failed to
    vary visual_source/style, or a rendering bug reusing one scene's
    image for another)."""
    out_dir = tmp_path_factory.mktemp("qc_repeated")
    path = out_dir / "repeated.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=purple:s=1080x1920:d=12",
         "-c:v", "libx264", "-t", "12", str(path)],
        capture_output=True, timeout=30, check=True,
    )
    return path


@pytest.fixture(scope="module")
def black_frame_export(tmp_path_factory):
    """A real export that is genuinely, entirely black - the real
    defect check_exported_frames()'s own black_frames check is meant to
    catch (a scene whose visual failed to render but still made it into
    the export, or a corrupted encode)."""
    out_dir = tmp_path_factory.mktemp("qc_black")
    path = out_dir / "black.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=1080x1920:d=8",
         "-c:v", "libx264", "-t", "8", str(path)],
        capture_output=True, timeout=30, check=True,
    )
    return path


def test_extract_representative_frames_returns_real_files(correct_export):
    frames = qc.extract_representative_frames(correct_export, interval_seconds=3.0)
    assert len(frames) >= 4  # a 15s video sampled every 3s
    for frame_path in frames:
        assert frame_path.is_file()
        assert frame_path.stat().st_size > 0


def test_extract_representative_frames_raises_for_missing_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(qc.shutil, "which", lambda name: None)
    with pytest.raises(qc.FFmpegError, match="not found"):
        qc.extract_representative_frames(tmp_path / "does_not_exist.mp4")


def test_varied_scenes_produce_no_repeated_visuals_warning(varied_scenes_export):
    issues = qc.check_exported_frames(varied_scenes_export, interval_seconds=3.0)
    checks_seen = {i.check for i in issues}
    assert "repeated_visuals" not in checks_seen
    assert "black_frames" not in checks_seen


def test_repeated_identical_scenes_are_flagged(repeated_scenes_export):
    issues = qc.check_exported_frames(repeated_scenes_export, interval_seconds=3.0)
    repeated = [i for i in issues if i.check == "repeated_visuals"]
    assert len(repeated) == 1
    assert repeated[0].severity == "warning"


def test_black_export_is_flagged_as_a_failure(black_frame_export):
    issues = qc.check_exported_frames(black_frame_export, interval_seconds=2.0)
    black = [i for i in issues if i.check == "black_frames"]
    assert len(black) == 1
    assert black[0].severity == "fail"


def test_dark_but_non_black_style_is_not_flagged(tmp_path_factory):
    # A genuinely dark (but not BLACK) rendered style - e.g. this
    # codebase's own "luxury"/"modern" DesignStyle presets - must not be
    # misflagged as a black/blank frame. #1F1B12 is well above the
    # black-frame threshold once accounting for real anti-aliased text
    # on top, but this test uses a plain dark-gray fill (higher than
    # near-black) to directly confirm the threshold itself doesn't
    # misfire on ordinary dark content.
    out_dir = tmp_path_factory.mktemp("qc_dark_style")
    path = out_dir / "dark.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=0x1F1B12:s=1080x1920:d=6",
         "-c:v", "libx264", "-t", "6", str(path)],
        capture_output=True, timeout=30, check=True,
    )
    issues = qc.check_exported_frames(path, interval_seconds=3.0)
    black = [i for i in issues if i.check == "black_frames"]
    assert black == []


def test_check_exported_frames_never_raises_for_a_quality_finding(black_frame_export):
    # Only extract_representative_frames() itself raising (ffmpeg
    # missing/failing) should propagate - a genuine QUALITY finding
    # (black frames, repeated visuals) must come back as QualityIssues,
    # never an exception.
    issues = qc.check_exported_frames(black_frame_export)
    assert isinstance(issues, list)


def test_run_quality_control_inspect_frames_true_includes_frame_checks(black_frame_export):
    report = qc.run_quality_control(export_path=black_frame_export, inspect_frames=True)
    checks_seen = {i.check for i in report.issues}
    assert "black_frames" in checks_seen


def test_run_quality_control_inspect_frames_false_by_default(black_frame_export):
    report = qc.run_quality_control(export_path=black_frame_export)
    checks_seen = {i.check for i in report.issues}
    assert "black_frames" not in checks_seen


def test_run_quality_control_reports_ffmpeg_failure_as_issue_not_exception(tmp_path, monkeypatch):
    monkeypatch.setattr(qc.shutil, "which", lambda name: None)
    report = qc.run_quality_control(export_path=tmp_path / "nonexistent.mp4", inspect_frames=True)
    assert any(i.check == "frame_extraction" and i.severity == "fail" for i in report.issues)
