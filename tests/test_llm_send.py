"""Tests for LLMClient.send()'s optional max_tokens/system overrides:
added so an isolated, tool-free request (jarvis.core.commit_message) can
use its own short token budget and system prompt without touching the
main conversational agent's defaults. No real API calls - the underlying
anthropic.Anthropic client is mocked.

Real, reported bug fix ("JARVIS won't open at all: ImportError: DLL
load failed while importing jiter") made `import anthropic` a deferred,
runtime import inside LLMClient.__init__() itself, rather than a
module-level import in jarvis.core.llm (see that module's own
docstring for the full fix) - LLMClient() construction genuinely
performs `import anthropic` now, at the moment it's called, instead of
that having already happened (or already failed) at module-import time.
`fake_api_key` below therefore also stubs `sys.modules["anthropic"]`
with a MagicMock for the duration of each test, so these tests exercise
send()'s own real logic without depending on whether the real anthropic
package can actually be imported on the machine running the suite (it
may be blocked by Windows Smart App Control - see jarvis.core.llm's own
docstring) - matching this file's own "No real API calls" convention
one level earlier than before."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from jarvis.core.llm import MAX_TOKENS, BASE_SYSTEM_PROMPT, LLMClient


@pytest.fixture
def fake_api_key(monkeypatch):
    monkeypatch.setattr("jarvis.core.llm.ANTHROPIC_API_KEY", "sk-ant-fake-for-tests")
    monkeypatch.setitem(sys.modules, "anthropic", MagicMock())


def test_send_without_overrides_uses_defaults(fake_api_key):
    client = LLMClient()
    with patch.object(client, "_client") as mock_anthropic:
        client.send([{"role": "user", "content": "hi"}], [])
    kwargs = mock_anthropic.messages.create.call_args[1]
    assert kwargs["max_tokens"] == MAX_TOKENS
    assert kwargs["system"] == BASE_SYSTEM_PROMPT


def test_send_with_max_tokens_override(fake_api_key):
    client = LLMClient()
    with patch.object(client, "_client") as mock_anthropic:
        client.send([{"role": "user", "content": "hi"}], [], max_tokens=100)
    kwargs = mock_anthropic.messages.create.call_args[1]
    assert kwargs["max_tokens"] == 100


def test_send_with_system_override(fake_api_key):
    client = LLMClient()
    with patch.object(client, "_client") as mock_anthropic:
        client.send([{"role": "user", "content": "hi"}], [], system="Custom system prompt")
    kwargs = mock_anthropic.messages.create.call_args[1]
    assert kwargs["system"] == "Custom system prompt"


def test_send_with_both_overrides(fake_api_key):
    client = LLMClient()
    with patch.object(client, "_client") as mock_anthropic:
        client.send(
            [{"role": "user", "content": "hi"}], [], max_tokens=50, system="Short prompt"
        )
    kwargs = mock_anthropic.messages.create.call_args[1]
    assert kwargs["max_tokens"] == 50
    assert kwargs["system"] == "Short prompt"


def test_system_override_ignores_project_notes(fake_api_key):
    # A caller passing an explicit system prompt gets exactly that text -
    # project_notes (appended to the default system prompt) must not leak
    # into an isolated, unrelated request like a commit-message suggestion.
    client = LLMClient(project_notes="This is a Flask app.")
    with patch.object(client, "_client") as mock_anthropic:
        client.send([{"role": "user", "content": "hi"}], [], system="Isolated prompt")
    kwargs = mock_anthropic.messages.create.call_args[1]
    assert kwargs["system"] == "Isolated prompt"
    assert "Flask" not in kwargs["system"]


def test_agent_loop_send_signature_unaffected(fake_api_key):
    # jarvis.core.agent.Agent never passes max_tokens/system - confirm the
    # two-positional-argument call shape it uses still works exactly as
    # before this change.
    client = LLMClient()
    with patch.object(client, "_client") as mock_anthropic:
        client.send([{"role": "user", "content": "hi"}], [{"name": "some_tool"}])
    kwargs = mock_anthropic.messages.create.call_args[1]
    assert kwargs["tools"] == [{"name": "some_tool"}]
    assert kwargs["max_tokens"] == MAX_TOKENS
