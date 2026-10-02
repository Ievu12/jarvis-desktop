"""Quality Control (module brief, section 20): "Before final export
check: duration, resolution, aspect ratio, subtitle synchronization,
text readability, spelling, audio volume, voice clarity, scene
transitions, safe zones, no text cutoffs, no empty frames. If something
fails, show the problem and offer: [Fix automatically] [Edit manually]."

Every check here inspects MEASURABLE properties of an already-rendered
Reel export (or, for Mode B, its own scene visuals before export) -
never a subjective/AI "does this look good" judgment call, matching
this codebase's established "describe candidates/results by measurable
characteristics, never invent a quality score" convention (see
jarvis.video_studio.highlights/.analysis's own docstrings for the same
principle applied to highlight detection).

Several of the module brief's listed checks are explicitly N/A for
this codebase's actual capabilities, and this module says so rather
than silently skipping them or fabricating a pass:
  - "Audio volume" / "Voice clarity": there is no voiceover audio in
    either Mode A's silent export note (Mode A's export DOES have
    original audio - checked) or Mode B's export (always silent, no
    TTS - see jarvis.reel_generator.export's own docstring). Checked
    only when the export actually has an audio stream (Mode A).
  - "Scene transitions": both modes use a hard cut between scenes/clips
    (no crossfade/transition effect exists in either
    jarvis.reel_generator.export or jarvis.video_studio.export) - this
    is reported as an informational note, never a failure, since a
    hard cut is a valid, common Reel editing choice, not a defect.
  - "Spelling": no spell-checking library is a dependency of this
    project - flagged as NOT PERFORMED rather than silently skipped,
    so a caller/UI never mistakenly implies spelling was checked.

"[Fix automatically]" is offered only for checks this module can
actually auto-correct without ambiguity (currently: none require a
person's judgment call to fix blindly - each surfaced issue instead
names the specific regenerate/edit action to take, matching this
codebase's "never make an irreversible change based on an AI
assumption" convention applied to visual edits)."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from jarvis.reel_generator.scenes import SceneVisual
from jarvis.reel_generator.storyboard import Scene, strip_hashtags
from jarvis.reel_generator.visual_plan import ScenePlan
from jarvis.video_studio.ffmpeg_utils import FFmpegError, probe_video

_EXPECTED_WIDTH = 1080
_EXPECTED_HEIGHT = 1920
_EXPECTED_ASPECT = "9:16"

# Safe-zone placement (module brief section 8's "keep text away from
# areas Instagram's UI may cover") is enforced at RENDER time, inside
# jarvis.design_studio.render (the same _SAFE_AREA_TOP_FRACTION/
# _SAFE_AREA_BOTTOM_FRACTION constants that module already established
# and hand-tested) - every scene/cover this package produces goes
# through that renderer, so there is no separate post-render safe-zone
# check here to re-verify: there is no OCR/text-position-detection
# capability in this codebase to inspect an already-exported MP4's
# burned-in text position, so re-checking it after the fact would only
# be able to re-run the same layout math the renderer already applied,
# not genuinely verify the final pixels - re-stating that guarantee
# here would be misleading rather than an independent check.

# A duration this far off the requested target is flagged - ffmpeg's
# own encoding overhead (keyframe alignment, container padding) means
# an exact match is unrealistic to require.
_DURATION_TOLERANCE_SECONDS = 1.5

Severity = Literal["fail", "warning", "info"]


@dataclass(frozen=True)
class QualityIssue:
    check: str
    severity: Severity
    message: str
    suggested_action: str | None
    """A specific, concrete next step (e.g. "Regenerate Scene 3",
    "Approve a shorter script") - never a vague "please review", per
    the module brief's "[Edit manually]" action needing something
    actual to point at. None for informational notes with no action
    needed."""


@dataclass(frozen=True)
class QualityReport:
    issues: tuple[QualityIssue, ...]

    @property
    def has_failures(self) -> bool:
        return any(i.severity == "fail" for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity == "warning" for i in self.issues)

    @property
    def passed(self) -> bool:
        """True only if there are no failures AND no warnings - an
        info-only report (e.g. just the "hard cut, no transition
        effect" note) still counts as passed, since info issues are
        never a defect."""
        return not self.has_failures and not self.has_warnings


def check_export_file(output_path: Path, *, target_duration_seconds: float | None = None) -> list[QualityIssue]:
    """Checks measurable properties of an already-rendered export file:
    resolution, aspect ratio, duration (against `target_duration_seconds`
    if given), and whether it has an empty/zero-duration result. Never
    raises - a file that can't even be probed is reported as a `fail`
    issue, not an exception."""
    issues: list[QualityIssue] = []
    try:
        probe = probe_video(output_path)
    except FFmpegError as e:
        return [QualityIssue(
            check="file_integrity", severity="fail", message=f"Couldn't read the exported file: {e}",
            suggested_action="Export again.",
        )]

    if probe.duration_seconds <= 0:
        issues.append(QualityIssue(
            check="empty_frames", severity="fail", message="The exported file has no playable duration.",
            suggested_action="Export again - check that every scene has a valid visual.",
        ))

    if probe.width != _EXPECTED_WIDTH or probe.height != _EXPECTED_HEIGHT:
        issues.append(QualityIssue(
            check="resolution", severity="fail",
            message=f"Expected {_EXPECTED_WIDTH}x{_EXPECTED_HEIGHT}, got {probe.width}x{probe.height}.",
            suggested_action="Export again.",
        ))

    if probe.width and probe.height and probe.width != probe.height:
        # Aspect ratio derived from the SAME measured width/height as
        # the resolution check above, not re-probed - a 1080x1920 file
        # is always 9:16 by construction, so this only meaningfully
        # differs from _EXPECTED_ASPECT when the resolution check above
        # has already failed; kept as a separate, clearly-labeled check
        # anyway since the module brief lists it as its own item.
        import math

        divisor = math.gcd(probe.width, probe.height)
        actual_aspect = f"{probe.width // divisor}:{probe.height // divisor}"
        if actual_aspect != _EXPECTED_ASPECT:
            issues.append(QualityIssue(
                check="aspect_ratio", severity="fail",
                message=f"Expected {_EXPECTED_ASPECT}, got {actual_aspect}.",
                suggested_action="Export again.",
            ))

    if target_duration_seconds is not None:
        diff = abs(probe.duration_seconds - target_duration_seconds)
        if diff > _DURATION_TOLERANCE_SECONDS:
            issues.append(QualityIssue(
                check="duration", severity="warning",
                message=(
                    f"Requested {target_duration_seconds:.0f}s, exported "
                    f"{probe.duration_seconds:.1f}s ({diff:.1f}s off)."
                ),
                suggested_action="Regenerate the script/plan with a different duration if this matters.",
            ))

    if not probe.has_audio:
        issues.append(QualityIssue(
            check="audio", severity="info",
            message="This export has no audio track - add your own voiceover/music before posting if wanted.",
            suggested_action=None,
        ))
    else:
        issues.append(QualityIssue(
            check="audio", severity="info",
            message="Audio volume/clarity checks are not performed by this codebase - listen before posting.",
            suggested_action=None,
        ))

    issues.append(QualityIssue(
        check="scene_transitions", severity="info",
        message="Scenes/clips use a hard cut - no transition effect is applied.",
        suggested_action=None,
    ))
    issues.append(QualityIssue(
        check="spelling", severity="info",
        message="Spelling was not automatically checked - proofread the on-screen text/caption yourself.",
        suggested_action=None,
    ))

    return issues


def check_scene_visuals(scenes: tuple[Scene, ...], visuals: list[SceneVisual]) -> list[QualityIssue]:
    """Checks each scene has a successfully rendered visual (module
    brief: "no empty frames") and that its on-screen text isn't
    obviously too long to read comfortably in its own scene duration.
    Never raises."""
    issues: list[QualityIssue] = []
    visuals_by_number = {v.scene_number: v for v in visuals}

    for scene in scenes:
        visual = visuals_by_number.get(scene.number)
        if visual is None:
            issues.append(QualityIssue(
                check="empty_frames", severity="fail",
                message=f"Scene {scene.number} has no rendered visual yet.",
                suggested_action="Generate scene visuals before exporting.",
            ))
            continue
        if visual.error is not None or visual.image_path is None:
            issues.append(QualityIssue(
                check="empty_frames", severity="fail",
                message=f"Scene {scene.number}'s visual failed to render: {visual.error}",
                suggested_action=f"Regenerate Scene {scene.number}.",
            ))
            continue

        # A rough, honest readability heuristic: this codebase has no
        # OCR/text-measurement-at-render-time hook available here (the
        # actual pixel-accurate wrap already happened inside
        # jarvis.design_studio.render - by the time this check runs,
        # the text is already laid out) - so this checks the SOURCE
        # text's word count against the scene's own duration using the
        # same reading-pace assumption jarvis.reel_generator.script
        # uses for SPEAKING pace, adjusted for silent reading (faster
        # than speaking) - flagged as a warning, not a fail, since it's
        # an estimate, not a measured overflow.
        word_count = len(scene.on_screen_text.split())
        max_comfortable_words = max(1, round(scene.duration_seconds * 3.5))
        if word_count > max_comfortable_words:
            issues.append(QualityIssue(
                check="text_readability", severity="warning",
                message=(
                    f"Scene {scene.number}'s on-screen text ({word_count} words) may be too long "
                    f"to comfortably read in {scene.duration_seconds:.1f}s."
                ),
                suggested_action=f"Shorten Scene {scene.number}'s text or regenerate the storyboard.",
            ))

    return issues


def check_no_hashtags_in_scene_text(
    scenes: tuple[Scene, ...], *, plans_by_number: dict[int, ScenePlan] | None = None,
) -> list[QualityIssue]:
    """Real, reported requirement ("hashtags must never appear in the
    final video - only in the separate written caption"): checks every
    scene's on_screen_text, and every ScenePlan text cue when one exists
    for that scene, for a literal "#" character.

    jarvis.reel_generator.storyboard already strips hashtags from both
    fields at generation/validation time, and
    jarvis.reel_generator.scenes/.scene_render strip them again at the
    actual render choke point (defense in depth - see
    jarvis.reel_generator.storyboard.strip_hashtags()'s own docstring
    for the full history) - this is a THIRD, independent check, run
    against the scene DATA right before approval, so a hashtag reaching
    this point at all (e.g. a person manually typing one into the EDIT
    SCENE dialog after those earlier fixes ran) is caught here as a
    real, visible quality-control failure rather than silently exported.
    Never raises."""
    issues: list[QualityIssue] = []
    plans_by_number = plans_by_number or {}
    for scene in scenes:
        if strip_hashtags(scene.on_screen_text) != scene.on_screen_text:
            issues.append(QualityIssue(
                check="hashtags_in_video", severity="fail",
                message=f"Scene {scene.number}'s on-screen text contains a hashtag: \"{scene.on_screen_text}\".",
                suggested_action=f"Edit Scene {scene.number}'s text to remove the hashtag - hashtags belong only in the caption.",
            ))
        plan = plans_by_number.get(scene.number)
        if plan is None:
            continue
        for cue in plan.text_cues:
            if strip_hashtags(cue.text) != cue.text:
                issues.append(QualityIssue(
                    check="hashtags_in_video", severity="fail",
                    message=f"Scene {scene.number}'s on-screen text cue contains a hashtag: \"{cue.text}\".",
                    suggested_action=f"Regenerate Scene {scene.number}'s visual plan to remove the hashtag.",
                ))
    return issues


def check_cover_and_caption(*, cover_path: Path | None, caption_text: str | None) -> list[QualityIssue]:
    """Checks the cover exists/is readable and the caption isn't empty
    - module brief: "no text cutoffs" (a cover that fails to open at
    all is the clearest form of that), and a Reel with a missing
    caption is an easy, worth-flagging gap before hand-off. Never
    raises."""
    issues: list[QualityIssue] = []

    if cover_path is None:
        issues.append(QualityIssue(
            check="cover", severity="warning", message="No Reel cover has been generated yet.",
            suggested_action="Generate a Reel cover.",
        ))
    elif not cover_path.is_file():
        issues.append(QualityIssue(
            check="cover", severity="fail", message=f"The cover file is missing: {cover_path}",
            suggested_action="Regenerate the Reel cover.",
        ))

    if caption_text is None or not caption_text.strip():
        issues.append(QualityIssue(
            check="caption", severity="warning", message="No caption has been generated yet.",
            suggested_action="Generate a caption.",
        ))

    return issues


def check_cover_integration(
    *, cover_integration_mode: str, cover_path: Path | None, export_path: Path | None,
    cover_intro_duration_seconds: float = 1.5,
) -> list[QualityIssue]:
    """Cover-in-export bug fix, requirement 7: "add a pre-export check
    that the selected cover is included according to the user's
    settings". `cover_integration_mode` is one of
    jarvis.reel_generator.db.COVER_INTEGRATION_MODE_CHOICES.

    For INSTAGRAM (the default for every project, and the ONLY mode
    that existed before this bug fix - cover used for the profile grid
    only, never spliced into the video) this check does nothing at all
    - a missing/absent cover in that mode is check_cover_and_caption()'s
    own existing `warning`, not a new hard failure this fix introduces;
    re-flagging it here too would make APPROVE REEL wrongly block a
    perfectly normal Instagram-Cover-only project that simply hasn't
    generated a cover yet (a real regression this function was first
    written with, caught by tests/test_gui_reel_generator_dashboard.py
    ::test_reel_workflow_status_progresses_through_the_state_machine).

    For INTRO/BOTH, this is a REAL, measured check, not just "was a
    path passed in": it extracts the actual first frame of the already-
    exported video (jarvis.video_studio.ffmpeg_utils.extract_frame() at
    timestamp 0.0) and compares it, pixel-for-pixel at a small thumbnail
    resolution (the same _frame_similarity() helper check_exported_
    frames() already uses), against the cover image itself. A close
    match means the cover genuinely made it into the export; a mismatch
    (or a missing/absent cover file, or an export shorter than the
    cover's own intro duration, which would mean it physically couldn't
    have been spliced in at all) is a real, measured `fail` - never a
    fabricated pass just because a mode was configured. Never raises -
    a failure to even probe/extract is itself reported as a `fail`
    QualityIssue."""
    issues: list[QualityIssue] = []

    from jarvis.reel_generator.db import COVER_INTEGRATION_MODE_INSTAGRAM

    if cover_integration_mode == COVER_INTEGRATION_MODE_INSTAGRAM:
        return issues

    if cover_path is None:
        issues.append(QualityIssue(
            check="cover_integration", severity="fail",
            message=f"Cover integration is set to '{cover_integration_mode}', but no cover has been generated/selected.",
            suggested_action="Generate or select a cover before exporting.",
        ))
        return issues
    if not cover_path.is_file():
        issues.append(QualityIssue(
            check="cover_integration", severity="fail",
            message=f"Cover integration is set to '{cover_integration_mode}', but the cover file is missing: {cover_path}",
            suggested_action="Regenerate the Reel cover.",
        ))
        return issues

    if export_path is None or not export_path.is_file():
        issues.append(QualityIssue(
            check="cover_integration", severity="fail",
            message=f"Cover integration is set to '{cover_integration_mode}', but no exported video exists yet to verify against.",
            suggested_action="Export the Reel, then re-run this check.",
        ))
        return issues

    try:
        from jarvis.video_studio.ffmpeg_utils import extract_frame

        probe = probe_video(export_path)
        if probe.duration_seconds < cover_intro_duration_seconds:
            issues.append(QualityIssue(
                check="cover_integration", severity="fail",
                message=(
                    f"The export is only {probe.duration_seconds:.1f}s long - too short to contain the "
                    f"{cover_intro_duration_seconds:.1f}s cover intro."
                ),
                suggested_action="Export again with the cover intro enabled.",
            ))
            return issues

        import tempfile

        with tempfile.TemporaryDirectory(prefix="jarvis_cover_qc_") as tmp_dir:
            first_frame_path = Path(tmp_dir) / "first_frame.png"
            extract_frame(export_path, timestamp_seconds=0.0, output_path=first_frame_path)
            similarity = _frame_similarity(first_frame_path, cover_path)
    except FFmpegError as e:
        issues.append(QualityIssue(
            check="cover_integration", severity="fail",
            message=f"Couldn't verify the cover is in the export: {e}",
            suggested_action="Export again.",
        ))
        return issues

    if similarity < _DUPLICATE_FRAME_SIMILARITY_THRESHOLD:
        issues.append(QualityIssue(
            check="cover_integration", severity="fail",
            message="The export's first frame doesn't match the selected cover - the cover doesn't appear to be included.",
            suggested_action="Export again (REGENERATE) so the cover intro is rebuilt.",
        ))

    return issues


_BLACK_FRAME_MEAN_THRESHOLD = 12.0
# A grayscale mean below this is treated as an effectively black/blank
# frame - hand-chosen well below any real Pillow-rendered scene's own
# darkest style preset (this codebase's own darkest DesignStyle,
# "luxury", uses #0D0D0D/#1F1B12 backgrounds, which still average well
# above this threshold once headline/CTA text and any composited
# element are drawn on top) so a genuinely rendered dark scene is never
# misflagged, while an actually-corrupt/undecoded black frame is
# reliably caught.

_DUPLICATE_FRAME_SIMILARITY_THRESHOLD = 0.995
# Two consecutive SAMPLED frames (see extract_representative_frames()'s
# own sampling interval) with a pixel-difference similarity above this
# are flagged as "the same visual repeated" - module brief's own
# "repeated identical visuals" check. A single still-image scene
# legitimately produces many byte-identical raw frames DURING its own
# duration (that's how a still-image "video" works, matching this
# module's own real ffmpeg export pipeline) - this check only ever
# compares frames already sampled several seconds apart (see
# extract_representative_frames()'s own default interval), so two
# samples landing within the SAME scene's own duration are expected to
# be similar; the check exists to catch TWO DIFFERENT SCENES that
# somehow rendered near-identical output (the real defect this
# reasonably guards against - e.g. a Smart Visual Director plan that
# failed to vary visual_source/style across scenes, or a rendering bug
# that silently reused one scene's image for another).


@dataclass(frozen=True)
class FrameSample:
    timestamp_seconds: float
    mean_brightness: float
    is_effectively_black: bool


def extract_representative_frames(video_path: Path, *, interval_seconds: float = 2.0) -> list[Path]:
    """Extracts real frames from an ALREADY-EXPORTED video at
    `interval_seconds` intervals via a real ffmpeg subprocess call
    (module brief requirement 12: "Open the actual generated MP4 and
    inspect several frames" / "Do NOT only test that the file exists")
    - saved as PNGs in a temp directory next to the source video's own
    exports_dir (never inside a person's real project's own tree).
    Raises FFmpegError if ffmpeg is missing or the extraction itself
    fails - this is a genuine prerequisite failure, not a quality
    issue to report gracefully, since no frames means no inspection is
    possible at all."""
    import subprocess
    import tempfile

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise FFmpegError("FFmpeg was not found on PATH.")

    frames_dir = Path(tempfile.mkdtemp(prefix="jarvis_frame_qc_"))
    cmd = [
        ffmpeg, "-y", "-i", str(video_path), "-vf", f"fps=1/{interval_seconds}",
        str(frames_dir / "frame_%03d.png"),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
    except subprocess.TimeoutExpired:
        raise FFmpegError(f"Frame extraction timed out for {video_path.name}.") from None
    if result.returncode != 0:
        raise FFmpegError(f"Frame extraction failed: {result.stderr.strip()[-400:]}")

    return sorted(frames_dir.glob("frame_*.png"))


def _frame_mean_brightness(frame_path: Path) -> float:
    from PIL import Image

    with Image.open(frame_path) as img:
        grayscale = img.convert("L")
        histogram = grayscale.histogram()
    total_pixels = sum(histogram)
    if total_pixels == 0:
        return 0.0
    weighted_sum = sum(value * count for value, count in enumerate(histogram))
    return weighted_sum / total_pixels


def _frame_similarity(frame_a: Path, frame_b: Path) -> float:
    """A cheap, real (not simulated) pixel-difference similarity score
    between two frames: both resized to a small fixed thumbnail (so
    differing source resolutions/minor encoding noise don't dominate
    the comparison), converted to grayscale, and compared byte-by-byte.
    Returns a value in [0, 1] - 1.0 means pixel-identical at this
    thumbnail resolution."""
    from PIL import Image

    size = (64, 114)  # a small 9:16 thumbnail - enough to detect real compositional differences
    with Image.open(frame_a) as a, Image.open(frame_b) as b:
        thumb_a = a.convert("L").resize(size)
        thumb_b = b.convert("L").resize(size)
        bytes_a = thumb_a.tobytes()
        bytes_b = thumb_b.tobytes()

    total = len(bytes_a)
    if total == 0:
        return 1.0
    matching = sum(1 for x, y in zip(bytes_a, bytes_b) if abs(x - y) <= 8)
    return matching / total


def check_exported_frames(video_path: Path, *, interval_seconds: float = 2.0) -> list[QualityIssue]:
    """Module brief requirement 12's own hard requirement: extracts REAL
    frames from the ALREADY-EXPORTED video (via extract_representative_frames())
    and inspects them for black/empty frames and repeated-identical-
    visual runs across DIFFERENT sampled moments - the two failure
    modes this codebase can genuinely, measurably detect from pixel data
    alone (no OCR capability exists to detect duplicated/overlapping
    TEXT after the fact - see this module's own docstring for why that
    stays a render-time guarantee instead, verified separately by
    tests/test_reel_generator_scene_render.py's own real-pixel checks).
    Never raises for a QUALITY finding - only extract_representative_frames()
    itself raising (ffmpeg missing/failing) propagates, since that means
    no inspection happened at all, not that inspection found nothing
    wrong."""
    issues: list[QualityIssue] = []
    frame_paths = extract_representative_frames(video_path, interval_seconds=interval_seconds)
    if not frame_paths:
        issues.append(QualityIssue(
            check="frame_extraction", severity="fail",
            message="Couldn't extract any frames from the exported video for inspection.",
            suggested_action="Export again.",
        ))
        return issues

    samples: list[FrameSample] = []
    for i, frame_path in enumerate(frame_paths):
        brightness = _frame_mean_brightness(frame_path)
        samples.append(FrameSample(
            timestamp_seconds=i * interval_seconds, mean_brightness=brightness,
            is_effectively_black=brightness < _BLACK_FRAME_MEAN_THRESHOLD,
        ))

    black_frames = [s for s in samples if s.is_effectively_black]
    if black_frames:
        timestamps = ", ".join(f"{s.timestamp_seconds:.0f}s" for s in black_frames)
        issues.append(QualityIssue(
            check="black_frames", severity="fail",
            message=f"Found {len(black_frames)} effectively black/blank frame(s) at: {timestamps}.",
            suggested_action="Regenerate the scene visual(s) around that timestamp and export again.",
        ))

    repeated_runs = 0
    for i in range(1, len(frame_paths)):
        similarity = _frame_similarity(frame_paths[i - 1], frame_paths[i])
        if similarity >= _DUPLICATE_FRAME_SIMILARITY_THRESHOLD:
            repeated_runs += 1
    if repeated_runs > 0:
        issues.append(QualityIssue(
            check="repeated_visuals", severity="warning",
            message=(
                f"{repeated_runs} of {len(frame_paths) - 1} sampled frame transition(s) look nearly "
                "identical - scenes may not be visually varied enough."
            ),
            suggested_action="Run SMART VISUAL DIRECTOR again, or vary the Visual Style/scenes manually.",
        ))

    return issues


def run_quality_control(
    *, export_path: Path | None, target_duration_seconds: float | None = None,
    scenes: tuple[Scene, ...] | None = None, visuals: list[SceneVisual] | None = None,
    plans_by_number: dict[int, ScenePlan] | None = None,
    check_cover_and_caption_presence: bool = False,
    cover_path: Path | None = None, caption_text: str | None = None,
    inspect_frames: bool = False,
    cover_integration_mode: str | None = None, cover_intro_duration_seconds: float = 1.5,
) -> QualityReport:
    """Runs every applicable check given whatever inputs are available
    - a caller mid-pipeline (e.g. before export exists yet) can pass
    only `scenes`/`visuals` and still get a useful partial report,
    matching this codebase's established "partial result is still
    useful" convention. `check_cover_and_caption_presence` must be
    explicitly set True by the caller (typically right before offering
    Export/hand-off, module brief section 20's own "before final
    export" framing) - `cover_path=None`/`caption_text=None` are
    otherwise ambiguous between "not applicable yet" and "genuinely
    missing", so this flag is the caller's explicit statement that a
    cover/caption SHOULD exist by this point and their absence is worth
    flagging. `inspect_frames=True` additionally runs
    check_exported_frames() against `export_path` - real ffmpeg frame
    extraction plus pixel inspection (module brief requirement 12's own
    hard requirement), OFF by default since it's real subprocess/CPU
    work a caller should opt into deliberately (e.g. right before
    telling the person their Reel is ready), not on every lightweight
    metadata-only quality check. A frame-extraction failure itself
    (ffmpeg missing/erroring) is reported as a `fail` QualityIssue here
    rather than propagating, so a caller only ever needs to handle
    QualityReport, never a separate exception path, for this one
    optional check. `plans_by_number`, if given alongside `scenes`, also
    runs check_no_hashtags_in_scene_text() against each scene's own
    ScenePlan text cues, not just its on_screen_text - see that
    function's own docstring for why this is a real, independent third
    check on top of the two earlier generation/render-time hashtag
    fixes.

    `cover_integration_mode`, if given (cover-in-export bug fix,
    requirement 7), additionally runs check_cover_integration() - a
    REAL, measured check (pixel comparison against the export's own
    first frame, not just "was a path configured") that the selected
    cover actually made it into the export per that mode, when the mode
    is Intro/Both. None (the default) skips this check entirely - an
    older caller/project untouched by this fix behaves exactly as
    before. Never raises."""
    issues: list[QualityIssue] = []

    if export_path is not None:
        issues.extend(check_export_file(export_path, target_duration_seconds=target_duration_seconds))
    if scenes is not None and visuals is not None:
        issues.extend(check_scene_visuals(scenes, visuals))
    if scenes is not None:
        issues.extend(check_no_hashtags_in_scene_text(scenes, plans_by_number=plans_by_number))
    if check_cover_and_caption_presence:
        issues.extend(check_cover_and_caption(cover_path=cover_path, caption_text=caption_text))
    if cover_integration_mode is not None:
        issues.extend(check_cover_integration(
            cover_integration_mode=cover_integration_mode, cover_path=cover_path, export_path=export_path,
            cover_intro_duration_seconds=cover_intro_duration_seconds,
        ))
    if inspect_frames and export_path is not None:
        try:
            issues.extend(check_exported_frames(export_path))
        except FFmpegError as e:
            issues.append(QualityIssue(
                check="frame_extraction", severity="fail",
                message=f"Couldn't extract frames for inspection: {e}",
                suggested_action="Confirm ffmpeg is installed and export again.",
            ))

    return QualityReport(issues=tuple(issues))
