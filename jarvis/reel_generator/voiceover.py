"""Voiceover generation (module brief section 7 / Stage B): turns an
approved Reel's own narration text into a REAL, saved audio file via
jarvis.voice.text_to_speech.synthesize_to_file() - the file-based
counterpart to that module's speak() (which only ever plays audio
through the speaker, never saves it).

Narration text is the storyboard's own scenes' voice_text, concatenated
IN SCENE ORDER with a short pause between scenes (a period + space -
matches how a person reading the script aloud would naturally pause at
a scene boundary) - the same text a person already approved when they
approved the script/storyboard, never re-generated or rephrased here.
This keeps the voiceover's own spoken content exactly matching what the
Reel's on-screen text/story director already reflects, and keeps this
module simple: it has no LLM call of its own at all.

Nothing here fakes success: jarvis.voice.text_to_speech
.is_file_synthesis_configured() gates this exactly the same way
jarvis.reel_generator.image_generation.is_configured() gates real image
generation (see jarvis.reel_generator.scene_render's own docstring for
that established precedent) - if AZURE_SPEECH_KEY/AZURE_SPEECH_REGION
aren't both set, generate_voiceover() returns a clear
VoiceoverResult(ok=False, error=...) naming exactly those two
environment variables, never a silent no-op and never a fake/placeholder
audio file.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from jarvis.reel_generator.storyboard import Scene
from jarvis.voice.text_to_speech import is_file_synthesis_configured, synthesize_to_file

# Azure Neural voice per Reel language (jarvis.reel_generator.brief
# .LANGUAGE_CHOICES: "lt"/"en" only) - "lt" reuses the exact same
# default voice jarvis.voice.text_to_speech.speak() already uses
# (lt-LT-OnaNeural), so a Lithuanian Reel's voiceover sounds like the
# same JARVIS voice a person already hears in voice mode.
VOICE_CHOICES_BY_LANGUAGE: dict[str, tuple[str, ...]] = {
    "lt": ("lt-LT-OnaNeural", "lt-LT-LeonasNeural"),
    "en": ("en-US-JennyNeural", "en-US-GuyNeural", "en-GB-SoniaNeural"),
}
DEFAULT_VOICE_BY_LANGUAGE: dict[str, str] = {
    "lt": "lt-LT-OnaNeural",
    "en": "en-US-JennyNeural",
}

# Plain SSML prosody rate choices (see jarvis.voice.text_to_speech
# ._build_azure_ssml()'s own docstring for accepted values) - a small,
# fixed menu rather than an arbitrary free-text rate, so the GUI can
# offer a simple dropdown instead of a percentage input.
SPEED_CHOICES = ("slow", "medium", "fast")
DEFAULT_SPEED = "medium"


@dataclass
class VoiceoverResult:
    """generate_voiceover()'s outcome - same shape/spirit as
    jarvis.voice.text_to_speech.SynthesizeToFileResult, with the
    narration text it was built from also included (so a caller/GUI can
    show exactly what was spoken, e.g. for a text preview alongside an
    audio player)."""

    ok: bool
    output_path: Path | None = None
    narration_text: str = ""
    error: str | None = None


def build_narration_text(scenes: tuple[Scene, ...]) -> str:
    """Concatenates every scene's own voice_text, in scene order, into
    one narration script - every scene's own text that doesn't already
    end with sentence punctuation gets a period appended (including the
    LAST scene), so Azure's own SSML always pauses/breathes between
    scenes AND ends the whole narration on a proper sentence boundary,
    never mid-phrase. Never raises; an empty `scenes` returns an empty
    string (generate_voiceover() below then reports a clear "no
    narration text" error rather than calling Azure with nothing)."""
    parts: list[str] = []
    for scene in scenes:
        text = scene.voice_text.strip()
        if not text:
            continue
        if not text.endswith((".", "!", "?")):
            text = text + "."
        parts.append(text)
    return " ".join(parts)


def generate_voiceover(
    scenes: tuple[Scene, ...], *, output_path: Path, language: str = "en",
    voice_name: str | None = None, speed: str = DEFAULT_SPEED,
) -> VoiceoverResult:
    """Generates a real voiceover audio file for `scenes` (a Reel's
    approved Storyboard.scenes, in order) via Azure Neural TTS, saved to
    `output_path`. Returns a VoiceoverResult that never claims success
    falsely:
    - empty/whitespace-only narration (no scenes, or every scene's
      voice_text is blank) -> ok=False, clear error, no Azure call
    - AZURE_SPEECH_KEY/AZURE_SPEECH_REGION not configured -> ok=False,
      clear error naming exactly those two environment variables (via
      jarvis.voice.text_to_speech.synthesize_to_file()'s own identical
      message), no Azure call
    - the real Azure request fails (network/credentials/quota) ->
      ok=False, clear error, no file written
    - success -> ok=True, output_path set to a real, non-empty WAV file
      that was actually written to disk

    `voice_name`, if not given, defaults to DEFAULT_VOICE_BY_LANGUAGE's
    own entry for `language` (falling back to the English default for
    an unrecognized language code, rather than raising - a Reel's own
    language is validated once, at brief-creation time, by
    jarvis.reel_generator.brief; this function trusts it rather than
    re-validating it a second time)."""
    narration_text = build_narration_text(scenes)
    if not narration_text:
        return VoiceoverResult(ok=False, narration_text="", error="This Reel has no narration text to speak yet.")

    resolved_voice = voice_name or DEFAULT_VOICE_BY_LANGUAGE.get(language, DEFAULT_VOICE_BY_LANGUAGE["en"])

    if not is_file_synthesis_configured():
        # Same message synthesize_to_file() itself would return - stated
        # here too (rather than only relying on that function's own
        # check) so this module's own docstring/callers can rely on this
        # exact error text without needing to call into
        # text_to_speech directly first.
        return VoiceoverResult(
            ok=False, narration_text=narration_text,
            error=(
                "Voiceover generation is not configured yet. Set the AZURE_SPEECH_KEY and "
                "AZURE_SPEECH_REGION environment variables (Azure Cognitive Services Speech, "
                "Neural TTS) to enable it."
            ),
        )

    result = synthesize_to_file(narration_text, output_path=output_path, voice_name=resolved_voice, rate=speed)
    if not result.ok:
        return VoiceoverResult(ok=False, narration_text=narration_text, error=result.error)
    return VoiceoverResult(ok=True, output_path=result.output_path, narration_text=narration_text)
