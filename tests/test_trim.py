"""Tests for jarvis.session.trim: history trimming must never split a
tool_use message from its paired tool_result message, must preserve the
most recent context, and must leave a visible marker when it trims."""

from __future__ import annotations

from jarvis.session.trim import TRIM_MARKER_PREFIX, trim_history


def _user(text: str) -> dict:
    return {"role": "user", "content": text}


def _assistant_text(text: str) -> dict:
    return {"role": "assistant", "content": [{"type": "text", "text": text}]}


def _assistant_tool_use(tool_id: str, name: str = "read_file") -> dict:
    return {
        "role": "assistant",
        "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": {}}],
    }


def _tool_result(tool_id: str, content: str = "result") -> dict:
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content}],
    }


# --- basic behavior ---------------------------------------------------------


def test_empty_history_returns_empty():
    assert trim_history([]) == []


def test_small_history_under_budget_is_unchanged():
    history = [_user("hi"), _assistant_text("hello")]
    result = trim_history(history, max_chars=10_000)
    assert result == history


def test_small_history_is_not_the_same_object():
    # trim_history must not mutate or alias the input list.
    history = [_user("hi")]
    result = trim_history(history, max_chars=10_000)
    assert result is not history


def test_original_history_list_untouched():
    history = [_user(f"message {i}") for i in range(50)]
    original_copy = list(history)
    trim_history(history, max_chars=50)
    assert history == original_copy


# --- trimming behavior -------------------------------------------------------


def test_trims_when_over_budget():
    history = [_user(f"message number {i} with some padding text") for i in range(200)]
    result = trim_history(history, max_chars=500)
    assert len(result) < len(history)


def test_trim_keeps_most_recent_turn():
    history = [_user(f"old message {i}") for i in range(100)]
    history.append(_user("the very last and most recent message"))
    result = trim_history(history, max_chars=100)
    assert result[-1] == history[-1]


def test_trim_inserts_marker_when_trimming_occurs():
    history = [_user(f"padding message {i} " * 5) for i in range(100)]
    result = trim_history(history, max_chars=200)
    assert result[0]["role"] == "user"
    assert isinstance(result[0]["content"], str)
    assert result[0]["content"].startswith(TRIM_MARKER_PREFIX)


def test_no_marker_inserted_when_nothing_trimmed():
    history = [_user("short")]
    result = trim_history(history, max_chars=10_000)
    assert not any(
        isinstance(t.get("content"), str) and t["content"].startswith(TRIM_MARKER_PREFIX)
        for t in result
    )


def test_at_least_one_logical_turn_always_survives_even_if_oversized():
    # A single huge turn that alone exceeds the budget must still be kept -
    # trimming it away would destroy the turn currently being responded to.
    huge_turn = _user("x" * 10_000)
    history = [_user("older, smaller message"), huge_turn]
    result = trim_history(history, max_chars=10)
    assert result[-1] == huge_turn


# --- tool_use / tool_result pairing must never be split ---------------------


def test_tool_use_and_tool_result_pair_trimmed_together():
    history = [_user("old context")] * 50
    history += [_assistant_tool_use("t1"), _tool_result("t1")]
    history.append(_user("recent question"))

    result = trim_history(history, max_chars=120)

    # If the pair survives at all, both halves must be present together -
    # never just the tool_use or just the tool_result.
    has_tool_use = any(
        isinstance(t.get("content"), list)
        and any(b.get("type") == "tool_use" for b in t["content"])
        for t in result
    )
    has_tool_result = any(
        isinstance(t.get("content"), list)
        and any(b.get("type") == "tool_result" for b in t["content"])
        for t in result
    )
    assert has_tool_use == has_tool_result


def test_tool_use_pair_never_split_across_many_trims():
    # Build a long history of alternating plain turns and tool_use/result
    # pairs, then trim aggressively and check pairing integrity throughout.
    history = []
    for i in range(30):
        history.append(_user(f"question {i}"))
        history.append(_assistant_tool_use(f"tool_{i}"))
        history.append(_tool_result(f"tool_{i}", f"result {i}"))

    result = trim_history(history, max_chars=300)

    open_tool_use_ids = set()
    for turn in result:
        content = turn.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if block.get("type") == "tool_use":
                open_tool_use_ids.add(block["id"])
            elif block.get("type") == "tool_result":
                tool_id = block["tool_use_id"]
                assert tool_id in open_tool_use_ids, (
                    f"tool_result for {tool_id} appears without its tool_use"
                )
                open_tool_use_ids.discard(tool_id)

    # Every tool_use that survived trimming must have had its result kept too.
    assert open_tool_use_ids == set()


def test_trimmed_result_fits_within_budget_including_marker():
    from jarvis.session.trim import _turn_chars

    history = [_user(f"message {i}") for i in range(500)]
    result = trim_history(history, max_chars=1000)
    total = sum(_turn_chars(t) for t in result)
    assert total <= 1000


def test_repeated_trimming_is_idempotent_once_under_budget():
    history = [_user(f"message {i}") for i in range(500)]
    once = trim_history(history, max_chars=1000)
    twice = trim_history(once, max_chars=1000)
    assert once == twice
