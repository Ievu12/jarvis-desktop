"""The voice-mode conversation loop: listen -> understand -> speak ->
listen again. Calls jarvis.core.agent.Agent.step() exactly the way
jarvis.cli.main._run_agent_turn() already does for typed REPL input -
this module adds no new capability to the agent, no new tool, and no
new risk path. Voice input is not a trusted shortcut: transcribed text
goes through the identical tool-approval/RiskLevel checks as anything
typed, and every existing integration (Instagram included) behaves
exactly as it does in the text REPL.

Entered via the REPL's 'voice' command or 'jarvis voice' CLI argument
(see jarvis.cli.main) - never automatically, and never in a way that
bypasses jarvis.core.approval.confirm_side_effect's interactive y/N
prompts for a side-effecting tool: run_voice_mode still reads that
confirmation from real stdin, so a write action still pauses for a
typed y/N exactly as it does in the text REPL, even while voice mode is
otherwise listening/speaking. This is a deliberate, unchanged safety
boundary - voice mode adds convenience to read/dictate around that
prompt, not a way past it.
"""

from __future__ import annotations

from typing import Any

from jarvis.core.agent import Agent, StepInterrupted
from jarvis.session.store import save_history
from jarvis.voice.speech_to_text import listen_once
from jarvis.voice.text_to_speech import speak

# Said (in Lithuanian) to stop voice mode and return to the normal typed
# REPL - checked case-insensitively against the whole transcribed
# utterance, substring match, so small phrasing variations ("sustok",
# "sustabdyk balso režimą") all work rather than requiring an exact
# match a person is unlikely to hit by voice.
_STOP_PHRASES = ("sustok", "sustabdyk", "baik", "stop", "išjunk balso")

_GREETING = "Balso režimas įjungtas. Klausau."
_STOPPED_MESSAGE = "Balso režimas išjungtas."


def _is_stop_phrase(text: str) -> bool:
    lowered = text.lower()
    return any(phrase in lowered for phrase in _STOP_PHRASES)


def _speak_and_report(text: str, *, notice_already_shown: bool) -> bool:
    """Calls text_to_speech.speak() and prints its notice (e.g. "no
    Lithuanian voice configured") at most once per voice-mode session,
    not on every single turn - the person only needs to be told that
    once. Returns whether the notice has now been shown (to update the
    caller's `notice_already_shown` for the next call)."""
    result = speak(text)
    if result.notice and not notice_already_shown:
        print(f"[voice] {result.notice}")
        return True
    return notice_already_shown


def run_voice_mode(agent: Agent, history: list[dict[str, Any]]) -> None:
    """Runs the listen -> Agent.step() -> speak cycle until the person
    says a stop phrase or interrupts with Ctrl+C. Prints the same text
    a typed turn would print (so a person watching the terminal, not
    just listening, can follow along) in addition to speaking it aloud.
    Saves history after every turn, identically to the text REPL, so a
    voice turn is never lost if voice mode is interrupted right after.
    """
    print(f"[voice] {_GREETING}")
    notice_shown = _speak_and_report(_GREETING, notice_already_shown=False)

    while True:
        print("[voice] Klausau...")
        listen_result = listen_once()

        if not listen_result.ok:
            # An expected, recoverable failure (silence, unintelligible
            # audio, no network) - tell the person and listen again,
            # rather than exiting voice mode entirely.
            message = listen_result.error or "Nepavyko atpažinti kalbos."
            print(f"[voice] {message}")
            notice_shown = _speak_and_report(message, notice_already_shown=notice_shown)
            continue

        user_input = listen_result.text
        assert user_input is not None  # guaranteed by ListenResult.success()
        print(f"you (voice)> {user_input}")

        if _is_stop_phrase(user_input):
            print(f"[voice] {_STOPPED_MESSAGE}")
            speak(_STOPPED_MESSAGE)
            return

        try:
            reply = agent.step(history, user_input)
        except StepInterrupted:
            print("\n[voice] Veiksmas atšauktas. Nepatvirtintas veiksmas nebuvo įvykdytas.\n")
            save_history(history)
            continue
        except KeyboardInterrupt:
            print(f"\n[voice] {_STOPPED_MESSAGE}")
            save_history(history)
            return
        except Exception as e:
            error_message = f"Įvyko klaida: {e}"
            print(f"[voice] {error_message}")
            notice_shown = _speak_and_report(
                "Įvyko klaida vykdant užklausą.", notice_already_shown=notice_shown
            )
            continue

        print(f"jarvis (voice)> {reply}\n")
        save_history(history)
        notice_shown = _speak_and_report(reply, notice_already_shown=notice_shown)
