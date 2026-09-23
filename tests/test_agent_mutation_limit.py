"""Tests for jarvis.core.agent's mutation-count safety limit
(MAX_MUTATIONS_PER_STEP): the concrete guard against an unsupervised
"fix -> test -> fix -> test -> ..." loop, or any other runaway chain of
mutating tool calls within a single step(). Counts mutating tool calls
(file-mutating tools and run_command) across the whole step(), including
across multiple LLM round-trips, not just within one assistant response.
Once the limit is reached, a system note is appended telling the model to
stop and check in with the user instead of continuing on its own. No real
API calls - mirrors test_agent_stale_context_note.py's mocking approach."""

from __future__ import annotations

from unittest.mock import MagicMock

from jarvis.core.agent import (
    MAX_MUTATIONS_PER_STEP,
    STALE_CONTEXT_NOTE_PREFIX,
    Agent,
)
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


def _all_note_texts(history: list) -> list[str]:
    notes = []
    for turn in history:
        if turn["role"] != "user" or not isinstance(turn["content"], list):
            continue
        notes.extend(_text_blocks(turn))
    return notes


# --- under the limit: no mutation-limit note, ordinary staleness note only --------


def test_under_limit_no_mutation_limit_note():
    blocks = [_tool_use_block("t1", "write_file", {"path": "a.txt"})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_StubTool("write_file"))

    history: list = []
    Agent(llm, tools).step(history, "make one change")

    turn = _last_user_turn(history)
    texts = _text_blocks(turn)
    assert len(texts) == 1
    assert "reaching the safety limit" not in texts[0]


# --- reaching the limit within multiple LLM round-trips ----------------------------


def test_limit_reached_across_multiple_round_trips_triggers_note():
    # One mutating tool call per LLM round-trip, repeated until the limit
    # is reached, then a final plain-text response.
    responses = []
    for i in range(MAX_MUTATIONS_PER_STEP):
        responses.append(_tool_use_response([_tool_use_block(f"t{i}", "write_file", {"path": f"{i}.txt"})]))
    responses.append(_text_response("done"))

    llm = MagicMock()
    llm.send.side_effect = responses

    tools = ToolRegistry()
    tools.register(_StubTool("write_file"))

    history: list = []
    Agent(llm, tools).step(history, "make many changes")

    all_notes = _all_note_texts(history)
    limit_notes = [n for n in all_notes if "reaching the safety limit" in n]
    assert len(limit_notes) == 1
    assert STALE_CONTEXT_NOTE_PREFIX in limit_notes[0]
    assert str(MAX_MUTATIONS_PER_STEP) in limit_notes[0]


def test_note_appears_only_once_the_limit_is_actually_reached():
    # One fewer mutation than the limit - the limit note must NOT appear
    # even on the very last mutating call before the limit.
    responses = []
    for i in range(MAX_MUTATIONS_PER_STEP - 1):
        responses.append(_tool_use_response([_tool_use_block(f"t{i}", "write_file", {"path": f"{i}.txt"})]))
    responses.append(_text_response("done"))

    llm = MagicMock()
    llm.send.side_effect = responses

    tools = ToolRegistry()
    tools.register(_StubTool("write_file"))

    history: list = []
    Agent(llm, tools).step(history, "make almost-too-many changes")

    all_notes = _all_note_texts(history)
    limit_notes = [n for n in all_notes if "reaching the safety limit" in n]
    assert limit_notes == []


# --- reaching the limit within a single assistant response (multiple blocks) ------


def test_limit_reached_within_a_single_response_with_multiple_tool_use_blocks():
    blocks = [
        _tool_use_block(f"t{i}", "write_file", {"path": f"{i}.txt"})
        for i in range(MAX_MUTATIONS_PER_STEP)
    ]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_StubTool("write_file"))

    history: list = []
    Agent(llm, tools).step(history, "make many changes in one go")

    turn = _last_user_turn(history)
    texts = _text_blocks(turn)
    assert any("reaching the safety limit" in t for t in texts)


# --- exceeding the limit further still only produces one note per response --------


def test_exceeding_limit_still_only_one_note_per_turn():
    blocks = [
        _tool_use_block(f"t{i}", "write_file", {"path": f"{i}.txt"})
        for i in range(MAX_MUTATIONS_PER_STEP + 3)
    ]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_StubTool("write_file"))

    history: list = []
    Agent(llm, tools).step(history, "way too many changes")

    turn = _last_user_turn(history)
    limit_notes = [t for t in _text_blocks(turn) if "reaching the safety limit" in t]
    assert len(limit_notes) == 1


# --- only mutating calls count toward the limit -------------------------------------


def test_read_only_calls_do_not_count_toward_the_limit():
    # MAX_MUTATIONS_PER_STEP read-only calls, followed by well under the
    # limit worth of mutating calls - must not trigger the note.
    blocks = [
        _tool_use_block(f"r{i}", "get_workflow_state", {"stage": "scan"})
        for i in range(MAX_MUTATIONS_PER_STEP)
    ] + [_tool_use_block("m1", "write_file", {"path": "a.txt"})]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_StubTool("get_workflow_state"))
    tools.register(_StubTool("write_file"))

    history: list = []
    Agent(llm, tools).step(history, "read a lot, write once")

    turn = _last_user_turn(history)
    limit_notes = [t for t in _text_blocks(turn) if "reaching the safety limit" in t]
    assert limit_notes == []


def test_failed_mutating_calls_do_not_count_toward_the_limit():
    blocks = [
        _tool_use_block(f"f{i}", "write_file", {"path": f"{i}.txt"})
        for i in range(MAX_MUTATIONS_PER_STEP)
    ]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_StubTool("write_file", ok=False))  # every call fails

    history: list = []
    Agent(llm, tools).step(history, "attempt many changes, all denied")

    turn = _last_user_turn(history)
    limit_notes = [t for t in _text_blocks(turn) if "reaching the safety limit" in t]
    assert limit_notes == []


# --- a fresh step() gets a fresh count ------------------------------------------------


def test_mutation_count_resets_between_separate_steps():
    # First step() uses up the whole limit; a second, independent step()
    # must not inherit that count - each user turn gets its own budget.
    first_blocks = [
        _tool_use_block(f"a{i}", "write_file", {"path": f"{i}.txt"})
        for i in range(MAX_MUTATIONS_PER_STEP)
    ]
    second_blocks = [_tool_use_block("b1", "write_file", {"path": "next.txt"})]

    llm = MagicMock()
    llm.send.side_effect = [
        _tool_use_response(first_blocks),
        _text_response("first turn done"),
        _tool_use_response(second_blocks),
        _text_response("second turn done"),
    ]

    tools = ToolRegistry()
    tools.register(_StubTool("write_file"))

    history: list = []
    agent = Agent(llm, tools)
    agent.step(history, "first turn: many changes")
    agent.step(history, "second turn: one more change")

    # Only the tool_result+note message from the SECOND step should be
    # checked - it must not carry a limit note, since its own count (1)
    # never reached the limit.
    second_turn = _last_user_turn(history)
    limit_notes = [t for t in _text_blocks(second_turn) if "reaching the safety limit" in t]
    assert limit_notes == []


# --- run_command counts toward the limit too -----------------------------------------


def test_run_command_counts_toward_the_limit():
    blocks = [
        _tool_use_block(f"c{i}", "run_command", {"command": "git-add", "args": [f"{i}.txt"]})
        for i in range(MAX_MUTATIONS_PER_STEP)
    ]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_StubTool("run_command"))

    history: list = []
    Agent(llm, tools).step(history, "run many commands")

    turn = _last_user_turn(history)
    limit_notes = [t for t in _text_blocks(turn) if "reaching the safety limit" in t]
    assert len(limit_notes) == 1


# --- the note instructs stopping and checking in, not another fix -------------------


def test_limit_note_instructs_stopping_and_asking_the_user():
    blocks = [
        _tool_use_block(f"t{i}", "write_file", {"path": f"{i}.txt"})
        for i in range(MAX_MUTATIONS_PER_STEP)
    ]
    llm = MagicMock()
    llm.send.side_effect = [_tool_use_response(blocks), _text_response("done")]

    tools = ToolRegistry()
    tools.register(_StubTool("write_file"))

    history: list = []
    Agent(llm, tools).step(history, "make many changes")

    turn = _last_user_turn(history)
    note = next(t for t in _text_blocks(turn) if "reaching the safety limit" in t)
    assert "Stop making further changes on your own" in note
    assert "ask the user" in note
