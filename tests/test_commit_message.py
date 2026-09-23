"""Tests for jarvis.core.commit_message.suggest_commit_message: a single,
tool-free, history-free LLM call suggesting a commit message for staged
changes. Purely advisory - never raises, always falls back to None on any
failure. No real API calls - LLMClient is mocked throughout."""

from __future__ import annotations

from unittest.mock import MagicMock

from jarvis.core.commit_message import suggest_commit_message


def _text_response(text: str):
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text

    response = MagicMock()
    response.content = [text_block]
    return response


# --- happy path -----------------------------------------------------------------


def test_returns_suggested_message():
    llm = MagicMock()
    llm.send.return_value = _text_response("Fix off-by-one error in loop")

    result = suggest_commit_message(llm, ["a.py"], "diff content")
    assert result == "Fix off-by-one error in loop"


def test_strips_whitespace_from_response():
    llm = MagicMock()
    llm.send.return_value = _text_response("  Add tests for parser  \n")

    result = suggest_commit_message(llm, ["a.py"], "diff content")
    assert result == "Add tests for parser"


def test_prompt_includes_staged_file_names():
    llm = MagicMock()
    llm.send.return_value = _text_response("message")

    suggest_commit_message(llm, ["src/foo.py", "src/bar.py"], "some diff")

    prompt = llm.send.call_args[0][0][0]["content"]
    assert "src/foo.py" in prompt
    assert "src/bar.py" in prompt


def test_prompt_includes_diff_content():
    llm = MagicMock()
    llm.send.return_value = _text_response("message")

    suggest_commit_message(llm, ["a.py"], "+added line\n-removed line")

    prompt = llm.send.call_args[0][0][0]["content"]
    assert "+added line" in prompt
    assert "-removed line" in prompt


def test_no_tools_offered():
    llm = MagicMock()
    llm.send.return_value = _text_response("message")

    suggest_commit_message(llm, ["a.py"], "diff")

    tools_arg = llm.send.call_args[0][1]
    assert tools_arg == []


def test_uses_isolated_system_prompt_not_the_main_agent_prompt():
    llm = MagicMock()
    llm.send.return_value = _text_response("message")

    suggest_commit_message(llm, ["a.py"], "diff")

    kwargs = llm.send.call_args[1]
    assert "system" in kwargs
    assert "JARVIS" not in kwargs["system"]  # not the conversational agent's prompt


def test_bounded_max_tokens():
    llm = MagicMock()
    llm.send.return_value = _text_response("message")

    suggest_commit_message(llm, ["a.py"], "diff")

    kwargs = llm.send.call_args[1]
    assert kwargs["max_tokens"] < 4096  # well under the main agent's budget


# --- diff truncation for very large diffs -----------------------------------------


def test_huge_diff_is_truncated_in_prompt():
    llm = MagicMock()
    llm.send.return_value = _text_response("message")

    huge_diff = "x" * 50_000
    suggest_commit_message(llm, ["a.py"], huge_diff)

    prompt = llm.send.call_args[0][0][0]["content"]
    assert len(prompt) < 50_000
    assert "truncated" in prompt.lower()


# --- missing diff handled gracefully -----------------------------------------------


def test_none_diff_does_not_crash():
    llm = MagicMock()
    llm.send.return_value = _text_response("message")

    result = suggest_commit_message(llm, ["a.py"], None)
    assert result == "message"


def test_empty_staged_files_returns_none_without_calling_llm():
    llm = MagicMock()
    result = suggest_commit_message(llm, [], "diff")
    assert result is None
    llm.send.assert_not_called()


# --- failure modes always return None, never raise ---------------------------------


def test_llm_exception_returns_none():
    llm = MagicMock()
    llm.send.side_effect = RuntimeError("API error")

    result = suggest_commit_message(llm, ["a.py"], "diff")
    assert result is None


def test_network_error_returns_none():
    llm = MagicMock()
    llm.send.side_effect = ConnectionError("network down")

    result = suggest_commit_message(llm, ["a.py"], "diff")
    assert result is None


def test_empty_response_text_returns_none():
    llm = MagicMock()
    llm.send.return_value = _text_response("")

    result = suggest_commit_message(llm, ["a.py"], "diff")
    assert result is None


def test_whitespace_only_response_returns_none():
    llm = MagicMock()
    llm.send.return_value = _text_response("   \n  ")

    result = suggest_commit_message(llm, ["a.py"], "diff")
    assert result is None


def test_response_with_no_text_blocks_returns_none():
    llm = MagicMock()
    response = MagicMock()
    response.content = []
    llm.send.return_value = response

    result = suggest_commit_message(llm, ["a.py"], "diff")
    assert result is None
