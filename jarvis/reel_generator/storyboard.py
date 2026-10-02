"""Storyboard generation (module brief, section 5): breaks an approved
ReelScript into scenes - one per natural beat of the script - each with
a scene number, time range, on-screen text (module brief section 9:
"Do NOT simply place the entire script on screen... use concise
phrases"), and a short visual description used to choose a fitting
background style for that scene's text-card render (jarvis.reel_generator
.scenes, this stage's next module).

One isolated, tool-free, JSON-only LLM call (own private copy of
_extract_json()/_call_llm_for_json(), same convention as
jarvis.reel_generator.brief/.script). Unlike the script (exactly 3
fixed segments: hook/value/cta), a storyboard's scene COUNT varies with
the script's own content - the VALUE segment of a "3 benefits" Reel
naturally wants 3 scenes, not 1; the HOOK and CTA segments each get
their own single scene. The model is given each script segment
separately and asked to split VALUE into as many scenes as it
naturally contains distinct points (capped, see _MAX_SCENES) rather
than being told a fixed number up front.

Nothing here renders a visual - this module only produces the
storyboard's STRUCTURE and text; jarvis.reel_generator.scenes (the next
module) turns each Scene into an actual rendered image.

This module's own "Smart Visual Director" brief (requirements 1-8, 18)
adds generate_visual_plan() - a SEPARATE, opt-in function, not a change
to generate_storyboard() above - which takes an already-generated
Storyboard and produces a matching VisualPlan (one ScenePlan per Scene:
visual source, supporting visuals, text cue timing/animation, sticker,
motion, transition, emotion/pacing/lighting, hook treatment). Kept as
its own function/LLM call rather than merged into generate_storyboard()
itself for one deliberate reason: generate_storyboard() is this
package's own ORIGINAL, load-bearing call, already used by every
existing Reel Generator project and thoroughly tested on its own exact
3-field (segment_kind/on_screen_text/visual_description) shape - a
second, independent call means the module brief's own hard requirement
("Preserve all current functionality... Do not break the existing AI
Reel Generator") holds structurally: generate_storyboard()'s own
prompt, validation, and return shape are literally unmodified by this
addition, not just behaviorally unchanged. A caller that never calls
generate_visual_plan() (the dashboard's own original render path, still
available) gets EXACTLY today's Reel Generator, unchanged."""

from __future__ import annotations

import dataclasses
import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from jarvis.core.llm import LLMClient
from jarvis.reel_generator import visual_quality

logger = logging.getLogger(__name__)
# Standard library logging, added specifically to trace generate_visual_plan()'s
# own real, hand-tested-but-not-yet-fully-diagnosed intermittent failure
# (see that function's own docstring) - _call_llm_for_json()'s own
# "except Exception: return None" was a real blind spot: a genuine SDK
# exception (rate limit, connection error, API error) and a genuinely
# malformed JSON response were previously indistinguishable from the
# caller's point of view, both just silently becoming None. No handler
# is configured here - see jarvis.gui.views.reel_generator.dashboard's
# own identical logger docstring for why that's fine (the root logger's
# own handler, or Python's lastResort stderr handler, still surfaces
# this in a terminal launch).
from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.script import ReelScript
from jarvis.reel_generator.visual_plan import (
    B_ROLL_TYPE_CHOICES,
    CAMERA_MOVEMENT_CHOICES,
    CAMERA_SHOT_CHOICES,
    LIGHTING_CHOICES,
    MOTION_CHOICES,
    MUSIC_MOOD_CHOICES,
    PACING_CHOICES,
    STICKER_NAME_CHOICES,
    TEXT_ANIMATION_CHOICES,
    TEXT_POSITION_CHOICES,
    TRANSITION_CHOICES,
    VISUAL_SOURCE_CHOICES,
    DEFAULT_B_ROLL_TYPE,
    DEFAULT_CAMERA_MOVEMENT,
    DEFAULT_CAMERA_SHOT,
    DEFAULT_LIGHTING,
    DEFAULT_MOTION,
    DEFAULT_MUSIC_MOOD,
    DEFAULT_PACING,
    DEFAULT_TRANSITION,
    DEFAULT_VISUAL_SOURCE,
    VISUAL_STYLE_CHOICES,
    DEFAULT_VISUAL_STYLE,
    ScenePlan,
    TextCue,
    VisualPlan,
)

_MAX_GENERATION_TOKENS = 1500
_VISUAL_PLAN_MAX_GENERATION_TOKENS = 12000
# generate_visual_plan()'s own token budget - each scene's ScenePlan is
# roughly as large as a whole generate_storyboard() response (a detailed
# main_visual_prompt alone can run 30-50 words, plus 1-2 text_cues, each
# with their own several fields, plus the Visual Story Director brief's
# own additional camera/environment/subject_action/b_roll/voiceover
# fields), so a MAX_SCENES=8 storyboard needs considerably more headroom
# than the plain 3-field scene generation above. Originally 4000, then
# 8000, now raised again after extending the schema with the Visual
# Story Director's own additional per-scene fields - hand-tested against
# the real Claude API to surface a genuine, intermittent, reproducible
# failure: a real response was truncated mid-JSON (the model's own
# output simply ran out of budget before finishing the array), which _extract_json()
# correctly reports as unparseable (see that function's own docstring)
# - generate_visual_plan() then correctly returns None (never crashes,
# never fakes success; the dashboard's own caller already shows this as
# a real, visible error status - see
# jarvis.gui.views.reel_generator.dashboard's own
# _handle_visual_plan_result()). Raising the budget reduces how often
# this genuinely happens in the first place, rather than just handling
# it more gracefully after the fact.

_STICKER_NAMES_FOR_PROMPT = ", ".join(n for n in STICKER_NAME_CHOICES if n != "none")

_JSON_RESPONSE_INSTRUCTION = (
    "Reply with ONLY a single valid JSON object matching the requested shape - "
    "no explanation, no markdown code fences, no leading/trailing text of any "
    "kind. The response must be parseable by a strict JSON parser as-is."
)

# A hard ceiling on scene count - keeps a storyboard readable/reviewable
# (module brief section 5 shows each scene as its own reviewable card)
# and keeps the later scenes/export stage's per-scene rendering work
# bounded, even if a script's VALUE segment covers many distinct points.
_MAX_SCENES = 8


@dataclass(frozen=True)
class Scene:
    number: int  # 1-indexed, in order
    start_seconds: float
    end_seconds: float
    segment_kind: str  # which ReelScript segment this scene belongs to: hook/value/cta
    voice_text: str  # the underlying script line(s) this scene covers (module brief: "Voice/text")
    on_screen_text: str  # SHORT on-screen phrase - never the full voice_text verbatim
    visual_description: str  # a short description used to pick a fitting scene style
    mood: str = ""  # optional scene mood/tone (e.g. "tense", "hopeful") - Reel Generator leaves
    # this blank; jarvis.story_generator's own scenes fill it in (module brief section 3's
    # per-scene "mood" field). Defaulted so every existing Scene(...) call site in this
    # package keeps working unmodified.
    transition: str = ""  # optional suggested transition INTO the next scene (e.g. "hard cut",
    # "fade") - same "blank for Reel Generator, filled in by story_generator" reasoning as mood.

    @property
    def duration_seconds(self) -> float:
        return round(self.end_seconds - self.start_seconds, 1)


@dataclass(frozen=True)
class Storyboard:
    scenes: tuple[Scene, ...]


def _system_prompt(brief: ReelBrief) -> str:
    return (
        "You are a short-form Instagram Reel storyboard artist. Given an approved Reel "
        "script (already split into HOOK, VALUE, and CTA segments with timestamps), break "
        "it into a scene-by-scene storyboard - one scene per natural beat. The HOOK segment "
        "is always exactly one scene. The CTA segment is always exactly one scene. The VALUE "
        "segment becomes one scene per distinct point/beat it naturally contains (e.g. a "
        f"\"3 benefits\" script's VALUE becomes 3 scenes, one per benefit) - up to {_MAX_SCENES} "
        "scenes total across the whole storyboard; if VALUE has no distinct sub-points, it "
        "stays exactly one scene. Every scene's start_seconds/end_seconds must fall strictly "
        "within its own segment's own time range and scenes within a segment must be "
        "contiguous. For each scene write a SHORT on-screen text phrase (a few words, NEVER "
        "the full script line verbatim - this is a caption card, not a subtitle) and a short "
        "visual_description (a plain description of what the scene should show, for choosing "
        "a background style later - there is no AI image/video generation, so keep this "
        "generic/abstract, e.g. \"calm morning light\", \"a person stretching\", never a "
        "specific unavailable effect). on_screen_text is burned directly into the exported "
        "video's own pixels as a caption/subtitle - it must NEVER contain a hashtag (no \"#\" "
        "character at all, even for a CTA scene like \"follow for more\") or an @handle; "
        "hashtags belong only in the Reel's separate written caption, generated elsewhere, "
        "never in on-screen video text. "
        f"Write on_screen_text in {brief.language_label}.\n\n"
        + _JSON_RESPONSE_INSTRUCTION + "\n\n"
        "Respond with a JSON object with exactly one field, \"scenes\", an array of scene "
        "objects in order, each with fields: \"segment_kind\" (one of: hook, value, cta), "
        "\"start_seconds\" (number), \"end_seconds\" (number), \"voice_text\" (the underlying "
        "script text this scene covers, a substring/paraphrase of that segment's own line), "
        "\"on_screen_text\" (short phrase), and \"visual_description\" (short phrase)."
    )


def _extract_json(text: str) -> Any | None:
    stripped = text.strip()
    fence_match = re.match(r"^```(?:json)?\s*\n(.*)\n```$", stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


HASHTAG_PATTERN = re.compile(r"#\w+")
# Real, reported bug fix: on_screen_text/text_cue text get burned
# directly into the exported video's own pixels - as scene-image
# headlines (jarvis.reel_generator.scenes/.scene_render) AND as
# ffmpeg-burned subtitles (jarvis.reel_generator.export) - never into a
# separately-editable caption field. This module's own storyboard/
# visual-plan system prompts now explicitly forbid a hashtag in either
# field (see _system_prompt()'s own on_screen_text instruction just
# above), but an LLM instruction is not a hard guarantee, so this is a
# defensive SECOND layer applied to every scene's on_screen_text and
# every text cue's text right where each is validated - never relied on
# alone. Hashtags belong ONLY in the Reel's separate written caption
# (jarvis.reel_generator.caption), generated from a completely different
# prompt/call and never touched by this function.


def strip_hashtags(text: str) -> str:
    """Removes any "#word" token from `text` (a hashtag burned into
    on-screen video text - never a person's own topic/CTA text that
    happens to use "#" for some other reason, which this codebase has no
    real example of) and collapses the resulting double-spacing. Returns
    `text` completely unchanged if it contains no "#" at all - the
    overwhelmingly common case - so this never alters ordinary scene
    text."""
    if "#" not in text:
        return text
    cleaned = HASHTAG_PATTERN.sub("", text)
    return re.sub(r"\s{2,}", " ", cleaned).strip()


def _call_llm_for_json(llm: LLMClient, *, system: str, prompt: str) -> Any | None:
    try:
        response = llm.send(
            [{"role": "user", "content": prompt}], [], max_tokens=_MAX_GENERATION_TOKENS, system=system,
        )
    except Exception:
        return None

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        return None
    return _extract_json(text)


_SEGMENT_KINDS = ("hook", "value", "cta")


def _validate_scenes(data: Any, *, script: ReelScript) -> list[dict] | None:
    if not isinstance(data, dict):
        return None
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not scenes or len(scenes) > _MAX_SCENES:
        return None

    segment_ranges = {s.kind: (s.start_seconds, s.end_seconds) for s in script.segments}

    validated: list[dict] = []
    seen_kinds: set[str] = set()
    previous_end_by_kind: dict[str, float] = {}
    for raw in scenes:
        if not isinstance(raw, dict):
            return None
        kind = raw.get("segment_kind")
        voice_text = raw.get("voice_text")
        on_screen_text = raw.get("on_screen_text")
        visual_description = raw.get("visual_description")
        start = raw.get("start_seconds")
        end = raw.get("end_seconds")

        if kind not in _SEGMENT_KINDS or kind not in segment_ranges:
            return None
        if not isinstance(voice_text, str) or not voice_text.strip():
            return None
        if not isinstance(on_screen_text, str) or not on_screen_text.strip():
            return None
        # Defensive hashtag strip (see strip_hashtags()'s own docstring)
        # applied here, before the emptiness check below, so a response
        # that was ONLY a hashtag (e.g. on_screen_text: "#reels") is
        # rejected and retried like any other malformed on_screen_text,
        # rather than silently becoming a blank caption card.
        on_screen_text = strip_hashtags(on_screen_text)
        if not on_screen_text.strip():
            return None
        if not isinstance(visual_description, str) or not visual_description.strip():
            return None
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            return None
        start, end = float(start), float(end)
        if end <= start:
            return None

        segment_start, segment_end = segment_ranges[kind]
        # Tolerate small drift at the segment's own boundary (matching
        # jarvis.reel_generator.script's own seam-drift tolerance) but
        # never accept a scene meaningfully outside its own segment's
        # time range.
        if start < segment_start - 0.5 or end > segment_end + 0.5:
            return None

        previous_end = previous_end_by_kind.get(kind)
        if previous_end is not None and start < previous_end - 0.5:
            return None
        previous_end_by_kind[kind] = end
        seen_kinds.add(kind)

        validated.append({
            "segment_kind": kind, "start_seconds": start, "end_seconds": end,
            "voice_text": voice_text.strip(), "on_screen_text": on_screen_text.strip(),
            "visual_description": visual_description.strip(),
        })

    # HOOK and CTA must each appear (module brief: "always exactly one
    # scene" for each) - VALUE is the only segment allowed to expand
    # into multiple scenes, but must still appear at least once.
    if not {"hook", "value", "cta"}.issubset(seen_kinds):
        return None

    return validated


def generate_storyboard(llm: LLMClient, brief: ReelBrief, script: ReelScript) -> Storyboard | None:
    """Generates a Storyboard from an already-approved ReelScript.
    Returns None on any failure (LLM error, malformed response, a scene
    outside its own segment's time range, a missing hook/value/cta
    segment) - never raises. Does not render any visuals - see
    jarvis.reel_generator.scenes for that."""
    segment_lines = "\n".join(
        f"{s.label} ({s.start_seconds}-{s.end_seconds}s): {s.text}" for s in script.segments
    )
    prompt = f"Topic: {brief.topic}\nStyle: {brief.style}\n\nApproved script:\n{segment_lines}"

    result = _call_llm_for_json(llm, system=_system_prompt(brief), prompt=prompt)
    validated = _validate_scenes(result, script=script)
    if validated is None:
        return None

    scenes = tuple(
        Scene(
            number=i, start_seconds=s["start_seconds"], end_seconds=s["end_seconds"],
            segment_kind=s["segment_kind"], voice_text=s["voice_text"],
            on_screen_text=s["on_screen_text"], visual_description=s["visual_description"],
        )
        for i, s in enumerate(validated, start=1)
    )
    return Storyboard(scenes=scenes)


# --- Smart Visual Director: generate_visual_plan() --------------------------------------
#
# A SEPARATE LLM call from generate_storyboard() above - see this
# module's own docstring for why. Takes an already-generated Storyboard
# (Scene objects already have their own segment_kind/voice_text/
# on_screen_text/visual_description/duration - this call only adds the
# VISUAL layer on top) and asks for one ScenePlan per scene, with scene
# VARIETY explicitly required (requirement 7: never the same visual
# treatment for every scene) and the first scene (assumed hook) getting
# special treatment (requirement 6).


def _visual_plan_system_prompt(brief: ReelBrief, storyboard: Storyboard, *, visual_style: str) -> str:
    scene_lines = "\n".join(
        f"Scene {s.number} ({s.segment_kind}, {s.duration_seconds:g}s) - voice line: \"{s.voice_text}\" | "
        f"on-screen text: \"{s.on_screen_text}\" | rough idea: {s.visual_description}"
        for s in storyboard.scenes
    )
    style_line = f"\nOverall visual style: {visual_style}." if visual_style else ""
    return (
        "You are a Visual Story Director for short-form Instagram Reels - given an approved "
        "storyboard (a list of scenes, each with its own voice line and on-screen text), turn it "
        "into a real, cinematic visual shot list. Think like a director shooting real lifestyle "
        "footage or photography, not a slideshow: for every scene, decide EXACTLY what the viewer "
        "would actually SEE on screen - a specific subject doing a specific action in a specific "
        "environment, shot a specific way - never a vague or generic description. This is the "
        "single most important creative decision: avoid using the same visual composition, camera "
        "shot, or B-roll type for every scene - vary visual_source, camera_shot, camera_movement, "
        "environment, and transition across scenes so the Reel feels like a professionally shot "
        "and edited video, not a repeated template. The FIRST scene is the hook (the first 1-3 "
        "seconds) - it must have visually strong, attention-grabbing treatment (motion should "
        "rarely be \"static\" for the hook) and \"is_hook\" must be true only for that "
        f"scene.{style_line}\n\n"
        f"Storyboard:\n{scene_lines}\n\n"
        "For each scene write:\n"
        "\"voiceover\" (a natural spoken narration line for this scene, adapted from its own voice "
        "line above - what a narrator would actually say),\n"
        "\"visual_source\" (one of: " + ", ".join(VISUAL_SOURCE_CHOICES) + " - vary this across "
        "scenes rather than repeating the same choice; prefer ai_generated/b_roll/lifestyle-style "
        "sources for anything showing a real person/place/action, reserve typography ONLY for a "
        "scene where no meaningful visual is possible),\n"
        "\"environment\" (the specific setting/location, e.g. \"cozy bedroom with morning window "
        "light\", \"kitchen counter with fresh produce\"),\n"
        "\"subject_action\" (specifically what the subject is doing, e.g. \"wrapped in a blanket, "
        "sipping tea, looking tired\", \"pouring water into a glass\"),\n"
        "\"main_visual_prompt\" (a DETAILED, camera-ready description combining environment + "
        "subject_action + mood into one vivid sentence, written like a real photography brief - "
        "specific enough that a photographer could shoot it exactly as described, e.g. \"A woman "
        "sitting on a sofa in the morning, wrapped in a soft blanket, looking tired, warm tea mug "
        "and tissues on the table, natural window light, cinematic lifestyle photography\"),\n"
        "\"camera_shot\" (one of: " + ", ".join(CAMERA_SHOT_CHOICES) + "),\n"
        "\"camera_movement\" (one of: " + ", ".join(CAMERA_MOVEMENT_CHOICES) + " - vary across "
        "scenes),\n"
        "\"b_roll_type\" (one of: " + ", ".join(B_ROLL_TYPE_CHOICES) + " - \"none\" if this scene's "
        "own main visual is enough on its own),\n"
        "\"supporting_visuals\" (an array of 0-3 short labels for additional elements when they'd "
        "genuinely help, e.g. \"steam from mug\", \"before/after split\", \"circle highlight\" - an "
        "empty array is fine and often correct),\n"
        "\"text_cues\" (an array of 1-2 on-screen text overlay objects, each with \"text\" (short "
        "phrase, never the full narration, and NEVER a hashtag or \"#\" character - hashtags "
        "belong only in the Reel's separate written caption, never composited into the video "
        "itself), \"position\" (one of: " + ", ".join(TEXT_POSITION_CHOICES)
        + "), \"start_seconds\"/\"end_seconds\" (relative to THIS SCENE's own start, both within "
        "0-duration_seconds), and \"animation\" (one of: " + ", ".join(TEXT_ANIMATION_CHOICES) + " - "
        "prefer word_by_word or typewriter for the hook scene, vary elsewhere)),\n"
        "\"text_animation\" (a short free-text note on the overall on-screen text's own animation "
        "feel for this scene, e.g. \"word-by-word reveal\", \"quick pop-in\"),\n"
        "\"sticker\" (one of: none, " + _STICKER_NAMES_FOR_PROMPT + " - pick \"none\" for most "
        "scenes; only pick a real sticker when it genuinely improves the storytelling, never "
        "decoratively on every scene),\n"
        "\"sticker_start_seconds\" (relative to this scene's own start, ignored if sticker is "
        "\"none\"),\n"
        "\"motion\" (one of: " + ", ".join(MOTION_CHOICES) + " - vary this across scenes),\n"
        "\"transition\" (the transition INTO the NEXT scene, one of: " + ", ".join(TRANSITION_CHOICES)
        + " - avoid excessive transitions, mostly \"cut\"/\"fade\" with an occasional more dramatic "
        "choice for emphasis),\n"
        "\"music_mood\" (one of: " + ", ".join(MUSIC_MOOD_CHOICES) + "),\n"
        "\"emotion\" (one or two words), \"pacing\" (one of: " + ", ".join(PACING_CHOICES) + "), and "
        "\"lighting\" (one of: " + ", ".join(LIGHTING_CHOICES) + ").\n\n"
        + _JSON_RESPONSE_INSTRUCTION + "\n\n"
        "Respond with a JSON object with exactly one field, \"scenes\", an array of scene plan "
        "objects in the SAME order and COUNT as the storyboard above, each with fields: "
        "\"scene_number\" (matching the storyboard's own scene numbers), \"voiceover\", "
        "\"visual_source\", \"environment\", \"subject_action\", \"main_visual_prompt\", "
        "\"camera_shot\", \"camera_movement\", \"b_roll_type\", \"supporting_visuals\", "
        "\"text_cues\", \"text_animation\", \"sticker\", \"sticker_start_seconds\", \"motion\", "
        "\"transition\", \"music_mood\", \"emotion\", \"pacing\", \"lighting\"."
    )


def _validate_visual_plan_text_cue(raw: Any, *, scene_duration: float) -> dict | None:
    if not isinstance(raw, dict):
        return None
    text = raw.get("text")
    position = raw.get("position")
    start = raw.get("start_seconds")
    end = raw.get("end_seconds")
    animation = raw.get("animation")

    if not isinstance(text, str) or not text.strip():
        return None
    # Defensive hashtag strip (see strip_hashtags()'s own docstring) -
    # a text cue is composited directly into the scene image's own
    # pixels (jarvis.reel_generator.scene_render), same leak path as
    # on_screen_text above. A cue that was ONLY a hashtag is dropped
    # entirely (returns None, same as any other malformed cue) rather
    # than kept as a blank on-screen overlay.
    text = strip_hashtags(text)
    if not text.strip():
        return None
    if position not in TEXT_POSITION_CHOICES:
        position = "center"
    if animation not in TEXT_ANIMATION_CHOICES:
        animation = "fade_in"
    if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
        start, end = 0.0, scene_duration
    start = max(0.0, float(start))
    end = min(scene_duration, float(end))
    if end <= start:
        start, end = 0.0, scene_duration

    return {"text": text.strip(), "position": position, "start_seconds": start, "end_seconds": end, "animation": animation}


def _validate_scene_plan(raw: Any, *, scene_number: int, scene_duration: float, is_hook: bool) -> ScenePlan:
    """Builds one scene's ScenePlan from the model's own (possibly
    partially malformed) response for it, falling back to safe defaults
    field-by-field rather than failing the whole call - a story/Reel
    shouldn't be thrown away over one scene's stray "lighting" value.
    Matches jarvis.story_generator.story_scenes's own established
    per-field-fallback convention exactly (see that module's own
    _validate_visual_plan() docstring for the full reasoning)."""
    plan = raw if isinstance(raw, dict) else {}

    visual_source = plan.get("visual_source")
    if visual_source not in VISUAL_SOURCE_CHOICES:
        visual_source = DEFAULT_VISUAL_SOURCE

    main_visual_prompt = plan.get("main_visual_prompt")
    main_visual_prompt = main_visual_prompt.strip() if isinstance(main_visual_prompt, str) and main_visual_prompt.strip() else ""

    supporting_visuals_raw = plan.get("supporting_visuals")
    supporting_visuals = tuple(
        s.strip() for s in supporting_visuals_raw if isinstance(s, str) and s.strip()
    )[:3] if isinstance(supporting_visuals_raw, list) else ()

    text_cues_raw = plan.get("text_cues")
    text_cues = []
    if isinstance(text_cues_raw, list):
        for cue_raw in text_cues_raw[:2]:
            validated_cue = _validate_visual_plan_text_cue(cue_raw, scene_duration=scene_duration)
            if validated_cue is not None:
                text_cues.append(TextCue(**validated_cue))

    sticker = plan.get("sticker")
    if sticker not in STICKER_NAME_CHOICES:
        sticker = "none"
    sticker_start = plan.get("sticker_start_seconds")
    if not isinstance(sticker_start, (int, float)):
        sticker_start = 0.0
    sticker_start = max(0.0, min(scene_duration, float(sticker_start)))

    motion = plan.get("motion")
    if motion not in MOTION_CHOICES:
        motion = DEFAULT_MOTION

    transition = plan.get("transition")
    if transition not in TRANSITION_CHOICES:
        transition = DEFAULT_TRANSITION

    emotion = plan.get("emotion")
    emotion = emotion.strip() if isinstance(emotion, str) and emotion.strip() else "neutral"

    pacing = plan.get("pacing")
    if pacing not in PACING_CHOICES:
        pacing = DEFAULT_PACING

    lighting = plan.get("lighting")
    if lighting not in LIGHTING_CHOICES:
        lighting = DEFAULT_LIGHTING

    voiceover = plan.get("voiceover")
    voiceover = voiceover.strip() if isinstance(voiceover, str) else ""

    camera_shot = plan.get("camera_shot")
    if camera_shot not in CAMERA_SHOT_CHOICES:
        camera_shot = DEFAULT_CAMERA_SHOT

    camera_movement = plan.get("camera_movement")
    if camera_movement not in CAMERA_MOVEMENT_CHOICES:
        camera_movement = DEFAULT_CAMERA_MOVEMENT

    environment = plan.get("environment")
    environment = environment.strip() if isinstance(environment, str) else ""

    subject_action = plan.get("subject_action")
    subject_action = subject_action.strip() if isinstance(subject_action, str) else ""

    b_roll_type = plan.get("b_roll_type")
    if b_roll_type not in B_ROLL_TYPE_CHOICES:
        b_roll_type = DEFAULT_B_ROLL_TYPE

    text_animation = plan.get("text_animation")
    text_animation = text_animation.strip() if isinstance(text_animation, str) else ""

    music_mood = plan.get("music_mood")
    if music_mood not in MUSIC_MOOD_CHOICES:
        music_mood = DEFAULT_MUSIC_MOOD

    plan = ScenePlan(
        scene_number=scene_number, visual_type="photo_style", main_visual_prompt=main_visual_prompt,
        supporting_visuals=supporting_visuals, text_cues=tuple(text_cues), sticker=sticker,
        sticker_start_seconds=sticker_start, motion=motion, emotion=emotion, pacing=pacing,
        lighting=lighting, visual_source=visual_source, transition=transition, is_hook=is_hook,
        voiceover=voiceover, camera_shot=camera_shot, camera_movement=camera_movement,
        environment=environment, subject_action=subject_action, b_roll_type=b_roll_type,
        text_animation=text_animation, music_mood=music_mood,
    )
    # Visual Reel Generator stage's own quality gate ("would this scene
    # still make sense with text removed" -> visual_quality_score) -
    # computed here, once, right after the plan's own real fields are
    # known, so every caller (generate_visual_plan()'s own retry-on-
    # low-quality loop, a reopened project reading a stored plan, a
    # single-scene regeneration) sees the SAME score without needing to
    # recompute it - see jarvis.reel_generator.visual_quality's own
    # docstring for exactly how it's scored.
    return dataclasses.replace(plan, visual_quality_score=visual_quality.score_scene_plan(plan).score)


_VISUAL_PLAN_MAX_ATTEMPTS = 4
# Hand-tested directly against the real Claude API (not just mocked
# tests): a genuine, intermittent (roughly 1-in-3 to 1-in-4 in several
# observed sessions, independent per call - not tied to any specific
# storyboard's own content, confirmed by re-running the SAME storyboard
# repeatedly and seeing both outcomes) failure where a single call's own
# JSON response is malformed
# (a real, non-deterministic LLM output quality issue, not a bug in
# this module's own request/parsing code - confirmed by capturing and
# inspecting several real raw responses side by side, most valid, one
# genuinely broken JSON) or returns the wrong scene count. Retrying the
# SAME call a few times is the standard, correct mitigation for this
# class of failure (an LLM's own occasional malformed output), the same
# way this codebase already retries other flaky operations rather than
# trying to out-guess or repair a specific malformation after the fact.


def _call_llm_for_json_logged(llm: LLMClient, *, system: str, prompt: str, attempt: int) -> Any | None:
    """Same job as _call_llm_for_json() above, but for
    generate_visual_plan()'s own calls specifically - logs EXACTLY which
    of the three real, distinct failure modes occurred (an SDK exception
    e.g. rate limit/connection error; an empty response; malformed JSON)
    rather than collapsing all three into an indistinguishable None, the
    way _call_llm_for_json() does for generate_storyboard()'s own calls
    (left completely unmodified - see this module's own docstring on why
    generate_visual_plan() never touches that function or its callers).
    Added specifically because this function's own retry behavior (see
    _VISUAL_PLAN_MAX_ATTEMPTS) was hand-tested to sometimes fail even
    after every retry, in a live session, without ever pinning down
    WHICH of the three causes was actually occurring - this closes that
    diagnostic gap for any future occurrence, visible in the terminal/
    log exactly like this codebase's other logger.exception() usages."""
    try:
        response = llm.send(
            [{"role": "user", "content": prompt}], [], max_tokens=_VISUAL_PLAN_MAX_GENERATION_TOKENS, system=system,
        )
    except Exception:
        logger.exception("generate_visual_plan() attempt %d: LLM call raised an exception", attempt)
        return None

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        logger.warning("generate_visual_plan() attempt %d: LLM returned an empty response", attempt)
        return None

    parsed = _extract_json(text)
    if parsed is None:
        logger.warning(
            "generate_visual_plan() attempt %d: response was not valid JSON (len=%d). "
            "First 300 chars: %r ... Last 300 chars: %r",
            attempt, len(text), text[:300], text[-300:],
        )
    return parsed


def generate_visual_plan(
    llm: LLMClient, brief: ReelBrief, storyboard: Storyboard, *, visual_style: str = "",
) -> VisualPlan | None:
    """Generates a VisualPlan (one ScenePlan per scene, requirements
    1-8/18's Smart Visual Director) from an already-generated
    Storyboard. Retries the LLM call up to _VISUAL_PLAN_MAX_ATTEMPTS
    times if a response is malformed JSON or has the wrong scene COUNT
    (both real, hand-tested, intermittent LLM output failures - see
    _VISUAL_PLAN_MAX_ATTEMPTS's own docstring) - every OTHER malformed
    field within an otherwise-valid response degrades to a safe default
    per-field instead of triggering a retry (see
    _validate_scene_plan()'s own docstring). Returns None only if every
    attempt fails - never raises; each attempt's own specific failure
    reason is logged (see _call_llm_for_json_logged()'s own docstring)
    so a real, live failure can be diagnosed from the terminal/log
    instead of only ever seeing an undifferentiated None. The first
    scene in `storyboard.scenes` is treated as the hook (requirement 6)
    regardless of its own segment_kind (a Reel's HOOK segment is always
    scene 1, per generate_storyboard()'s own "HOOK segment is always
    exactly one scene" rule). Does not render anything - see
    jarvis.reel_generator.scene_render for that.

    Visual Reel Generator stage's own quality gate: a SUCCESSFULLY
    parsed plan (right shape, valid JSON) is additionally checked via
    jarvis.reel_generator.visual_quality.score_scene_plan() (per scene)
    - if any scene scores below MIN_RENDERABLE_QUALITY_SCORE, this
    counts as a retry-worthy attempt too (same attempt budget as a
    malformed-JSON retry, not an additional one), since "the visual
    plan" the module brief refers to regenerating on a failed quality
    check IS this same LLM call. The BEST-scoring attempt seen across
    all tries is kept as a fallback if NONE fully passes (never returns
    None just because quality is low - a low-scoring plan is still
    real, usable data; the storyboard UI surfaces the score/reasons so
    a person can regenerate a specific scene manually, matching this
    codebase's own "never silently discard real generated content"
    convention)."""
    if not storyboard.scenes:
        return None

    system = _visual_plan_system_prompt(brief, storyboard, visual_style=visual_style)
    prompt = f"Topic: {brief.topic}\nStyle: {brief.style}"

    best_plan: VisualPlan | None = None
    best_min_score = -1

    for attempt in range(_VISUAL_PLAN_MAX_ATTEMPTS):
        result = _call_llm_for_json_logged(llm, system=system, prompt=prompt, attempt=attempt)
        if not isinstance(result, dict):
            continue
        scenes_raw = result.get("scenes")
        if not isinstance(scenes_raw, list) or len(scenes_raw) != len(storyboard.scenes):
            logger.warning(
                "generate_visual_plan() attempt %d: expected %d scenes, got %s",
                attempt, len(storyboard.scenes),
                len(scenes_raw) if isinstance(scenes_raw, list) else type(scenes_raw).__name__,
            )
            continue

        scenes_by_number = {s.number: s for s in storyboard.scenes}
        plans = []
        for i, raw in enumerate(scenes_raw):
            scene_number = raw.get("scene_number") if isinstance(raw, dict) else None
            if not isinstance(scene_number, int) or scene_number not in scenes_by_number:
                scene_number = storyboard.scenes[i].number  # fall back to positional matching
            scene = scenes_by_number[scene_number]
            plans.append(_validate_scene_plan(
                raw, scene_number=scene_number, scene_duration=scene.duration_seconds,
                is_hook=(scene_number == storyboard.scenes[0].number),
            ))

        candidate = VisualPlan(scenes=tuple(plans), visual_style=visual_style)
        min_score = min(p.visual_quality_score for p in plans)
        if min_score >= visual_quality.MIN_RENDERABLE_QUALITY_SCORE:
            return candidate  # every scene passes the quality gate - done

        logger.warning(
            "generate_visual_plan() attempt %d: lowest scene quality score was %d (below the %d floor) - retrying",
            attempt, min_score, visual_quality.MIN_RENDERABLE_QUALITY_SCORE,
        )
        if min_score > best_min_score:
            best_plan, best_min_score = candidate, min_score

    if best_plan is not None:
        logger.error(
            "generate_visual_plan() never reached the %d quality floor after %d attempts - "
            "returning the best-scoring plan seen (lowest scene score %d)",
            visual_quality.MIN_RENDERABLE_QUALITY_SCORE, _VISUAL_PLAN_MAX_ATTEMPTS, best_min_score,
        )
        return best_plan

    logger.error(
        "generate_visual_plan() failed after %d attempts for a %d-scene storyboard",
        _VISUAL_PLAN_MAX_ATTEMPTS, len(storyboard.scenes),
    )
    return None


# --- Storyboard Creative Controls stage: single-scene targeted regeneration --------------
#
# CHANGE CAMERA / CHANGE STYLE / CHANGE VISUAL each regenerate ONE
# ASPECT of ONE scene's already-existing ScenePlan, via its own small,
# focused LLM call - never the whole VisualPlan (that remains
# generate_visual_plan()'s own job). All three share
# _regenerate_scene_aspect() for the actual call/retry/validation
# machinery, differing only in their own system prompt and which
# ScenePlan fields the model is asked to change vs. keep untouched.
# Each returns an updated ScenePlan (same scene_number, same object
# shape as generate_visual_plan()'s own per-scene output) with a freshly
# recomputed visual_quality_score - or None if every attempt failed
# (never raises), matching this module's own established
# "None on total failure, safe per-field fallback otherwise" convention.

_SCENE_ASPECT_MAX_ATTEMPTS = 3
# A smaller budget than _VISUAL_PLAN_MAX_ATTEMPTS (4) - this call asks
# for ONE scene's worth of JSON, a much smaller/simpler response than a
# whole multi-scene plan, so the same malformed-JSON failure class (see
# _VISUAL_PLAN_MAX_ATTEMPTS's own docstring) is expected to be rarer
# here; 3 attempts is still real headroom without letting a single
# button click burn an excessive number of retries/tokens.


def _call_llm_for_single_scene_json(llm: LLMClient, *, system: str, prompt: str, attempt: int, action: str) -> Any | None:
    """Same job as _call_llm_for_json_logged() above, for the
    single-scene aspect-regeneration calls specifically - logs under
    `action`'s own name (e.g. "regenerate_scene_camera") so a real
    failure is traceable to which button click caused it."""
    try:
        response = llm.send(
            [{"role": "user", "content": prompt}], [], max_tokens=600, system=system,
        )
    except Exception:
        logger.exception("%s() attempt %d: LLM call raised an exception", action, attempt)
        return None

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        logger.warning("%s() attempt %d: LLM returned an empty response", action, attempt)
        return None

    parsed = _extract_json(text)
    if parsed is None:
        logger.warning("%s() attempt %d: response was not valid JSON. First 200 chars: %r", action, attempt, text[:200])
    return parsed


def _regenerate_scene_aspect(
    llm: LLMClient, *, scene: Scene, plan: ScenePlan, system: str, action: str,
) -> ScenePlan | None:
    """Shared retry/validate loop for CHANGE CAMERA/STYLE/VISUAL - calls
    `system` (already built by the caller, describing exactly what
    should change and what must stay the same) up to
    _SCENE_ASPECT_MAX_ATTEMPTS times, validates the response the SAME
    way _validate_scene_plan() would (reusing that exact function, so a
    malformed individual field degrades to a safe default instead of
    failing the whole call - identical convention to
    generate_visual_plan()'s own per-field fallback), and recomputes
    visual_quality_score. Returns None only if every attempt's own LLM
    call/JSON parsing failed outright (never for a low quality score -
    a single-aspect regeneration has no retry-on-low-quality loop of its
    own the way generate_visual_plan() does, since the caller/GUI shows
    the person the new score directly and lets them try again
    themselves, rather than silently retrying on their behalf for a
    targeted, deliberate single click)."""
    prompt = f"scene_number: {scene.number}\ncurrent on_screen_text: {scene.on_screen_text}"
    for attempt in range(_SCENE_ASPECT_MAX_ATTEMPTS):
        result = _call_llm_for_single_scene_json(llm, system=system, prompt=prompt, attempt=attempt, action=action)
        if not isinstance(result, dict):
            continue
        # _validate_scene_plan() expects a "scenes"-array-shaped dict
        # elsewhere in this module - here the model returns ONE scene
        # object directly (no array wrapper needed for a single-scene
        # call), so scene_number is read straight off `result` itself
        # rather than iterated from a list.
        new_plan = _validate_scene_plan(
            result, scene_number=scene.number, scene_duration=scene.duration_seconds, is_hook=plan.is_hook,
        )
        return new_plan
    logger.error("%s() failed after %d attempts for scene %d", action, _SCENE_ASPECT_MAX_ATTEMPTS, scene.number)
    return None


def regenerate_scene_camera(llm: LLMClient, *, scene: Scene, plan: ScenePlan, camera_shot: str | None = None) -> ScenePlan | None:
    """CHANGE CAMERA: regenerates ONLY this scene's camera_shot/
    camera_movement (and, since a new angle can call for a re-blocked
    subject, subject_action) while explicitly preserving the scene's own
    purpose/content (on_screen_text, environment, visual_source,
    emotion, everything else). If `camera_shot` is given (one of
    CAMERA_SHOT_CHOICES - the GUI's own CHANGE CAMERA menu selection),
    the model is told to use EXACTLY that shot and only decide a
    matching camera_movement/subject_action; if omitted, the model picks
    a shot different from the scene's own CURRENT one."""
    shot_instruction = (
        f"Use EXACTLY this camera shot: \"{camera_shot}\"." if camera_shot in CAMERA_SHOT_CHOICES
        else f"Pick a DIFFERENT camera_shot than the current one (\"{plan.camera_shot}\")."
    )
    system = (
        "You are a Visual Story Director adjusting ONE scene's camera direction for a short-form "
        "Instagram Reel. Keep the scene's own PURPOSE, subject, and environment exactly the same - "
        f"only change HOW it's shot.\n\nScene's current details:\n"
        f"on_screen_text: \"{scene.on_screen_text}\"\nenvironment: \"{plan.environment}\"\n"
        f"subject_action: \"{plan.subject_action}\"\ncurrent camera_shot: \"{plan.camera_shot}\"\n"
        f"current camera_movement: \"{plan.camera_movement}\"\n\n{shot_instruction}\n\n"
        "Respond with a JSON object for this ONE scene with fields: \"scene_number\" (must be "
        f"{scene.number}), \"camera_shot\" (one of: " + ", ".join(CAMERA_SHOT_CHOICES) + "), "
        "\"camera_movement\" (one of: " + ", ".join(CAMERA_MOVEMENT_CHOICES) + "), \"subject_action\" "
        "(re-describe the subject's action/pose to match the NEW camera shot, if needed - otherwise "
        "repeat the same one), and \"main_visual_prompt\" (rewrite the full camera-ready description "
        "reflecting the new shot). Keep \"environment\", \"visual_source\", \"emotion\" unchanged from "
        f"the current values above (visual_source: \"{plan.visual_source}\", emotion: \"{plan.emotion}\").\n\n"
        + _JSON_RESPONSE_INSTRUCTION
    )
    result = _regenerate_scene_aspect(llm, scene=scene, plan=plan, system=system, action="regenerate_scene_camera")
    if result is None:
        return None
    # Preserve every field the prompt asked the model to leave alone,
    # even if it answered inconsistently - a real, structural guarantee
    # (not just a prompt instruction) that CHANGE CAMERA never silently
    # changes the scene's own environment/visual_source/emotion/text_cues.
    return dataclasses.replace(
        result, environment=plan.environment, visual_source=plan.visual_source, emotion=plan.emotion,
        text_cues=plan.text_cues, sticker=plan.sticker, sticker_start_seconds=plan.sticker_start_seconds,
        voiceover=plan.voiceover, b_roll_type=plan.b_roll_type, music_mood=plan.music_mood,
        supporting_visuals=plan.supporting_visuals, motion=plan.motion, transition=plan.transition,
        pacing=plan.pacing, lighting=plan.lighting,
    )


def regenerate_scene_style(llm: LLMClient, *, scene: Scene, plan: ScenePlan, visual_style: str) -> ScenePlan | None:
    """CHANGE STYLE: regenerates this scene's main_visual_prompt/
    lighting to match a NEWLY CHOSEN whole-look style (one of
    VISUAL_STYLE_CHOICES, e.g. "cinematic"/"luxury"/"minimal") while
    keeping the scene's own subject/action/environment/camera exactly
    the same - only the aesthetic TREATMENT changes, not what the scene
    actually shows."""
    style_label = visual_style if visual_style in VISUAL_STYLE_CHOICES else DEFAULT_VISUAL_STYLE
    system = (
        f"You are a Visual Story Director restyling ONE scene for a short-form Instagram Reel to match "
        f"a \"{style_label}\" visual style. Keep the scene's own SUBJECT, ACTION, ENVIRONMENT, and CAMERA "
        "shot/movement exactly the same - only change the aesthetic treatment (lighting, mood, "
        "rendering feel) to genuinely feel like a \"" + style_label + "\" Reel.\n\n"
        f"Scene's current details:\nenvironment: \"{plan.environment}\"\nsubject_action: "
        f"\"{plan.subject_action}\"\ncamera_shot: \"{plan.camera_shot}\"\ncamera_movement: "
        f"\"{plan.camera_movement}\"\ncurrent main_visual_prompt: \"{plan.main_visual_prompt}\"\n\n"
        "Respond with a JSON object for this ONE scene with fields: \"scene_number\" (must be "
        f"{scene.number}), \"main_visual_prompt\" (rewritten to genuinely feel like the \"{style_label}\" "
        "style, same subject/action/environment/camera), and \"lighting\" (one of: "
        + ", ".join(LIGHTING_CHOICES) + " - pick whichever best matches this style). Keep "
        "\"camera_shot\"/\"camera_movement\"/\"environment\"/\"subject_action\" unchanged from the "
        "current values above.\n\n" + _JSON_RESPONSE_INSTRUCTION
    )
    result = _regenerate_scene_aspect(llm, scene=scene, plan=plan, system=system, action="regenerate_scene_style")
    if result is None:
        return None
    return dataclasses.replace(
        result, environment=plan.environment, subject_action=plan.subject_action,
        camera_shot=plan.camera_shot, camera_movement=plan.camera_movement, visual_source=plan.visual_source,
        text_cues=plan.text_cues, sticker=plan.sticker, sticker_start_seconds=plan.sticker_start_seconds,
        voiceover=plan.voiceover, b_roll_type=plan.b_roll_type, music_mood=plan.music_mood,
        supporting_visuals=plan.supporting_visuals, motion=plan.motion, transition=plan.transition,
        pacing=plan.pacing, emotion=plan.emotion,
    )


def regenerate_scene_visual(llm: LLMClient, *, scene: Scene, plan: ScenePlan) -> ScenePlan | None:
    """CHANGE VISUAL: "create a completely different visual
    interpretation of the same scene idea" - regenerates
    environment/subject_action/main_visual_prompt (and, since a
    genuinely different visual often calls for it, camera_shot/
    camera_movement) from scratch, keeping only the scene's own
    PURPOSE (on_screen_text/voice_text, unchanged - this is a Scene-
    level field this function never touches at all) the same. This is
    the ONE of the three that's allowed to change camera framing too
    (unlike CHANGE CAMERA, which ONLY changes camera; unlike CHANGE
    STYLE, which ONLY changes aesthetic treatment) - a genuinely
    different visual interpretation often naturally wants a different
    shot to match."""
    system = (
        "You are a Visual Story Director creating a COMPLETELY DIFFERENT visual interpretation of ONE "
        "scene for a short-form Instagram Reel - same underlying idea/purpose, but a genuinely "
        "different subject, action, environment, and camera treatment than what's there now. This is "
        "not a small tweak - imagine reshooting this scene from scratch with a different creative "
        f"concept.\n\nScene's own purpose (on-screen text, must still be communicated visually): "
        f"\"{scene.on_screen_text}\"\nCurrent visual (to move AWAY from): \"{plan.main_visual_prompt}\"\n\n"
        "Respond with a JSON object for this ONE scene with fields: \"scene_number\" (must be "
        f"{scene.number}), \"environment\" (a genuinely different setting/location than the current "
        "one), \"subject_action\" (a genuinely different subject/action), \"main_visual_prompt\" (a "
        "detailed, camera-ready description of this new interpretation), \"camera_shot\" (one of: "
        + ", ".join(CAMERA_SHOT_CHOICES) + "), \"camera_movement\" (one of: "
        + ", ".join(CAMERA_MOVEMENT_CHOICES) + "), and \"visual_source\" (one of: "
        + ", ".join(VISUAL_SOURCE_CHOICES) + ").\n\n" + _JSON_RESPONSE_INSTRUCTION
    )
    result = _regenerate_scene_aspect(llm, scene=scene, plan=plan, system=system, action="regenerate_scene_visual")
    if result is None:
        return None
    return dataclasses.replace(
        result, text_cues=plan.text_cues, sticker=plan.sticker, sticker_start_seconds=plan.sticker_start_seconds,
        voiceover=plan.voiceover, b_roll_type=plan.b_roll_type, music_mood=plan.music_mood,
        supporting_visuals=plan.supporting_visuals, motion=plan.motion, transition=plan.transition,
        pacing=plan.pacing, lighting=plan.lighting, emotion=plan.emotion,
    )
