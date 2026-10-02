"""Tests for jarvis.video_studio.highlights: rule-based candidate
window building (no LLM) plus find_highlights() with a mocked
LLMClient (no real API calls - same mocking pattern as
tests/test_instagram_ai_manager_ai_services.py). Confirms: candidate
windows split at scene changes/large gaps/max duration, find_highlights
never calls the LLM with tools (so it can never trigger a real
action), insufficient-data cases (no transcript, no candidate windows,
LLM failure, malformed response) all return insufficient_data=True
rather than raising, and every returned candidate's confidence is one
of the model's own low/medium/high labels - never a fabricated
numeric score."""

from __future__ import annotations

import dataclasses
import json
from unittest.mock import MagicMock

from jarvis.video_studio.analysis import VideoAnalysis
from jarvis.video_studio.ffmpeg_utils import SceneChange
from jarvis.video_studio.highlights import HighlightResult, _build_candidate_windows, find_highlights
from jarvis.video_studio.transcribe import TranscriptionResult, TranscriptSegment


def _text_response(text: str):
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    response = MagicMock()
    response.content = [text_block]
    return response


def _json_response(value) -> MagicMock:
    return _text_response(json.dumps(value))


def _analysis(scene_changes=(), duration=30.0) -> VideoAnalysis:
    return VideoAnalysis(
        duration_seconds=duration, width=640, height=360, aspect_ratio="16:9", fps=25.0,
        has_audio=True, file_size_bytes=1000, video_codec="h264", audio_codec="aac",
        scene_changes=list(scene_changes), silence_gaps=[], error=None,
    )


def _transcription(segments) -> TranscriptionResult:
    full_text = " ".join(s.text for s in segments)
    return TranscriptionResult(segments=list(segments), full_text=full_text, language=None, error=None)


# --- _build_candidate_windows (no LLM) --------------------------------------------------


def test_no_segments_produces_no_windows():
    windows = _build_candidate_windows(_analysis(), _transcription([]))
    assert windows == []


def test_continuous_speech_becomes_one_window():
    segments = [
        TranscriptSegment(0.0, 3.0, "First sentence here now."),
        TranscriptSegment(3.0, 6.0, "Second sentence follows closely."),
    ]
    windows = _build_candidate_windows(_analysis(), _transcription(segments))
    assert len(windows) == 1
    assert windows[0].start_seconds == 0.0
    assert windows[0].end_seconds == 6.0


def test_scene_change_splits_window():
    segments = [
        TranscriptSegment(0.0, 3.0, "First sentence here now."),
        TranscriptSegment(10.0, 13.0, "Different topic starts here."),
    ]
    analysis = _analysis(scene_changes=[SceneChange(timestamp_seconds=6.0, score=20.0)])
    windows = _build_candidate_windows(analysis, _transcription(segments))
    assert len(windows) == 2
    assert windows[0].end_seconds == 3.0
    assert windows[1].start_seconds == 10.0


def test_large_gap_splits_window_even_without_scene_change():
    segments = [
        TranscriptSegment(0.0, 3.0, "First sentence here now."),
        TranscriptSegment(20.0, 23.0, "Much later sentence begins."),
    ]
    windows = _build_candidate_windows(_analysis(), _transcription(segments))
    assert len(windows) == 2


def test_short_fragment_filtered_out():
    segments = [TranscriptSegment(0.0, 1.0, "Thanks.")]
    windows = _build_candidate_windows(_analysis(), _transcription(segments))
    assert windows == []


def test_window_capped_at_max_duration():
    # 40 short segments, 3s apart with no gaps/scene changes - spans
    # well over 90s if left unsplit; must be split into multiple windows.
    segments = [
        TranscriptSegment(i * 3.0, i * 3.0 + 2.5, f"Sentence number {i} here.")
        for i in range(40)
    ]
    windows = _build_candidate_windows(_analysis(duration=150.0), _transcription(segments))
    assert len(windows) > 1
    assert all(w.duration_seconds <= 90.0 for w in windows)


# --- find_highlights: insufficient data -----------------------------------------------


def test_no_transcript_is_insufficient_data():
    llm = MagicMock()
    empty = TranscriptionResult(segments=[], full_text="", language=None, error="No speech detected")
    result = find_highlights(llm, _analysis(), empty)
    assert result.insufficient_data is True
    assert result.candidates == []
    llm.send.assert_not_called()


def test_transcript_with_error_is_insufficient_data():
    llm = MagicMock()
    errored = TranscriptionResult(segments=[], full_text="", language=None, error="boom")
    result = find_highlights(llm, _analysis(), errored)
    assert result.insufficient_data is True
    llm.send.assert_not_called()


def test_only_short_fragments_is_insufficient_data():
    llm = MagicMock()
    segments = [TranscriptSegment(0.0, 1.0, "Ok.")]
    result = find_highlights(llm, _analysis(), _transcription(segments))
    assert result.insufficient_data is True
    llm.send.assert_not_called()


def test_llm_exception_is_insufficient_data_not_raised():
    llm = MagicMock()
    llm.send.side_effect = RuntimeError("network down")
    segments = [TranscriptSegment(0.0, 5.0, "A reasonably long sentence about something.")]
    result = find_highlights(llm, _analysis(), _transcription(segments))
    assert result.insufficient_data is True
    assert "network down" in (result.message or "")


def test_malformed_json_response_is_insufficient_data():
    llm = MagicMock()
    llm.send.return_value = _text_response("not json at all")
    segments = [TranscriptSegment(0.0, 5.0, "A reasonably long sentence about something.")]
    result = find_highlights(llm, _analysis(), _transcription(segments))
    assert result.insufficient_data is True


def test_empty_list_response_is_insufficient_data():
    llm = MagicMock()
    llm.send.return_value = _json_response([])
    segments = [TranscriptSegment(0.0, 5.0, "A reasonably long sentence about something.")]
    result = find_highlights(llm, _analysis(), _transcription(segments))
    assert result.insufficient_data is True


# --- find_highlights: valid responses ---------------------------------------------------


def test_valid_response_produces_candidates():
    llm = MagicMock()
    llm.send.return_value = _json_response(
        [{"index": 0, "reason": "Strong hook.", "suggested_hook": "Did you know...", "confidence": "high"}]
    )
    segments = [TranscriptSegment(0.0, 5.0, "A reasonably long sentence about something interesting.")]
    result = find_highlights(llm, _analysis(), _transcription(segments))
    assert result.insufficient_data is False
    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.reason == "Strong hook."
    assert candidate.suggested_hook == "Did you know..."
    assert candidate.confidence == "high"
    assert candidate.start_seconds == 0.0
    assert candidate.end_seconds == 5.0


def test_call_never_offers_tools():
    llm = MagicMock()
    llm.send.return_value = _json_response(
        [{"index": 0, "reason": "r", "suggested_hook": "h", "confidence": "low"}]
    )
    segments = [TranscriptSegment(0.0, 5.0, "A reasonably long sentence about something interesting.")]
    find_highlights(llm, _analysis(), _transcription(segments))
    call_args = llm.send.call_args
    assert call_args[0][1] == []  # tools argument is always empty


def test_invalid_confidence_value_skips_that_candidate():
    llm = MagicMock()
    llm.send.return_value = _json_response(
        [{"index": 0, "reason": "r", "suggested_hook": "h", "confidence": "extremely high"}]
    )
    segments = [TranscriptSegment(0.0, 5.0, "A reasonably long sentence about something interesting.")]
    result = find_highlights(llm, _analysis(), _transcription(segments))
    assert result.insufficient_data is True  # the one candidate was rejected, leaving none


def test_out_of_range_index_skips_that_candidate():
    llm = MagicMock()
    llm.send.return_value = _json_response(
        [{"index": 99, "reason": "r", "suggested_hook": "h", "confidence": "low"}]
    )
    segments = [TranscriptSegment(0.0, 5.0, "A reasonably long sentence about something interesting.")]
    result = find_highlights(llm, _analysis(), _transcription(segments))
    assert result.insufficient_data is True


def test_candidates_sorted_by_start_time():
    llm = MagicMock()
    llm.send.return_value = _json_response(
        [
            {"index": 1, "reason": "r2", "suggested_hook": "h2", "confidence": "low"},
            {"index": 0, "reason": "r1", "suggested_hook": "h1", "confidence": "high"},
        ]
    )
    segments = [
        TranscriptSegment(0.0, 3.0, "First sentence here about something."),
        TranscriptSegment(20.0, 23.0, "Second much later sentence about something."),
    ]
    result = find_highlights(llm, _analysis(), _transcription(segments))
    assert [c.start_seconds for c in result.candidates] == [0.0, 20.0]


def test_from_dict_round_trips_through_json_including_nested_candidates():
    # Regression test for a real bug found by hand-testing: dataclasses
    # .asdict() recursively converts nested dataclasses
    # (HighlightCandidate) to plain dicts, so HighlightResult(**data)
    # (bare unpacking) left `candidates` as a list of dicts instead of
    # HighlightCandidate instances - an AttributeError the first time
    # .start_seconds was accessed on one (found via the Reject button's
    # own handler, which reads candidate.start_seconds/.end_seconds).
    llm = MagicMock()
    llm.send.return_value = _json_response(
        [{"index": 0, "reason": "r", "suggested_hook": "h", "confidence": "high"}]
    )
    segments = [TranscriptSegment(0.0, 5.0, "A reasonably long sentence about something interesting.")]
    original = find_highlights(llm, _analysis(), _transcription(segments))
    as_dict = dataclasses.asdict(original)
    reconstructed = HighlightResult.from_dict(as_dict)
    assert reconstructed == original
    for candidate in reconstructed.candidates:
        assert candidate.start_seconds is not None  # would raise AttributeError if still a dict
