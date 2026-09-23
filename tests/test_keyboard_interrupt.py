"""Tests that Ctrl+C (KeyboardInterrupt) is handled gracefully at every
layer - approval prompts, tools, and the agent loop - without ever being
misread as an approval, and without weakening the sandbox or approval
flow. No real filesystem access outside JARVIS_ROOT, no API key used."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from jarvis.config import JARVIS_ROOT
from jarvis.core.agent import Agent, StepInterrupted
from jarvis.core.approval import confirm_outside_sandbox, confirm_side_effect
from jarvis.tools.base import Tool, ToolRegistry, ToolResult
from jarvis.tools.fs import WriteFileTool


def _interrupting_input(*args, **kwargs):
    raise KeyboardInterrupt


# --- approval.py: interrupt during the prompt itself -----------------------


def test_confirm_side_effect_interrupt_is_not_treated_as_approval():
    with patch("builtins.input", side_effect=_interrupting_input):
        with pytest.raises(KeyboardInterrupt):
            confirm_side_effect("write something")
    # If it didn't raise, a caller checking `if not confirm_side_effect(...)`
    # could misread an interrupt as approval - the raise is what prevents that.


def test_confirm_outside_sandbox_interrupt_is_not_treated_as_approval():
    with patch("builtins.input", side_effect=_interrupting_input):
        with pytest.raises(KeyboardInterrupt):
            confirm_outside_sandbox(JARVIS_ROOT.parent / "outside.txt")


def test_confirm_side_effect_interrupt_is_logged_as_denied(tmp_path, monkeypatch):
    log_calls = []
    monkeypatch.setattr("jarvis.core.approval.log_event", lambda *a, **k: log_calls.append(k))

    with patch("builtins.input", side_effect=_interrupting_input):
        with pytest.raises(KeyboardInterrupt):
            confirm_side_effect("write something")

    assert len(log_calls) == 1
    assert log_calls[0]["approved"] is False
    assert log_calls[0]["interrupted"] is True


# --- tools/fs.py: interrupt during an approval prompt inside a tool --------


def test_write_file_tool_interrupt_during_approval_propagates(tmp_path):
    target = JARVIS_ROOT / ".jarvis" / "interrupt_test_write.txt"
    tool = WriteFileTool()

    with patch("builtins.input", side_effect=_interrupting_input):
        with pytest.raises(KeyboardInterrupt):
            tool.run(path=str(target), content="should never be written")

    # The interrupt must abort before the write happens - no partial file.
    assert not target.exists()


def test_write_file_tool_interrupt_does_not_create_file_even_if_preexisting_dir():
    scratch = JARVIS_ROOT / ".jarvis" / "interrupt_test_scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    target = scratch / "file.txt"
    tool = WriteFileTool()

    try:
        with patch("builtins.input", side_effect=_interrupting_input):
            with pytest.raises(KeyboardInterrupt):
                tool.run(path=str(target), content="nope")
        assert not target.exists()
    finally:
        if target.exists():
            target.unlink()
        scratch.rmdir()


# --- core/agent.py: interrupt during the step loop --------------------------


def _make_agent_with_llm(send_side_effect):
    llm = MagicMock()
    llm.send.side_effect = send_side_effect
    tools = ToolRegistry()
    return Agent(llm, tools)


def test_step_interrupt_during_llm_call_raises_step_interrupted():
    agent = _make_agent_with_llm(send_side_effect=KeyboardInterrupt)
    history = []

    with pytest.raises(StepInterrupted):
        agent.step(history, "do something")


def test_step_interrupt_rolls_back_history_to_checkpoint():
    agent = _make_agent_with_llm(send_side_effect=KeyboardInterrupt)
    history = [{"role": "user", "content": "earlier turn"}]

    with pytest.raises(StepInterrupted):
        agent.step(history, "this turn gets interrupted")

    # Only the pre-existing turn survives; the interrupted user message and
    # any partial assistant response must not linger in history.
    assert history == [{"role": "user", "content": "earlier turn"}]


def test_step_interrupt_during_tool_execution_rolls_back_assistant_message():
    # Simulate: first LLM call returns a tool_use response, then executing
    # the tool raises KeyboardInterrupt (e.g. user hit Ctrl+C at an
    # approval prompt inside the tool).
    tool_use_block = MagicMock()
    tool_use_block.type = "tool_use"
    tool_use_block.name = "interrupting_tool"
    tool_use_block.id = "tool_1"
    tool_use_block.input = {}
    tool_use_block.model_dump.return_value = {"type": "tool_use", "name": "interrupting_tool"}

    response = MagicMock()
    response.content = [tool_use_block]
    response.stop_reason = "tool_use"

    llm = MagicMock()
    llm.send.return_value = response

    class InterruptingTool(Tool):
        name = "interrupting_tool"
        description = "test tool that raises KeyboardInterrupt"
        input_schema = {"type": "object", "properties": {}}

        def run(self, **kwargs) -> ToolResult:
            raise KeyboardInterrupt

    tools = ToolRegistry()
    tools.register(InterruptingTool())

    agent = Agent(llm, tools)
    history = [{"role": "user", "content": "prior turn"}]

    with pytest.raises(StepInterrupted):
        agent.step(history, "trigger the interrupting tool")

    assert history == [{"role": "user", "content": "prior turn"}]


def test_step_completes_normally_when_not_interrupted():
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "all good"
    text_block.model_dump.return_value = {"type": "text", "text": "all good"}

    response = MagicMock()
    response.content = [text_block]
    response.stop_reason = "end_turn"

    llm = MagicMock()
    llm.send.return_value = response

    agent = Agent(llm, ToolRegistry())
    history = []

    result = agent.step(history, "hello")
    assert result == "all good"
    assert len(history) == 2  # user turn + assistant turn, both retained
