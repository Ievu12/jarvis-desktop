"""Tests for jarvis.core.agent's tool-call audit logging: every tool the
agent decides to invoke - not just ones behind a confirm_side_effect
approval prompt - is recorded via jarvis.core.audit.log_event("tool_call",
...), so the audit trail shows what the agent decided to check or do, not
only what it got approval for. Uses an isolated AUDIT_LOG_FILE (never the
real project's log) and mocked LLM/tool responses, mirroring
test_agent_multi_tool_and_errors.py's approach - no real API calls."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from jarvis.core import audit
from jarvis.core.agent import Agent
from jarvis.tools.base import Tool, ToolRegistry, ToolResult


@pytest.fixture
def isolated_audit(tmp_path, monkeypatch):
    # jarvis.core.agent does `from jarvis.core.audit import log_event`, so
    # it holds its own reference to the function object - patching
    # audit.AUDIT_LOG_FILE is enough because log_event() reads that module
    # attribute by name at call time, not a value captured at import time.
    log_path = tmp_path / "audit.log"
    monkeypatch.setattr(audit, "AUDIT_LOG_FILE", log_path)
    return log_path


def _read_entries(log_path) -> list[dict]:
    if not log_path.exists():
        return []
    entries = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


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


class _FailingResultTool(Tool):
    name = "sometimes_fails"
    description = "returns ok=False"
    input_schema = {"type": "object", "properties": {}}

    def run(self, **kwargs) -> ToolResult:
        return ToolResult(ok=False, output="denied for some reason")


class _RaisingTool(Tool):
    name = "buggy_tool"
    description = "raises an unexpected exception"
    input_schema = {"type": "object", "properties": {}}

    def run(self, **kwargs) -> ToolResult:
        raise RuntimeError("simulated bug")


# --- a successful tool call is logged ------------------------------------------


def test_successful_tool_call_logged(isolated_audit):
    blocks = [_tool_use_block("t1", "echo", {"path": "x.txt"})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_EchoTool())

    Agent(llm, tools).step([], "call echo")

    entries = _read_entries(isolated_audit)
    tool_calls = [e for e in entries if e["event"] == "tool_call"]
    assert len(tool_calls) == 1
    assert tool_calls[0]["tool"] == "echo"
    assert tool_calls[0]["ok"] is True


def test_successful_tool_call_logged_input(isolated_audit):
    blocks = [_tool_use_block("t1", "echo", {"path": "x.txt", "stage": "scan"})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_EchoTool())

    Agent(llm, tools).step([], "call echo")

    entries = _read_entries(isolated_audit)
    tool_call = next(e for e in entries if e["event"] == "tool_call")
    assert tool_call["input"] == {"path": "x.txt", "stage": "scan"}


# --- a tool returning ok=False is logged with ok=False --------------------------


def test_tool_result_failure_logged_as_not_ok(isolated_audit):
    blocks = [_tool_use_block("t1", "sometimes_fails", {})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_FailingResultTool())

    Agent(llm, tools).step([], "call the failing tool")

    entries = _read_entries(isolated_audit)
    tool_call = next(e for e in entries if e["event"] == "tool_call")
    assert tool_call["tool"] == "sometimes_fails"
    assert tool_call["ok"] is False


# --- unknown tool name is logged too ---------------------------------------------


def test_unknown_tool_call_logged(isolated_audit):
    blocks = [_tool_use_block("t1", "does_not_exist", {"a": 1})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("handled")]

    Agent(llm, ToolRegistry()).step([], "call a missing tool")

    entries = _read_entries(isolated_audit)
    tool_call = next(e for e in entries if e["event"] == "tool_call")
    assert tool_call["tool"] == "does_not_exist"
    assert tool_call["ok"] is False
    assert "Unknown tool" in tool_call["error"]


# --- a tool raising an exception is logged as not ok ------------------------------


def test_raising_tool_logged_as_not_ok(isolated_audit):
    blocks = [_tool_use_block("t1", "buggy_tool", {})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("recovered")]

    tools = ToolRegistry()
    tools.register(_RaisingTool())

    Agent(llm, tools).step([], "trigger the bug")

    entries = _read_entries(isolated_audit)
    tool_call = next(e for e in entries if e["event"] == "tool_call")
    assert tool_call["ok"] is False


# --- multiple tool calls in one turn each get their own entry ---------------------


def test_multiple_tool_calls_each_logged_separately(isolated_audit):
    blocks = [
        _tool_use_block("t1", "echo", {"which": "first"}),
        _tool_use_block("t2", "echo", {"which": "second"}),
    ]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_EchoTool())

    Agent(llm, tools).step([], "call echo twice")

    entries = _read_entries(isolated_audit)
    tool_calls = [e for e in entries if e["event"] == "tool_call"]
    assert len(tool_calls) == 2
    inputs = {json.dumps(e["input"], sort_keys=True) for e in tool_calls}
    assert json.dumps({"which": "first"}, sort_keys=True) in inputs
    assert json.dumps({"which": "second"}, sort_keys=True) in inputs


# --- bulk text payload arguments are summarized, not stored verbatim, in the audit log --


def test_write_file_content_argument_replaced_with_size_summary(isolated_audit):
    blocks = [
        _tool_use_block(
            "t1", "echo", {"path": "notes.txt", "content": "a" * 5000}
        )
    ]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_EchoTool())

    Agent(llm, tools).step([], "write a big file")

    entries = _read_entries(isolated_audit)
    tool_call = next(e for e in entries if e["event"] == "tool_call")
    # The 5000 raw characters must never appear in the log...
    assert "a" * 5000 not in json.dumps(tool_call)
    # ...but a size summary showing how much changed must be present.
    assert tool_call["input"]["path"] == "notes.txt"
    assert "5000 char" in tool_call["input"]["content"]


@pytest.mark.parametrize("summarized_key", ["content", "new_content", "text", "replacement"])
def test_various_bulk_text_arguments_summarized_not_stored_verbatim(isolated_audit, summarized_key):
    blocks = [_tool_use_block("t1", "echo", {"path": "x.txt", summarized_key: "bulk text"})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_EchoTool())

    Agent(llm, tools).step([], "call with a bulk text arg")

    entries = _read_entries(isolated_audit)
    tool_call = next(e for e in entries if e["event"] == "tool_call")
    assert tool_call["input"][summarized_key] != "bulk text"
    assert "9 char" in tool_call["input"][summarized_key]


def test_bulk_text_summary_includes_line_count(isolated_audit):
    blocks = [_tool_use_block("t1", "echo", {"path": "x.txt", "content": "line1\nline2\nline3"})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_EchoTool())

    Agent(llm, tools).step([], "call with multi-line content")

    entries = _read_entries(isolated_audit)
    tool_call = next(e for e in entries if e["event"] == "tool_call")
    assert "3 line" in tool_call["input"]["content"]


def test_non_string_bulk_arg_value_left_untouched(isolated_audit):
    # Defensive: if a future tool ever passes a non-string value under one
    # of these argument names, summarization must not crash - it should
    # just pass the value through unchanged.
    blocks = [_tool_use_block("t1", "echo", {"path": "x.txt", "content": None})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_EchoTool())

    Agent(llm, tools).step([], "call with a null content arg")

    entries = _read_entries(isolated_audit)
    tool_call = next(e for e in entries if e["event"] == "tool_call")
    assert tool_call["input"]["content"] is None


# --- an interrupted call logs no tool_call entry (rolled back, not silently lost) --


def test_keyboard_interrupt_during_tool_run_logs_no_tool_call_entry(isolated_audit):
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

    from jarvis.core.agent import StepInterrupted

    with pytest.raises(StepInterrupted):
        Agent(llm, tools).step([], "trigger the interrupt")

    entries = _read_entries(isolated_audit)
    tool_calls = [e for e in entries if e["event"] == "tool_call"]
    assert tool_calls == []


# --- audit logging never changes the tool_result content seen by the model --------


def test_audit_logging_does_not_alter_tool_result_content(isolated_audit):
    blocks = [_tool_use_block("t1", "echo", {"a": 1})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_EchoTool())

    history: list = []
    Agent(llm, tools).step(history, "call echo")

    tool_result_message = next(
        turn for turn in history
        if turn["role"] == "user" and isinstance(turn["content"], list)
        and turn["content"] and turn["content"][0].get("type") == "tool_result"
    )
    assert "echoed: {'a': 1}" in tool_result_message["content"][0]["content"]
