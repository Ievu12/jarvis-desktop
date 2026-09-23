"""LLM-suggested commit message: a single, tool-free, history-free call to
Claude asking for a short commit message describing the currently staged
changes. Purely advisory - this module never runs git, never writes a
file, and its return value is only ever offered to the user as a
suggestion at the existing 'jarvis commit' / REPL 'commit' stdin prompt
(jarvis.cli.main._prompt_for_commit_message), which still requires the
user to accept or replace it before anything is committed. The actual
commit still goes through the same confirm_side_effect() approval and
jarvis.tools.shell._git_commit() argv-building already used everywhere
else - nothing here can cause a commit to happen.

Deliberately isolated from the main conversational agent: no tools, no
session history, no multi-turn context - just this one prompt and this
one diff, so a failure or a slow/unavailable API never blocks the
existing, LLM-free commit flow. Every failure mode (no API key, network
error, empty response) returns None rather than raising, so callers can
fall back to the plain stdin prompt exactly as before this feature
existed.
"""

from __future__ import annotations

from jarvis.core.llm import LLMClient

# Kept well short of MAX_TOKENS - a commit message is a one-liner (or a
# short summary + body at most), never a place for a long explanation.
_MAX_SUGGESTION_TOKENS = 200

# A diff can be arbitrarily large; only a bounded prefix is sent so a
# single big change doesn't blow up the request or the suggestion's
# relevance (a message summarizing 5000 lines of diff is not useful
# anyway - the first portion is normally enough to see the shape of the
# change).
_MAX_DIFF_CHARS = 6000

# A minimal, self-contained system prompt - deliberately NOT
# jarvis.core.llm.BASE_SYSTEM_PROMPT, which is written for the
# conversational, tool-using agent (workflow discipline, approval
# reminders, multi-turn context rules) that have no bearing on a single
# "write a commit message" request and would only waste tokens or risk
# confusing the model into responding conversationally instead of with
# just the message text.
_SYSTEM_PROMPT = (
    "You write concise, conventional git commit messages from a diff. "
    "Reply with ONLY the commit message text - no explanation, no quotes, "
    "no markdown formatting, no leading/trailing whitespace."
)

_PROMPT_TEMPLATE = (
    "Suggest a concise git commit message (a single summary line, "
    "imperative mood, under 72 characters, optionally followed by a short "
    "body if genuinely useful) for the following staged changes.\n\n"
    "Staged files:\n{files}\n\n"
    "Diff:\n{diff}"
)


def _build_prompt(staged_files: list[str], diff: str | None) -> str:
    files_text = "\n".join(f"- {f}" for f in staged_files) or "(none listed)"
    diff_text = diff if diff else "(no diff content available)"
    if len(diff_text) > _MAX_DIFF_CHARS:
        diff_text = diff_text[:_MAX_DIFF_CHARS] + "\n... [diff truncated]"
    return _PROMPT_TEMPLATE.format(files=files_text, diff=diff_text)


def suggest_commit_message(
    llm: LLMClient, staged_files: list[str], diff: str | None
) -> str | None:
    """Ask the LLM for a one-shot commit message suggestion for the given
    staged files/diff. Returns None (never raises) if the LLM call fails
    for any reason, or if it returns an empty/whitespace-only response -
    callers must treat None as "no suggestion available" and fall back to
    their existing behavior, not as an error to surface.

    No tools are offered and no conversation history is involved - this
    is a single, isolated request, not a turn in the main agent loop.
    """
    if not staged_files:
        return None

    prompt = _build_prompt(staged_files, diff)
    try:
        response = llm.send(
            [{"role": "user", "content": prompt}],
            [],
            max_tokens=_MAX_SUGGESTION_TOKENS,
            system=_SYSTEM_PROMPT,
        )
    except Exception:
        # Any failure (network, auth, rate limit, SDK error) - this
        # feature is purely advisory, so it must never block or crash the
        # existing commit flow. Deliberately broad: the caller only cares
        # that a suggestion either exists or doesn't.
        return None

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    return text or None
