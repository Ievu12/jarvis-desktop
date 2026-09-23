"""Tests for jarvis.voice.text_to_speech.speak: text -> spoken audio,
three-tier fallback (Lithuanian SAPI voice -> Azure Neural TTS -> default
SAPI voice + explicit notice). pyttsx3, Azure's HTTP endpoints
(urllib.request.urlopen), and winsound are all mocked throughout - no
real speaker/audio output, no real SAPI engine access, no real network
call to Azure. Confirms: each tier is tried in order, a working earlier
tier means a later one is never attempted, Azure is only attempted when
both AZURE_SPEECH_KEY and AZURE_SPEECH_REGION are set, every network/
engine failure degrades to the next tier rather than raising, and the
final fallback always carries a clear notice explaining Lithuanian isn't
available."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis.voice.text_to_speech import SpeakResult, speak


def _voice(name: str, voice_id: str):
    voice = MagicMock()
    voice.name = name
    voice.id = voice_id
    return voice


def _mock_pyttsx3_with_voices(voices):
    mock_engine = MagicMock()
    mock_engine.getProperty.return_value = voices
    mock_pyttsx3 = MagicMock()
    mock_pyttsx3.init.return_value = mock_engine
    return mock_pyttsx3, mock_engine


_LT_VOICE = _voice("Microsoft Lithuanian - Lithuania", "id-lt-lt")


# --- blank text: no-op success, never touches any tier -------------------------------


def test_speak_empty_string_is_a_noop_success():
    result = speak("")
    assert result == SpeakResult(ok=True, notice=None)


def test_speak_whitespace_only_is_a_noop_success():
    assert speak("   \n  ").ok is True


def test_speak_noop_never_imports_pyttsx3():
    with patch.dict("sys.modules", {"pyttsx3": None}):
        assert speak("").ok is True


# --- tier 1: local Lithuanian SAPI voice, when installed ------------------------------


def test_speak_uses_lithuanian_sapi_voice_when_installed():
    mock_pyttsx3, mock_engine = _mock_pyttsx3_with_voices([_LT_VOICE])
    with patch.dict("sys.modules", {"pyttsx3": mock_pyttsx3}):
        result = speak("labas")

    assert result == SpeakResult(ok=True, notice=None)
    mock_engine.setProperty.assert_any_call("voice", "id-lt-lt")
    mock_engine.say.assert_called_with("labas")


def test_speak_never_calls_azure_when_lithuanian_sapi_voice_available():
    mock_pyttsx3, _ = _mock_pyttsx3_with_voices([_LT_VOICE])
    with patch.dict("sys.modules", {"pyttsx3": mock_pyttsx3}):
        with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_KEY", "fake-key"):
            with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_REGION", "westeurope"):
                with patch("jarvis.voice.text_to_speech._speak_via_azure_neural_tts") as mock_azure:
                    speak("labas")
    mock_azure.assert_not_called()


# --- tier 2: Azure Neural TTS, only when credentials configured and no local voice ---


def test_speak_falls_back_to_azure_when_no_lithuanian_sapi_voice():
    mock_pyttsx3, _ = _mock_pyttsx3_with_voices([])  # no Lithuanian voice installed
    with patch.dict("sys.modules", {"pyttsx3": mock_pyttsx3}):
        with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_KEY", "fake-key"):
            with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_REGION", "westeurope"):
                with patch(
                    "jarvis.voice.text_to_speech._speak_via_azure_neural_tts", return_value=True
                ) as mock_azure:
                    result = speak("labas")

    assert result == SpeakResult(ok=True, notice=None)
    mock_azure.assert_called_once_with("labas", key="fake-key", region="westeurope")


def test_speak_never_attempts_azure_when_credentials_not_configured():
    mock_pyttsx3, _ = _mock_pyttsx3_with_voices([])
    with patch.dict("sys.modules", {"pyttsx3": mock_pyttsx3}):
        with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_KEY", None):
            with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_REGION", None):
                with patch("jarvis.voice.text_to_speech._speak_via_azure_neural_tts") as mock_azure:
                    speak("labas")
    mock_azure.assert_not_called()


def test_speak_never_attempts_azure_when_only_key_is_set():
    mock_pyttsx3, _ = _mock_pyttsx3_with_voices([])
    with patch.dict("sys.modules", {"pyttsx3": mock_pyttsx3}):
        with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_KEY", "fake-key"):
            with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_REGION", None):
                with patch("jarvis.voice.text_to_speech._speak_via_azure_neural_tts") as mock_azure:
                    speak("labas")
    mock_azure.assert_not_called()


def test_speak_falls_back_to_tier_3_when_azure_request_fails():
    mock_pyttsx3, mock_engine = _mock_pyttsx3_with_voices([])
    with patch.dict("sys.modules", {"pyttsx3": mock_pyttsx3}):
        with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_KEY", "fake-key"):
            with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_REGION", "westeurope"):
                with patch(
                    "jarvis.voice.text_to_speech._speak_via_azure_neural_tts", return_value=False
                ):
                    result = speak("labas")

    assert result.ok is True
    assert result.notice is not None
    mock_engine.say.assert_called_with("labas")  # fell through to default SAPI voice


# --- tier 3: default (non-Lithuanian) SAPI voice + explicit notice ------------------


def test_speak_falls_back_to_default_voice_with_notice_when_nothing_lithuanian_available():
    mock_pyttsx3, mock_engine = _mock_pyttsx3_with_voices([_voice("Microsoft David", "id-david")])
    with patch.dict("sys.modules", {"pyttsx3": mock_pyttsx3}):
        with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_KEY", None):
            with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_REGION", None):
                result = speak("labas")

    assert result.ok is True
    assert result.notice is not None
    assert "lietuvi" in result.notice.lower()
    mock_engine.say.assert_called_with("labas")


def test_notice_mentions_azure_env_vars_as_an_option():
    mock_pyttsx3, _ = _mock_pyttsx3_with_voices([])
    with patch.dict("sys.modules", {"pyttsx3": mock_pyttsx3}):
        with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_KEY", None):
            with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_REGION", None):
                result = speak("labas")
    assert "AZURE_SPEECH_KEY" in result.notice
    assert "AZURE_SPEECH_REGION" in result.notice


def test_speak_returns_not_ok_when_every_tier_fails():
    mock_pyttsx3 = MagicMock()
    mock_pyttsx3.init.side_effect = RuntimeError("no SAPI engine found")
    with patch.dict("sys.modules", {"pyttsx3": mock_pyttsx3}):
        with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_KEY", None):
            with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_REGION", None):
                result = speak("labas")
    assert result.ok is False
    assert result.notice is not None


def test_speak_returns_not_ok_when_missing_pyttsx3_dependency_entirely():
    with patch.dict("sys.modules", {"pyttsx3": None}):
        with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_KEY", None):
            with patch("jarvis.voice.text_to_speech.AZURE_SPEECH_REGION", None):
                result = speak("labas")
    assert result.ok is False


# --- _speak_via_azure_neural_tts: HTTP calls mocked, never touches the real network --


def test_azure_tts_fetches_token_then_posts_ssml_and_plays_audio():
    from jarvis.voice.text_to_speech import _speak_via_azure_neural_tts

    mock_token_response = MagicMock()
    mock_token_response.__enter__.return_value.read.return_value = b"fake-token"
    mock_audio_response = MagicMock()
    mock_audio_response.__enter__.return_value.read.return_value = b"RIFF....WAVEfmt "

    with patch("urllib.request.urlopen", side_effect=[mock_token_response, mock_audio_response]):
        with patch("jarvis.voice.text_to_speech._play_wav_bytes", return_value=True) as mock_play:
            result = _speak_via_azure_neural_tts("labas", key="fake-key", region="westeurope")

    assert result is True
    mock_play.assert_called_once_with(b"RIFF....WAVEfmt ")


def test_azure_tts_returns_false_when_token_fetch_fails():
    import urllib.error

    from jarvis.voice.text_to_speech import _speak_via_azure_neural_tts

    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no network")):
        result = _speak_via_azure_neural_tts("labas", key="fake-key", region="westeurope")
    assert result is False


def test_azure_tts_returns_false_when_synthesis_request_fails():
    import urllib.error

    from jarvis.voice.text_to_speech import _speak_via_azure_neural_tts

    mock_token_response = MagicMock()
    mock_token_response.__enter__.return_value.read.return_value = b"fake-token"

    with patch(
        "urllib.request.urlopen",
        side_effect=[mock_token_response, urllib.error.URLError("synthesis failed")],
    ):
        result = _speak_via_azure_neural_tts("labas", key="fake-key", region="westeurope")
    assert result is False


def test_azure_tts_uses_lt_lt_neural_voice_in_ssml():
    from jarvis.voice.text_to_speech import _build_azure_ssml

    ssml = _build_azure_ssml("labas")
    assert "lt-LT" in ssml
    assert "lt-LT-OnaNeural" in ssml
    assert "labas" in ssml


def test_azure_tts_ssml_escapes_special_characters():
    from jarvis.voice.text_to_speech import _build_azure_ssml

    ssml = _build_azure_ssml('<tag> & "quoted"')
    assert "<tag>" not in ssml
    assert "&lt;tag&gt;" in ssml


def test_azure_tts_never_leaks_the_key_into_url():
    mock_token_response = MagicMock()
    mock_token_response.__enter__.return_value.read.return_value = b"fake-token"
    mock_audio_response = MagicMock()
    mock_audio_response.__enter__.return_value.read.return_value = b"RIFF"

    captured_requests = []

    def _capture(request, timeout=None):
        captured_requests.append(request)
        return mock_token_response if len(captured_requests) == 1 else mock_audio_response

    from jarvis.voice.text_to_speech import _speak_via_azure_neural_tts

    with patch("urllib.request.urlopen", side_effect=_capture):
        with patch("jarvis.voice.text_to_speech._play_wav_bytes", return_value=True):
            _speak_via_azure_neural_tts("labas", key="super-secret-key", region="westeurope")

    for request in captured_requests:
        assert "super-secret-key" not in request.full_url


# --- _play_wav_bytes: validates WAV, uses stdlib winsound, never raises -------------


def test_play_wav_bytes_rejects_non_wav_data_without_raising():
    from jarvis.voice.text_to_speech import _play_wav_bytes

    assert _play_wav_bytes(b"not a wav file at all") is False


def test_play_wav_bytes_plays_valid_wav_via_winsound():
    import io
    import wave

    from jarvis.voice.text_to_speech import _play_wav_bytes

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(b"\x00\x00" * 100)
    wav_bytes = buffer.getvalue()

    mock_winsound = MagicMock()
    with patch.dict("sys.modules", {"winsound": mock_winsound}):
        result = _play_wav_bytes(wav_bytes)

    assert result is True
    mock_winsound.PlaySound.assert_called_once()


def test_play_wav_bytes_returns_false_when_winsound_raises():
    import io
    import wave

    from jarvis.voice.text_to_speech import _play_wav_bytes

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(24000)
        wav_file.writeframes(b"\x00\x00" * 100)
    wav_bytes = buffer.getvalue()

    mock_winsound = MagicMock()
    mock_winsound.PlaySound.side_effect = RuntimeError("playback device busy")
    with patch.dict("sys.modules", {"winsound": mock_winsound}):
        result = _play_wav_bytes(wav_bytes)

    assert result is False
