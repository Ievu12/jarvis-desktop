"""Text -> spoken audio. Three-tier fallback, tried in order:

1. A Lithuanian voice installed locally via Windows SAPI (pyttsx3) -
   fully offline, no network call, no API key. As of this writing,
   Microsoft ships no Lithuanian SAPI/OneCore voice for any Windows
   version (confirmed by inspecting System.Speech.Synthesis
   .SpeechSynthesizer.GetInstalledVoices() and the SAPI registry keys
   directly - installing the Lithuanian Windows display-language pack
   does NOT install a matching TTS voice, since the language pack and
   the TTS voice are separate Microsoft components). This tier exists
   so that IF a Lithuanian SAPI voice ever becomes available (a future
   Windows update, or a third-party SAPI voice pack installed by the
   person), it is used automatically with no code change - see
   _find_lithuanian_voice_id().
2. Azure Cognitive Services Speech (Neural TTS, voice lt-LT-OnaNeural) -
   used only if AZURE_SPEECH_KEY/AZURE_SPEECH_REGION are set (see
   jarvis.config). Natural-sounding Lithuanian, but requires network
   access and a Microsoft Azure account/API key; the Windows OS display
   language is never touched by this - it is a plain HTTPS REST call.
3. Neither available: speak() falls back to whatever default (English)
   SAPI voice Windows has, and returns a clear, explicit notice string
   the caller can show/say instead of pretending the reply was spoken
   in Lithuanian - see SpeakResult.notice.

speak() never raises for an expected failure (no TTS engine, no
network, no Azure credentials, Azure request failure) - every case is
reported via the returned SpeakResult so callers (voice_loop.py) can
tell the person clearly, the same pattern speech_to_text.listen_once()
uses.
"""

from __future__ import annotations

import io
import struct
import wave
from dataclasses import dataclass

from jarvis.config import AZURE_SPEECH_KEY, AZURE_SPEECH_REGION

# Substrings (lowercased) that identify a Lithuanian voice across the
# different naming conventions Windows/SAPI voice packs use in practice
# (e.g. "Microsoft Lithuanian ...", locale ids containing "lt-LT" or
# "lithuania"). Matched against both the voice's `name` and `id` -
# pyttsx3/SAPI does not expose a single normalized locale field reliably
# across Windows versions.
_LITHUANIAN_VOICE_MARKERS = ("lithuania", "lietuv", "lt-lt", "lt_lt")

_AZURE_NEURAL_VOICE_NAME = "lt-LT-OnaNeural"
_AZURE_TOKEN_URL_TEMPLATE = "https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
_AZURE_TTS_URL_TEMPLATE = "https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
_AZURE_AUDIO_FORMAT = "riff-24khz-16bit-mono-pcm"  # plain WAV - playable via stdlib winsound
_AZURE_REQUEST_TIMEOUT_SECONDS = 15

_NO_LITHUANIAN_VOICE_NOTICE = (
    "Lietuviško balso nėra sukonfigūruota - JARVIS kalbės anglišku balsu. "
    "Kad JARVIS kalbėtų lietuviškai, arba įdiek Lithuanian Windows Speech "
    "balsą (jei Microsoft jį pasiūlys tavo Windows versijai), arba nustatyk "
    "AZURE_SPEECH_KEY ir AZURE_SPEECH_REGION aplinkos kintamuosius (Azure "
    "Cognitive Services Speech, nemokamas F0 tarifas)."
)


@dataclass
class SpeakResult:
    """speak()'s outcome. `ok` is False only when nothing could be
    spoken at all (no TTS engine/credentials worked); `notice` is set
    whenever speech happened but NOT in Lithuanian, so a caller can
    surface that distinction instead of silently claiming Lithuanian was
    used."""

    ok: bool
    notice: str | None = None


def _find_lithuanian_voice_id(engine) -> str | None:
    try:
        voices = engine.getProperty("voices")
    except Exception:
        return None
    for voice in voices or []:
        haystack = f"{getattr(voice, 'name', '')} {getattr(voice, 'id', '')}".lower()
        if any(marker in haystack for marker in _LITHUANIAN_VOICE_MARKERS):
            return voice.id
    return None


def _speak_via_sapi(text: str, *, voice_id: str | None) -> bool:
    """Speaks via pyttsx3/Windows SAPI, optionally forcing a specific
    installed voice. Returns False (never raises) if the engine can't be
    started or say()/runAndWait() fails."""
    try:
        import pyttsx3
    except ImportError:
        return False

    try:
        engine = pyttsx3.init()
    except Exception:
        return False

    if voice_id is not None:
        try:
            engine.setProperty("voice", voice_id)
        except Exception:
            pass  # fall through and speak with whatever voice is already selected

    try:
        engine.say(text)
        engine.runAndWait()
    except Exception:
        return False
    finally:
        try:
            engine.stop()
        except Exception:
            pass

    return True


def _fetch_azure_access_token(key: str, region: str) -> str | None:
    import urllib.error
    import urllib.request

    url = _AZURE_TOKEN_URL_TEMPLATE.format(region=region)
    request = urllib.request.Request(
        url, method="POST", headers={"Ocp-Apim-Subscription-Key": key}
    )
    try:
        with urllib.request.urlopen(request, timeout=_AZURE_REQUEST_TIMEOUT_SECONDS) as response:
            return response.read().decode("utf-8")
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _build_azure_ssml(text: str) -> str:
    # Minimal, safe XML escaping - the spoken text is arbitrary JARVIS
    # output, never assumed to already be XML-safe.
    escaped = (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&apos;")
    )
    return (
        '<speak version="1.0" xml:lang="lt-LT">'
        f'<voice xml:lang="lt-LT" name="{_AZURE_NEURAL_VOICE_NAME}">{escaped}</voice>'
        "</speak>"
    )


def _play_wav_bytes(wav_bytes: bytes) -> bool:
    """Plays a WAV byte stream through the default speaker via the
    stdlib winsound module - no extra audio-playback dependency needed
    since Azure is asked for plain riff-24khz-16bit-mono-pcm output.
    Validates it's readable WAV data first (wave.open) so a malformed or
    non-WAV response from Azure fails cleanly instead of playing noise
    or raising an unhandled error out of winsound."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb"):
            pass  # just validating the container is well-formed WAV
    except (wave.Error, EOFError, struct.error):
        return False

    try:
        import winsound
    except ImportError:
        return False

    try:
        winsound.PlaySound(wav_bytes, winsound.SND_MEMORY)
    except RuntimeError:
        return False
    return True


def _speak_via_azure_neural_tts(text: str, *, key: str, region: str) -> bool:
    """Synthesizes `text` as Lithuanian speech via Azure Cognitive
    Services Speech (Neural TTS) and plays it. Returns False (never
    raises) for any network/credential/audio failure - a network call
    to a paid (F0 free-tier) cloud service is exactly the kind of
    external dependency that must degrade to the next fallback tier
    rather than crash voice mode."""
    import urllib.error
    import urllib.request

    access_token = _fetch_azure_access_token(key, region)
    if access_token is None:
        return False

    url = _AZURE_TTS_URL_TEMPLATE.format(region=region)
    body = _build_azure_ssml(text).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": _AZURE_AUDIO_FORMAT,
            "User-Agent": "jarvis-voice",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_AZURE_REQUEST_TIMEOUT_SECONDS) as response:
            audio_bytes = response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return False

    return _play_wav_bytes(audio_bytes)


def speak(text: str) -> SpeakResult:
    """Speak `text` aloud through the default speaker, preferring
    Lithuanian (see module docstring for the three-tier fallback order).
    A blank/empty text is a no-op success - there is nothing to fail at
    speaking silence."""
    if not text or not text.strip():
        return SpeakResult(ok=True)

    # Tier 1: a Lithuanian SAPI voice installed locally, if one exists.
    try:
        import pyttsx3

        probe_engine = pyttsx3.init()
        lithuanian_voice_id = _find_lithuanian_voice_id(probe_engine)
        try:
            probe_engine.stop()
        except Exception:
            pass
    except Exception:
        lithuanian_voice_id = None

    if lithuanian_voice_id is not None:
        if _speak_via_sapi(text, voice_id=lithuanian_voice_id):
            return SpeakResult(ok=True)
        # A Lithuanian voice was found but speaking still failed for some
        # other reason (engine error) - fall through to Azure/default
        # rather than giving up immediately.

    # Tier 2: Azure Neural TTS, only if credentials are configured.
    if AZURE_SPEECH_KEY and AZURE_SPEECH_REGION:
        if _speak_via_azure_neural_tts(text, key=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION):
            return SpeakResult(ok=True)
        # Azure was configured but the request failed (network, invalid
        # key/region, quota) - fall through to tier 3 rather than
        # leaving the person with no spoken reply at all.

    # Tier 3: whatever default (non-Lithuanian) SAPI voice exists.
    spoke = _speak_via_sapi(text, voice_id=None)
    if not spoke:
        return SpeakResult(ok=False, notice=_NO_LITHUANIAN_VOICE_NOTICE)
    return SpeakResult(ok=True, notice=_NO_LITHUANIAN_VOICE_NOTICE)
