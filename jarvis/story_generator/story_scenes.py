"""Story scene generation (module brief, requirements 2-3, and this
feature's own visual-richness brief, requirements 1-7): splits an
approved 8-beat StoryStructure into 5-10 visual scenes, each with a
scene number, duration, voice-over/narration, on-screen text, visual
description, mood, transition suggestion, AND a full per-scene visual
plan (jarvis.story_generator.visual_plan.ScenePlan - visual type,
supporting visuals, on-screen text cue timing/position/animation,
sticker, motion, emotion/pacing/lighting).

Deliberately reuses jarvis.reel_generator.storyboard.Scene/Storyboard
AS-IS (see this package's own __init__.py docstring) rather than
defining a parallel StoryScene dataclass - a Scene's `segment_kind`
field holds the beat kind it came from (one of
jarvis.story_generator.structure.BEAT_NAMES, not Reel Generator's own
hook/value/cta) and Scene's `mood`/`transition` fields (added
specifically for this package - see that dataclass's own docstring)
are populated here. This is exactly what lets
jarvis.reel_generator.scenes.render_all_scenes()/
jarvis.reel_generator.export.export_reel_video() be called completely
unmodified downstream for a plan-less/degraded scene, and what lets
jarvis.story_generator.scene_render's own richer rendering read a
Scene's plain fields as its fallback when a ScenePlan is missing.

ONE isolated, tool-free, JSON-only LLM call produces BOTH the Scene
fields and each scene's ScenePlan together (no second LLM round-trip -
the model already has full context on the story/scene when it writes
voice_text/on_screen_text, and the visual plan is just more detail
about that same scene, not an independent decision)."""

from __future__ import annotations

import json
import re
from typing import Any

from jarvis.core.llm import LLMClient
from jarvis.reel_generator.storyboard import Scene, Storyboard
from jarvis.story_generator.structure import BEAT_NAMES, StoryStructure
from jarvis.story_generator.visual_plan import (
    MOTION_CHOICES,
    PACING_CHOICES,
    LIGHTING_CHOICES,
    STICKER_NAME_CHOICES,
    TEXT_ANIMATION_CHOICES,
    TEXT_POSITION_CHOICES,
    VISUAL_TYPE_CHOICES,
    DEFAULT_LIGHTING,
    DEFAULT_MOTION,
    DEFAULT_PACING,
    DEFAULT_VISUAL_TYPE,
    ScenePlan,
    TextCue,
    VisualPlan,
)

_MAX_GENERATION_TOKENS = 4000
# Raised from Stage 1's 2500 - each scene now also carries its own
# ScenePlan (visual type, supporting visuals, text cues, sticker,
# motion, emotion/pacing/lighting), roughly doubling the JSON response
# size for the same 5-10 scene count.

_JSON_RESPONSE_INSTRUCTION = (
    "Reply with ONLY a single valid JSON object matching the requested shape - "
    "no explanation, no markdown code fences, no leading/trailing text of any "
    "kind. The response must be parseable by a strict JSON parser as-is."
)

# Module brief requirement 2: "Convert the story into 5-10 visual
# scenes."
_MIN_SCENES = 5
_MAX_SCENES = 10

# A per-scene default duration (seconds) used only when the model
# doesn't give a usable one - matches jarvis.reel_generator.script's own
# "a plain, checkable, honest number" preference over trusting an
# unchecked model-reported value outright; here it's a floor/fallback,
# not the primary source (start_seconds/end_seconds still come from the
# model when they validate).
_DEFAULT_SCENE_DURATION_SECONDS = 4.0

_STICKER_NAMES_FOR_PROMPT = ", ".join(n for n in STICKER_NAME_CHOICES if n != "none")


def _system_prompt(structure: StoryStructure) -> str:
    return (
        "You are a short-form vertical video storyboard artist and visual director. Given a "
        "complete 8-beat story (hook, setup, conflict, emotional_development, turning_point, "
        f"transformation, conclusion, cta), break it into between {_MIN_SCENES} and {_MAX_SCENES} "
        "visual scenes total, in order, covering every beat (a simple beat may become exactly one "
        "scene, a richer beat may become two or three) - never skip a beat, never reorder beats. "
        "For each scene write:\n\n"
        "SCENE FIELDS:\n"
        "\"beat_kind\" (which beat this scene belongs to, one of: " + ", ".join(BEAT_NAMES) + "), "
        "\"duration_seconds\" (a number between 2 and 8), \"voice_text\" (the voice-over/narration "
        "line for this scene - a natural spoken adaptation of that beat's text, not a verbatim "
        "copy), \"on_screen_text\" (a SHORT on-screen caption phrase, a few words, never the full "
        "voice_text verbatim), \"visual_description\" (a short, plain description of what the "
        "scene should show), \"mood\" (one or two words, e.g. \"tense\", \"hopeful\"), and "
        "\"transition\" (a short suggested transition INTO the NEXT scene, e.g. \"hard cut\", "
        "\"fade to black\", \"slow zoom\" - the LAST scene's transition should be \"end\").\n\n"
        "VISUAL PLAN FIELDS (\"visual_plan\" object, for the SAME scene):\n"
        "\"visual_type\" (one of: " + ", ".join(VISUAL_TYPE_CHOICES) + " - whichever best fits what "
        "this scene should show), \"main_visual_prompt\" (a DETAILED description of the main visual "
        "that directly supports the meaning and emotion of the narration - specific enough that an "
        "artist could draw it, e.g. \"a cluttered desk at night, a single lamp lighting a laptop "
        "showing a failed sales dashboard\", not just \"a desk\"), \"supporting_visuals\" (an array "
        "of 0-3 short labels for additional elements when they'd genuinely help, e.g. \"arrow\", "
        "\"before/after split\", \"screenshot\", \"circle highlight\" - an empty array is fine and "
        "often correct), \"text_cues\" (an array of 1-2 on-screen text overlay objects, each with "
        "\"text\" (short phrase), \"position\" (one of: " + ", ".join(TEXT_POSITION_CHOICES) + "), "
        "\"start_seconds\"/\"end_seconds\" (relative to THIS SCENE's own start, both within "
        "0-duration_seconds), and \"animation\" (one of: " + ", ".join(TEXT_ANIMATION_CHOICES) + ")), "
        "\"sticker\" (one of: none, " + _STICKER_NAMES_FOR_PROMPT + " - pick \"none\" for most "
        "scenes; only pick a real sticker when it genuinely improves the storytelling, never "
        "decoratively on every scene), \"sticker_start_seconds\" (relative to this scene's own "
        "start, ignored if sticker is \"none\"), \"motion\" (one of: " + ", ".join(MOTION_CHOICES) + " "
        "- the camera/zoom movement for this scene), \"emotion\" (one or two words), \"pacing\" "
        "(one of: " + ", ".join(PACING_CHOICES) + "), and \"lighting\" (one of: "
        + ", ".join(LIGHTING_CHOICES) + ").\n\n"
        f"Write voice_text/on_screen_text/text_cues text in {_language_label(structure)}.\n\n"
        + _JSON_RESPONSE_INSTRUCTION + "\n\n"
        "Respond with a JSON object with exactly one field, \"scenes\", an array of scene objects "
        "in order, each with fields: \"beat_kind\", \"duration_seconds\", \"voice_text\", "
        "\"on_screen_text\", \"visual_description\", \"mood\", \"transition\", and \"visual_plan\" "
        "(an object with the visual plan fields listed above)."
    )


def _language_label(structure: StoryStructure) -> str:
    return "Lithuanian" if structure.language == "lt" else "English"


def _extract_json(text: str) -> Any | None:
    stripped = text.strip()
    fence_match = re.match(r"^```(?:json)?\s*\n(.*)\n```$", stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


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


def _validate_text_cue(raw: Any, *, scene_duration: float) -> dict | None:
    if not isinstance(raw, dict):
        return None
    text = raw.get("text")
    position = raw.get("position")
    start = raw.get("start_seconds")
    end = raw.get("end_seconds")
    animation = raw.get("animation")

    if not isinstance(text, str) or not text.strip():
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


def _validate_visual_plan(raw: Any, *, scene_number: int, scene_duration: float) -> dict:
    """Validates a scene's "visual_plan" object. Unlike scene-level
    validation (a malformed scene fails the WHOLE response - see
    _validate_scenes() below), an individual visual-plan field falling
    back to a safe default rather than failing the whole story is
    deliberate: the visual plan is enrichment on top of an
    already-valid scene, and a story shouldn't be thrown away over one
    scene's stray "lighting" value - matches
    jarvis.reel_generator.scenes.render_all_scenes()'s own "one item's
    imperfection doesn't sink the whole batch" spirit, applied here at
    the field level instead of the scene level."""
    plan = raw if isinstance(raw, dict) else {}

    visual_type = plan.get("visual_type")
    if visual_type not in VISUAL_TYPE_CHOICES:
        visual_type = DEFAULT_VISUAL_TYPE

    main_visual_prompt = plan.get("main_visual_prompt")
    if not isinstance(main_visual_prompt, str) or not main_visual_prompt.strip():
        main_visual_prompt = ""

    supporting_visuals_raw = plan.get("supporting_visuals")
    supporting_visuals = tuple(
        s.strip() for s in supporting_visuals_raw if isinstance(s, str) and s.strip()
    )[:3] if isinstance(supporting_visuals_raw, list) else ()

    text_cues_raw = plan.get("text_cues")
    text_cues = []
    if isinstance(text_cues_raw, list):
        for cue_raw in text_cues_raw[:2]:
            validated_cue = _validate_text_cue(cue_raw, scene_duration=scene_duration)
            if validated_cue is not None:
                text_cues.append(validated_cue)

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

    emotion = plan.get("emotion")
    emotion = emotion.strip() if isinstance(emotion, str) and emotion.strip() else "neutral"

    pacing = plan.get("pacing")
    if pacing not in PACING_CHOICES:
        pacing = DEFAULT_PACING

    lighting = plan.get("lighting")
    if lighting not in LIGHTING_CHOICES:
        lighting = DEFAULT_LIGHTING

    return {
        "scene_number": scene_number, "visual_type": visual_type, "main_visual_prompt": main_visual_prompt,
        "supporting_visuals": supporting_visuals, "text_cues": text_cues, "sticker": sticker,
        "sticker_start_seconds": sticker_start, "motion": motion, "emotion": emotion,
        "pacing": pacing, "lighting": lighting,
    }


def _validate_scenes(data: Any) -> list[dict] | None:
    if not isinstance(data, dict):
        return None
    scenes = data.get("scenes")
    if not isinstance(scenes, list) or not (_MIN_SCENES <= len(scenes) <= _MAX_SCENES):
        return None

    validated: list[dict] = []
    seen_beats: set[str] = set()
    for i, raw in enumerate(scenes, start=1):
        if not isinstance(raw, dict):
            return None
        beat_kind = raw.get("beat_kind")
        voice_text = raw.get("voice_text")
        on_screen_text = raw.get("on_screen_text")
        visual_description = raw.get("visual_description")
        mood = raw.get("mood")
        transition = raw.get("transition")
        duration = raw.get("duration_seconds")

        if beat_kind not in BEAT_NAMES:
            return None
        if not isinstance(voice_text, str) or not voice_text.strip():
            return None
        if not isinstance(on_screen_text, str) or not on_screen_text.strip():
            return None
        if not isinstance(visual_description, str) or not visual_description.strip():
            return None
        if not isinstance(mood, str) or not mood.strip():
            return None
        if not isinstance(transition, str) or not transition.strip():
            return None
        if not isinstance(duration, (int, float)) or duration <= 0:
            duration = _DEFAULT_SCENE_DURATION_SECONDS
        duration = float(duration)

        seen_beats.add(beat_kind)
        visual_plan = _validate_visual_plan(raw.get("visual_plan"), scene_number=i, scene_duration=duration)
        validated.append({
            "beat_kind": beat_kind, "duration_seconds": duration,
            "voice_text": voice_text.strip(), "on_screen_text": on_screen_text.strip(),
            "visual_description": visual_description.strip(),
            "mood": mood.strip(), "transition": transition.strip(),
            "visual_plan": visual_plan,
        })

    # Every beat must be covered by at least one scene (module brief
    # requirement 1's full arc must survive into requirement 2's scenes -
    # a story missing its own turning point/transformation scene isn't
    # really the same story anymore).
    if not set(BEAT_NAMES).issubset(seen_beats):
        return None

    return validated


def generate_story_scenes(llm: LLMClient, structure: StoryStructure) -> tuple[Storyboard, VisualPlan] | None:
    """Generates a Storyboard (5-10 Scenes, reusing
    jarvis.reel_generator.storyboard.Scene/Storyboard as-is) AND its
    matching VisualPlan (one ScenePlan per Scene) from an already-
    approved StoryStructure, in a single LLM call. Returns None on any
    failure (LLM error, malformed response, wrong scene count, a beat
    left uncovered) - never raises. A malformed VISUAL PLAN field for
    an otherwise-valid scene falls back to a safe default rather than
    failing the whole generation (see _validate_visual_plan()'s own
    docstring) - only a malformed SCENE fails the whole call. Does not
    render any visuals - see jarvis.story_generator.scene_render for
    that."""
    beat_lines = "\n".join(f"{b.label} ({b.kind}): {b.text}" for b in structure.beats)
    prompt = f"Story type: {structure.story_type}\n\nApproved story:\n{beat_lines}"

    result = _call_llm_for_json(llm, system=_system_prompt(structure), prompt=prompt)
    validated = _validate_scenes(result)
    if validated is None:
        return None

    scenes = []
    plans = []
    cursor = 0.0
    for i, s in enumerate(validated, start=1):
        start = cursor
        end = cursor + s["duration_seconds"]
        scenes.append(Scene(
            number=i, start_seconds=start, end_seconds=end,
            segment_kind=s["beat_kind"], voice_text=s["voice_text"],
            on_screen_text=s["on_screen_text"], visual_description=s["visual_description"],
            mood=s["mood"], transition=s["transition"],
        ))
        vp = s["visual_plan"]
        plans.append(ScenePlan(
            scene_number=i, visual_type=vp["visual_type"], main_visual_prompt=vp["main_visual_prompt"],
            supporting_visuals=vp["supporting_visuals"],
            text_cues=tuple(TextCue(**c) for c in vp["text_cues"]),
            sticker=vp["sticker"], sticker_start_seconds=vp["sticker_start_seconds"],
            motion=vp["motion"], emotion=vp["emotion"], pacing=vp["pacing"], lighting=vp["lighting"],
        ))
        cursor = end

    return Storyboard(scenes=tuple(scenes)), VisualPlan(scenes=tuple(plans))
