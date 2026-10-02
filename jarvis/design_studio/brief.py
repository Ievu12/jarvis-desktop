"""Design Brief generation (module brief, section 4): turns a plain-
language design request ("Create a Story about morning yoga") into a
structured DesignBrief - topic, objective, audience, tone, format,
style, headline, supporting text, CTA - via one isolated, tool-free,
JSON-only LLM call. Exact same pattern as
jarvis.instagram_ai_manager.ai_services (see that module's own
docstring for the full rationale: no tools offered, so this call can
never trigger a real action; returns None on any failure, never
raises) - this module has its own copy of the small
_extract_json()/_call_llm_for_json() helpers rather than importing
jarvis.instagram_ai_manager.ai_services' private ones, matching this
codebase's established "each feature module's backend stays
self-contained" convention (see jarvis.video_studio.highlights/.reel/
.cover, which each already do the same thing).

Per the module's brief (section 2): "If some information is missing,
JARVIS should intelligently choose a suitable default rather than
requiring the user to fill in many fields." - the system prompt below
explicitly instructs the model to fill in every field with a sensible
default when the request doesn't specify it, rather than asking a
clarifying question or leaving a field blank; this module never
returns a partially-empty brief when generation succeeds at all - see
_validate_brief() for the completeness check that enforces this.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from jarvis.core.llm import LLMClient
from jarvis.design_studio.styles import AUTO_STYLE, DESIGN_STYLES

_MAX_GENERATION_TOKENS = 1200

_JSON_RESPONSE_INSTRUCTION = (
    "Reply with ONLY a single valid JSON object matching the requested shape - "
    "no explanation, no markdown code fences, no leading/trailing text of any "
    "kind. The response must be parseable by a strict JSON parser as-is."
)

# The module brief's own supported formats (section 3) - each format's
# exact pixel dimensions live in jarvis.design_studio.render (the
# rendering module, not this one), since this module only picks WHICH
# format fits the request, never draws anything itself.
FORMAT_CHOICES = ("story", "post", "square", "reel_cover", "carousel")

_FORMAT_LABELS = {
    "story": "Instagram Story (1080x1920, 9:16)",
    "post": "Instagram Post (1080x1350, 4:5)",
    "square": "Square Post (1080x1080, 1:1)",
    "reel_cover": "Reel Cover (1080x1920, 9:16)",
    "carousel": "Carousel (1080x1350, 4:5)",
}

_STYLE_NAMES = tuple(DESIGN_STYLES.keys())

_SYSTEM_PROMPT = (
    "You are an Instagram visual design assistant. Given a person's plain-"
    "language request for a design, produce a complete, structured design "
    "brief - a headline, supporting text, and CTA that will be rendered as "
    "TEXT on a colored/gradient background (there is no AI image generation "
    "available - never describe or imply a photo/illustration that would "
    "need to be generated; the visual is typography and color only, "
    "optionally with a user-supplied photo composited in separately, which "
    "you have no control over). If the request doesn't specify some detail "
    "(objective, audience, tone, format, style, amount of text, CTA), choose "
    "a sensible, concrete default yourself - never leave a field vague or "
    "ask a follow-up question. Keep the headline short (an Instagram-cover-"
    "style few words, not a sentence) and supporting text brief (a line or "
    "two, not a paragraph) - this is a visual with a text overlay, not an "
    "article. Write in the same language the request itself is written in."
    "\n\n" + _JSON_RESPONSE_INSTRUCTION + "\n\n"
    "Respond with a JSON object with exactly these string fields: topic, "
    "objective, audience, tone, headline, supporting_text, cta, and two "
    "more fields: \"format\" (one of: " + ", ".join(FORMAT_CHOICES) + " - "
    "pick whichever best fits the request; default to \"post\" if genuinely "
    "ambiguous) and \"style\" (one of: " + ", ".join(_STYLE_NAMES) + " - "
    "pick whichever best matches the request's topic/tone; e.g. a yoga "
    "request should usually get \"yoga\" or \"wellness\", a product promo "
    "might get \"bold\" or \"modern\")."
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
class DesignBrief:
    topic: str
    objective: str
    audience: str
    tone: str
    headline: str
    supporting_text: str
    cta: str
    format: str  # one of FORMAT_CHOICES
    style: str  # one of DESIGN_STYLES' keys - never AUTO_STYLE (already resolved)

    @property
    def format_label(self) -> str:
        return _FORMAT_LABELS.get(self.format, self.format)


_REQUIRED_STRING_FIELDS = (
    "topic", "objective", "audience", "tone", "headline", "supporting_text", "cta",
)


def _validate_brief(data: Any) -> dict[str, str] | None:
    if not isinstance(data, dict):
        return None
    if not all(isinstance(data.get(f), str) and data[f].strip() for f in _REQUIRED_STRING_FIELDS):
        return None
    fmt = data.get("format")
    style = data.get("style")
    if not isinstance(fmt, str) or fmt not in FORMAT_CHOICES:
        return None
    if not isinstance(style, str) or style not in DESIGN_STYLES:
        return None
    return {f: data[f].strip() for f in _REQUIRED_STRING_FIELDS} | {"format": fmt, "style": style}


def generate_design_brief(
    llm: LLMClient, request_text: str, *,
    forced_format: str | None = None, forced_style: str | None = None,
) -> DesignBrief | None:
    """Generates a complete DesignBrief from `request_text` (the
    person's plain-language description). `forced_format`/`forced_style`
    let the UI's own Format/Style dropdowns override the model's pick
    (module brief section 1's "[Story 9:16 ▼]"/"[Choose automatically ▼]"
    controls) - passed through to the prompt as a hard constraint rather
    than silently overwritten after the fact, so the model can still
    write a headline/CTA that actually fits the constrained format.
    `forced_style` of AUTO_STYLE (or None) leaves the choice to the
    model. Returns None on any failure (empty request, LLM error,
    malformed/incomplete response) - never raises, never returns a
    brief with a missing/empty field."""
    if not request_text.strip():
        return None

    prompt_parts = [f"Design request: \"{request_text.strip()}\""]
    if forced_format is not None and forced_format in FORMAT_CHOICES:
        prompt_parts.append(f"The format MUST be \"{forced_format}\" - do not choose a different one.")
    if forced_style is not None and forced_style != AUTO_STYLE and forced_style in DESIGN_STYLES:
        prompt_parts.append(f"The style MUST be \"{forced_style}\" - do not choose a different one.")
    prompt = "\n".join(prompt_parts)

    result = _call_llm_for_json(llm, system=_SYSTEM_PROMPT, prompt=prompt)
    validated = _validate_brief(result)
    if validated is None:
        return None

    return DesignBrief(
        topic=validated["topic"], objective=validated["objective"], audience=validated["audience"],
        tone=validated["tone"], headline=validated["headline"], supporting_text=validated["supporting_text"],
        cta=validated["cta"], format=validated["format"], style=validated["style"],
    )
