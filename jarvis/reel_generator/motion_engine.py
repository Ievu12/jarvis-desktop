"""Motion Engine - NATURAL MOTION / HYBRID modes' own "decides what to
ask for" layer, sitting between the GUI and
jarvis.reel_generator.video_generation's real Runway API call. This
module owns three things: the MotionSettings vocabulary (requirement 3:
intensity/camera/people/environment/duration/style), turning a scene's
already-existing ScenePlan fields (main_visual_prompt/camera_movement/
environment/subject_action - see jarvis.reel_generator.visual_plan's
own docstring, these ALREADY exist and needed no new Scene/ScenePlan
fields) plus MotionSettings into an actual motion_prompt string, and -
most importantly - being the single, explicit isolation boundary
between a Motion Engine failure and the rest of the Reel pipeline (see
generate_motion_for_scene()'s own docstring for why this is the ONE
place in this whole feature allowed a bare `except Exception`).

STATIC mode never imports or calls anything in this module at all - a
bug here can only ever affect a scene that explicitly asked for motion,
never a Static-mode scene, and even for a scene that DID ask for
motion, a failure here always degrades to "use the still image instead"
(exactly like jarvis.reel_generator.image_generation's own honest
gpt-image-1-fails-fall-back-to-Pillow convention), never a crash, never
a fabricated fake clip.

This codebase has NO body-deformation, flicker, or object-distortion
detection capability of any kind, and none is claimed here -
quality_precheck_clip() below can only catch black/blank frames and
gross duplicate-frame runs (the same measurable-property checks
jarvis.reel_generator.quality_control already established for exported
Reels), which is explicitly disclosed rather than silently implied to
be more thorough than it is."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jarvis.reel_generator import video_generation
from jarvis.reel_generator.quality_control import QualityIssue
from jarvis.reel_generator.scenes import SceneMotionClip, SceneVisual
from jarvis.reel_generator.storyboard import Scene
from jarvis.reel_generator.visual_plan import ScenePlan

MOTION_INTENSITY_CHOICES = ("low", "medium", "high")
DEFAULT_MOTION_INTENSITY = "medium"

MOTION_STYLE_CHOICES = ("natural", "cinematic", "lifestyle", "product", "yoga")
# Requirement 3's own exact style list.
DEFAULT_MOTION_STYLE = "natural"

_DEFAULT_CLIP_DURATION_SECONDS = 5.0
_MIN_CLIP_DURATION_SECONDS = 3.0
_MAX_CLIP_DURATION_SECONDS = 10.0

_INTENSITY_ADVERBS = {
    "low": "subtly and slowly",
    "medium": "naturally",
    "high": "dynamically and energetically",
}

_STYLE_CINEMATOGRAPHY_PHRASES = {
    "natural": "with realistic, true-to-life motion",
    "cinematic": "with smooth, deliberate cinematic camera motion",
    "lifestyle": "with gentle handheld realism, like an authentic lifestyle video",
    "product": "with a slow, elegant, commercial product-showcase motion",
    "yoga": "with calm, controlled, flowing movement suited to a yoga/wellness scene",
}


@dataclass(frozen=True)
class MotionSettings:
    """Requirement 3's own settings vocabulary. Can be applied to the
    whole Reel (one MotionSettings shared by every scene) or per-scene
    (a dict[int, MotionSettings] keyed by scene number, built by the
    GUI's own Whole-Reel-vs-Per-Scene toggle) - this dataclass itself
    doesn't know or care which; jarvis.reel_generator.db's own
    motion_settings_data JSON blob is what actually stores that
    distinction (see that column's own docstring)."""

    intensity: str = DEFAULT_MOTION_INTENSITY
    camera_movement_enabled: bool = True
    people_movement_enabled: bool = True
    environment_animation_enabled: bool = True
    clip_duration_seconds: float = _DEFAULT_CLIP_DURATION_SECONDS
    style: str = DEFAULT_MOTION_STYLE


def validate_motion_settings(settings: MotionSettings) -> None:
    """Raises ValueError only for a genuinely malformed MotionSettings
    (an intensity/style outside their own *_CHOICES tuple, or a
    duration outside the sane 3-10s range) - mirrors this codebase's
    established choice-tuple validation convention (e.g.
    jarvis.reel_generator.visual_plan's own *_CHOICES tables, validated
    the same way at their own call sites)."""
    if settings.intensity not in MOTION_INTENSITY_CHOICES:
        raise ValueError(f"Unknown motion intensity: {settings.intensity!r}")
    if settings.style not in MOTION_STYLE_CHOICES:
        raise ValueError(f"Unknown motion style: {settings.style!r}")
    if not (_MIN_CLIP_DURATION_SECONDS <= settings.clip_duration_seconds <= _MAX_CLIP_DURATION_SECONDS):
        raise ValueError(
            f"clip_duration_seconds must be between {_MIN_CLIP_DURATION_SECONDS} and "
            f"{_MAX_CLIP_DURATION_SECONDS}, got {settings.clip_duration_seconds}"
        )


def build_motion_prompt(scene: Scene, plan: ScenePlan, settings: MotionSettings) -> str:
    """Pure string composition - the ONE inspectable place a person can
    see exactly what text gets sent to a paid third-party API before it
    happens, matching this codebase's own established preference for
    prompt-building to be its own small, readable, directly-testable
    function (see jarvis.reel_generator.image_generation._build_prompt()
    for the same pattern applied to gpt-image-1).

    Combines: `plan.subject_action` (what's happening) and
    `plan.environment` (where) - both already-existing ScenePlan fields,
    no new data needed (requirement 4 is already satisfied by the
    existing data model, see this module's own docstring) - filtered by
    which of the three movement toggles are enabled, `plan.camera_movement`
    (only if `camera_movement_enabled`), an intensity adverb, and a
    style-specific cinematography phrase. A disabled toggle simply
    omits that clause entirely, rather than sending a contradictory
    "don't move" instruction the model may or may not honor."""
    clauses: list[str] = []
    if settings.people_movement_enabled and plan.subject_action:
        clauses.append(plan.subject_action)
    if settings.environment_animation_enabled and plan.environment:
        clauses.append(f"in {plan.environment}")
    if not clauses and plan.main_visual_prompt:
        # Neither people nor environment movement is enabled/available -
        # fall back to the scene's own general visual description so the
        # prompt is never empty (an empty motion_prompt is rejected by
        # jarvis.reel_generator.video_generation.generate_scene_video()
        # itself, matching image_generation.py's own empty-prompt guard).
        clauses.append(plan.main_visual_prompt)

    base = ", ".join(clauses) if clauses else scene.visual_description
    adverb = _INTENSITY_ADVERBS.get(settings.intensity, _INTENSITY_ADVERBS[DEFAULT_MOTION_INTENSITY])
    style_phrase = _STYLE_CINEMATOGRAPHY_PHRASES.get(settings.style, _STYLE_CINEMATOGRAPHY_PHRASES[DEFAULT_MOTION_STYLE])

    prompt = f"{base}, moving {adverb} {style_phrase}."
    if settings.camera_movement_enabled and plan.camera_movement and plan.camera_movement != "static":
        prompt += f" Camera: {plan.camera_movement}."
    return prompt


def generate_motion_for_scene(
    scene: Scene, plan: ScenePlan, visual: SceneVisual, settings: MotionSettings, *, output_path: Path,
) -> SceneMotionClip:
    """The critical isolation function - NEVER raises, always returns a
    well-formed SceneMotionClip, no matter what goes wrong. This is the
    single boundary guaranteeing requirement 11's "a Motion module
    failure must NEVER break STATIC mode" at the function-call level:
    the GUI's own background-threaded call site can trust this function
    unconditionally, exactly like every other never-raising boundary
    function in this codebase (jarvis.reel_generator.image_generation
    .generate_scene_image(), jarvis.voice.text_to_speech.speak())."""
    try:
        if visual.image_path is None or visual.error is not None:
            return SceneMotionClip(
                scene_number=scene.number, video_path=None, source_image_path=visual.image_path,
                error="no still image available to animate", provider_task_id=None,
                duration_seconds=settings.clip_duration_seconds,
            )
        if not video_generation.is_configured():
            return SceneMotionClip(
                scene_number=scene.number, video_path=None, source_image_path=visual.image_path,
                error="RUNWAY_API_KEY is not configured - scene will use its still image",
                provider_task_id=None, duration_seconds=settings.clip_duration_seconds,
            )
        prompt = build_motion_prompt(scene, plan, settings)
        video, error = video_generation.generate_scene_video(
            visual.image_path, prompt, duration_seconds=settings.clip_duration_seconds,
        )
        if video is None:
            return SceneMotionClip(
                scene_number=scene.number, video_path=None, source_image_path=visual.image_path,
                error=error, provider_task_id=None, duration_seconds=settings.clip_duration_seconds,
            )
        video_generation.save_scene_video(video, output_path=output_path)
        return SceneMotionClip(
            scene_number=scene.number, video_path=output_path, source_image_path=visual.image_path,
            error=None, provider_task_id=None, duration_seconds=settings.clip_duration_seconds,
        )
    except Exception as e:
        # The ONE deliberate, explicitly-documented bare `except
        # Exception` in this entire feature. Justification: this is the
        # OUTERMOST boundary of the whole new Motion Engine subsystem,
        # called from a GUI background thread
        # (run_generation_in_background()) - its job is guaranteeing the
        # caller ALWAYS gets a well-formed SceneMotionClip back, never an
        # unhandled exception that could crash a background worker.
        # video_generation.py itself deliberately does NOT catch bare
        # Exception (only the specific urllib exception classes) so a
        # real bug in that module still surfaces loudly during testing -
        # this catch exists only at this one, outermost seam, and only
        # to protect the rest of the running application from an
        # unexpected failure in a brand-new, less-battle-tested
        # subsystem, never to hide a bug during development.
        return SceneMotionClip(
            scene_number=scene.number, video_path=None, source_image_path=visual.image_path,
            error=f"unexpected motion generation error: {e}", provider_task_id=None,
            duration_seconds=settings.clip_duration_seconds,
        )


def generate_motion_for_scenes(
    scenes: tuple[Scene, ...], plans: dict[int, ScenePlan], visuals: list[SceneVisual],
    settings_by_scene: dict[int, MotionSettings], *, output_dir: Path,
) -> list[SceneMotionClip]:
    """Generates motion for every scene present as a key in
    `settings_by_scene` (Natural Motion: every scene; Hybrid: only the
    scenes the person marked as "moving") - a scene NOT present in
    `settings_by_scene` is simply skipped, never attempted, never
    producing a SceneMotionClip at all (Hybrid's own static scenes stay
    exactly as plain SceneVisual-only, same as a Static-mode project).

    One failure never stops the batch - same "one failure doesn't abort
    the batch" convention as jarvis.reel_generator.scenes
    .render_all_scenes()."""
    visuals_by_number = {v.scene_number: v for v in visuals}
    clips: list[SceneMotionClip] = []
    for scene in scenes:
        settings = settings_by_scene.get(scene.number)
        if settings is None:
            continue
        plan = plans.get(scene.number)
        visual = visuals_by_number.get(scene.number)
        if plan is None or visual is None:
            clips.append(SceneMotionClip(
                scene_number=scene.number, video_path=None, source_image_path=visual.image_path if visual else None,
                error="no visual plan or rendered visual available for this scene", provider_task_id=None,
                duration_seconds=settings.clip_duration_seconds,
            ))
            continue
        output_path = output_dir / f"clip_{scene.number:02d}.mp4"
        clips.append(generate_motion_for_scene(scene, plan, visual, settings, output_path=output_path))
    return clips


def quality_precheck_clip(clip_path: Path) -> list[QualityIssue]:
    """Thin reuse of jarvis.reel_generator.quality_control's own
    real-frame-extraction machinery (extract_representative_frames()/
    the same black-frame and duplicate-frame checks check_exported_frames()
    already applies to a full exported Reel), run here against ONE
    generated clip in isolation, before it's ever spliced into an
    export.

    Honest limitation (see this module's own docstring): this can ONLY
    catch a black/blank clip or a clip that's suspiciously identical
    across its own sampled frames (e.g. a provider silently returning a
    near-static "clip") - it CANNOT detect body deformation, flicker,
    or object distortion, because no such capability exists anywhere in
    this codebase. A caller/UI must present this as "automated basic
    check only - please preview manually", never as a guarantee the
    clip is visually correct."""
    from jarvis.reel_generator.quality_control import check_exported_frames

    return check_exported_frames(clip_path, interval_seconds=1.0)
