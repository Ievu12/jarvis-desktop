"""Verifies Agent.step() actually applies history trimming before calling
the LLM, so long-running sessions can't grow the request payload without
bound. No real API calls - the LLM client is mocked."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from jarvis.core.agent import Agent
from jarvis.session.trim import TRIM_MARKER_PREFIX, trim_history
from jarvis.tools.base import ToolRegistry


def _text_response(text: str):
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    text_block.model_dump.return_value = {"type": "text", "text": text}

    response = MagicMock()
    response.content = [text_block]
    response.stop_reason = "end_turn"
    return response


def test_step_calls_trim_history_before_sending():
    llm = MagicMock()
    llm.send.return_value = _text_response("ok")
    agent = Agent(llm, ToolRegistry())
    history = [{"role": "user", "content": "earlier turn"}]

    captured = []

    def spy(h):
        captured.append(list(h))  # snapshot, since history[:] mutates in place afterward
        return trim_history(h)

    with patch("jarvis.core.agent.trim_history", side_effect=spy) as mock_trim:
        agent.step(history, "new question")

    mock_trim.assert_called_once()
    # Called with the history as it stood before this turn's user message
    # was appended - i.e. only fully-completed prior turns are considered.
    assert captured[0] == [{"role": "user", "content": "earlier turn"}]


def test_step_trims_using_low_budget_end_to_end(monkeypatch):
    # Force a tiny effective budget so trimming is guaranteed to trigger,
    # and confirm the marker turn actually reaches the LLM request.
    monkeypatch.setattr(
        "jarvis.core.agent.trim_history",
        lambda h: trim_history(h, max_chars=200),
    )

    llm = MagicMock()
    llm.send.return_value = _text_response("ok")

    agent = Agent(llm, ToolRegistry())
    history = [
        {"role": "user", "content": f"old message {i} with padding text"} for i in range(200)
    ]

    agent.step(history, "final question")

    sent_history = llm.send.call_args[0][0]
    assert any(
        isinstance(t.get("content"), str) and t["content"].startswith(TRIM_MARKER_PREFIX)
        for t in sent_history
    )
    assert any(t.get("content") == "final question" for t in sent_history)


def test_step_does_not_trim_when_history_is_small():
    llm = MagicMock()
    llm.send.return_value = _text_response("ok")
    agent = Agent(llm, ToolRegistry())
    history = [{"role": "user", "content": "short"}]

    agent.step(history, "another short message")

    sent_history = llm.send.call_args[0][0]
    assert not any(
        isinstance(t.get("content"), str) and t["content"].startswith(TRIM_MARKER_PREFIX)
        for t in sent_history
    )
