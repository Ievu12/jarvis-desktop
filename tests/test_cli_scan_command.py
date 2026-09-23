"""Tests for the CLI's 'scan' command and the shared _run_agent_turn
helper it uses (jarvis.cli.main): the canned survey prompt is sent
through the normal agent loop, gets identical interrupt/error handling
and history persistence as a regular conversational turn, and - most
importantly - the prompt itself instructs the model never to write
JARVIS.md automatically."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jarvis.cli.main import SCAN_PROMPT, _run_agent_turn
from jarvis.core.agent import StepInterrupted


def test_scan_prompt_forbids_automatic_writing():
    assert "do not write" in SCAN_PROMPT.lower()
    assert "JARVIS.md" in SCAN_PROMPT


def test_scan_prompt_asks_to_survey_not_invent():
    assert "don't invent" in SCAN_PROMPT.lower() or "do not invent" in SCAN_PROMPT.lower()


def test_scan_prompt_mentions_reviewing_before_saving():
    assert "review" in SCAN_PROMPT.lower()


# --- _run_agent_turn: shared turn-handling logic -----------------------------


def test_run_agent_turn_prints_reply_and_saves_history(capsys):
    agent = MagicMock()
    agent.step.return_value = "here is the survey result"
    history = []

    with patch("jarvis.cli.main.save_history") as mock_save:
        _run_agent_turn(agent, history, "some prompt")

    captured = capsys.readouterr()
    assert "here is the survey result" in captured.out
    mock_save.assert_called_once_with(history)


def test_run_agent_turn_handles_step_interrupted(capsys):
    agent = MagicMock()
    agent.step.side_effect = StepInterrupted("interrupted")
    history = []

    with patch("jarvis.cli.main.save_history") as mock_save:
        _run_agent_turn(agent, history, "some prompt")

    captured = capsys.readouterr()
    assert "cancelled" in captured.out.lower()
    mock_save.assert_called_once_with(history)


def test_run_agent_turn_handles_generic_exception_without_crashing(capsys):
    agent = MagicMock()
    agent.step.side_effect = RuntimeError("boom")
    history = []

    with patch("jarvis.cli.main.save_history") as mock_save:
        _run_agent_turn(agent, history, "some prompt")  # must not raise

    captured = capsys.readouterr()
    assert "error" in captured.out.lower()
    mock_save.assert_not_called()  # history not saved on a genuine crash


def test_run_agent_turn_passes_the_exact_prompt_to_agent_step():
    agent = MagicMock()
    agent.step.return_value = "ok"
    history = []

    with patch("jarvis.cli.main.save_history"):
        _run_agent_turn(agent, history, SCAN_PROMPT)

    agent.step.assert_called_once_with(history, SCAN_PROMPT)
