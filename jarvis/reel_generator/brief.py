"""Reel Brief generation (module brief, section 2): turns a plain-
language Reel idea ("Create a short Reel about 3 benefits of morning
yoga.") into a structured ReelBrief - topic, audience, objective, tone,
duration, style, CTA - via one isolated, tool-free, JSON-only LLM call.

Same pattern as jarvis.design_studio.brief (see that module's own
docstring for the full rationale) - this module has its own copy of
the small _extract_json()/_call_llm_for_json() helpers rather than
importing another module's private ones, matching this codebase's
established "each feature module's backend stays self-contained"
convention.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from jarvis.core.llm import LLMClient

_MAX_GENERATION_TOKENS = 800

_JSON_RESPONSE_INSTRUCTION = (
    "Reply with ONLY a single valid JSON object matching the requested shape - "
    "no explanation, no markdown code fences, no leading/trailing text of any "
    "kind. The response must be parseable by a strict JSON parser as-is."
)

# Module brief section 1's duration dropdown.
DURATION_CHOICES = (15, 20, 30, 45, 60)
DEFAULT_DURATION_SECONDS = 20

# Module brief section 1's style dropdown ("Automatic" resolved away by
# the model itself - the brief never returns "automatic" as a final
# style, matching jarvis.design_studio.styles.AUTO_STYLE's own
# resolve-before-storing convention).
STYLE_CHOICES = (
    "educational", "inspirational", "storytelling", "beauty", "yoga",
    "lifestyle", "product", "ugc", "motivational",
)

LANGUAGE_LITHUANIAN = "lt"
LANGUAGE_ENGLISH = "en"
LANGUAGE_CHOICES = (LANGUAGE_LITHUANIAN, LANGUAGE_ENGLISH)
_LANGUAGE_NAMES = {LANGUAGE_LITHUANIAN: "Lithuanian", LANGUAGE_ENGLISH: "English"}

_SYSTEM_PROMPT = (
    "You are a short-form Instagram Reel strategist. Given a person's plain-"
    "language Reel idea, produce a complete, structured Reel brief - never "
    "ask a follow-up question, never leave a field vague; if the idea "
    "doesn't specify some detail (audience, objective, tone, style, CTA), "
    "choose a sensible, concrete default yourself.\n\n" + _JSON_RESPONSE_INSTRUCTION + "\n\n"
    "Respond with a JSON object with exactly these string fields: topic, "
    "audience, objective, tone, cta, and one more field: \"style\" (one of: "
    + ", ".join(STYLE_CHOICES) + " - pick whichever best matches the idea's "
    "topic/tone; e.g. a yoga idea should usually get \"yoga\" or "
    "\"lifestyle\", a product idea might get \"product\" or \"ugc\")."
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


@dataclass(frozen=True)
class ReelBrief:
    topic: str
    audience: str
    objective: str
    tone: str
    cta: str
    style: str  # one of STYLE_CHOICES
    duration_seconds: int  # one of DURATION_CHOICES
    language: str  # one of LANGUAGE_CHOICES

    @property
    def language_label(self) -> str:
        return _LANGUAGE_NAMES.get(self.language, self.language)


_REQUIRED_STRING_FIELDS = ("topic", "audience", "objective", "tone", "cta")


def _validate_brief(data: Any) -> dict[str, str] | None:
    if not isinstance(data, dict):
        return None
    if not all(isinstance(data.get(f), str) and data[f].strip() for f in _REQUIRED_STRING_FIELDS):
        return None
    style = data.get("style")
    if not isinstance(style, str) or style not in STYLE_CHOICES:
        return None
    return {f: data[f].strip() for f in _REQUIRED_STRING_FIELDS} | {"style": style}


def generate_reel_brief(
    llm: LLMClient, request_text: str, *,
    duration_seconds: int = DEFAULT_DURATION_SECONDS,
    forced_style: str | None = None,
    language: str = LANGUAGE_ENGLISH,
) -> ReelBrief | None:
    """Generates a complete ReelBrief from `request_text` (the person's
    plain-language Reel idea). `duration_seconds` and `language` are
    UI-selected settings (module brief section 1), stored on the brief
    as-is (never inferred by the model) since they're explicit person
    choices, not something to extract from free text. `forced_style`
    lets the Style dropdown override the model's own pick, passed
    through as a hard constraint. Returns None on any failure (empty
    request, invalid duration/language, LLM error, malformed/incomplete
    response) - never raises, never returns a brief with a missing
    field."""
    if not request_text.strip():
        return None
    if duration_seconds not in DURATION_CHOICES:
        return None
    if language not in LANGUAGE_CHOICES:
        return None

    prompt_parts = [
        f"Reel idea: \"{request_text.strip()}\"",
        f"Target duration: {duration_seconds} seconds.",
        f"Write the topic/audience/objective/tone/cta fields in {_LANGUAGE_NAMES[language]}.",
    ]
    if forced_style is not None and forced_style in STYLE_CHOICES:
        prompt_parts.append(f"The style MUST be \"{forced_style}\" - do not choose a different one.")
    prompt = "\n".join(prompt_parts)

    result = _call_llm_for_json(llm, system=_SYSTEM_PROMPT, prompt=prompt)
    validated = _validate_brief(result)
    if validated is None:
        return None

    return ReelBrief(
        topic=validated["topic"], audience=validated["audience"], objective=validated["objective"],
        tone=validated["tone"], cta=validated["cta"], style=validated["style"],
        duration_seconds=duration_seconds, language=language,
    )
