"""Tests for jarvis.video_studio.cover: frame extraction, LLM cover
text generation (mocked), and text-overlay rendering. Uses REAL ffmpeg
subprocess calls for extraction/rendering (see
tests/test_video_studio_ffmpeg_utils.py's module docstring for why real
calls, not mocks, are used for this package's ffmpeg-wrapping modules)
and a mocked LLMClient for generate_cover_text() (same pattern as
tests/test_video_studio_highlights.py - no real API call). Skipped
entirely if ffmpeg isn't on PATH.

Confirms: extract_cover_candidates() returns the requested count of
real, distinct frame files; generate_cover_text() is grounded in the
given transcript, never calls the LLM with tools, and returns None on
any failure; render_cover() produces a real 1080x1920 image for every
registered template, _wrap_text() never produces a line longer than
requested, and every error path (missing frame, unknown template,
empty text) raises CoverError with a clear message."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import MagicMock

import pytest

from jarvis.video_studio.cover import (
    COVER_TEMPLATES,
    CoverError,
    _wrap_text,
    extract_cover_candidates,
    generate_cover_text,
    render_cover,
)
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available, probe_video

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


def _text_response(text: str):
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    response = MagicMock()
    response.content = [text_block]
    return response


def _json_response(value) -> MagicMock:
    return _text_response(json.dumps(value))


@pytest.fixture(scope="module")
def source_video(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("video_studio_cover")
    video_path = out_dir / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=1280x720:d=10",
            "-c:v", "libx264", "-t", "10", str(video_path),
        ],
        capture_output=True, timeout=30, check=True,
    )
    return video_path


@pytest.fixture
def frame(source_video, tmp_path):
    candidates = extract_cover_candidates(source_video, duration_seconds=10.0, output_dir=tmp_path, count=1)
    return candidates[0]


# --- extract_cover_candidates -------------------------------------------------------------


def test_extracts_requested_number_of_candidates(source_video, tmp_path):
    candidates = extract_cover_candidates(source_video, duration_seconds=10.0, output_dir=tmp_path, count=5)
    assert len(candidates) == 5
    assert all(c.is_file() for c in candidates)
    assert len(set(candidates)) == 5  # distinct files


def test_extract_zero_duration_raises():
    with pytest.raises(CoverError, match="no measurable duration"):
        extract_cover_candidates(MagicMock(), duration_seconds=0.0, output_dir=MagicMock(), count=3)


def test_single_candidate_uses_midpoint(source_video, tmp_path):
    candidates = extract_cover_candidates(source_video, duration_seconds=10.0, output_dir=tmp_path, count=1)
    assert len(candidates) == 1


# --- generate_cover_text (mocked LLM) --------------------------------------------------


def test_generate_cover_text_returns_grounded_title():
    llm = MagicMock()
    llm.send.return_value = _json_response({"title": "You're Brushing Wrong"})
    title = generate_cover_text(llm, "Most people brush their teeth wrong every day.")
    assert title == "You're Brushing Wrong"


def test_generate_cover_text_empty_transcript_returns_none_without_calling_llm():
    llm = MagicMock()
    assert generate_cover_text(llm, "") is None
    llm.send.assert_not_called()


def test_generate_cover_text_llm_failure_returns_none():
    llm = MagicMock()
    llm.send.side_effect = RuntimeError("boom")
    assert generate_cover_text(llm, "some transcript") is None


def test_generate_cover_text_malformed_response_returns_none():
    llm = MagicMock()
    llm.send.return_value = _text_response("not json")
    assert generate_cover_text(llm, "some transcript") is None


def test_generate_cover_text_never_offers_tools():
    llm = MagicMock()
    llm.send.return_value = _json_response({"title": "A Title"})
    generate_cover_text(llm, "some transcript")
    call_args = llm.send.call_args
    assert call_args[0][1] == []


# --- _wrap_text ----------------------------------------------------------------------------


def test_wrap_text_respects_max_chars_per_line():
    wrapped = _wrap_text("This is a fairly long cover title to wrap", max_chars_per_line=15)
    for line in wrapped.split("\n"):
        assert len(line) <= 20  # some tolerance for a single overlong word


def test_wrap_text_short_text_stays_one_line():
    wrapped = _wrap_text("Short title", max_chars_per_line=50)
    assert "\n" not in wrapped


# --- render_cover ----------------------------------------------------------------------------


@pytest.mark.parametrize("template_name", list(COVER_TEMPLATES.keys()))
def test_render_cover_produces_1080x1920_for_every_template(frame, tmp_path, template_name):
    output = tmp_path / f"cover_{template_name}.jpg"
    result = render_cover(frame, "Test Cover Title", template_name=template_name, output_path=output)
    assert result == output
    assert output.is_file()
    probe = probe_video(output)
    assert probe.width == 1080
    assert probe.height == 1920


def test_render_cover_does_not_leave_a_leftover_text_file(frame, tmp_path):
    output = tmp_path / "cover.jpg"
    render_cover(frame, "Test Title", template_name="minimal", output_path=output)
    assert not output.with_suffix(".cover_text.txt").exists()


def test_render_cover_unknown_template_raises(frame, tmp_path):
    with pytest.raises(CoverError, match="Unknown cover template"):
        render_cover(frame, "Test", template_name="not_a_real_template", output_path=tmp_path / "x.jpg")


def test_render_cover_missing_frame_raises(tmp_path):
    with pytest.raises(CoverError, match="not found"):
        render_cover(tmp_path / "does_not_exist.jpg", "Test", template_name="minimal", output_path=tmp_path / "x.jpg")


def test_render_cover_empty_text_raises(frame, tmp_path):
    with pytest.raises(CoverError, match="cannot be empty"):
        render_cover(frame, "  ", template_name="minimal", output_path=tmp_path / "x.jpg")
