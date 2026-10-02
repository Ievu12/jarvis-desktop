"""Isolated, tool-free LLM calls that generate Instagram content drafts:
Reel ideas, hooks, captions, CTAs, hashtags, Story sequences, and weekly
plans. Every function here follows the exact pattern
jarvis.core.commit_message.suggest_commit_message() already established
for a one-shot, isolated LLM request - no tools offered, no
conversation history, a dedicated system prompt - so a generation call
can NEVER trigger a real Instagram action (there is nothing here for
the model to call) and never touches the main conversational agent's
history. Every function returns None (never raises) on any failure -
missing API key, network error, malformed/non-JSON response - so a
calling UI can show a clear "generation failed" state rather than
crashing.

Every function asks the model to respond with ONLY a JSON object/array
matching a documented shape, parses it, and validates that shape before
returning - a response that doesn't parse or doesn't match is treated
as a failure (None), never partially trusted or silently reshaped.
These functions generate DRAFT text only - none of them publishes,
schedules, or sends anything to Instagram; jarvis.integrations
.connectors.instagram.InstagramConnector remains the only code path
that talks to the Instagram API at all, and this module never imports
it for writing (only jarvis.instagram_ai_manager.analytics_services
reads from it, for analysis - see that module).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from jarvis.core.llm import LLMClient

_MAX_GENERATION_TOKENS = 3000

_JSON_RESPONSE_INSTRUCTION = (
    "Reply with ONLY a single valid JSON value matching the requested shape - "
    "no explanation, no markdown code fences, no leading/trailing text of any "
    "kind. The response must be parseable by a strict JSON parser as-is."
)


def _extract_json(text: str) -> Any | None:
    """Parses `text` as JSON, tolerating the model wrapping its answer in
    a markdown code fence (```json ... ```) despite being asked not to -
    a common enough deviation that stripping it here is more robust than
    relying on prompt compliance alone. Returns None (never raises) if
    the result still isn't valid JSON."""
    stripped = text.strip()
    fence_match = re.match(r"^```(?:json)?\s*\n(.*)\n```$", stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _call_llm_for_json(llm: LLMClient, *, system: str, prompt: str) -> Any | None:
    """Shared request/parse logic for every generate_*() function below.
    Returns None on any failure (network, auth, malformed JSON) - never
    raises, matching jarvis.core.commit_message's established contract
    for isolated, advisory LLM calls."""
    try:
        response = llm.send(
            [{"role": "user", "content": prompt}],
            [],
            max_tokens=_MAX_GENERATION_TOKENS,
            system=system,
        )
    except Exception:
        return None

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    if not text:
        return None
    return _extract_json(text)


@dataclass(frozen=True)
class GenerationError:
    """Returned instead of None by functions where a caller needs to
    distinguish "the model responded but its JSON didn't match the
    expected shape" from "the request failed outright" - most functions
    here return plain None for both (matching commit_message's simpler
    contract), but the richer generators (Reel ideas, weekly plans)
    return this so a UI can show *why* generation didn't produce usable
    content, per the brief's explicit request for clear error states."""

    message: str


# --- A. Reel ideas ---------------------------------------------------------------------


_REEL_IDEA_SYSTEM_PROMPT = (
    "You are an Instagram content strategist. You generate concrete, "
    "specific Reel ideas for a given topic/niche/goal - never generic "
    "platitudes. " + _JSON_RESPONSE_INSTRUCTION + "\n\n"
    "Each idea in the JSON array must be an object with exactly these "
    "string fields: title, concept, target_audience, suggested_format, "
    "hook, cta, estimated_difficulty (one of: \"easy\", \"medium\", \"hard\"), "
    "why_it_may_work."
)


def generate_reel_ideas(llm: LLMClient, topic: str, *, count: int = 10) -> list[dict] | None:
    """Generates `count` Reel ideas for `topic` (a niche/goal/topic
    string, e.g. "skincare", "yoga", "self development"). Returns a list
    of dicts (title/concept/target_audience/suggested_format/hook/cta/
    estimated_difficulty/why_it_may_work), or None if generation failed
    or the model's response didn't match that shape. Does not save
    anything - the caller (a view) decides when to call
    jarvis.instagram_ai_manager.db.save_reel_idea_set()."""
    if not topic.strip():
        return None

    prompt = (
        f"Generate exactly {count} distinct Instagram Reel ideas for this "
        f"topic/niche/goal: \"{topic.strip()}\"."
    )
    result = _call_llm_for_json(llm, system=_REEL_IDEA_SYSTEM_PROMPT, prompt=prompt)
    if not isinstance(result, list):
        return None

    required_fields = {
        "title", "concept", "target_audience", "suggested_format",
        "hook", "cta", "estimated_difficulty", "why_it_may_work",
    }
    valid_ideas = [
        idea for idea in result
        if isinstance(idea, dict) and required_fields.issubset(idea.keys())
    ]
    return valid_ideas or None


# --- B. Hook generator ----------------------------------------------------------------


_HOOK_CATEGORIES = (
    "curiosity", "problem_solution", "educational", "controversial_but_factual",
    "emotional", "storytelling", "question", "authority", "short_punchy",
)

_HOOK_SYSTEM_PROMPT = (
    "You are an Instagram copywriting specialist focused on opening "
    "hooks (the first line/seconds of a Reel or post). " + _JSON_RESPONSE_INSTRUCTION + "\n\n"
    "Respond with a JSON object whose keys are exactly these hook "
    f"categories: {', '.join(_HOOK_CATEGORIES)}. Each value must be a JSON "
    "array of 2-3 distinct hook strings in that category, written for the "
    "given topic."
)


def generate_hooks(llm: LLMClient, topic: str) -> dict[str, list[str]] | None:
    """Generates hooks for `topic`, grouped by category (see
    _HOOK_CATEGORIES). Returns a dict {category: [hook, ...]}, or None on
    failure. A category the model omits or returns malformed is simply
    left out of the result rather than failing the whole call - a
    caller showing 7 of 9 categories is better than showing nothing."""
    if not topic.strip():
        return None

    prompt = f"Generate hooks for this Instagram content topic: \"{topic.strip()}\"."
    result = _call_llm_for_json(llm, system=_HOOK_SYSTEM_PROMPT, prompt=prompt)
    if not isinstance(result, dict):
        return None

    cleaned: dict[str, list[str]] = {}
    for category in _HOOK_CATEGORIES:
        value = result.get(category)
        if isinstance(value, list) and all(isinstance(h, str) for h in value) and value:
            cleaned[category] = value
    return cleaned or None


# --- C. Caption generator --------------------------------------------------------------


_CAPTION_TONES = ("friendly", "expert", "inspirational", "educational", "personal", "sales")

_CAPTION_SYSTEM_PROMPT = (
    "You are an Instagram caption writer. " + _JSON_RESPONSE_INSTRUCTION + "\n\n"
    "Respond with a JSON object with exactly these string fields: "
    "short_caption, medium_caption, long_caption."
)


def generate_caption(
    llm: LLMClient, *, topic: str, content_description: str, hook: str | None,
    tone: str, target_audience: str | None, cta: str | None,
) -> dict[str, str] | None:
    """Generates short/medium/long caption variants. `tone` must be one
    of _CAPTION_TONES (an unrecognized tone still gets sent to the
    model verbatim rather than silently substituted - the caller's
    responsibility to offer only the documented tones in its UI).
    Returns {short_caption, medium_caption, long_caption} or None."""
    if not topic.strip() or not content_description.strip():
        return None

    prompt_parts = [
        f"Topic: {topic.strip()}",
        f"Reel/content description: {content_description.strip()}",
        f"Tone: {tone}",
    ]
    if hook:
        prompt_parts.append(f"Hook already chosen: {hook}")
    if target_audience:
        prompt_parts.append(f"Target audience: {target_audience}")
    if cta:
        prompt_parts.append(f"CTA to include: {cta}")
    prompt = "Write an Instagram caption in three lengths.\n\n" + "\n".join(prompt_parts)

    result = _call_llm_for_json(llm, system=_CAPTION_SYSTEM_PROMPT, prompt=prompt)
    if not isinstance(result, dict):
        return None
    required = {"short_caption", "medium_caption", "long_caption"}
    if not required.issubset(result.keys()):
        return None
    if not all(isinstance(result[k], str) and result[k].strip() for k in required):
        return None
    return {k: result[k] for k in required}


# --- D. CTA generator -------------------------------------------------------------------


_CTA_CATEGORIES = (
    "comments", "saves", "shares", "follows", "dm", "website", "product", "engagement",
)

_CTA_SYSTEM_PROMPT = (
    "You are an Instagram growth copywriter focused on calls-to-action. "
    + _JSON_RESPONSE_INSTRUCTION + "\n\n"
    "Respond with a JSON object whose keys are exactly these categories: "
    f"{', '.join(_CTA_CATEGORIES)}. Each value must be a JSON array of "
    "1-3 distinct, specific CTA strings for that category, relevant to "
    "the given content topic - not generic phrases."
)


def generate_cta(llm: LLMClient, topic: str) -> dict[str, list[str]] | None:
    """Generates CTAs grouped by category (see _CTA_CATEGORIES). Returns
    {category: [cta, ...]}, or None on failure. Same partial-result
    tolerance as generate_hooks()."""
    if not topic.strip():
        return None

    prompt = f"Generate calls-to-action for this Instagram content topic: \"{topic.strip()}\"."
    result = _call_llm_for_json(llm, system=_CTA_SYSTEM_PROMPT, prompt=prompt)
    if not isinstance(result, dict):
        return None

    cleaned: dict[str, list[str]] = {}
    for category in _CTA_CATEGORIES:
        value = result.get(category)
        if isinstance(value, list) and all(isinstance(c, str) for c in value) and value:
            cleaned[category] = value
    return cleaned or None


# --- E. Hashtag assistant ---------------------------------------------------------------


_HASHTAG_GROUPS = ("niche", "medium_competition", "broader", "branded")

_HASHTAG_SYSTEM_PROMPT = (
    "You are an Instagram hashtag research assistant. Generate hashtags "
    "that are ACTUALLY RELEVANT to the given content topic - never "
    "generic high-volume hashtags unrelated to the topic just because "
    "they're popular. " + _JSON_RESPONSE_INSTRUCTION + "\n\n"
    "Respond with a JSON object with exactly these keys: niche (5-8 "
    "specific, low-competition hashtags closely tied to the topic), "
    "medium_competition (5-8 hashtags with moderate reach), broader "
    "(3-5 larger/more general hashtags still relevant to the topic), "
    "branded (2-3 hashtag SUGGESTIONS suitable for a personal/business "
    "brand on this topic, clearly marked as suggestions since the "
    "actual brand name isn't known to you). Every value is a JSON array "
    "of strings, each starting with '#', no spaces."
)


def generate_hashtags(llm: LLMClient, topic: str) -> dict[str, list[str]] | None:
    """Generates hashtags grouped by competition tier (see
    _HASHTAG_GROUPS). Returns {group: [hashtag, ...]}, or None on
    failure."""
    if not topic.strip():
        return None

    prompt = f"Generate hashtags for this Instagram content topic: \"{topic.strip()}\"."
    result = _call_llm_for_json(llm, system=_HASHTAG_SYSTEM_PROMPT, prompt=prompt)
    if not isinstance(result, dict):
        return None

    cleaned: dict[str, list[str]] = {}
    for group in _HASHTAG_GROUPS:
        value = result.get(group)
        if isinstance(value, list) and all(isinstance(h, str) for h in value) and value:
            cleaned[group] = value
    return cleaned or None


# --- F. Story builder ---------------------------------------------------------------------


_STORY_SYSTEM_PROMPT = (
    "You are an Instagram Story sequence designer. " + _JSON_RESPONSE_INSTRUCTION + "\n\n"
    "Respond with a JSON array of Story objects, each with exactly these "
    "string fields: story_number (as a string, e.g. \"1\"), stage (a short "
    "label like \"Hook\", \"Problem\", \"Value\", \"Interaction\", \"CTA\" - "
    "chosen to fit the sequence's narrative arc, not necessarily this "
    "exact list), text, visual_idea, interactive_element, "
    "sticker_suggestion, cta."
)


def generate_story_sequence(
    llm: LLMClient, *, topic: str, goal: str, num_stories: int,
) -> list[dict] | None:
    """Generates a Story sequence of `num_stories` Stories for `topic`
    with the given `goal`. Returns a list of Story dicts (story_number/
    stage/text/visual_idea/interactive_element/sticker_suggestion/cta),
    or None on failure. `num_stories` is clamped to a sane range (1-15)
    before being sent to the model, so a caller passing a stray large
    number doesn't produce a runaway-length generation request."""
    if not topic.strip() or not goal.strip():
        return None
    num_stories = max(1, min(num_stories, 15))

    prompt = (
        f"Create a sequence of exactly {num_stories} Instagram Stories.\n\n"
        f"Topic: {topic.strip()}\nGoal: {goal.strip()}"
    )
    result = _call_llm_for_json(llm, system=_STORY_SYSTEM_PROMPT, prompt=prompt)
    if not isinstance(result, list):
        return None

    required_fields = {
        "story_number", "stage", "text", "visual_idea",
        "interactive_element", "sticker_suggestion", "cta",
    }
    valid_stories = [
        s for s in result if isinstance(s, dict) and required_fields.issubset(s.keys())
    ]
    return valid_stories or None


# --- G. Weekly content plan ---------------------------------------------------------------


_WEEKDAYS_EN = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

_WEEKLY_PLAN_SYSTEM_PROMPT = (
    "You are an Instagram content planner. " + _JSON_RESPONSE_INSTRUCTION + "\n\n"
    "Respond with a JSON object whose keys are exactly: "
    f"{', '.join(_WEEKDAYS_EN)}. Each value is a JSON array of content-item "
    "objects scheduled for that day (an empty array [] for a day with "
    "nothing planned - never omit a day). Each content-item object has "
    "exactly these string fields: format (e.g. \"Reel\", \"Story\", "
    "\"Carousel\"), topic, hook, cta, suggested_posting_time (a plain "
    "time-of-day description, e.g. \"08:00\" or \"evening\"), objective."
)


def generate_weekly_plan(
    llm: LLMClient, *, num_reels: int, num_stories: int, num_carousels: int,
    preferred_days: list[str], niche: str, weekly_goal: str,
) -> dict[str, list[dict]] | None:
    """Generates a Monday-Sunday content plan. Returns
    {weekday_lowercase_english: [content_item, ...]} for all 7 days
    (days with nothing planned map to an empty list), or None on
    failure. `preferred_days` is advisory context for the model, not
    validated against a fixed vocabulary here - the caller's UI is
    responsible for collecting it in whatever form it chooses to
    display it back."""
    if not niche.strip() or not weekly_goal.strip():
        return None

    prompt = (
        f"Create a Monday-Sunday Instagram content plan.\n\n"
        f"Reels this week: {num_reels}\n"
        f"Stories this week: {num_stories}\n"
        f"Carousels this week: {num_carousels}\n"
        f"Preferred posting days: {', '.join(preferred_days) if preferred_days else '(no preference)'}\n"
        f"Niche: {niche.strip()}\n"
        f"Weekly goal: {weekly_goal.strip()}"
    )
    result = _call_llm_for_json(llm, system=_WEEKLY_PLAN_SYSTEM_PROMPT, prompt=prompt)
    if not isinstance(result, dict):
        return None

    required_item_fields = {"format", "topic", "hook", "cta", "suggested_posting_time", "objective"}
    cleaned: dict[str, list[dict]] = {}
    for day in _WEEKDAYS_EN:
        items = result.get(day)
        if not isinstance(items, list):
            cleaned[day] = []
            continue
        cleaned[day] = [
            item for item in items
            if isinstance(item, dict) and required_item_fields.issubset(item.keys())
        ]
    # At least one day must have at least one valid item for this to be
    # a usable plan - an all-empty result (every day malformed) is
    # treated as a failure, not a legitimately empty week.
    if not any(cleaned.values()):
        return None
    return cleaned
