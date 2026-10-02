"""Tests for jarvis.video_studio.reel: rule-based clip selection/
trimming (no LLM) plus generate_reel_edit() with a mocked LLMClient (no
real API calls - same mocking pattern as
tests/test_video_studio_highlights.py). Confirms: candidates are
selected highest-confidence-first up to the target duration, an
over-long candidate is TRIMMED rather than rejected (a real bug found
during development - see test_select_candidates_trims_a_too_long_candidate_instead_of_rejecting_it),
final clip order is chronological (source-video order) regardless of
selection order, generate_reel_edit() never offers tools, and every
insufficient-data case (no candidates, none fit, LLM failure/malformed
response) returns insufficient_data=True rather than raising."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from jarvis.video_studio.highlights import HighlightCandidate
from jarvis.video_studio.reel import TARGET_DURATIONS, _select_candidates, generate_reel_edit


def _text_response(text: str):
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    response = MagicMock()
    response.content = [text_block]
    return response


def _json_response(value) -> MagicMock:
    return _text_response(json.dumps(value))


def _candidate(start, end, confidence="high", text="Some transcript text here.") -> HighlightCandidate:
    return HighlightCandidate(
        start_seconds=start, end_seconds=end, transcript_text=text,
        reason="r", suggested_hook="h", confidence=confidence,
    )


# --- _select_candidates (no LLM) --------------------------------------------------------


def test_selects_candidates_up_to_target_duration():
    candidates = [_candidate(0.0, 10.0), _candidate(20.0, 30.0)]
    selected = _select_candidates(candidates, target_duration_seconds=15)
    assert len(selected) >= 1
    total = sum(c.duration_seconds for c in selected)
    assert total <= 15 * 1.2


def test_prefers_higher_confidence_candidates():
    low = _candidate(0.0, 10.0, confidence="low")
    high = _candidate(50.0, 60.0, confidence="high")
    selected = _select_candidates([low, high], target_duration_seconds=10)
    assert high in selected


def test_select_candidates_trims_a_too_long_candidate_instead_of_rejecting_it():
    # Regression test for a real bug found during development: a
    # single candidate longer than the target*1.2 budget was rejected
    # entirely, leaving an EMPTY selection even though the module's
    # own brief lists "trimming" as part of the edit - fixed by
    # trimming the candidate's tail to fit instead.
    long_candidate = _candidate(0.0, 100.0)
    selected = _select_candidates([long_candidate], target_duration_seconds=15)
    assert len(selected) == 1
    assert selected[0].start_seconds == 0.0
    assert selected[0].end_seconds == 15 * 1.2  # trimmed to the max budget, not rejected


def test_final_order_is_chronological_not_selection_order():
    early_low = _candidate(0.0, 5.0, confidence="low")
    late_high = _candidate(50.0, 55.0, confidence="high")
    selected = _select_candidates([early_low, late_high], target_duration_seconds=20)
    starts = [c.start_seconds for c in selected]
    assert starts == sorted(starts)


def test_empty_candidates_returns_empty_selection():
    assert _select_candidates([], target_duration_seconds=30) == []


# --- generate_reel_edit: insufficient data ------------------------------------------------


def test_no_candidates_is_insufficient_data():
    llm = MagicMock()
    result = generate_reel_edit(llm, [], target_duration_seconds=30, style="Educational", pacing="Normal")
    assert result.insufficient_data is True
    llm.send.assert_not_called()


def test_llm_exception_is_insufficient_data_not_raised():
    llm = MagicMock()
    llm.send.side_effect = RuntimeError("network down")
    result = generate_reel_edit(
        llm, [_candidate(0.0, 10.0)], target_duration_seconds=30, style="Educational", pacing="Normal",
    )
    assert result.insufficient_data is True
    assert "network down" in (result.message or "")


def test_malformed_json_response_is_insufficient_data():
    llm = MagicMock()
    llm.send.return_value = _text_response("not json")
    result = generate_reel_edit(
        llm, [_candidate(0.0, 10.0)], target_duration_seconds=30, style="Educational", pacing="Normal",
    )
    assert result.insufficient_data is True


def test_mismatched_clip_captions_length_is_insufficient_data():
    llm = MagicMock()
    llm.send.return_value = _json_response({"hook": "h", "cta": "c", "clip_captions": []})  # 0 captions for 1 clip
    result = generate_reel_edit(
        llm, [_candidate(0.0, 10.0)], target_duration_seconds=30, style="Educational", pacing="Normal",
    )
    assert result.insufficient_data is True


# --- generate_reel_edit: valid response ---------------------------------------------------


def test_valid_response_produces_a_plan():
    llm = MagicMock()
    llm.send.return_value = _json_response(
        {"hook": "Big hook here", "cta": "Follow for more", "clip_captions": ["Caption one"]}
    )
    result = generate_reel_edit(
        llm, [_candidate(0.0, 10.0)], target_duration_seconds=30, style="Educational", pacing="Normal",
    )
    assert result.insufficient_data is False
    assert result.hook == "Big hook here"
    assert result.cta == "Follow for more"
    assert len(result.clips) == 1
    assert result.clips[0].caption_text == "Caption one"
    assert result.total_duration_seconds == 10.0


def test_call_never_offers_tools():
    llm = MagicMock()
    llm.send.return_value = _json_response({"hook": "h", "cta": "c", "clip_captions": ["cap"]})
    generate_reel_edit(llm, [_candidate(0.0, 10.0)], target_duration_seconds=30, style="Educational", pacing="Normal")
    call_args = llm.send.call_args
    assert call_args[0][1] == []


def test_unsupported_target_duration_snaps_to_nearest():
    llm = MagicMock()
    llm.send.return_value = _json_response({"hook": "h", "cta": "c", "clip_captions": ["cap"]})
    result = generate_reel_edit(
        llm, [_candidate(0.0, 10.0)], target_duration_seconds=100, style="Educational", pacing="Normal",
    )
    assert result.target_duration_seconds in TARGET_DURATIONS
    assert result.target_duration_seconds == 90  # nearest to 100
