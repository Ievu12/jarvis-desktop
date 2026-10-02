"""Tests for jarvis.instagram_ai_manager.ai_services: isolated,
tool-free LLM calls generating Instagram content drafts (Reel ideas,
hooks, captions, CTAs, hashtags, Story sequences, weekly plans). No
real API calls - LLMClient is mocked throughout, following the same
pattern as tests/test_commit_message.py. Confirms: valid JSON responses
are parsed and validated correctly, malformed/wrong-shape responses
return None (or a partial result where the function documents partial
tolerance) rather than raising, markdown code fences around the JSON
are tolerated, and every function is truly tool-free (llm.send() is
always called with an empty tools list, per the module's own safety
guarantee that generation can never trigger a real Instagram action)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from jarvis.instagram_ai_manager import ai_services


def _text_response(text: str):
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    response = MagicMock()
    response.content = [text_block]
    return response


def _json_response(value) -> MagicMock:
    return _text_response(json.dumps(value))


_VALID_REEL_IDEA = {
    "title": "t", "concept": "c", "target_audience": "a", "suggested_format": "f",
    "hook": "h", "cta": "cta", "estimated_difficulty": "easy", "why_it_may_work": "w",
}


# --- shared: every generator call is tool-free -----------------------------------------


def test_generate_reel_ideas_calls_llm_with_no_tools():
    llm = MagicMock()
    llm.send.return_value = _json_response([_VALID_REEL_IDEA])
    ai_services.generate_reel_ideas(llm, "skincare")
    call_args = llm.send.call_args
    assert call_args[0][1] == []  # tools argument is always empty


def test_generate_reel_ideas_calls_llm_with_no_conversation_history():
    llm = MagicMock()
    llm.send.return_value = _json_response([_VALID_REEL_IDEA])
    ai_services.generate_reel_ideas(llm, "skincare")
    messages = llm.send.call_args[0][0]
    assert len(messages) == 1  # a single, isolated user message - no prior turns


# --- A. generate_reel_ideas ------------------------------------------------------------


def test_generate_reel_ideas_returns_valid_ideas():
    llm = MagicMock()
    llm.send.return_value = _json_response([_VALID_REEL_IDEA, _VALID_REEL_IDEA])
    result = ai_services.generate_reel_ideas(llm, "skincare", count=2)
    assert result == [_VALID_REEL_IDEA, _VALID_REEL_IDEA]


def test_generate_reel_ideas_returns_none_on_non_json_response():
    llm = MagicMock()
    llm.send.return_value = _text_response("Sorry, I can't help with that.")
    result = ai_services.generate_reel_ideas(llm, "skincare")
    assert result is None


def test_generate_reel_ideas_returns_none_on_wrong_top_level_type():
    llm = MagicMock()
    llm.send.return_value = _json_response({"not": "a list"})
    result = ai_services.generate_reel_ideas(llm, "skincare")
    assert result is None


def test_generate_reel_ideas_filters_out_incomplete_items():
    llm = MagicMock()
    incomplete = {"title": "only a title"}
    llm.send.return_value = _json_response([_VALID_REEL_IDEA, incomplete])
    result = ai_services.generate_reel_ideas(llm, "skincare")
    assert result == [_VALID_REEL_IDEA]


def test_generate_reel_ideas_returns_none_for_empty_topic():
    llm = MagicMock()
    result = ai_services.generate_reel_ideas(llm, "   ")
    assert result is None
    llm.send.assert_not_called()


def test_generate_reel_ideas_returns_none_when_llm_raises():
    llm = MagicMock()
    llm.send.side_effect = RuntimeError("network error")
    result = ai_services.generate_reel_ideas(llm, "skincare")  # must not raise
    assert result is None


def test_generate_reel_ideas_tolerates_markdown_code_fence():
    llm = MagicMock()
    fenced = "```json\n" + json.dumps([_VALID_REEL_IDEA]) + "\n```"
    llm.send.return_value = _text_response(fenced)
    result = ai_services.generate_reel_ideas(llm, "skincare")
    assert result == [_VALID_REEL_IDEA]


def test_generate_reel_ideas_prompt_includes_topic():
    llm = MagicMock()
    llm.send.return_value = _json_response([_VALID_REEL_IDEA])
    ai_services.generate_reel_ideas(llm, "self development")
    prompt = llm.send.call_args[0][0][0]["content"]
    assert "self development" in prompt


# --- B. generate_hooks -------------------------------------------------------------------


def test_generate_hooks_returns_all_categories():
    llm = MagicMock()
    payload = {cat: ["hook 1", "hook 2"] for cat in ai_services._HOOK_CATEGORIES}
    llm.send.return_value = _json_response(payload)
    result = ai_services.generate_hooks(llm, "yoga")
    assert set(result.keys()) == set(ai_services._HOOK_CATEGORIES)


def test_generate_hooks_omits_malformed_categories_but_keeps_valid_ones():
    llm = MagicMock()
    payload = {"curiosity": ["valid hook"], "question": "not a list"}
    llm.send.return_value = _json_response(payload)
    result = ai_services.generate_hooks(llm, "yoga")
    assert result == {"curiosity": ["valid hook"]}


def test_generate_hooks_returns_none_when_all_categories_invalid():
    llm = MagicMock()
    llm.send.return_value = _json_response({"unexpected_key": ["x"]})
    result = ai_services.generate_hooks(llm, "yoga")
    assert result is None


def test_generate_hooks_returns_none_for_empty_topic():
    llm = MagicMock()
    result = ai_services.generate_hooks(llm, "")
    assert result is None
    llm.send.assert_not_called()


# --- C. generate_caption ------------------------------------------------------------------


_VALID_CAPTION = {"short_caption": "s", "medium_caption": "m", "long_caption": "l"}


def test_generate_caption_returns_all_three_lengths():
    llm = MagicMock()
    llm.send.return_value = _json_response(_VALID_CAPTION)
    result = ai_services.generate_caption(
        llm, topic="skincare", content_description="a reel about routines", hook="hook",
        tone="friendly", target_audience="women 20-30", cta="save this",
    )
    assert result == _VALID_CAPTION


def test_generate_caption_returns_none_on_missing_field():
    llm = MagicMock()
    llm.send.return_value = _json_response({"short_caption": "s", "medium_caption": "m"})
    result = ai_services.generate_caption(
        llm, topic="skincare", content_description="x", hook=None, tone="friendly",
        target_audience=None, cta=None,
    )
    assert result is None


def test_generate_caption_returns_none_on_empty_string_value():
    llm = MagicMock()
    llm.send.return_value = _json_response(
        {"short_caption": "", "medium_caption": "m", "long_caption": "l"}
    )
    result = ai_services.generate_caption(
        llm, topic="skincare", content_description="x", hook=None, tone="friendly",
        target_audience=None, cta=None,
    )
    assert result is None


def test_generate_caption_prompt_includes_tone():
    llm = MagicMock()
    llm.send.return_value = _json_response(_VALID_CAPTION)
    ai_services.generate_caption(
        llm, topic="skincare", content_description="x", hook=None, tone="expert",
        target_audience=None, cta=None,
    )
    prompt = llm.send.call_args[0][0][0]["content"]
    assert "expert" in prompt


def test_generate_caption_returns_none_for_empty_topic():
    llm = MagicMock()
    result = ai_services.generate_caption(
        llm, topic="", content_description="x", hook=None, tone="friendly",
        target_audience=None, cta=None,
    )
    assert result is None
    llm.send.assert_not_called()


# --- D. generate_cta ---------------------------------------------------------------------


def test_generate_cta_returns_all_categories():
    llm = MagicMock()
    payload = {cat: ["cta text"] for cat in ai_services._CTA_CATEGORIES}
    llm.send.return_value = _json_response(payload)
    result = ai_services.generate_cta(llm, "skincare")
    assert set(result.keys()) == set(ai_services._CTA_CATEGORIES)


def test_generate_cta_returns_none_on_non_dict_response():
    llm = MagicMock()
    llm.send.return_value = _json_response(["not", "a", "dict"])
    result = ai_services.generate_cta(llm, "skincare")
    assert result is None


# --- E. generate_hashtags -----------------------------------------------------------------


def test_generate_hashtags_returns_all_groups():
    llm = MagicMock()
    payload = {group: ["#tag1", "#tag2"] for group in ai_services._HASHTAG_GROUPS}
    llm.send.return_value = _json_response(payload)
    result = ai_services.generate_hashtags(llm, "skincare")
    assert set(result.keys()) == set(ai_services._HASHTAG_GROUPS)


def test_generate_hashtags_includes_branded_group():
    llm = MagicMock()
    payload = {group: ["#tag"] for group in ai_services._HASHTAG_GROUPS}
    llm.send.return_value = _json_response(payload)
    result = ai_services.generate_hashtags(llm, "skincare")
    assert "branded" in result


# --- F. generate_story_sequence ------------------------------------------------------------


_VALID_STORY = {
    "story_number": "1", "stage": "Hook", "text": "t", "visual_idea": "v",
    "interactive_element": "i", "sticker_suggestion": "s", "cta": "c",
}


def test_generate_story_sequence_returns_valid_stories():
    llm = MagicMock()
    llm.send.return_value = _json_response([_VALID_STORY, _VALID_STORY])
    result = ai_services.generate_story_sequence(llm, topic="yoga", goal="engagement", num_stories=2)
    assert result == [_VALID_STORY, _VALID_STORY]


def test_generate_story_sequence_clamps_num_stories_to_max():
    llm = MagicMock()
    llm.send.return_value = _json_response([_VALID_STORY])
    ai_services.generate_story_sequence(llm, topic="yoga", goal="engagement", num_stories=999)
    prompt = llm.send.call_args[0][0][0]["content"]
    assert "15" in prompt  # clamped to the documented max


def test_generate_story_sequence_clamps_num_stories_to_min():
    llm = MagicMock()
    llm.send.return_value = _json_response([_VALID_STORY])
    ai_services.generate_story_sequence(llm, topic="yoga", goal="engagement", num_stories=0)
    prompt = llm.send.call_args[0][0][0]["content"]
    assert "exactly 1 " in prompt


def test_generate_story_sequence_returns_none_for_empty_goal():
    llm = MagicMock()
    result = ai_services.generate_story_sequence(llm, topic="yoga", goal="", num_stories=5)
    assert result is None
    llm.send.assert_not_called()


# --- G. generate_weekly_plan ---------------------------------------------------------------


_VALID_PLAN_ITEM = {
    "format": "Reel", "topic": "yoga", "hook": "h", "cta": "c",
    "suggested_posting_time": "08:00", "objective": "engagement",
}


def test_generate_weekly_plan_returns_all_seven_days():
    llm = MagicMock()
    payload = {day: [_VALID_PLAN_ITEM] for day in ai_services._WEEKDAYS_EN}
    llm.send.return_value = _json_response(payload)
    result = ai_services.generate_weekly_plan(
        llm, num_reels=3, num_stories=5, num_carousels=1, preferred_days=["monday"],
        niche="yoga", weekly_goal="grow followers",
    )
    assert set(result.keys()) == set(ai_services._WEEKDAYS_EN)


def test_generate_weekly_plan_fills_missing_day_with_empty_list():
    llm = MagicMock()
    payload = {day: [_VALID_PLAN_ITEM] for day in ai_services._WEEKDAYS_EN if day != "sunday"}
    llm.send.return_value = _json_response(payload)
    result = ai_services.generate_weekly_plan(
        llm, num_reels=3, num_stories=5, num_carousels=1, preferred_days=[],
        niche="yoga", weekly_goal="grow followers",
    )
    assert result["sunday"] == []


def test_generate_weekly_plan_returns_none_when_all_days_empty():
    llm = MagicMock()
    payload = {day: [] for day in ai_services._WEEKDAYS_EN}
    llm.send.return_value = _json_response(payload)
    result = ai_services.generate_weekly_plan(
        llm, num_reels=0, num_stories=0, num_carousels=0, preferred_days=[],
        niche="yoga", weekly_goal="grow followers",
    )
    assert result is None


def test_generate_weekly_plan_returns_none_for_empty_niche():
    llm = MagicMock()
    result = ai_services.generate_weekly_plan(
        llm, num_reels=1, num_stories=1, num_carousels=1, preferred_days=[],
        niche="", weekly_goal="grow followers",
    )
    assert result is None
    llm.send.assert_not_called()
