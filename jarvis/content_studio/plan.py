"""Content Plan generation (module brief, sections 1-2): "Suprasti mano
temą ir pasiūlyti turinio planą. Leisti pasirinkti, ką kurti: Instagram
Reel / Story / Post 4:5 / Carousel / PDF / pilną Content Package."

One isolated, tool-free, JSON-only LLM call, modeled directly on
jarvis.instagram_ai_manager.ai_services.generate_weekly_plan()'s exact
shape (system prompt + strict-JSON instruction + per-item required-
field validation + "all empty = failure" check) - the closest existing
precedent for "given topic X, produce a structured, multi-item content
plan" in this codebase. This module has its own private copy of
_extract_json()/_call_llm_for_json(), matching this codebase's
established "each feature module's backend stays self-contained"
convention (see jarvis.reel_generator.brief/.script's own docstrings
for the same reasoning applied to their own private copies).

generate_weekly_plan() itself is NOT reused directly - it is Instagram-
specific (Reels/Stories/Carousels scheduled across a week) and has no
concept of a Post, a PDF, or a single-topic (not weekly) plan, so its
shape is copied, not its function."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from jarvis.core.llm import LLMClient

_MAX_GENERATION_TOKENS = 1200

_JSON_RESPONSE_INSTRUCTION = (
    "Reply with ONLY a single valid JSON object matching the requested shape - "
    "no explanation, no markdown code fences, no leading/trailing text of any "
    "kind. The response must be parseable by a strict JSON parser as-is."
)

# The module brief's own listed content types (section 2) - each one a
# real, existing JARVIS module this plan's items point a person toward,
# never a new content type this package invents.
CONTENT_TYPES = ("reel", "story", "post", "carousel", "pdf")

_CONTENT_TYPE_LABELS = {
    "reel": "Instagram Reel", "story": "Instagram Story", "post": "Instagram Post (4:5)",
    "carousel": "Carousel", "pdf": "PDF",
}

_SYSTEM_PROMPT = (
    "You are a social media content strategist. Given a single topic, propose a "
    "content plan covering these content types: " + ", ".join(CONTENT_TYPES) + ". "
    "For EACH content type, write one short content-item suggestion: a specific "
    "angle/hook suited to that format (a Reel angle is different from a static "
    "Post angle, which is different from a multi-slide Carousel angle, which is "
    "different from a longer-form PDF angle) plus a one-line objective and a "
    "suggested CTA. Ground every suggestion in the actual topic given - never a "
    "generic placeholder. Write in the same language the topic itself is "
    "written in.\n\n" + _JSON_RESPONSE_INSTRUCTION + "\n\n"
    "Respond with a JSON object with exactly one field, \"items\", an array of "
    "content-item objects, one per content type in the list above (same order), "
    "each with exactly these string fields: \"content_type\" (one of: "
    + ", ".join(CONTENT_TYPES) + "), \"angle\" (the specific angle/hook for this "
    "format), \"objective\", \"cta\"."
)


@dataclass(frozen=True)
class ContentPlanItem:
    content_type: str  # one of CONTENT_TYPES
    angle: str
    objective: str
    cta: str

    @property
    def label(self) -> str:
        return _CONTENT_TYPE_LABELS.get(self.content_type, self.content_type)


@dataclass(frozen=True)
class ContentPlan:
    topic: str
    items: tuple[ContentPlanItem, ...]  # one per CONTENT_TYPES entry, same order

    def item_for(self, content_type: str) -> ContentPlanItem | None:
        return next((i for i in self.items if i.content_type == content_type), None)


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


_REQUIRED_ITEM_FIELDS = ("content_type", "angle", "objective", "cta")


def _validate_items(data: Any) -> list[dict[str, str]] | None:
    if not isinstance(data, dict):
        return None
    items = data.get("items")
    if not isinstance(items, list):
        return None

    cleaned: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if not all(isinstance(item.get(f), str) and item[f].strip() for f in _REQUIRED_ITEM_FIELDS):
            continue
        content_type = item["content_type"].strip().lower()
        if content_type not in CONTENT_TYPES:
            continue
        cleaned.append({
            "content_type": content_type, "angle": item["angle"].strip(),
            "objective": item["objective"].strip(), "cta": item["cta"].strip(),
        })

    # At least one usable item is required - an all-malformed response
    # is a genuine failure, not a legitimately empty plan (unlike
    # generate_weekly_plan()'s own "empty day" concept, every content
    # TYPE is always relevant to a single-topic plan, so there is no
    # equivalent "intentionally empty" case here).
    if not cleaned:
        return None
    return cleaned


def generate_content_plan(llm: LLMClient, topic: str) -> ContentPlan | None:
    """Generates a ContentPlan proposing one angle/objective/CTA per
    content type (module brief: "Suprasti mano temą ir pasiūlyti turinio
    planą") for `topic`. Returns None on any failure (empty topic, LLM
    error, malformed/entirely-invalid response) - never raises. A
    partially-malformed response (e.g. the model skips one content
    type) still returns a plan with the remaining valid items, matching
    this codebase's established "partial success is still useful"
    convention (see jarvis.design_studio.variants.generate_variants()'s
    own docstring for the same principle applied elsewhere) - a caller
    can check plan.item_for(content_type) is None to detect a missing
    one."""
    if not topic.strip():
        return None

    prompt = f"Topic: \"{topic.strip()}\""
    result = _call_llm_for_json(llm, system=_SYSTEM_PROMPT, prompt=prompt)
    validated = _validate_items(result)
    if validated is None:
        return None

    items = tuple(
        ContentPlanItem(content_type=i["content_type"], angle=i["angle"], objective=i["objective"], cta=i["cta"])
        for i in validated
    )
    return ContentPlan(topic=topic.strip(), items=items)
