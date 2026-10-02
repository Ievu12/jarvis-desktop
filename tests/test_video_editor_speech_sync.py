"""Tests for jarvis.video_editor.speech_sync: pure, I/O-free text
matching against real WordTiming data - no ffmpeg needed for most
tests (plain WordTiming objects are hand-built here); one real,
ffmpeg/whisper-dependent test at the bottom proves the whole chain
(real speech -> real transcription -> real phrase match) end to end."""

from __future__ import annotations

import subprocess

import pytest

from jarvis.video_editor.captions import WordTiming, generate_word_timings
from jarvis.video_editor.speech_sync import (
    SpeechMatch,
    SpeechTrigger,
    find_phrase_matches,
    resolve_trigger_timing,
)
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available
from jarvis.video_studio.transcribe import model_is_downloaded

_WORDS = [
    WordTiming(start_seconds=0.0, end_seconds=0.3, text="This"),
    WordTiming(start_seconds=0.3, end_seconds=0.5, text="is"),
    WordTiming(start_seconds=0.5, end_seconds=0.6, text="a"),
    WordTiming(start_seconds=0.6, end_seconds=1.0, text="great"),
    WordTiming(start_seconds=1.0, end_seconds=1.5, text="offer"),
    WordTiming(start_seconds=1.5, end_seconds=2.0, text="today."),
]


def test_find_phrase_matches_finds_a_single_word():
    matches = find_phrase_matches(_WORDS, "great")
    assert matches == [SpeechMatch(start_seconds=0.6, end_seconds=1.0, matched_text="great")]


def test_find_phrase_matches_finds_a_multi_word_phrase():
    matches = find_phrase_matches(_WORDS, "great offer")
    assert matches == [SpeechMatch(start_seconds=0.6, end_seconds=1.5, matched_text="great offer")]


def test_find_phrase_matches_is_case_insensitive():
    matches = find_phrase_matches(_WORDS, "GREAT OFFER")
    assert len(matches) == 1


def test_find_phrase_matches_tolerates_punctuation_in_transcription():
    matches = find_phrase_matches(_WORDS, "today")
    assert matches == [SpeechMatch(start_seconds=1.5, end_seconds=2.0, matched_text="today.")]


def test_find_phrase_matches_returns_empty_for_no_match():
    assert find_phrase_matches(_WORDS, "nonexistent phrase") == []


def test_find_phrase_matches_returns_empty_for_empty_phrase():
    assert find_phrase_matches(_WORDS, "   ") == []


def test_find_phrase_matches_returns_empty_for_empty_words():
    assert find_phrase_matches([], "great offer") == []


def test_find_phrase_matches_preserves_lithuanian_diacritics():
    words = [
        WordTiming(start_seconds=0.0, end_seconds=0.5, text="ačiū"),
        WordTiming(start_seconds=0.5, end_seconds=1.0, text="aciu"),
    ]
    # "ačiū" and "aciu" must NOT match each other - diacritics are real,
    # distinct Lithuanian letters, never stripped for comparison.
    matches = find_phrase_matches(words, "aciu")
    assert matches == [SpeechMatch(start_seconds=0.5, end_seconds=1.0, matched_text="aciu")]
    matches_with_diacritic = find_phrase_matches(words, "ačiū")
    assert matches_with_diacritic == [SpeechMatch(start_seconds=0.0, end_seconds=0.5, matched_text="ačiū")]


def test_find_phrase_matches_finds_multiple_occurrences():
    words = [
        WordTiming(start_seconds=0.0, end_seconds=0.3, text="go"),
        WordTiming(start_seconds=0.3, end_seconds=0.6, text="go"),
        WordTiming(start_seconds=0.6, end_seconds=0.9, text="stop"),
        WordTiming(start_seconds=0.9, end_seconds=1.2, text="go"),
    ]
    matches = find_phrase_matches(words, "go")
    assert len(matches) == 3


def test_speech_trigger_validate_rejects_empty_phrase():
    trigger = SpeechTrigger(phrase="   ")
    assert any("no phrase" in p for p in trigger.validate())


def test_speech_trigger_validate_rejects_negative_lead_in():
    trigger = SpeechTrigger(phrase="hi", lead_in_seconds=-1.0)
    assert any("lead-in" in p for p in trigger.validate())


def test_speech_trigger_validate_rejects_non_positive_hold():
    trigger = SpeechTrigger(phrase="hi", hold_seconds=0.0)
    assert any("hold duration" in p for p in trigger.validate())


def test_resolve_trigger_timing_applies_lead_in_and_hold():
    trigger = SpeechTrigger(phrase="great offer", lead_in_seconds=0.2, hold_seconds=3.0)
    resolved = resolve_trigger_timing(trigger, _WORDS)
    assert len(resolved) == 1
    assert resolved[0].start_seconds == pytest.approx(0.4)
    assert resolved[0].end_seconds == pytest.approx(3.4)
    assert resolved[0].matched_text == "great offer"


def test_resolve_trigger_timing_clamps_lead_in_at_zero():
    trigger = SpeechTrigger(phrase="This", lead_in_seconds=5.0, hold_seconds=1.0)
    resolved = resolve_trigger_timing(trigger, _WORDS)
    assert resolved[0].start_seconds == 0.0


def test_resolve_trigger_timing_returns_empty_for_invalid_trigger():
    trigger = SpeechTrigger(phrase="")
    assert resolve_trigger_timing(trigger, _WORDS) == []


def test_resolve_trigger_timing_returns_empty_for_no_match():
    trigger = SpeechTrigger(phrase="nonexistent")
    assert resolve_trigger_timing(trigger, _WORDS) == []


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
@pytest.mark.skipif(not model_is_downloaded(), reason="whisper model not downloaded")
def test_real_transcription_and_phrase_match_end_to_end(tmp_path):
    ps_script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{tmp_path / 'speech.wav'}'); "
        "$s.Speak('This is a great offer today'); "
        "$s.Dispose()"
    )
    try:
        subprocess.run(["powershell", "-Command", ps_script], capture_output=True, timeout=30, check=True)
    except Exception:
        pytest.skip("Windows SAPI speech synthesis not available")
    speech_wav = tmp_path / "speech.wav"
    if not speech_wav.is_file():
        pytest.skip("Windows SAPI speech synthesis not available")

    clip = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=3", "-i", str(speech_wav),
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(clip)],
        capture_output=True, timeout=30, check=True,
    )

    words = generate_word_timings(clip, language="en")
    matches = find_phrase_matches(words, "great offer")
    assert len(matches) == 1
    assert matches[0].end_seconds > matches[0].start_seconds
    assert "offer" in matches[0].matched_text.lower()
