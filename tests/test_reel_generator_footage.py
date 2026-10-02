"""Tests for jarvis.reel_generator.footage: Mode A ("create Reel from
my footage") orchestration over AI Video Studio's own already-existing
pipeline (storage/transcribe/analysis/highlights/reel/export). Uses
REAL ffmpeg + REAL downloaded whisper.cpp model (skipped if either is
unavailable, matching tests/test_video_studio_transcribe.py's own
precedent) for transcription/analysis/export, and a MOCKED LLMClient
for the two LLM-calling steps (find_highlights/generate_reel_edit) -
those functions have their own dedicated test suites
(test_video_studio_highlights.py/test_video_studio_reel.py); this
file's job is confirming footage.py wires the pipeline together
correctly and in the right order, not re-testing each step's own
internals.

Confirms: a video with real speech produces a usable FootageReelResult
(new Video Studio project created, transcript/analysis/highlights/plan
all saved to VIDEO_STUDIO_DB_FILE), a video with no speech stops early
with insufficient_data=True and a clear message (never calling the LLM
at all), an unsupported file extension returns a plain error string
before any Video Studio project is created, and export_footage_reel()
writes a new file under the linked project's own exports_dir without
ever overwriting a previous export."""

from __future__ import annotations

import shutil
import subprocess
from typing import Any
from unittest.mock import MagicMock

import pytest

from jarvis.reel_generator import footage
from jarvis.video_studio import db as video_db
from jarvis.video_studio import storage as video_storage
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available
from jarvis.video_studio.transcribe import model_is_downloaded

_HAS_POWERSHELL = shutil.which("powershell") is not None

pytestmark = pytest.mark.skipif(
    not (ffmpeg_available() and model_is_downloaded()),
    reason="ffmpeg not on PATH or the whisper.cpp model hasn't been downloaded yet",
)


@pytest.fixture(autouse=True)
def _isolated_video_studio(tmp_path, monkeypatch):
    db_file = tmp_path / "video_studio.db"
    projects_dir = tmp_path / "video_projects"
    monkeypatch.setattr(video_db, "VIDEO_STUDIO_DB_FILE", db_file)
    monkeypatch.setattr(video_storage, "VIDEO_STUDIO_PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(footage.video_db, "VIDEO_STUDIO_DB_FILE", db_file)
    monkeypatch.setattr(footage.video_storage, "VIDEO_STUDIO_PROJECTS_DIR", projects_dir)


@pytest.fixture(scope="module")
def speech_video(tmp_path_factory):
    if not _HAS_POWERSHELL:
        pytest.skip("PowerShell (for SAPI text-to-speech) not available")

    out_dir = tmp_path_factory.mktemp("reel_generator_footage")
    wav_path = out_dir / "speech.wav"
    video_path = out_dir / "speech.mp4"

    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$synth.SetOutputToWaveFile('{wav_path}'); "
        "$synth.Speak('Here are three ways morning yoga can improve your whole day. "
        "First, it boosts your energy. Second, it calms your mind. Third, it improves flexibility.'); "
        "$synth.Dispose()"
    )
    result = subprocess.run(
        ["powershell", "-NonInteractive", "-Command", script],
        capture_output=True, timeout=30,
    )
    if result.returncode != 0 or not wav_path.is_file():
        pytest.skip("SAPI text-to-speech unavailable on this machine")

    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=1080x1920:d=12",
            "-i", str(wav_path), "-c:v", "libx264", "-c:a", "aac", "-shortest", str(video_path),
        ],
        capture_output=True, timeout=60, check=True,
    )
    return video_path


@pytest.fixture(scope="module")
def silent_video(tmp_path_factory):
    out_dir = tmp_path_factory.mktemp("reel_generator_footage_silent")
    video_path = out_dir / "silent.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=1080x1920:d=5",
            "-c:v", "libx264", "-t", "5", str(video_path),
        ],
        capture_output=True, timeout=30, check=True,
    )
    return video_path


def _fake_highlight_result(candidates_text: str = "First, it boosts your energy."):
    from jarvis.video_studio.highlights import HighlightCandidate, HighlightResult

    return HighlightResult(
        candidates=[
            HighlightCandidate(
                start_seconds=0.0, end_seconds=4.0, transcript_text=candidates_text,
                reason="Clear opening statement.", suggested_hook="3 yoga benefits", confidence="high",
            ),
        ],
        insufficient_data=False, message=None,
    )


def _fake_edit_plan(**overrides: Any):
    from jarvis.video_studio.reel import PlannedClip, ReelEditPlan

    defaults: dict[str, Any] = dict(
        clips=[PlannedClip(0.0, 4.0, "First, it boosts your energy.", "Boosts energy")],
        hook="3 yoga benefits", cta="Save this", target_duration_seconds=15, style="Educational",
        pacing="Normal", total_duration_seconds=4.0, silence_removal_suggested=False,
        zoom_crop_suggested=False, insufficient_data=False, message=None,
    )
    defaults.update(overrides)
    return ReelEditPlan(**defaults)


def test_footage_with_speech_produces_usable_plan(speech_video, monkeypatch):
    llm = MagicMock()
    highlight_result = _fake_highlight_result()
    plan = _fake_edit_plan()
    with monkeypatch.context() as m:
        m.setattr(footage.video_highlights, "find_highlights", MagicMock(return_value=highlight_result))
        m.setattr(footage.video_reel, "generate_reel_edit", MagicMock(return_value=plan))
        result = footage.create_reel_from_footage(
            llm, speech_video, target_duration_seconds=15, style="Educational", pacing="Normal",
        )

    assert isinstance(result, footage.FootageReelResult)
    assert result.insufficient_data is False
    assert not result.transcription.error
    assert len(result.transcription.segments) > 0
    assert result.highlights.candidates
    assert result.plan.clips

    saved = video_db.get_project(result.video_project_id)
    assert saved is not None
    assert saved.transcript_data is not None
    assert saved.analysis_data is not None
    assert saved.highlights_data is not None
    assert saved.reel_plan_data is not None


def test_footage_source_is_never_modified(speech_video, monkeypatch):
    original_bytes = speech_video.read_bytes()
    llm = MagicMock()
    with monkeypatch.context() as m:
        m.setattr(footage.video_highlights, "find_highlights", MagicMock(return_value=_fake_highlight_result()))
        m.setattr(footage.video_reel, "generate_reel_edit", MagicMock(return_value=_fake_edit_plan()))
        footage.create_reel_from_footage(llm, speech_video, target_duration_seconds=15, style="Educational", pacing="Normal")
    assert speech_video.read_bytes() == original_bytes


def test_footage_with_no_speech_stops_before_llm(silent_video):
    llm = MagicMock()
    result = footage.create_reel_from_footage(
        llm, silent_video, target_duration_seconds=15, style="Educational", pacing="Normal",
    )
    assert isinstance(result, footage.FootageReelResult)
    assert result.insufficient_data is True
    assert result.message is not None
    llm.send.assert_not_called()


def test_unsupported_file_returns_error_string_before_any_project(tmp_path):
    llm = MagicMock()
    bad_file = tmp_path / "not_a_video.txt"
    bad_file.write_text("hello")
    result = footage.create_reel_from_footage(llm, bad_file, target_duration_seconds=15, style="Educational", pacing="Normal")
    assert isinstance(result, str)
    assert "Unsupported" in result or "unsupported" in result
    assert video_db.list_projects() == []


def test_export_footage_reel_writes_new_file(speech_video, monkeypatch, tmp_path):
    llm = MagicMock()
    with monkeypatch.context() as m:
        m.setattr(footage.video_highlights, "find_highlights", MagicMock(return_value=_fake_highlight_result()))
        m.setattr(footage.video_reel, "generate_reel_edit", MagicMock(return_value=_fake_edit_plan()))
        result = footage.create_reel_from_footage(llm, speech_video, target_duration_seconds=15, style="Educational", pacing="Normal")
    assert isinstance(result, footage.FootageReelResult)

    export_result = footage.export_footage_reel(
        result.video_project_id, speech_video.name, result.plan, export_format="instagram_reel",
    )
    assert not isinstance(export_result, str)
    assert export_result.output_path.is_file()
    assert export_result.width == 1080
    assert export_result.height == 1920


def test_export_footage_reel_never_overwrites_previous_export(speech_video, monkeypatch):
    llm = MagicMock()
    with monkeypatch.context() as m:
        m.setattr(footage.video_highlights, "find_highlights", MagicMock(return_value=_fake_highlight_result()))
        m.setattr(footage.video_reel, "generate_reel_edit", MagicMock(return_value=_fake_edit_plan()))
        result = footage.create_reel_from_footage(llm, speech_video, target_duration_seconds=15, style="Educational", pacing="Normal")
    assert isinstance(result, footage.FootageReelResult)

    export_1 = footage.export_footage_reel(result.video_project_id, speech_video.name, result.plan)
    export_2 = footage.export_footage_reel(result.video_project_id, speech_video.name, result.plan)
    assert not isinstance(export_1, str)
    assert not isinstance(export_2, str)
    assert export_1.output_path != export_2.output_path
    assert export_1.output_path.is_file()
    assert export_2.output_path.is_file()


def test_export_footage_reel_unknown_format_returns_error():
    result = footage.export_footage_reel("some-id", "video.mp4", _fake_edit_plan(), export_format="not_a_real_format")
    assert isinstance(result, str)
    assert "Unknown" in result
