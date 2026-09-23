"""The orchestration loop: sends conversation to the LLM, executes any tool
calls it requests via the tool registry, feeds results back, repeats until
the model produces a final text response."""

from __future__ import annotations

from typing import Any

from jarvis.core.audit import log_event
from jarvis.core.llm import LLMClient
from jarvis.session.trim import trim_history
from jarvis.tools.base import ToolRegistry


class StepInterrupted(Exception):
    """Raised when Ctrl+C interrupts an in-progress step (LLM call, tool
    execution, or an approval prompt). The caller decides how to recover;
    history up to the last fully-completed exchange is left intact."""


# Argument names that carry bulk text payloads (a full file's new content,
# a replace/edit body) - never written to the audit log verbatim, since
# that would bloat the log and duplicate what's already visible in the
# tool_result within session history. Replaced with a short size summary
# instead of being dropped outright, so the audit trail still shows *how
# much* changed even though it deliberately doesn't store *what*.
_TOOL_INPUT_ARGS_TO_SUMMARIZE = {"content", "new_content", "text", "replacement"}


def _summarize_bulk_text_arg(value: Any) -> Any:
    if not isinstance(value, str):
        return value  # not text - leave whatever it is untouched
    line_count = value.count("\n") + 1 if value else 0
    return f"<{len(value)} char(s), {line_count} line(s)>"

# Tools that always write to the project's files (never read-only) - a
# successful call to any of these means anything the agent already knows
# about the project's state (from an earlier get_workflow_state/scan, or
# from its own reasoning) may now be stale.
_FILE_MUTATING_TOOLS = {
    "write_file",
    "append_to_file",
    "delete_file",
    "create_directory",
    "move_file",
    "replace_in_file",
    "edit_file_lines",
}

# run_command is the one tool whose effect depends on which logical
# command was requested (most of its allowlist - git-status/git-log/
# git-diff/ls/cat/pytest - is read-only). Conservative by design: treated
# as potentially state-changing whenever it succeeds, rather than
# duplicating shell.py's own allowlist logic here to distinguish the
# read-only entries. A false positive here only costs the model one extra,
# cheap get_workflow_state call - a false negative would let it act on
# state it silently no longer has right.
_RUN_COMMAND_TOOL = "run_command"

STALE_CONTEXT_NOTE_PREFIX = "[JARVIS session note] "

# Ceiling on how many mutating tool calls (file writes, git-init/-add/
# -commit, or any run_command) a single step() is allowed to make before
# the agent is told to stop and check in with the user, rather than keep
# trying more changes on its own - the concrete guard against an
# unsupervised "fix -> test -> fix -> test -> ..." loop. Counts across ALL
# mutating tools in the turn, not just file-write retries after a test
# failure - a simpler, more general safeguard than trying to detect that
# one specific pattern. Deliberately generous: a legitimate multi-step
# task (several files touched once each) should essentially never hit
# this, so it mainly catches genuine repeated-attempt loops.
MAX_MUTATIONS_PER_STEP = 8


def _build_stale_context_note(tool_name: str) -> str:
    return (
        f"{STALE_CONTEXT_NOTE_PREFIX}The '{tool_name}' action above just "
        "changed the project. Anything you learned about the project's "
        "state before this point (from an earlier get_workflow_state call, "
        "a file you read, or your own prior reasoning) may now be out of "
        "date. Re-check with get_workflow_state (or re-read the specific "
        "file) before relying on it again - don't assume it still holds."
    )


def _build_mutation_limit_note(count: int) -> str:
    return (
        f"{STALE_CONTEXT_NOTE_PREFIX}You have now made {count} changes to "
        "the project in this single turn, reaching the safety limit "
        f"({MAX_MUTATIONS_PER_STEP}). Stop making further changes on your "
        "own. Summarize what you've done and what still isn't resolved, "
        "and ask the user how they'd like to proceed - don't attempt "
        "another fix without them confirming first."
    )


def _summarize_tool_input(tool_input: dict[str, Any]) -> dict[str, Any]:
    """Reduce a tool call's arguments to a compact, audit-log-safe summary:
    keeps small/identifying arguments (paths, commands, flags, stage
    names) as-is, so the audit trail shows *what* was targeted, and
    replaces bulk text payloads with a short size summary (char/line
    count) rather than the raw text - large enough to see how much
    changed, small enough to never bloat the log or duplicate file
    content already visible in the session's own history.
    """
    return {
        k: (_summarize_bulk_text_arg(v) if k in _TOOL_INPUT_ARGS_TO_SUMMARIZE else v)
        for k, v in tool_input.items()
    }


class Agent:
    def __init__(self, llm: LLMClient, tools: ToolRegistry) -> None:
        self._llm = llm
        self._tools = tools

    def step(self, history: list[dict[str, Any]], user_input: str) -> str:
        # Trim before adding this turn, at a safe boundary where every
        # existing tool_use is already paired with its tool_result - never
        # mid-turn, where a partial pair could be split apart.
        trimmed = trim_history(history)
        history[:] = trimmed

        # Snapshot so an interrupt partway through this step can roll the
        # in-memory history back to the last fully-completed exchange,
        # rather than leaving a half-written turn (e.g. an assistant
        # message appended but its tool results never added).
        checkpoint = len(history)
        history.append({"role": "user", "content": user_input})

        try:
            return self._run_loop(history)
        except KeyboardInterrupt:
            del history[checkpoint:]
            raise StepInterrupted("Interrupted before this turn completed.")

    def _run_loop(self, history: list[dict[str, Any]]) -> str:
        # Counts mutating tool calls across the whole step() (i.e. across
        # every iteration of this while loop, not reset per iteration) -
        # this is what lets MAX_MUTATIONS_PER_STEP catch a loop that spans
        # several LLM round-trips within one user turn, not just repeated
        # calls within a single assistant response.
        mutation_count = 0

        while True:
            response = self._llm.send(history, self._tools.schemas())
            content_blocks = response.content

            # Store as plain dicts, not SDK objects (e.g. ThinkingBlock),
            # so history stays JSON-serializable for session persistence.
            serialized_blocks = [block.model_dump() for block in content_blocks]
            history.append({"role": "assistant", "content": serialized_blocks})

            if response.stop_reason != "tool_use":
                return "".join(
                    block.text for block in content_blocks if block.type == "text"
                )

            tool_results = []
            mutated_by: list[str] = []
            for block in content_blocks:
                if block.type != "tool_use":
                    continue
                tool = self._tools.get(block.name)
                if tool is None:
                    result_text = f"Unknown tool: {block.name}"
                    is_error = True
                    # Audited too, even though there's no Tool instance to
                    # run - a model requesting a nonexistent tool is itself
                    # a decision worth seeing in the audit trail.
                    log_event(
                        "tool_call",
                        tool=block.name,
                        input=_summarize_tool_input(block.input),
                        ok=False,
                        error=result_text,
                    )
                else:
                    # A KeyboardInterrupt raised here (e.g. during an
                    # approval prompt) is a BaseException, not an
                    # Exception, so it is NOT caught by the except below -
                    # it propagates up to step(), which rolls back the
                    # whole turn. It must never be swallowed into a
                    # normal tool result. Note this also means no
                    # "tool_call" audit entry is logged for an interrupted
                    # call - confirm_side_effect()/confirm_outside_sandbox()
                    # already log their own "interrupted" entry for that
                    # case, so the decision isn't lost, just recorded under
                    # the approval event instead of a tool_call one.
                    #
                    # An unexpected plain Exception (a tool bug, not user
                    # interrupt) IS caught here and converted into a
                    # normal error tool_result. Without this, such an
                    # exception would propagate past this loop with
                    # content_blocks already appended to history but no
                    # matching tool_results ever added - leaving history
                    # permanently corrupted (an orphaned tool_use with no
                    # tool_result) for the rest of the process, since
                    # step() only rolls back on KeyboardInterrupt.
                    try:
                        result = tool.run(**block.input)
                        result_text = result.output
                        is_error = not result.ok
                    except Exception as e:
                        result_text = f"Tool '{block.name}' raised an unexpected error: {e}"
                        is_error = True

                    # Logged for every tool the agent actually decided to
                    # call, not just ones with a side-effect approval
                    # prompt (confirm_side_effect/confirm_outside_sandbox
                    # already log those separately) - so a read-only call
                    # like get_workflow_state is visible in the audit trail
                    # too, answering "what did the agent decide to check or
                    # do", not only "what did it get approval for".
                    log_event(
                        "tool_call",
                        tool=block.name,
                        input=_summarize_tool_input(block.input),
                        ok=not is_error,
                    )

                    if not is_error and (
                        block.name in _FILE_MUTATING_TOOLS or block.name == _RUN_COMMAND_TOOL
                    ):
                        mutated_by.append(block.name)
                        mutation_count += 1

                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result_text,
                        "is_error": is_error,
                    }
                )

            turn_content: list[dict[str, Any]] = list(tool_results)
            if mutated_by:
                # Appended as an extra text block in the SAME user message
                # as the tool_results, not a separate turn - the Anthropic
                # API requires a tool_use-bearing assistant turn to be
                # followed immediately by a user turn containing only that
                # turn's tool_result blocks (trim_history() relies on this
                # same pairing), so a standalone note turn can't be
                # inserted here without breaking that pairing. Mixing a
                # text block alongside the tool_result blocks in one user
                # message is valid and keeps the note directly next to the
                # action that made it true.
                #
                # The mutation-limit note takes priority over the ordinary
                # staleness note when both would apply after the same
                # response - one clear "stop and check in" instruction is
                # more useful here than two separate notes competing for
                # attention right as the loop is meant to end.
                if mutation_count >= MAX_MUTATIONS_PER_STEP:
                    note_text = _build_mutation_limit_note(mutation_count)
                else:
                    note_text = _build_stale_context_note(mutated_by[-1])
                turn_content.append({"type": "text", "text": note_text})

            history.append({"role": "user", "content": turn_content})
