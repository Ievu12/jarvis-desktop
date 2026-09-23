"""Tests for jarvis.voice.speech_to_text.listen_once: microphone ->
Lithuanian text via SpeechRecognition's Google Web Speech recognizer.
The speech_recognition module itself is mocked throughout - no real
microphone access, no real network call to Google's recognition
service. Confirms: success returns ListenResult.success with the
transcribed text, every expected failure mode (timeout, unintelligible
audio, no network, no microphone) returns ListenResult.failure with a
plain-language reason rather than raising, and the language parameter
passed to the recognizer is always 'lt-LT'."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

speech_recognition = pytest.importorskip("speech_recognition")

from jarvis.voice.speech_to_text import listen_once


def _patched_recognizer(**overrides):
    """Builds a fully mocked speech_recognition module tree: Recognizer
    instance, Microphone context manager, and whatever exception
    behavior the test needs - patched onto
    jarvis.voice.speech_to_text's own `import speech_recognition as sr`
    (a local import inside listen_once(), so we patch the module in
    sys.modules that import resolves to)."""
    mock_sr = MagicMock()
    mock_sr.WaitTimeoutError = speech_recognition.WaitTimeoutError
    mock_sr.UnknownValueError = speech_recognition.UnknownValueError
    mock_sr.RequestError = speech_recognition.RequestError

    mock_recognizer = MagicMock()
    mock_sr.Recognizer.return_value = mock_recognizer

    mock_microphone_cm = MagicMock()
    mock_sr.Microphone.return_value = mock_microphone_cm

    for key, value in overrides.items():
        setattr(mock_recognizer, key, value)

    return mock_sr, mock_recognizer


# --- success path ----------------------------------------------------------------


def test_listen_once_success_returns_transcribed_text():
    mock_sr, mock_recognizer = _patched_recognizer()
    mock_recognizer.recognize_google.return_value = "labas jarvis"

    with patch.dict("sys.modules", {"speech_recognition": mock_sr}):
        result = listen_once()

    assert result.ok is True
    assert result.text == "labas jarvis"
    assert result.error is None


def test_listen_once_passes_lithuanian_language_code():
    mock_sr, mock_recognizer = _patched_recognizer()
    mock_recognizer.recognize_google.return_value = "tekstas"

    with patch.dict("sys.modules", {"speech_recognition": mock_sr}):
        listen_once()

    _, kwargs = mock_recognizer.recognize_google.call_args
    assert kwargs.get("language") == "lt-LT"


def test_listen_once_strips_whitespace_from_transcribed_text():
    mock_sr, mock_recognizer = _patched_recognizer()
    mock_recognizer.recognize_google.return_value = "  labas  "

    with patch.dict("sys.modules", {"speech_recognition": mock_sr}):
        result = listen_once()

    assert result.text == "labas"


# --- failure paths: never raise, always ListenResult.failure ------------------------


def test_listen_once_timeout_returns_failure_not_exception():
    mock_sr, mock_recognizer = _patched_recognizer()
    mock_recognizer.listen.side_effect = speech_recognition.WaitTimeoutError()

    with patch.dict("sys.modules", {"speech_recognition": mock_sr}):
        result = listen_once()

    assert result.ok is False
    assert result.text is None
    assert "nieko neišgirdau" in result.error.lower() or "tyla" in result.error.lower()


def test_listen_once_unintelligible_audio_returns_failure():
    mock_sr, mock_recognizer = _patched_recognizer()
    mock_recognizer.recognize_google.side_effect = speech_recognition.UnknownValueError()

    with patch.dict("sys.modules", {"speech_recognition": mock_sr}):
        result = listen_once()

    assert result.ok is False
    assert result.error is not None


def test_listen_once_request_error_returns_failure_not_exception():
    mock_sr, mock_recognizer = _patched_recognizer()
    mock_recognizer.recognize_google.side_effect = speech_recognition.RequestError("no network")

    with patch.dict("sys.modules", {"speech_recognition": mock_sr}):
        result = listen_once()

    assert result.ok is False
    assert "nepasiekiama" in result.error.lower() or "ryšį" in result.error.lower()


def test_listen_once_no_microphone_returns_failure_not_exception():
    mock_sr, mock_recognizer = _patched_recognizer()
    mock_sr.Microphone.side_effect = OSError("no default input device")

    with patch.dict("sys.modules", {"speech_recognition": mock_sr}):
        result = listen_once()

    assert result.ok is False
    assert "mikrofon" in result.error.lower()


def test_listen_once_missing_dependency_returns_failure_not_import_error():
    with patch.dict("sys.modules", {"speech_recognition": None}):
        result = listen_once()

    assert result.ok is False
    assert result.error is not None


# --- ListenResult invariants ---------------------------------------------------------


def test_listen_result_success_sets_ok_true_and_error_none():
    from jarvis.voice.speech_to_text import ListenResult

    result = ListenResult.success("text")
    assert result.ok is True
    assert result.text == "text"
    assert result.error is None


def test_listen_result_failure_sets_ok_false_and_text_none():
    from jarvis.voice.speech_to_text import ListenResult

    result = ListenResult.failure("reason")
    assert result.ok is False
    assert result.text is None
    assert result.error == "reason"
