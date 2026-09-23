"""Tests for jarvis.core.agent's stale-context note: after a successful
call to any file-mutating tool or run_command, a short system note is
appended to the SAME user message as the tool_results (not a separate
turn), telling the model that anything it learned about the project's
state before this point may now be out of date. Read-only tools
(get_workflow_state, read_file, etc.) never trigger it. No real API
calls - mirrors test_agent_multi_tool_and_errors.py's mocking approach."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from jarvis.core.agent import STALE_CONTEXT_NOTE_PREFIX, Agent
from jarvis.session.trim import _logical_turn_boundaries
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


class _StubTool(Tool):
    """A stand-in tool registered under whatever name a test needs
    (write_file, run_command, get_workflow_state, ...), always succeeding,
    so tests can exercise _run_loop's mutation classification purely by
    name, without depending on the real fs/shell tools' own logic."""

    def __init__(self, name: str, ok: bool = True):
        self.name = name
        self.description = "stub"
        self.input_schema = {"type": "object", "properties": {}}
        self._ok = ok

    def run(self, **kwargs) -> ToolResult:
        return ToolResult(ok=self._ok, output="stub result")


def _last_user_turn(history: list) -> dict:
    return next(
        turn for turn in reversed(history)
        if turn["role"] == "user" and isinstance(turn["content"], list)
    )


def _text_blocks(turn: dict) -> list[str]:
    return [b["text"] for b in turn["content"] if b.get("type") == "text"]


# --- file-mutating tools trigger the note -----------------------------------------


@pytest.mark.parametrize(
    "tool_name",
    [
        "write_file",
        "append_to_file",
        "delete_file",
        "create_directory",
        "move_file",
        "replace_in_file",
        "edit_file_lines",
    ],
)
def test_successful_file_mutating_tool_triggers_note(tool_name):
    blocks = [_tool_use_block("t1", tool_name, {"path": "x.txt"})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_StubTool(tool_name))

    history: list = []
    Agent(llm, tools).step(history, "do something")

    turn = _last_user_turn(history)
    texts = _text_blocks(turn)
    assert any(STALE_CONTEXT_NOTE_PREFIX in t for t in texts)
    assert any(tool_name in t for t in texts)


def test_run_command_triggers_note():
    blocks = [_tool_use_block("t1", "run_command", {"command": "git-add", "args": ["x.txt"]})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_StubTool("run_command"))

    history: list = []
    Agent(llm, tools).step(history, "stage the file")

    turn = _last_user_turn(history)
    assert any(STALE_CONTEXT_NOTE_PREFIX in t for t in _text_blocks(turn))


# --- read-only tools never trigger the note --------------------------------------


@pytest.mark.parametrize(
    "tool_name", ["get_workflow_state", "read_file", "list_directory", "search_files", "manage_tasks", "create_plan"]
)
def test_read_only_tool_never_triggers_note(tool_name):
    blocks = [_tool_use_block("t1", tool_name, {})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_StubTool(tool_name))

    history: list = []
    Agent(llm, tools).step(history, "check something")

    turn = _last_user_turn(history)
    assert _text_blocks(turn) == []


# --- a failed mutating call does not trigger the note -----------------------------


def test_failed_write_file_call_does_not_trigger_note():
    blocks = [_tool_use_block("t1", "write_file", {"path": "x.txt"})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_StubTool("write_file", ok=False))

    history: list = []
    Agent(llm, tools).step(history, "write a file")

    turn = _last_user_turn(history)
    assert _text_blocks(turn) == []


def test_unknown_tool_named_like_a_mutating_tool_does_not_trigger_note():
    # An unregistered tool never actually runs - nothing was mutated -
    # so no note should appear even if the requested name matches a
    # known mutating tool name.
    blocks = [_tool_use_block("t1", "write_file", {"path": "x.txt"})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    history: list = []
    Agent(llm, ToolRegistry()).step(history, "write a file")  # empty registry

    turn = _last_user_turn(history)
    assert _text_blocks(turn) == []


def test_exception_raising_mutating_tool_does_not_trigger_note():
    class _RaisingTool(Tool):
        name = "write_file"
        description = "raises"
        input_schema = {"type": "object", "properties": {}}

        def run(self, **kwargs) -> ToolResult:
            raise RuntimeError("boom")

    blocks = [_tool_use_block("t1", "write_file", {"path": "x.txt"})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_RaisingTool())

    history: list = []
    Agent(llm, tools).step(history, "write a file")

    turn = _last_user_turn(history)
    assert _text_blocks(turn) == []


# --- the note is appended to the SAME message as tool_results, not a new turn -----


def test_note_is_in_same_message_as_tool_result_not_a_separate_turn():
    blocks = [_tool_use_block("t1", "write_file", {"path": "x.txt"})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_StubTool("write_file"))

    history: list = []
    Agent(llm, tools).step(history, "write a file")

    turn = _last_user_turn(history)
    types = [b.get("type") for b in turn["content"]]
    assert "tool_result" in types
    assert "text" in types


def test_logical_turn_pairing_unaffected_by_the_note():
    # trim_history()'s pairing logic must still see the tool_use assistant
    # turn immediately followed by exactly one user turn (now containing
    # both the tool_result and the note) - not split into two turns.
    blocks = [_tool_use_block("t1", "write_file", {"path": "x.txt"})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_StubTool("write_file"))

    history: list = []
    Agent(llm, tools).step(history, "write a file")

    boundaries = _logical_turn_boundaries(history)
    # user_input turn, then (assistant tool_use + user tool_result+note) as
    # one paired boundary, then the final assistant text turn.
    sizes = [end - start for start, end in boundaries]
    assert 2 in sizes  # the paired tool_use/tool_result(+note) boundary


# --- multiple tool calls in one turn: note triggers if ANY of them mutated --------


def test_note_triggers_if_any_call_in_the_turn_mutated():
    blocks = [
        _tool_use_block("t1", "get_workflow_state", {"stage": "scan"}),
        _tool_use_block("t2", "write_file", {"path": "x.txt"}),
    ]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_StubTool("get_workflow_state"))
    tools.register(_StubTool("write_file"))

    history: list = []
    Agent(llm, tools).step(history, "check and write")

    turn = _last_user_turn(history)
    assert any(STALE_CONTEXT_NOTE_PREFIX in t for t in _text_blocks(turn))


def test_only_one_note_appended_even_with_multiple_mutating_calls():
    blocks = [
        _tool_use_block("t1", "write_file", {"path": "a.txt"}),
        _tool_use_block("t2", "write_file", {"path": "b.txt"}),
    ]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_StubTool("write_file"))

    history: list = []
    Agent(llm, tools).step(history, "write two files")

    turn = _last_user_turn(history)
    note_blocks = [t for t in _text_blocks(turn) if STALE_CONTEXT_NOTE_PREFIX in t]
    assert len(note_blocks) == 1


# --- a subsequent turn proceeds normally after a note was added -------------------


def test_conversation_continues_normally_after_a_note():
    blocks = [_tool_use_block("t1", "write_file", {"path": "x.txt"})]
    llm = MagicMock()
    llm.send.side_effect = [
        _tool_use_response(blocks),
        _text_response("wrote it"),
        _text_response("second turn response"),
    ]

    tools = ToolRegistry()
    tools.register(_StubTool("write_file"))

    history: list = []
    Agent(llm, tools).step(history, "write a file")
    result = Agent(llm, tools).step(history, "what about now")

    assert result == "second turn response"
