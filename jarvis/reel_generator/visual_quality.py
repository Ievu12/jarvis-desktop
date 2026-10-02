"""Visual Reel Generator stage's own quality gate: "Before rendering,
JARVIS must evaluate: Would this scene still make sense with all text
removed? If NO: regenerate the visual plan. Add a visual_quality_score
from 1-10. Do not render scenes with a score below 7." and "Add a
visual variety system so consecutive scenes do not use the same:
camera angle, composition, background, movement, framing."

Both checks are LOCAL, DETERMINISTIC, and run entirely on an already-
generated VisualPlan/ScenePlan - no second LLM call (see this module's
own design note below), so they add no new intermittent-failure surface
on top of jarvis.reel_generator.storyboard.generate_visual_plan()'s own
already-hand-documented retry logic.

score_scene_plan() answers "would this scene still communicate its idea
with text removed" by checking the same concrete-detail qualities the
Visual Story Director's own system prompt already asks the LLM to
produce (jarvis.reel_generator.storyboard._visual_plan_system_prompt()'s
own "never a vague or generic description... specific enough that a
photographer could shoot it exactly as described") - this function
verifies the model actually followed that instruction, rather than
asking it to grade its own homework in a second round-trip call.

check_scene_variety() answers "do consecutive scenes repeat the same
camera_shot/camera_movement/motion/environment" by directly comparing
adjacent ScenePlan fields - the same kind of local, no-LLM check
jarvis.reel_generator.quality_control's own check_exported_frames()
already does for POST-export duplicate-frame detection, applied here
one stage earlier, to the PLAN itself, before any rendering happens."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.reel_generator.visual_plan import ScenePlan, VisualPlan

MIN_RENDERABLE_QUALITY_SCORE = 7
# "Do not render scenes with a score below 7." - the module's own hard
# floor; jarvis.reel_generator.storyboard.generate_visual_plan() (or a
# GUI regeneration flow) checks a scene's own score against this constant
# before treating it as ready to render.

_MIN_DETAILED_PROMPT_WORDS = 8
# A main_visual_prompt this short essentially can't be "specific enough
# that a photographer could shoot it exactly as described" (the Visual
# Story Director prompt's own bar) - hand-picked as a low, forgiving
# threshold (a genuinely detailed prompt from real generations runs
# 15-30+ words; this only catches truly bare/placeholder text).
_VAGUE_WORDS = frozenset({
    "scene", "background", "image", "picture", "visual", "something", "stuff", "thing", "generic",
})
# Generic filler words that indicate a vague, non-committal description
# rather than a concrete subject/action/environment - penalized when
# present, since their presence is itself evidence the model fell back
# to a placeholder-ish description instead of a real photography brief.


@dataclass(frozen=True)
class QualityScoreBreakdown:
    """score_scene_plan()'s own reasoning, not just its final number -
    shown in the storyboard UI so a person can see WHY a scene scored
    low, not just that it did."""

    score: int  # 1-10
    reasons: tuple[str, ...]  # human-readable notes on what lowered (or would have lowered) the score

    @property
    def passes(self) -> bool:
        return self.score >= MIN_RENDERABLE_QUALITY_SCORE


def score_scene_plan(plan: ScenePlan) -> QualityScoreBreakdown:
    """Scores one ScenePlan 1-10 on "would this scene still make sense
    with all on-screen text removed" - i.e. does main_visual_prompt (the
    actual visual, independent of any text_cues) carry the scene's own
    meaning on its own. Never raises; always returns a valid 1-10 score.

    Starts at 10 and subtracts for each concrete problem found:
    -6 if visual_source is "typography" (a plain text card BY
       DEFINITION has no meaning once its text is removed - this is the
       one automatic, unconditional penalty, since no amount of prompt
       detail changes what a typography card actually shows),
    -3 if main_visual_prompt is empty or shorter than
       _MIN_DETAILED_PROMPT_WORDS words (too vague/short to describe a
       real shootable scene),
    -2 if main_visual_prompt contains one of _VAGUE_WORDS (a concrete
       description doesn't need to say "background"/"image"/"scene" -
       using those words is itself a sign of vagueness),
    -1 if environment is blank (no stated setting/location),
    -1 if subject_action is blank (no stated subject/action) - EXCEPT
       when visual_source is "typography" (already penalized above;
       typography scenes are never expected to have a subject/action,
       so this would otherwise double-penalize the same root cause).
    Never goes below 1 or above 10."""
    reasons: list[str] = []
    score = 10

    if plan.visual_source == "typography":
        score -= 6
        reasons.append("visual_source is \"typography\" - a plain text card has no visual meaning once text is removed")

    prompt = plan.main_visual_prompt.strip()
    word_count = len(prompt.split())
    if not prompt or word_count < _MIN_DETAILED_PROMPT_WORDS:
        score -= 3
        reasons.append(
            f"main_visual_prompt is too short/vague ({word_count} words) to describe a concrete, shootable scene"
        )
    elif any(word.strip(".,!?").lower() in _VAGUE_WORDS for word in prompt.split()):
        score -= 2
        reasons.append("main_visual_prompt uses generic/vague wording instead of a concrete description")

    if not plan.environment.strip():
        score -= 1
        reasons.append("environment is not specified")

    if plan.visual_source != "typography" and not plan.subject_action.strip():
        score -= 1
        reasons.append("subject_action is not specified")

    score = max(1, min(10, score))
    return QualityScoreBreakdown(score=score, reasons=tuple(reasons))


@dataclass(frozen=True)
class VarietyIssue:
    """One consecutive-scene repetition check_scene_variety() found -
    `scene_number` is the LATER of the two adjacent scenes (the one
    that repeats its predecessor), matching how a person would think
    of it ("scene 3 repeats scene 2's own camera angle"), not the
    earlier one."""

    scene_number: int
    field: str  # which ScenePlan field repeated, e.g. "camera_shot"
    value: str  # the repeated value itself


_VARIETY_CHECKED_FIELDS = ("camera_shot", "camera_movement", "motion", "environment")
# "camera angle" -> camera_shot, "movement"/"framing" -> camera_movement
# and motion (this codebase's own two distinct movement concepts - see
# jarvis.reel_generator.visual_plan's own docstring for why camera_shot/
# camera_movement are purely descriptive while `motion` is what actually
# drives the exported video's own ffmpeg zoompan effect), "background"
# -> environment. "composition" has no single dedicated ScenePlan field
# of its own - camera_shot already captures the closest equivalent
# (wide/medium/close-up/macro/overhead/etc. IS a scene's composition),
# so it isn't checked as a separate field to avoid double-counting the
# same repetition under two different names.


def check_scene_variety(plan: VisualPlan) -> list[VarietyIssue]:
    """Checks every pair of CONSECUTIVE scenes in `plan` for a repeated
    camera_shot/camera_movement/motion/environment - "consecutive
    scenes do not use the same camera angle, composition, background,
    movement, framing." Empty/blank environment values are never
    flagged as a repeat (both scenes lacking an environment isn't a
    meaningful "same background" repetition - environment is optional
    free text, unlike the three fixed-vocabulary fields). Returns one
    VarietyIssue per repeated field per adjacent pair - a scene
    repeating TWO fields from its predecessor produces two issues, so a
    caller can see exactly what needs to change, not just that
    something does."""
    issues: list[VarietyIssue] = []
    scenes = plan.scenes
    for i in range(1, len(scenes)):
        previous, current = scenes[i - 1], scenes[i]
        for field in _VARIETY_CHECKED_FIELDS:
            previous_value = getattr(previous, field)
            current_value = getattr(current, field)
            if field == "environment" and not current_value.strip():
                continue
            if previous_value == current_value and (field != "environment" or previous_value.strip()):
                issues.append(VarietyIssue(scene_number=current.scene_number, field=field, value=current_value))
    return issues
