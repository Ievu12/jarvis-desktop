"""Script generation (module brief, section 3): turns an approved
ReelBrief into a short script structured as HOOK -> VALUE/STORY -> CTA,
each with an explicit start/end timestamp that fits inside the brief's
own `duration_seconds` - "The script must fit the selected duration.
Do not create scripts that are too long for the selected duration."

One isolated, tool-free, JSON-only LLM call (own private copy of
_extract_json()/_call_llm_for_json(), same convention as
jarvis.reel_generator.brief - see that module's docstring). Estimated
speaking duration (module brief: "Show estimated speaking duration.")
is computed locally from word count, not asked of the model, since a
fixed words-per-second rate is a simple, checkable, honest number - a
model-reported duration estimate would be an unverifiable guess dressed
up as data.

Nothing here is finalized until the person clicks "Approve" - see this
package's own docstring/GUI dashboard for the approval gate; this
module only ever GENERATES a proposed script, never renders or exports
anything from it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from jarvis.core.llm import LLMClient
from jarvis.reel_generator.brief import ReelBrief

_MAX_GENERATION_TOKENS = 1200

_JSON_RESPONSE_INSTRUCTION = (
    "Reply with ONLY a single valid JSON object matching the requested shape - "
    "no explanation, no markdown code fences, no leading/trailing text of any "
    "kind. The response must be parseable by a strict JSON parser as-is."
)

# Average spoken-word pace used to estimate speaking duration locally
# (module brief: "Show estimated speaking duration.") - a plain,
# checkable words-per-second rate, not an LLM guess. 2.5 words/sec
# (~150 wpm) is a common conversational-pace estimate for short-form
# social video narration - roughly what jarvis.video_studio.cover's own
# _wrap_text sizing assumptions are built around for on-screen reading
# pace, applied here to SPEAKING pace instead.
_WORDS_PER_SECOND = 2.5

_SEGMENT_KINDS = ("hook", "value", "cta")


@dataclass(frozen=True)
class ScriptSegment:
    kind: str  # one of _SEGMENT_KINDS
    start_seconds: float
    end_seconds: float
    text: str

    @property
    def duration_seconds(self) -> float:
        return round(self.end_seconds - self.start_seconds, 1)

    @property
    def label(self) -> str:
        return {"hook": "HOOK", "value": "VALUE", "cta": "CTA"}.get(self.kind, self.kind.upper())


@dataclass(frozen=True)
class ReelScript:
    segments: tuple[ScriptSegment, ...]  # always exactly 3: hook, value, cta, in order

    @property
    def full_text(self) -> str:
        return " ".join(s.text for s in self.segments)

    @property
    def estimated_speaking_seconds(self) -> float:
        word_count = len(self.full_text.split())
        return round(word_count / _WORDS_PER_SECOND, 1)


def _system_prompt(brief: ReelBrief) -> str:
    return (
        "You are a short-form Instagram Reel scriptwriter. Given an approved Reel "
        f"brief, write a spoken/on-screen script for a {brief.duration_seconds}-second "
        "Reel, structured as exactly three segments in this order: a HOOK (the first "
        "few seconds - must grab attention immediately, no slow build-up), VALUE "
        "(the main content - the actual tip/story/information, matching the brief's "
        "objective), and a CTA (a short closing call to action, using the brief's own "
        "cta field as guidance). The TOTAL script, spoken at a natural pace of about "
        f"{_WORDS_PER_SECOND} words per second, must fit within {brief.duration_seconds} "
        "seconds - keep it concise; a script that is too long for the duration is a "
        "failure. Write in " + brief.language_label + ".\n\n" + _JSON_RESPONSE_INSTRUCTION + "\n\n"
        "Respond with a JSON object with exactly one field, \"segments\", an array of "
        "exactly 3 objects in order (hook, value, cta), each with fields: \"kind\" (one "
        "of: hook, value, cta), \"start_seconds\" (number, where the segment starts), "
        "\"end_seconds\" (number, where it ends - segments must be contiguous, starting "
        "at 0, and the last segment's end_seconds must not exceed "
        f"{brief.duration_seconds}), and \"text\" (the actual script line for that segment)."
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


def _validate_segments(data: Any, *, duration_seconds: int) -> list[dict] | None:
    if not isinstance(data, dict):
        return None
    segments = data.get("segments")
    if not isinstance(segments, list) or len(segments) != 3:
        return None

    validated: list[dict] = []
    previous_end = 0.0
    for expected_kind, raw in zip(_SEGMENT_KINDS, segments):
        if not isinstance(raw, dict):
            return None
        kind = raw.get("kind")
        text = raw.get("text")
        start = raw.get("start_seconds")
        end = raw.get("end_seconds")
        if kind != expected_kind:
            return None
        if not isinstance(text, str) or not text.strip():
            return None
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            return None
        start = float(start)
        end = float(end)
        if end <= start:
            return None
        # Tolerate small floating-point/model drift at the seams rather
        # than rejecting an otherwise-good script over a fraction of a
        # second - but never accept a segment that overlaps its
        # predecessor by a meaningful amount or a total that blows past
        # the requested duration (module brief: "Do not create scripts
        # that are too long for the selected duration.").
        if start < previous_end - 0.5:
            return None
        previous_end = end
        validated.append({"kind": kind, "start_seconds": start, "end_seconds": end, "text": text.strip()})

    if previous_end > duration_seconds + 1.0:
        return None
    return validated


def generate_reel_script(llm: LLMClient, brief: ReelBrief) -> ReelScript | None:
    """Generates a 3-segment (hook/value/cta) ReelScript from an
    already-approved ReelBrief, timed to fit brief.duration_seconds.
    Returns None on any failure (LLM error, malformed response,
    segments that don't fit the duration or aren't in hook/value/cta
    order) - never raises. Does not render, render a storyboard, or
    export anything - purely text generation, shown to the person for
    approval before any further stage runs (module brief section 4)."""
    prompt = (
        f"Topic: {brief.topic}\nAudience: {brief.audience}\nObjective: {brief.objective}\n"
        f"Tone: {brief.tone}\nCTA guidance: {brief.cta}\nStyle: {brief.style}\n"
        f"Duration: {brief.duration_seconds} seconds"
    )
    result = _call_llm_for_json(llm, system=_system_prompt(brief), prompt=prompt)
    validated = _validate_segments(result, duration_seconds=brief.duration_seconds)
    if validated is None:
        return None

    segments = tuple(
        ScriptSegment(kind=s["kind"], start_seconds=s["start_seconds"], end_seconds=s["end_seconds"], text=s["text"])
        for s in validated
    )
    return ReelScript(segments=segments)
