"""Tests for jarvis.video_editor.ai_assistant.propose_reel_style: a
single, tool-free, history-free LLM call proposing a styling bundle
for a natural-language brief. Purely advisory - never raises, always
falls back to None on any failure. No real API calls - LLMClient is
mocked throughout, same convention tests/test_commit_message.py
already established for the sibling one-shot LLM feature."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from jarvis.video_editor.ai_assistant import AiReelProposal, StickerSuggestion, propose_reel_style


def _text_response(text: str):
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text

    response = MagicMock()
    response.content = [text_block]
    return response


_VALID_JSON = json.dumps({
    "motion": "zoom_in", "motion_intensity": 1.3, "fade": "fade_in", "fade_seconds": 0.5,
    "brightness": 0.05, "contrast": 1.1, "saturation": 1.1,
    "caption_position": "bottom", "caption_animation": "pop",
    "caption_color": "white", "caption_highlight_color": "#FFD166",
    "text_template_name": "Bold Reveal", "suggested_caption_text": "Breathe in, breathe out.",
    "music_mood_suggestion": "soft ambient piano",
    "stickers": [{"shape": "lotus", "animation": "fade_in_out", "x_fraction": 0.5, "y_fraction": 0.85}],
})


# --- happy path -----------------------------------------------------------------


def test_returns_a_fully_populated_proposal():
    llm = MagicMock()
    llm.send.return_value = _text_response(_VALID_JSON)

    result = propose_reel_style(llm, "calm yoga reel with a soft affirmation")
    assert isinstance(result, AiReelProposal)
    assert result.effect.motion == "zoom_in"
    assert result.effect.motion_intensity == 1.3
    assert result.caption_style.position == "bottom"
    assert result.caption_style.animation == "pop"
    assert result.text_template_name == "Bold Reveal"
    assert result.suggested_caption_text == "Breathe in, breathe out."
    assert result.music_mood_suggestion == "soft ambient piano"
    assert len(result.stickers) == 1
    assert result.stickers[0] == StickerSuggestion(shape="lotus", animation="fade_in_out", x_fraction=0.5, y_fraction=0.85)


def test_proposal_result_is_already_a_valid_effect_spec():
    llm = MagicMock()
    llm.send.return_value = _text_response(_VALID_JSON)

    result = propose_reel_style(llm, "brief")
    assert result.effect.validate() == []


def test_sticker_suggestion_converts_to_a_real_sticker_preset():
    llm = MagicMock()
    llm.send.return_value = _text_response(_VALID_JSON)

    result = propose_reel_style(llm, "brief")
    preset = result.stickers[0].to_preset()
    assert preset.shape == "lotus"
    assert preset.animation == "fade_in_out"


def test_prompt_includes_the_brief():
    llm = MagicMock()
    llm.send.return_value = _text_response(_VALID_JSON)

    propose_reel_style(llm, "a cozy autumn coffee shop vlog")

    prompt = llm.send.call_args[0][0][0]["content"]
    assert "cozy autumn coffee shop vlog" in prompt


def test_no_tools_offered():
    llm = MagicMock()
    llm.send.return_value = _text_response(_VALID_JSON)

    propose_reel_style(llm, "brief")

    tools_arg = llm.send.call_args[0][1]
    assert tools_arg == []


def test_uses_isolated_system_prompt_not_the_main_agent_prompt():
    llm = MagicMock()
    llm.send.return_value = _text_response(_VALID_JSON)

    propose_reel_style(llm, "brief")

    kwargs = llm.send.call_args[1]
    assert "system" in kwargs
    assert "JARVIS" not in kwargs["system"]


def test_bounded_max_tokens():
    llm = MagicMock()
    llm.send.return_value = _text_response(_VALID_JSON)

    propose_reel_style(llm, "brief")

    kwargs = llm.send.call_args[1]
    assert kwargs["max_tokens"] < 4096


# --- empty brief never calls the LLM --------------------------------------------


def test_empty_brief_returns_none_without_calling_llm():
    llm = MagicMock()
    result = propose_reel_style(llm, "")
    assert result is None
    llm.send.assert_not_called()


def test_whitespace_only_brief_returns_none_without_calling_llm():
    llm = MagicMock()
    result = propose_reel_style(llm, "   ")
    assert result is None
    llm.send.assert_not_called()


# --- failure modes always return None, never raise -------------------------------


def test_llm_exception_returns_none():
    llm = MagicMock()
    llm.send.side_effect = RuntimeError("API error")

    result = propose_reel_style(llm, "brief")
    assert result is None


def test_network_error_returns_none():
    llm = MagicMock()
    llm.send.side_effect = ConnectionError("network down")

    result = propose_reel_style(llm, "brief")
    assert result is None


def test_empty_response_text_returns_none():
    llm = MagicMock()
    llm.send.return_value = _text_response("")

    result = propose_reel_style(llm, "brief")
    assert result is None


def test_non_json_response_returns_none():
    llm = MagicMock()
    llm.send.return_value = _text_response("Sure! Here's a nice style for your reel: ...")

    result = propose_reel_style(llm, "brief")
    assert result is None


def test_json_array_instead_of_object_returns_none():
    llm = MagicMock()
    llm.send.return_value = _text_response(json.dumps(["not", "an", "object"]))

    result = propose_reel_style(llm, "brief")
    assert result is None


def test_response_with_no_text_blocks_returns_none():
    llm = MagicMock()
    response = MagicMock()
    response.content = []
    llm.send.return_value = response

    result = propose_reel_style(llm, "brief")
    assert result is None


# --- every field is coerced into real, safe choices - never trusts the model raw ----


def test_unknown_motion_value_falls_back_to_none_motion():
    llm = MagicMock()
    bad = json.loads(_VALID_JSON)
    bad["motion"] = "mega_ultra_zoom"  # not a real PhotoMotionKind
    llm.send.return_value = _text_response(json.dumps(bad))

    result = propose_reel_style(llm, "brief")
    assert result.effect.motion == "none"


def test_out_of_range_motion_intensity_is_clamped():
    llm = MagicMock()
    bad = json.loads(_VALID_JSON)
    bad["motion_intensity"] = 99.0
    llm.send.return_value = _text_response(json.dumps(bad))

    result = propose_reel_style(llm, "brief")
    assert result.effect.motion_intensity == 1.5


def test_negative_brightness_out_of_range_is_clamped():
    llm = MagicMock()
    bad = json.loads(_VALID_JSON)
    bad["brightness"] = -50.0
    llm.send.return_value = _text_response(json.dumps(bad))

    result = propose_reel_style(llm, "brief")
    assert result.effect.brightness == -0.3


def test_unknown_caption_animation_falls_back_to_word_by_word():
    llm = MagicMock()
    bad = json.loads(_VALID_JSON)
    bad["caption_animation"] = "disco_spin"
    llm.send.return_value = _text_response(json.dumps(bad))

    result = propose_reel_style(llm, "brief")
    assert result.caption_style.animation == "word_by_word"


def test_unknown_text_template_name_becomes_none():
    llm = MagicMock()
    bad = json.loads(_VALID_JSON)
    bad["text_template_name"] = "A Template That Does Not Exist"
    llm.send.return_value = _text_response(json.dumps(bad))

    result = propose_reel_style(llm, "brief")
    assert result.text_template_name is None


def test_missing_fields_fall_back_to_safe_defaults_without_raising():
    llm = MagicMock()
    llm.send.return_value = _text_response(json.dumps({}))

    result = propose_reel_style(llm, "brief")
    assert isinstance(result, AiReelProposal)
    assert result.effect.motion == "none"
    assert result.caption_style.position == "bottom"
    assert result.stickers == ()


def test_sticker_with_unknown_shape_is_dropped_not_kept():
    llm = MagicMock()
    bad = json.loads(_VALID_JSON)
    bad["stickers"] = [{"shape": "not_a_real_shape", "animation": "pop_in", "x_fraction": 0.5, "y_fraction": 0.5}]
    llm.send.return_value = _text_response(json.dumps(bad))

    result = propose_reel_style(llm, "brief")
    assert result.stickers == ()


def test_sticker_list_is_capped_at_three_even_if_the_model_sends_more():
    llm = MagicMock()
    bad = json.loads(_VALID_JSON)
    bad["stickers"] = [
        {"shape": "heart", "animation": "pop_in", "x_fraction": 0.1 * n, "y_fraction": 0.1 * n}
        for n in range(10)
    ]
    llm.send.return_value = _text_response(json.dumps(bad))

    result = propose_reel_style(llm, "brief")
    assert len(result.stickers) == 3


def test_sticker_x_y_fraction_out_of_range_is_clamped():
    llm = MagicMock()
    bad = json.loads(_VALID_JSON)
    bad["stickers"] = [{"shape": "heart", "animation": "pop_in", "x_fraction": 5.0, "y_fraction": -3.0}]
    llm.send.return_value = _text_response(json.dumps(bad))

    result = propose_reel_style(llm, "brief")
    assert result.stickers[0].x_fraction == 1.0
    assert result.stickers[0].y_fraction == 0.0


def test_non_dict_sticker_entries_are_skipped():
    llm = MagicMock()
    bad = json.loads(_VALID_JSON)
    bad["stickers"] = ["not a dict", 42, None]
    llm.send.return_value = _text_response(json.dumps(bad))

    result = propose_reel_style(llm, "brief")
    assert result.stickers == ()


def test_suggested_caption_text_is_truncated_to_a_sane_length():
    llm = MagicMock()
    bad = json.loads(_VALID_JSON)
    bad["suggested_caption_text"] = "x" * 500
    llm.send.return_value = _text_response(json.dumps(bad))

    result = propose_reel_style(llm, "brief")
    assert len(result.suggested_caption_text) <= 120
