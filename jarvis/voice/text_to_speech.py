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
from pathlib import Path

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


def _build_azure_ssml(text: str, *, voice_name: str = _AZURE_NEURAL_VOICE_NAME, rate: str = "medium") -> str:
    # Minimal, safe XML escaping - the spoken text is arbitrary JARVIS
    # output, never assumed to already be XML-safe. `voice_name` is
    # accepted as a parameter (defaulting to the same Lithuanian voice
    # speak() always used) so synthesize_to_file() callers can request a
    # different configured Azure Neural voice (e.g. an English voice for
    # an English-language Reel) without this function needing to know
    # about jarvis.reel_generator at all. `rate` is a plain SSML
    # <prosody rate="..."> value (Azure accepts "x-slow"/"slow"/
    # "medium"/"fast"/"x-fast", a percentage like "+10%", or a bare
    # multiplier like "1.1") - "medium" (i.e. no change from the voice's
    # own natural pace) is the same behavior as before this parameter
    # existed, so every pre-existing caller (speak()'s own playback
    # path) is completely unaffected.
    escaped = (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&quot;").replace("'", "&apos;")
    )
    lang = "-".join(voice_name.split("-")[:2]) if "-" in voice_name else "lt-LT"
    inner = f'<prosody rate="{rate}">{escaped}</prosody>' if rate != "medium" else escaped
    return (
        f'<speak version="1.0" xml:lang="{lang}">'
        f'<voice xml:lang="{lang}" name="{voice_name}">{inner}</voice>'
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


def _synthesize_azure_neural_tts_bytes(
    text: str, *, key: str, region: str, voice_name: str = _AZURE_NEURAL_VOICE_NAME, rate: str = "medium",
) -> bytes | None:
    """Synthesizes `text` via Azure Cognitive Services Speech (Neural
    TTS) and returns the raw WAV bytes (riff-24khz-16bit-mono-pcm,
    directly playable/writable, no further decoding needed), or None
    (never raises) for any network/credential failure - shared by both
    _speak_via_azure_neural_tts() (plays it immediately) and
    synthesize_to_file() (saves it to disk instead of/as well as
    playing it) below, so the actual HTTP/SSML logic exists in exactly
    one place."""
    import urllib.error
    import urllib.request

    access_token = _fetch_azure_access_token(key, region)
    if access_token is None:
        return None

    url = _AZURE_TTS_URL_TEMPLATE.format(region=region)
    body = _build_azure_ssml(text, voice_name=voice_name, rate=rate).encode("utf-8")
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
            return response.read()
    except (urllib.error.URLError, TimeoutError, OSError):
        return None


def _speak_via_azure_neural_tts(text: str, *, key: str, region: str) -> bool:
    """Synthesizes `text` as Lithuanian speech via Azure Cognitive
    Services Speech (Neural TTS) and plays it. Returns False (never
    raises) for any network/credential/audio failure - a network call
    to a paid (F0 free-tier) cloud service is exactly the kind of
    external dependency that must degrade to the next fallback tier
    rather than crash voice mode."""
    audio_bytes = _synthesize_azure_neural_tts_bytes(text, key=key, region=region)
    if audio_bytes is None:
        return False
    return _play_wav_bytes(audio_bytes)


def is_file_synthesis_configured() -> bool:
    """True if a real, file-savable TTS provider is configured -
    AZURE_SPEECH_KEY/AZURE_SPEECH_REGION are the only tier of speak()'s
    own three-tier fallback (see module docstring) that produces
    inspectable audio BYTES at all: tier 1 (local Windows SAPI voice)
    and tier 3 (default SAPI voice) both speak directly through the
    speaker via pyttsx3/engine.runAndWait(), with no supported way to
    capture that audio to a WAV file without a real, separate on-disk
    audio-capture layer this codebase does not have - so a caller
    needing a REAL saved voiceover file (jarvis.reel_generator.voiceover)
    must check this first and show a clear "not configured" notice
    rather than silently produce no file, or a file assembled by fakery,
    when only tiers 1/3 are available."""
    return bool(AZURE_SPEECH_KEY and AZURE_SPEECH_REGION)


@dataclass
class SynthesizeToFileResult:
    """synthesize_to_file()'s outcome. `ok` is False when nothing could
    be synthesized (not configured, network/credential failure) -
    `error` then holds a human-readable reason. `output_path` is set
    only on success and always refers to a real, non-empty WAV file
    that was actually written to disk."""

    ok: bool
    output_path: Path | None = None
    error: str | None = None


def synthesize_to_file(
    text: str, *, output_path: Path, voice_name: str = _AZURE_NEURAL_VOICE_NAME, rate: str = "medium",
) -> SynthesizeToFileResult:
    """Synthesizes `text` via Azure Neural TTS and writes the resulting
    WAV audio to `output_path` - never plays it. This is the file-based
    counterpart to speak() (which only ever plays audio through the
    speaker, never saves it - see that function's own docstring), built
    for callers needing a real, durable audio FILE (e.g.
    jarvis.reel_generator.voiceover, to later mux into an exported
    video) rather than an immediate spoken reply.

    Returns ok=False with a clear, actionable `error` (never raises,
    matching this module's own established "never fail silently, never
    fake success" convention) when:
    - `text` is blank (nothing to synthesize)
    - AZURE_SPEECH_KEY/AZURE_SPEECH_REGION are not both set (see
      is_file_synthesis_configured()) - there is genuinely no other
      file-savable TTS tier in this codebase (see that function's own
      docstring for exactly why SAPI can't fill this role)
    - the real Azure request itself fails (network/credentials/quota)
    - the response bytes aren't valid WAV audio
    - the file couldn't be written to `output_path` (disk/permissions)

    `voice_name` defaults to the same Lithuanian Neural voice speak()
    uses (lt-LT-OnaNeural) - a caller wanting a different configured
    Azure Neural voice (e.g. for an English-language Reel) passes one
    explicitly; this function does no validation of the name itself
    beyond passing it straight into the SSML request (Azure itself
    returns a clear error for an unknown voice name, surfaced here as
    this result's own `error`). `rate` is a plain SSML prosody rate
    (see _build_azure_ssml()'s own docstring for accepted values) -
    "medium" (the default) is the voice's natural pace, unchanged from
    every pre-existing caller's behavior."""
    if not text or not text.strip():
        return SynthesizeToFileResult(ok=False, error="There is no narration text to synthesize.")

    if not is_file_synthesis_configured():
        return SynthesizeToFileResult(
            ok=False,
            error=(
                "Voiceover generation is not configured yet. Set the AZURE_SPEECH_KEY and "
                "AZURE_SPEECH_REGION environment variables (Azure Cognitive Services Speech, "
                "Neural TTS) to enable it."
            ),
        )

    assert AZURE_SPEECH_KEY is not None and AZURE_SPEECH_REGION is not None  # is_file_synthesis_configured() just confirmed this
    audio_bytes = _synthesize_azure_neural_tts_bytes(
        text, key=AZURE_SPEECH_KEY, region=AZURE_SPEECH_REGION, voice_name=voice_name, rate=rate,
    )
    if audio_bytes is None:
        return SynthesizeToFileResult(
            ok=False,
            error="The Azure Speech request failed (network error, invalid credentials, or quota exceeded).",
        )

    try:
        with wave.open(io.BytesIO(audio_bytes), "rb"):
            pass  # validating the response really is well-formed WAV before writing it to disk
    except (wave.Error, EOFError, struct.error):
        return SynthesizeToFileResult(ok=False, error="Azure Speech returned data that wasn't valid audio.")

    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio_bytes)
    except OSError as e:
        return SynthesizeToFileResult(ok=False, error=f"Couldn't save the voiceover file: {e}")

    return SynthesizeToFileResult(ok=True, output_path=output_path)


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
