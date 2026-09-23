"""Tests for jarvis.morning_routine.run_morning_routine: sequences the
existing Agent.step()/speak() calls into greeting -> Instagram context
-> content brief -> spoken aloud. Agent, speak(), and save_history are
all mocked - no real Anthropic API call, no real speaker output, no
real Instagram/Gmail/Calendar access. Confirms: the greeting is spoken
first, exactly two Agent.step() calls happen (context request, then
brief prompt) with the content brief prompt built from whatever the
first call returned, StepInterrupted/exceptions degrade gracefully
without crashing, and the final brief is both spoken and returned."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jarvis import content_manager
from jarvis.core.agent import StepInterrupted
from jarvis.morning_routine import run_morning_routine
from jarvis.voice.text_to_speech import SpeakResult


@pytest.fixture(autouse=True)
def _isolated_topics_history(tmp_path, monkeypatch):
    """run_morning_routine() calls content_manager.record_used_topics()
    on a successful brief - redirected to a per-test tmp_path file here
    so no test in this file ever touches the real
    .jarvis/content_topics_history.json on disk."""
    history_file = tmp_path / "content_topics_history.json"
    monkeypatch.setattr(content_manager, "CONTENT_TOPICS_HISTORY_FILE", history_file)
    return history_file


def _agent_stub(*step_returns) -> MagicMock:
    agent = MagicMock()
    agent.step.side_effect = list(step_returns)
    return agent


def _speak_ok(*_args, **_kwargs) -> SpeakResult:
    return SpeakResult(ok=True, notice=None)


# --- normal run: greeting -> context -> brief -> spoken ------------------------------


def test_speaks_greeting_before_anything_else():
    agent = _agent_stub("reach: 100 (+10%)", "Reel: ...\nStory: ...\nCarousel: ...")
    history: list[dict] = []
    with patch("jarvis.morning_routine.speak", side_effect=_speak_ok) as mock_speak:
        with patch("jarvis.morning_routine.save_history"):
            run_morning_routine(agent, history)

    first_spoken = mock_speak.call_args_list[0].args[0]
    assert "Labas rytas" in first_spoken


def test_calls_agent_step_exactly_twice():
    agent = _agent_stub("reach: 100", "content brief text")
    history: list[dict] = []
    with patch("jarvis.morning_routine.speak", side_effect=_speak_ok):
        with patch("jarvis.morning_routine.save_history"):
            run_morning_routine(agent, history)
    assert agent.step.call_count == 2


def test_second_agent_step_call_uses_content_brief_prompt_with_context():
    agent = _agent_stub("reach: 100 (+10%)", "content brief text")
    history: list[dict] = []
    with patch("jarvis.morning_routine.speak", side_effect=_speak_ok):
        with patch("jarvis.morning_routine.save_history"):
            run_morning_routine(agent, history)

    second_call_prompt = agent.step.call_args_list[1].args[1]
    assert "reach: 100 (+10%)" in second_call_prompt
    assert "1 Reel" in second_call_prompt


def test_returns_the_content_brief_text():
    agent = _agent_stub("reach: 100", "final content brief")
    history: list[dict] = []
    with patch("jarvis.morning_routine.speak", side_effect=_speak_ok):
        with patch("jarvis.morning_routine.save_history"):
            result = run_morning_routine(agent, history)
    assert result == "final content brief"


def test_speaks_the_content_brief():
    agent = _agent_stub("reach: 100", "final content brief")
    history: list[dict] = []
    with patch("jarvis.morning_routine.speak", side_effect=_speak_ok) as mock_speak:
        with patch("jarvis.morning_routine.save_history"):
            run_morning_routine(agent, history)
    spoken_texts = [c.args[0] for c in mock_speak.call_args_list]
    assert "final content brief" in spoken_texts


def test_saves_history_after_each_step():
    agent = _agent_stub("reach: 100", "brief")
    history: list[dict] = []
    with patch("jarvis.morning_routine.speak", side_effect=_speak_ok):
        with patch("jarvis.morning_routine.save_history") as mock_save:
            run_morning_routine(agent, history)
    assert mock_save.call_count >= 2


# --- Instagram context step failing: degrades gracefully, briefing continues --------


def test_step_interrupted_during_context_request_still_produces_a_brief():
    agent = MagicMock()
    agent.step.side_effect = [StepInterrupted("cancelled"), "brief without context"]
    history: list[dict] = []
    with patch("jarvis.morning_routine.speak", side_effect=_speak_ok):
        with patch("jarvis.morning_routine.save_history"):
            result = run_morning_routine(agent, history)
    assert result == "brief without context"


def test_exception_during_context_request_still_produces_a_brief():
    agent = MagicMock()
    agent.step.side_effect = [RuntimeError("network error"), "brief without context"]
    history: list[dict] = []
    with patch("jarvis.morning_routine.speak", side_effect=_speak_ok):
        with patch("jarvis.morning_routine.save_history"):
            result = run_morning_routine(agent, history)
    assert result == "brief without context"


def test_context_failure_prompt_states_no_data_available():
    agent = MagicMock()
    agent.step.side_effect = [RuntimeError("network error"), "brief"]
    history: list[dict] = []
    with patch("jarvis.morning_routine.speak", side_effect=_speak_ok):
        with patch("jarvis.morning_routine.save_history"):
            run_morning_routine(agent, history)
    second_call_prompt = agent.step.call_args_list[1].args[1]
    assert "nesukonfigūruota" in second_call_prompt.lower() or "nėra prieinama" in second_call_prompt.lower()


# --- brief generation itself failing: reported, never crashes -----------------------


def test_step_interrupted_during_brief_generation_is_handled():
    agent = MagicMock()
    agent.step.side_effect = ["reach: 100", StepInterrupted("cancelled")]
    history: list[dict] = []
    with patch("jarvis.morning_routine.speak", side_effect=_speak_ok) as mock_speak:
        with patch("jarvis.morning_routine.save_history"):
            result = run_morning_routine(agent, history)  # must not raise
    assert "atšaukt" in result.lower()
    spoken_texts = [c.args[0] for c in mock_speak.call_args_list]
    assert any("atšaukt" in t.lower() for t in spoken_texts)


def test_exception_during_brief_generation_is_reported_not_raised():
    agent = MagicMock()
    agent.step.side_effect = ["reach: 100", RuntimeError("boom")]
    history: list[dict] = []
    with patch("jarvis.morning_routine.speak", side_effect=_speak_ok) as mock_speak:
        with patch("jarvis.morning_routine.save_history"):
            result = run_morning_routine(agent, history)  # must not raise
    assert "nepavyko" in result.lower()
    spoken_texts = [c.args[0] for c in mock_speak.call_args_list]
    assert any("nepavyko" in t.lower() for t in spoken_texts)


# --- topics are recorded after a successful brief ------------------------------------


def test_records_used_topics_after_successful_brief():
    agent = _agent_stub("reach: 100", "Reel: kaip pradėti rytą su joga\nStory: klausk sekėjų")
    history: list[dict] = []
    with patch("jarvis.morning_routine.speak", side_effect=_speak_ok):
        with patch("jarvis.morning_routine.save_history"):
            with patch("jarvis.morning_routine.record_used_topics") as mock_record:
                run_morning_routine(agent, history)
    mock_record.assert_called_once()


def test_does_not_record_topics_when_brief_generation_fails():
    agent = MagicMock()
    agent.step.side_effect = ["reach: 100", RuntimeError("boom")]
    history: list[dict] = []
    with patch("jarvis.morning_routine.speak", side_effect=_speak_ok):
        with patch("jarvis.morning_routine.save_history"):
            with patch("jarvis.morning_routine.record_used_topics") as mock_record:
                run_morning_routine(agent, history)
    mock_record.assert_not_called()


# --- voice notice (e.g. no Lithuanian voice) is surfaced, not swallowed -------------


def test_speak_notice_is_printed():
    agent = _agent_stub("reach: 100", "brief")
    history: list[dict] = []

    def _speak_with_notice(*_args, **_kwargs):
        return SpeakResult(ok=True, notice="Lietuviško balso nėra sukonfigūruota.")

    with patch("jarvis.morning_routine.speak", side_effect=_speak_with_notice):
        with patch("jarvis.morning_routine.save_history"):
            with patch("builtins.print") as mock_print:
                run_morning_routine(agent, history)

    printed = "\n".join(str(c.args[0]) for c in mock_print.call_args_list if c.args)
    assert "Lietuviško balso nėra sukonfigūruota." in printed


# --- MORNING_HOUR/MORNING_MINUTE are trivially changeable constants -----------------


def test_morning_hour_and_minute_are_plain_module_constants():
    import jarvis.morning_routine as mod

    assert isinstance(mod.MORNING_HOUR, int)
    assert isinstance(mod.MORNING_MINUTE, int)
    assert 0 <= mod.MORNING_HOUR <= 23
    assert 0 <= mod.MORNING_MINUTE <= 59


# --- never touches Gmail/Calendar connectors, never imports voice_loop's internals --


def test_module_does_not_import_gmail_or_calendar_connectors():
    import jarvis.morning_routine as mod

    module_globals = vars(mod)
    assert "GmailConnector" not in module_globals
    assert "GoogleCalendarConnector" not in module_globals


def test_module_does_not_import_instagram_connector_directly():
    import jarvis.morning_routine as mod

    module_globals = vars(mod)
    assert "InstagramConnector" not in module_globals


def test_module_does_not_modify_listen_once_or_voice_loop():
    import jarvis.morning_routine as mod

    module_globals = vars(mod)
    assert "listen_once" not in module_globals
    assert "run_voice_mode" not in module_globals
