"""Microphone -> Lithuanian text, via the SpeechRecognition package's
Google Web Speech API recognizer (free, no API key required - a Google
account/Anthropic API key is not involved; this is a separate, unrelated
free endpoint SpeechRecognition talks to directly). Audio captured here
is sent to Google's service for transcription; nothing else in JARVIS
sends this audio anywhere, and the transcribed text that comes back is
treated exactly like typed input - it carries no elevated trust and goes
through every normal tool-approval/RiskLevel check once handed to
jarvis.core.agent.Agent.step().

This module never raises out of listen_once() for an expected failure
(silence, unintelligible audio, no network, no microphone) - each is
reported via the returned ListenResult so a caller (voice_loop.py) can
tell the person clearly what happened, mirroring how the rest of
jarvis.tools/jarvis.integrations degrade gracefully instead of
crashing the process.
"""

from __future__ import annotations

from dataclasses import dataclass

_LANGUAGE_CODE = "lt-LT"

# How long to wait for the person to START speaking before giving up on
# this listen attempt (not how long they're allowed to keep talking -
# that's controlled by phrase_time_limit below). Generous enough that a
# brief pause before speaking doesn't spuriously time out.
_LISTEN_TIMEOUT_SECONDS = 8

# Hard cap on a single utterance's length, so one very long ambient sound
# (a TV, music) can't block the loop indefinitely if silence detection
# doesn't kick in.
_PHRASE_TIME_LIMIT_SECONDS = 30


@dataclass
class ListenResult:
    """listen_once()'s outcome. Exactly one of `text` or `error` is set -
    never both, never neither - so a caller can branch on `ok` without
    also having to null-check `text`."""

    ok: bool
    text: str | None
    error: str | None

    @staticmethod
    def success(text: str) -> "ListenResult":
        return ListenResult(ok=True, text=text, error=None)

    @staticmethod
    def failure(error: str) -> "ListenResult":
        return ListenResult(ok=False, text=None, error=error)


def listen_once() -> ListenResult:
    """Record one utterance from the default microphone and transcribe it
    as Lithuanian (lt-LT). Blocks until the person starts speaking (up to
    _LISTEN_TIMEOUT_SECONDS) and then until they stop (up to
    _PHRASE_TIME_LIMIT_SECONDS). Never raises - any expected failure
    (no microphone, no network, silence, unintelligible speech) comes
    back as ListenResult.failure(...) with a plain-language reason."""
    try:
        import speech_recognition as sr
    except ImportError:
        return ListenResult.failure(
            "SpeechRecognition/PyAudio nėra įdiegti - balso režimas negali veikti "
            "be jų (žr. pyproject.toml priklausomybes)."
        )

    recognizer = sr.Recognizer()

    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.5)
            try:
                audio = recognizer.listen(
                    source,
                    timeout=_LISTEN_TIMEOUT_SECONDS,
                    phrase_time_limit=_PHRASE_TIME_LIMIT_SECONDS,
                )
            except sr.WaitTimeoutError:
                return ListenResult.failure("Nieko neišgirdau - tyla per klausymosi laiką.")
    except OSError as e:
        return ListenResult.failure(
            f"Nepavyko pasiekti mikrofono ({e}) - patikrink, ar mikrofonas prijungtas "
            "ir ar JARVIS turi leidimą juo naudotis."
        )

    try:
        # recognize_google exists at runtime (confirmed) but is missing
        # from SpeechRecognition's bundled type stubs in this version.
        text = recognizer.recognize_google(audio, language=_LANGUAGE_CODE)  # type: ignore[attr-defined]
    except sr.UnknownValueError:
        return ListenResult.failure("Nepavyko atpažinti kalbos - pabandyk dar kartą.")
    except sr.RequestError as e:
        return ListenResult.failure(
            f"Balso atpažinimo paslauga nepasiekiama ({e}) - patikrink interneto ryšį."
        )

    text = text.strip()
    if not text:
        return ListenResult.failure("Nieko neišgirdau - tyla per klausymosi laiką.")
    return ListenResult.success(text)
