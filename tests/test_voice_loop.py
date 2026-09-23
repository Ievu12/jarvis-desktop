"""Tests for jarvis.voice.voice_loop.run_voice_mode: the listen ->
Agent.step() -> speak conversation loop. listen_once/speak/Agent are all
mocked - no real microphone, no real speaker, no real Anthropic API
call, no real speech_recognition/pyttsx3 access. Confirms: a normal turn
calls Agent.step() with the transcribed text and speaks the reply, a
stop phrase ends the loop without calling Agent.step(), a listen
failure is reported and the loop continues (never exits), and
Ctrl+C/StepInterrupted are both handled without crashing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis.core.agent import StepInterrupted
from jarvis.voice.speech_to_text import ListenResult
from jarvis.voice.text_to_speech import SpeakResult
from jarvis.voice.voice_loop import run_voice_mode


def _agent_stub(reply_text: str = "ok") -> MagicMock:
    agent = MagicMock()
    agent.step.return_value = reply_text
    return agent


# --- normal turn: listen -> Agent.step() -> speak ------------------------------------


def test_normal_turn_calls_agent_step_with_transcribed_text():
    agent = _agent_stub("JARVIS atsakymas")
    history: list[dict] = []
    with patch("jarvis.voice.voice_loop.listen_once") as mock_listen:
        mock_listen.side_effect = [
            ListenResult.success("koks oras šiandien"),
            ListenResult.success("sustok"),
        ]
        with patch("jarvis.voice.voice_loop.speak") as mock_speak:
            with patch("jarvis.voice.voice_loop.save_history"):
                run_voice_mode(agent, history)

    agent.step.assert_called_once_with(history, "koks oras šiandien")
    assert "JARVIS atsakymas" in [c.args[0] for c in mock_speak.call_args_list]


def test_normal_turn_saves_history_after_agent_step():
    agent = _agent_stub("atsakymas")
    history: list[dict] = []
    with patch("jarvis.voice.voice_loop.listen_once") as mock_listen:
        mock_listen.side_effect = [ListenResult.success("klausimas"), ListenResult.success("stop")]
        with patch("jarvis.voice.voice_loop.speak"):
            with patch("jarvis.voice.voice_loop.save_history") as mock_save:
                run_voice_mode(agent, history)
    assert mock_save.called


# --- stop phrase: ends the loop, never calls Agent.step() for it --------------------


def test_stop_phrase_ends_loop_without_calling_agent_step():
    agent = _agent_stub()
    history: list[dict] = []
    with patch("jarvis.voice.voice_loop.listen_once") as mock_listen:
        mock_listen.return_value = ListenResult.success("sustok")
        with patch("jarvis.voice.voice_loop.speak"):
            with patch("jarvis.voice.voice_loop.save_history"):
                run_voice_mode(agent, history)
    agent.step.assert_not_called()


def test_stop_phrase_is_case_insensitive_and_substring_matched():
    agent = _agent_stub()
    history: list[dict] = []
    with patch("jarvis.voice.voice_loop.listen_once") as mock_listen:
        mock_listen.return_value = ListenResult.success("Gerai, SUSTABDYK balso režimą dabar")
        with patch("jarvis.voice.voice_loop.speak"):
            with patch("jarvis.voice.voice_loop.save_history"):
                run_voice_mode(agent, history)
    agent.step.assert_not_called()


def test_stop_phrase_speaks_a_stopped_confirmation():
    agent = _agent_stub()
    history: list[dict] = []
    with patch("jarvis.voice.voice_loop.listen_once") as mock_listen:
        mock_listen.return_value = ListenResult.success("baik")
        with patch("jarvis.voice.voice_loop.speak") as mock_speak:
            with patch("jarvis.voice.voice_loop.save_history"):
                run_voice_mode(agent, history)
    spoken_texts = " ".join(c.args[0] for c in mock_speak.call_args_list)
    assert "išjungtas" in spoken_texts.lower()


# --- listen failure: reported, loop continues (never exits, never crashes) ----------


def test_listen_failure_is_reported_and_loop_continues():
    agent = _agent_stub("ok")
    history: list[dict] = []
    with patch("jarvis.voice.voice_loop.listen_once") as mock_listen:
        mock_listen.side_effect = [
            ListenResult.failure("Nieko neišgirdau - tyla per klausymosi laiką."),
            ListenResult.success("sustok"),
        ]
        with patch("jarvis.voice.voice_loop.speak") as mock_speak:
            with patch("jarvis.voice.voice_loop.save_history"):
                run_voice_mode(agent, history)

    agent.step.assert_not_called()  # the failed listen never reached Agent.step()
    spoken_texts = " ".join(c.args[0] for c in mock_speak.call_args_list)
    assert "tyla" in spoken_texts.lower()


# --- Agent.step() raising StepInterrupted: handled, loop continues ------------------


def test_step_interrupted_is_caught_and_loop_continues():
    agent = _agent_stub()
    agent.step.side_effect = [StepInterrupted("cancelled"), "ok"]
    history: list[dict] = []
    with patch("jarvis.voice.voice_loop.listen_once") as mock_listen:
        mock_listen.side_effect = [
            ListenResult.success("pirmas"),
            ListenResult.success("sustok"),
        ]
        with patch("jarvis.voice.voice_loop.speak"):
            with patch("jarvis.voice.voice_loop.save_history"):
                run_voice_mode(agent, history)
    assert agent.step.call_count == 1  # loop reached the stop phrase on the 2nd listen


def test_step_interrupted_never_propagates_out_of_run_voice_mode():
    agent = _agent_stub()
    agent.step.side_effect = StepInterrupted("cancelled")
    history: list[dict] = []
    with patch("jarvis.voice.voice_loop.listen_once") as mock_listen:
        mock_listen.side_effect = [
            ListenResult.success("pirmas"),
            ListenResult.success("sustok"),
        ]
        with patch("jarvis.voice.voice_loop.speak"):
            with patch("jarvis.voice.voice_loop.save_history"):
                run_voice_mode(agent, history)  # must not raise


# --- Ctrl+C during Agent.step(): ends voice mode cleanly -----------------------------


def test_keyboard_interrupt_during_agent_step_ends_voice_mode():
    agent = _agent_stub()
    agent.step.side_effect = KeyboardInterrupt()
    history: list[dict] = []
    with patch("jarvis.voice.voice_loop.listen_once") as mock_listen:
        mock_listen.return_value = ListenResult.success("kažkas")
        with patch("jarvis.voice.voice_loop.speak"):
            with patch("jarvis.voice.voice_loop.save_history"):
                run_voice_mode(agent, history)  # must not raise, must return
    assert agent.step.call_count == 1


# --- an unexpected exception from Agent.step(): reported, loop continues ------------


def test_unexpected_exception_from_agent_step_is_reported_and_loop_continues():
    agent = _agent_stub()
    agent.step.side_effect = [RuntimeError("boom"), "ok"]
    history: list[dict] = []
    with patch("jarvis.voice.voice_loop.listen_once") as mock_listen:
        mock_listen.side_effect = [
            ListenResult.success("pirmas"),
            ListenResult.success("sustok"),
        ]
        with patch("jarvis.voice.voice_loop.speak") as mock_speak:
            with patch("jarvis.voice.voice_loop.save_history"):
                run_voice_mode(agent, history)  # must not raise
    spoken_texts = " ".join(c.args[0] for c in mock_speak.call_args_list)
    assert "klaida" in spoken_texts.lower()


# --- greeting spoken at start -------------------------------------------------------


def test_greeting_is_spoken_at_start():
    agent = _agent_stub()
    history: list[dict] = []
    with patch("jarvis.voice.voice_loop.listen_once") as mock_listen:
        mock_listen.return_value = ListenResult.success("sustok")
        with patch("jarvis.voice.voice_loop.speak") as mock_speak:
            with patch("jarvis.voice.voice_loop.save_history"):
                run_voice_mode(agent, history)
    assert mock_speak.call_args_list[0].args[0] == "Balso režimas įjungtas. Klausau."


# --- SpeakResult.notice (e.g. "no Lithuanian voice configured") ----------------------


def test_speak_notice_is_printed_once_when_present():
    agent = _agent_stub("ok")
    history: list[dict] = []
    with patch("jarvis.voice.voice_loop.listen_once") as mock_listen:
        mock_listen.side_effect = [ListenResult.success("klausimas"), ListenResult.success("sustok")]
        with patch("jarvis.voice.voice_loop.speak") as mock_speak:
            mock_speak.return_value = SpeakResult(ok=True, notice="Lietuviško balso nėra sukonfigūruota.")
            with patch("jarvis.voice.voice_loop.save_history"):
                with patch("builtins.print") as mock_print:
                    run_voice_mode(agent, history)

    printed = "\n".join(str(c.args[0]) for c in mock_print.call_args_list if c.args)
    assert printed.count("Lietuviško balso nėra sukonfigūruota.") == 1


def test_speak_notice_absent_when_result_has_none():
    agent = _agent_stub("ok")
    history: list[dict] = []
    with patch("jarvis.voice.voice_loop.listen_once") as mock_listen:
        mock_listen.side_effect = [ListenResult.success("klausimas"), ListenResult.success("sustok")]
        with patch("jarvis.voice.voice_loop.speak") as mock_speak:
            mock_speak.return_value = SpeakResult(ok=True, notice=None)
            with patch("jarvis.voice.voice_loop.save_history"):
                with patch("builtins.print") as mock_print:
                    run_voice_mode(agent, history)

    printed = "\n".join(str(c.args[0]) for c in mock_print.call_args_list if c.args)
    assert "sukonfigūruota" not in printed


# --- never touches Instagram or any connector directly -------------------------------


def test_module_does_not_import_any_connector():
    import jarvis.voice.voice_loop as mod

    module_globals = vars(mod)
    for forbidden in (
        "InstagramConnector", "GmailConnector", "GoogleCalendarConnector",
        "EmailConnector", "StripeConnector",
    ):
        assert forbidden not in module_globals
