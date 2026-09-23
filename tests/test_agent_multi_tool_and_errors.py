"""Tests for jarvis.core.agent covering paths that were never directly
exercised: multiple tool_use blocks in a single assistant turn, an
unknown/unregistered tool name, and a tool that raises an unexpected
plain Exception (not KeyboardInterrupt). The last case is a regression
test for a real bug: before the fix, such an exception propagated out of
_run_loop with an assistant tool_use message already appended to history
but no matching tool_result ever added, leaving history permanently
corrupted (an orphaned tool_use) for the rest of the process, since only
KeyboardInterrupt triggered a rollback in step()."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jarvis.core.agent import Agent, StepInterrupted
from jarvis.tools.base import Tool, ToolRegistry, ToolResult


def _tool_use_block(tool_id: str, name: str, input_: dict | None = None):
    block = MagicMock()
    block.type = "tool_use"
    block.id = tool_id
    block.name = name
    block.input = input_ or {}
    block.model_dump.return_value = {
        "type": "tool_use",
        "id": tool_id,
        "name": name,
        "input": input_ or {},
    }
    return block


def _tool_use_response(blocks: list):
    response = MagicMock()
    response.content = blocks
    response.stop_reason = "tool_use"
    return response


def _text_response(text: str):
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text
    text_block.model_dump.return_value = {"type": "text", "text": text}

    response = MagicMock()
    response.content = [text_block]
    response.stop_reason = "end_turn"
    return response


class _EchoTool(Tool):
    name = "echo"
    description = "returns its input as output"
    input_schema = {"type": "object", "properties": {}}

    def run(self, **kwargs) -> ToolResult:
        return ToolResult(ok=True, output=f"echoed: {kwargs}")


class _FailingTool(Tool):
    name = "buggy_tool"
    description = "raises an unexpected exception"
    input_schema = {"type": "object", "properties": {}}

    def run(self, **kwargs) -> ToolResult:
        raise RuntimeError("simulated bug inside a tool")


# --- multiple tool calls in a single turn -----------------------------------


def test_multiple_tool_calls_in_one_turn_all_execute():
    blocks = [
        _tool_use_block("t1", "echo", {"a": 1}),
        _tool_use_block("t2", "echo", {"b": 2}),
    ]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_EchoTool())

    agent = Agent(llm, tools)
    history = []
    result = agent.step(history, "call echo twice")

    assert result == "done"
    # Find the tool_result message and confirm both tool_use_ids are present.
    tool_result_message = next(
        turn for turn in history
        if turn["role"] == "user" and isinstance(turn["content"], list)
        and turn["content"] and turn["content"][0].get("type") == "tool_result"
    )
    ids = {block["tool_use_id"] for block in tool_result_message["content"]}
    assert ids == {"t1", "t2"}


def test_multiple_tool_calls_results_correctly_paired_by_id():
    blocks = [
        _tool_use_block("t1", "echo", {"which": "first"}),
        _tool_use_block("t2", "echo", {"which": "second"}),
    ]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_EchoTool())

    agent = Agent(llm, tools)
    history = []
    agent.step(history, "call echo twice")

    tool_result_message = next(
        turn for turn in history
        if turn["role"] == "user" and isinstance(turn["content"], list)
        and turn["content"] and turn["content"][0].get("type") == "tool_result"
    )
    by_id = {b["tool_use_id"]: b["content"] for b in tool_result_message["content"]}
    assert "first" in by_id["t1"]
    assert "second" in by_id["t2"]


def test_mixed_known_and_unknown_tool_in_same_turn():
    blocks = [
        _tool_use_block("t1", "echo", {}),
        _tool_use_block("t2", "nonexistent_tool", {}),
    ]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_EchoTool())

    agent = Agent(llm, tools)
    history = []
    agent.step(history, "call two tools")

    tool_result_message = next(
        turn for turn in history
        if turn["role"] == "user" and isinstance(turn["content"], list)
        and turn["content"] and turn["content"][0].get("type") == "tool_result"
    )
    by_id = {b["tool_use_id"]: b for b in tool_result_message["content"]}
    assert by_id["t1"]["is_error"] is False
    assert by_id["t2"]["is_error"] is True
    assert "Unknown tool" in by_id["t2"]["content"]


# --- unknown tool name --------------------------------------------------------


def test_unknown_tool_name_reports_error_without_crashing():
    blocks = [_tool_use_block("t1", "does_not_exist", {})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("handled")]

    agent = Agent(llm, ToolRegistry())  # empty registry
    history = []
    result = agent.step(history, "call a tool that doesn't exist")

    assert result == "handled"
    tool_result_message = history[-2]  # assistant tool_use is history[-3], this is the result
    assert tool_result_message["content"][0]["is_error"] is True
    assert "Unknown tool: does_not_exist" in tool_result_message["content"][0]["content"]


# --- tool raising a plain exception (regression test for the history-corruption bug) ---


def test_tool_raising_exception_does_not_crash_the_step():
    blocks = [_tool_use_block("t1", "buggy_tool", {})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("recovered")]

    tools = ToolRegistry()
    tools.register(_FailingTool())

    agent = Agent(llm, tools)
    history = []
    result = agent.step(history, "call the buggy tool")

    assert result == "recovered"  # the loop continued past the exception


def test_tool_raising_exception_reports_as_error_tool_result():
    blocks = [_tool_use_block("t1", "buggy_tool", {})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("recovered")]

    tools = ToolRegistry()
    tools.register(_FailingTool())

    agent = Agent(llm, tools)
    history = []
    agent.step(history, "call the buggy tool")

    tool_result_message = next(
        turn for turn in history
        if turn["role"] == "user" and isinstance(turn["content"], list)
        and turn["content"] and turn["content"][0].get("type") == "tool_result"
    )
    result_block = tool_result_message["content"][0]
    assert result_block["is_error"] is True
    assert "unexpected error" in result_block["content"].lower()
    assert "simulated bug inside a tool" in result_block["content"]


def test_tool_raising_exception_does_not_leave_orphaned_tool_use_in_history():
    # The actual regression: before the fix, an exception here left the
    # assistant's tool_use message in history with no matching
    # tool_result ever appended - every tool_use in history must have a
    # corresponding tool_result by the time step() returns.
    blocks = [_tool_use_block("t1", "buggy_tool", {})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("recovered")]

    tools = ToolRegistry()
    tools.register(_FailingTool())

    agent = Agent(llm, tools)
    history = []
    agent.step(history, "call the buggy tool")

    tool_use_ids = set()
    tool_result_ids = set()
    for turn in history:
        content = turn.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "tool_use":
                tool_use_ids.add(block["id"])
            elif block.get("type") == "tool_result":
                tool_result_ids.add(block["tool_use_id"])

    assert tool_use_ids == tool_result_ids


def test_tool_raising_exception_allows_subsequent_steps_to_work_normally():
    # Confirms the session isn't left in a broken state for future turns -
    # a second, unrelated step() call after the exception must work fine.
    blocks = [_tool_use_block("t1", "buggy_tool", {})]
    llm = MagicMock()
    llm.send.side_effect = [
        _tool_use_response(blocks),
        _text_response("recovered from bug"),
        _text_response("second turn works fine"),
    ]

    tools = ToolRegistry()
    tools.register(_FailingTool())

    agent = Agent(llm, tools)
    history = []
    agent.step(history, "trigger the bug")
    result = agent.step(history, "a completely normal followup")

    assert result == "second turn works fine"


def test_keyboard_interrupt_from_tool_still_rolls_back_not_caught_as_plain_exception():
    # Confirms the fix's except Exception does NOT accidentally swallow
    # KeyboardInterrupt (it's a BaseException, not an Exception subclass,
    # but this is worth asserting explicitly rather than relying on that
    # being obviously true to every future reader/editor of this code).
    class _InterruptingTool(Tool):
        name = "interrupting_tool"
        description = "raises KeyboardInterrupt"
        input_schema = {"type": "object", "properties": {}}

        def run(self, **kwargs) -> ToolResult:
            raise KeyboardInterrupt

    blocks = [_tool_use_block("t1", "interrupting_tool", {})]
    llm = MagicMock()
    llm.send.return_value = _tool_use_response(blocks)

    tools = ToolRegistry()
    tools.register(_InterruptingTool())

    agent = Agent(llm, tools)
    history = [{"role": "user", "content": "earlier turn"}]

    with pytest.raises(StepInterrupted):
        agent.step(history, "trigger the interrupt")

    assert history == [{"role": "user", "content": "earlier turn"}]
