"""Tests for jarvis.reel_generator.voiceover: real narration-text
assembly (build_narration_text()) and voiceover generation
(generate_voiceover()), which delegates the actual synthesis to
jarvis.voice.text_to_speech.synthesize_to_file() - mocked here via the
same urllib.request.urlopen mocking pattern as
tests/test_text_to_speech.py (no real network call, no real Azure
credentials needed)."""

from __future__ import annotations

import io
import wave
from unittest.mock import MagicMock, patch

from jarvis.reel_generator.storyboard import Scene
from jarvis.reel_generator.voiceover import (
    DEFAULT_VOICE_BY_LANGUAGE,
    build_narration_text,
    generate_voiceover,
)


def _scene(number: int, voice_text: str, **overrides) -> Scene:
    defaults = dict(
        number=number, start_seconds=0.0, end_seconds=5.0, segment_kind="value",
        voice_text=voice_text, on_screen_text="TEXT", visual_description="a room",
    )
    defaults.update(overrides)
    return Scene(**defaults)


def _make_valid_wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(b"\x00\x00" * 100)
    return buffer.getvalue()


# --- build_narration_text() -------------------------------------------------------------


def test_build_narration_text_joins_scenes_in_order():
    scenes = (
        _scene(1, "Three ways to start your day"),
        _scene(2, "Stretch, breathe, move"),
        _scene(3, "Save this Reel"),
    )
    text = build_narration_text(scenes)
    assert text == "Three ways to start your day. Stretch, breathe, move. Save this Reel."


def test_build_narration_text_does_not_double_punctuate():
    scenes = (_scene(1, "Already ends with a period."), _scene(2, "Second line."))
    text = build_narration_text(scenes)
    assert text == "Already ends with a period. Second line."


def test_build_narration_text_skips_blank_scenes():
    scenes = (_scene(1, "First line"), _scene(2, "   "), _scene(3, "Third line"))
    text = build_narration_text(scenes)
    assert text == "First line. Third line."


def test_build_narration_text_empty_scenes_returns_empty_string():
    assert build_narration_text(()) == ""


# --- generate_voiceover() ----------------------------------------------------------------


def test_generate_voiceover_no_scenes_returns_clear_error_without_network_call(tmp_path):
    with patch("urllib.request.urlopen") as mock_urlopen:
        result = generate_voiceover((), output_path=tmp_path / "voice.wav")
    mock_urlopen.assert_not_called()
    assert result.ok is False
    assert result.output_path is None
    assert "no narration text" in result.error


def test_generate_voiceover_not_configured_returns_clear_error(tmp_path):
    scenes = (_scene(1, "Hello there."),)
    with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_KEY", None):
        with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_REGION", None):
            with patch("urllib.request.urlopen") as mock_urlopen:
                result = generate_voiceover(scenes, output_path=tmp_path / "voice.wav")
    mock_urlopen.assert_not_called()
    assert result.ok is False
    assert result.output_path is None
    assert "AZURE_SPEECH_KEY" in result.error
    assert "AZURE_SPEECH_REGION" in result.error
    # The narration text is still reported even on failure, so a caller/GUI can show what
    # WOULD have been spoken once voiceover generation is configured.
    assert result.narration_text == "Hello there."


def test_generate_voiceover_success_writes_real_wav_file(tmp_path):
    scenes = (
        _scene(1, "Three ways to start your day"),
        _scene(2, "Stretch, breathe, move"),
    )
    wav_bytes = _make_valid_wav_bytes()
    mock_token_response = MagicMock()
    mock_token_response.__enter__.return_value.read.return_value = b"fake-token"
    mock_audio_response = MagicMock()
    mock_audio_response.__enter__.return_value.read.return_value = wav_bytes

    output_path = tmp_path / "voiceover" / "narration.wav"
    with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_KEY", "fake-key"):
        with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_REGION", "westeurope"):
            with patch("urllib.request.urlopen", side_effect=[mock_token_response, mock_audio_response]):
                result = generate_voiceover(scenes, output_path=output_path, language="en")

    assert result.ok is True
    assert result.error is None
    assert result.output_path == output_path
    assert output_path.is_file()
    assert output_path.read_bytes() == wav_bytes
    assert result.narration_text == "Three ways to start your day. Stretch, breathe, move."


def test_generate_voiceover_azure_failure_returns_clear_error_no_file_written(tmp_path):
    import urllib.error

    scenes = (_scene(1, "Hello there."),)
    output_path = tmp_path / "voice.wav"
    with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_KEY", "fake-key"):
        with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_REGION", "westeurope"):
            with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no network")):
                result = generate_voiceover(scenes, output_path=output_path)

    assert result.ok is False
    assert result.output_path is None
    assert result.error is not None
    assert not output_path.exists()


def test_generate_voiceover_uses_default_voice_for_language(tmp_path):
    scenes = (_scene(1, "Labas rytas."),)
    wav_bytes = _make_valid_wav_bytes()
    mock_token_response = MagicMock()
    mock_token_response.__enter__.return_value.read.return_value = b"fake-token"
    mock_audio_response = MagicMock()
    mock_audio_response.__enter__.return_value.read.return_value = wav_bytes

    captured_requests = []

    def _capture(request, timeout=None):
        captured_requests.append(request)
        return mock_token_response if len(captured_requests) == 1 else mock_audio_response

    with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_KEY", "fake-key"):
        with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_REGION", "westeurope"):
            with patch("urllib.request.urlopen", side_effect=_capture):
                generate_voiceover(scenes, output_path=tmp_path / "voice.wav", language="lt")

    ssml_request = captured_requests[1]
    ssml_body = ssml_request.data.decode("utf-8")
    assert DEFAULT_VOICE_BY_LANGUAGE["lt"] in ssml_body


def test_generate_voiceover_custom_voice_name_overrides_default(tmp_path):
    scenes = (_scene(1, "Hello there."),)
    wav_bytes = _make_valid_wav_bytes()
    mock_token_response = MagicMock()
    mock_token_response.__enter__.return_value.read.return_value = b"fake-token"
    mock_audio_response = MagicMock()
    mock_audio_response.__enter__.return_value.read.return_value = wav_bytes

    captured_requests = []

    def _capture(request, timeout=None):
        captured_requests.append(request)
        return mock_token_response if len(captured_requests) == 1 else mock_audio_response

    with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_KEY", "fake-key"):
        with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_REGION", "westeurope"):
            with patch("urllib.request.urlopen", side_effect=_capture):
                generate_voiceover(
                    scenes, output_path=tmp_path / "voice.wav", language="en", voice_name="en-GB-SoniaNeural",
                )

    ssml_body = captured_requests[1].data.decode("utf-8")
    assert "en-GB-SoniaNeural" in ssml_body


def test_generate_voiceover_custom_speed_sets_prosody_rate(tmp_path):
    scenes = (_scene(1, "Hello there."),)
    wav_bytes = _make_valid_wav_bytes()
    mock_token_response = MagicMock()
    mock_token_response.__enter__.return_value.read.return_value = b"fake-token"
    mock_audio_response = MagicMock()
    mock_audio_response.__enter__.return_value.read.return_value = wav_bytes

    captured_requests = []

    def _capture(request, timeout=None):
        captured_requests.append(request)
        return mock_token_response if len(captured_requests) == 1 else mock_audio_response

    with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_KEY", "fake-key"):
        with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_REGION", "westeurope"):
            with patch("urllib.request.urlopen", side_effect=_capture):
                generate_voiceover(scenes, output_path=tmp_path / "voice.wav", speed="fast")

    ssml_body = captured_requests[1].data.decode("utf-8")
    assert 'rate="fast"' in ssml_body
