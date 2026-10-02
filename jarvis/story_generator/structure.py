"""Story structure generation (module brief, requirement 1): turns a
plain-language story idea into a complete 8-beat narrative structure -
Hook, Setup, Conflict/problem, Emotional development, Turning point,
Transformation, Conclusion, CTA - via one isolated, tool-free, JSON-only
LLM call.

Same _extract_json()/_call_llm_for_json() convention as
jarvis.reel_generator.brief/.script/.storyboard (see any of those
modules' own docstrings for the full rationale) - this module keeps its
own private copy rather than importing another module's, matching this
codebase's established "each feature module's backend stays
self-contained" convention.

Nothing here is finalized until the person clicks "APPROVE STORY" (see
this package's own GUI dashboard for the approval gate) - this module
only ever GENERATES a proposed structure, never splits it into scenes
or renders/exports anything (see jarvis.story_generator.story_scenes
for the next stage)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from jarvis.core.llm import LLMClient

_MAX_GENERATION_TOKENS = 1500

_JSON_RESPONSE_INSTRUCTION = (
    "Reply with ONLY a single valid JSON object matching the requested shape - "
    "no explanation, no markdown code fences, no leading/trailing text of any "
    "kind. The response must be parseable by a strict JSON parser as-is."
)

# The 8 fixed narrative beats (module brief requirement 1, exact order) -
# every StoryStructure always has exactly these 8 fields, regardless of
# story_type (see this package's own __init__.py docstring on
# modularity: story_type only steers the PROMPT, never this shape).
BEAT_NAMES: tuple[str, ...] = (
    "hook", "setup", "conflict", "emotional_development",
    "turning_point", "transformation", "conclusion", "cta",
)
_BEAT_LABELS = {
    "hook": "Hook", "setup": "Setup", "conflict": "Conflict/problem",
    "emotional_development": "Emotional development", "turning_point": "Turning point",
    "transformation": "Transformation", "conclusion": "Conclusion", "cta": "CTA",
}

# Module brief requirement 14's modular story-type list - purely a
# prompt-framing hint for generate_story_structure(); nothing downstream
# (story_scenes.py, storage.py, db.py, the GUI dashboard) ever branches
# on story_type again once a StoryStructure exists.
STORY_TYPE_CHOICES = (
    "personal", "educational", "product", "brand",
    "emotional", "motivational", "customer",
)
DEFAULT_STORY_TYPE = "personal"

LANGUAGE_LITHUANIAN = "lt"
LANGUAGE_ENGLISH = "en"
LANGUAGE_CHOICES = (LANGUAGE_LITHUANIAN, LANGUAGE_ENGLISH)
_LANGUAGE_NAMES = {LANGUAGE_LITHUANIAN: "Lithuanian", LANGUAGE_ENGLISH: "English"}


@dataclass(frozen=True)
class StoryBeat:
    kind: str  # one of BEAT_NAMES
    text: str  # the actual narrative content for this beat

    @property
    def label(self) -> str:
        return _BEAT_LABELS.get(self.kind, self.kind.replace("_", " ").title())


@dataclass(frozen=True)
class StoryStructure:
    story_type: str  # one of STORY_TYPE_CHOICES
    language: str  # one of LANGUAGE_CHOICES
    beats: tuple[StoryBeat, ...]  # always exactly 8, in BEAT_NAMES order

    @property
    def full_text(self) -> str:
        return " ".join(b.text for b in self.beats)

    def beat(self, kind: str) -> StoryBeat | None:
        return next((b for b in self.beats if b.kind == kind), None)


def _language_label(language: str) -> str:
    return _LANGUAGE_NAMES.get(language, "English")


def _system_prompt(story_type: str, language: str) -> str:
    beat_list = "\n".join(f'- "{kind}": {_BEAT_LABELS[kind]}' for kind in BEAT_NAMES)
    return (
        "You are a short-form video storyteller. Given a person's plain-language story idea "
        f"(a {story_type} story), write a complete narrative broken into exactly these 8 beats, "
        "in this exact order - never skip a beat, never merge two beats into one, never add an "
        f"extra beat:\n{beat_list}\n\n"
        "Each beat's text should be a short paragraph (2-4 sentences) that could be read aloud "
        "as narration for that part of the story. Write a genuinely complete, coherent arc - the "
        "CONFLICT should feel like a real problem, the TURNING POINT should feel like a real "
        "shift, and the TRANSFORMATION/CONCLUSION should pay off the setup and conflict. Never "
        "ask a follow-up question, never leave a beat vague; if the idea is thin, invent "
        "concrete, plausible specifics yourself rather than staying generic. "
        f"Write in {_language_label(language)}.\n\n" + _JSON_RESPONSE_INSTRUCTION + "\n\n"
        "Respond with a JSON object with exactly one field, \"beats\", an array of exactly 8 "
        "objects in the order given above, each with fields: \"kind\" (one of: "
        + ", ".join(BEAT_NAMES) + ") and \"text\" (the narration text for that beat)."
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


def _validate_beats(data: Any) -> list[dict] | None:
    if not isinstance(data, dict):
        return None
    beats = data.get("beats")
    if not isinstance(beats, list) or len(beats) != len(BEAT_NAMES):
        return None

    validated: list[dict] = []
    for expected_kind, raw in zip(BEAT_NAMES, beats):
        if not isinstance(raw, dict):
            return None
        kind = raw.get("kind")
        text = raw.get("text")
        if kind != expected_kind:
            return None
        if not isinstance(text, str) or not text.strip():
            return None
        validated.append({"kind": kind, "text": text.strip()})
    return validated


def generate_story_structure(
    llm: LLMClient, idea: str, *, story_type: str = DEFAULT_STORY_TYPE, language: str = LANGUAGE_ENGLISH,
) -> StoryStructure | None:
    """Generates a complete 8-beat StoryStructure from a plain-language
    story idea. Returns None on any failure (LLM error, malformed
    response, wrong beat count/order) - never raises. Does not split
    into scenes or render/export anything - purely text generation,
    shown to the person for review/edit and approval before any further
    stage runs (module brief requirements 5-6: edit/review, then
    "APPROVE STORY")."""
    if not idea.strip():
        return None
    if story_type not in STORY_TYPE_CHOICES:
        story_type = DEFAULT_STORY_TYPE
    if language not in LANGUAGE_CHOICES:
        language = LANGUAGE_ENGLISH

    prompt = f"Story idea: {idea.strip()}\nStory type: {story_type}"
    result = _call_llm_for_json(llm, system=_system_prompt(story_type, language), prompt=prompt)
    validated = _validate_beats(result)
    if validated is None:
        return None

    beats = tuple(StoryBeat(kind=b["kind"], text=b["text"]) for b in validated)
    return StoryStructure(story_type=story_type, language=language, beats=beats)
