"""Tests for LLMClient's system prompt construction with optional
project_notes: base prompt preserved, notes appended clearly delimited,
no notes leaves the prompt unchanged. No real API calls - only inspects
the constructed system prompt string."""

from __future__ import annotations

import pytest

from jarvis.core.llm import BASE_SYSTEM_PROMPT, LLMClient


@pytest.fixture
def fake_api_key(monkeypatch):
    monkeypatch.setattr("jarvis.core.llm.ANTHROPIC_API_KEY", "sk-ant-fake-for-tests")


def test_no_project_notes_leaves_prompt_unchanged(fake_api_key):
    client = LLMClient()
    assert client._system_prompt == BASE_SYSTEM_PROMPT


def test_project_notes_appended_to_prompt(fake_api_key):
    client = LLMClient(project_notes="This is a Flask app. Use tabs, not spaces.")
    assert "This is a Flask app. Use tabs, not spaces." in client._system_prompt


def test_base_prompt_still_present_alongside_notes(fake_api_key):
    client = LLMClient(project_notes="Some project-specific notes here.")
    assert BASE_SYSTEM_PROMPT in client._system_prompt


def test_project_notes_clearly_labeled_not_silently_merged(fake_api_key):
    client = LLMClient(project_notes="Custom notes.")
    assert "JARVIS.md" in client._system_prompt


def test_empty_string_project_notes_treated_as_no_notes(fake_api_key):
    client = LLMClient(project_notes="")
    assert client._system_prompt == BASE_SYSTEM_PROMPT


def test_none_project_notes_treated_as_no_notes(fake_api_key):
    client = LLMClient(project_notes=None)
    assert client._system_prompt == BASE_SYSTEM_PROMPT


def test_missing_api_key_still_raises_regardless_of_notes(monkeypatch):
    monkeypatch.setattr("jarvis.core.llm.ANTHROPIC_API_KEY", None)
    with pytest.raises(RuntimeError):
        LLMClient(project_notes="some notes")


def test_base_prompt_instructs_using_create_plan_for_multi_step_requests():
    assert "create_plan" in BASE_SYSTEM_PROMPT


def test_base_prompt_instructs_marking_tasks_complete_via_manage_tasks():
    assert "manage_tasks" in BASE_SYSTEM_PROMPT


# --- workflow-discipline instructions (get_workflow_state ordering, approval) ------


def test_base_prompt_instructs_using_get_workflow_state():
    assert "get_workflow_state" in BASE_SYSTEM_PROMPT


def test_base_prompt_instructs_checking_state_before_acting():
    assert "before proposing or taking any action" in BASE_SYSTEM_PROMPT


def test_base_prompt_instructs_not_repeating_the_same_stage_unnecessarily():
    assert "Don't call get_workflow_state again for the same stage" in BASE_SYSTEM_PROMPT


def test_base_prompt_instructs_preferring_read_only_inspection():
    assert "prefer" in BASE_SYSTEM_PROMPT.lower()
    assert "read-only inspection" in BASE_SYSTEM_PROMPT


def test_base_prompt_instructs_explaining_before_mutating_tools():
    assert "explain in plain text what you're about to" in BASE_SYSTEM_PROMPT


def test_base_prompt_instructs_approval_prompt_is_not_optional():
    assert "not optional" in BASE_SYSTEM_PROMPT


def test_base_prompt_instructs_never_bundling_approvals():
    assert "cannot skip, pre-approve, or bundle" in BASE_SYSTEM_PROMPT


def test_base_prompt_instructs_never_claiming_done_before_approval():
    assert "'is done' before its approval prompt has actually been answered" in BASE_SYSTEM_PROMPT


def test_base_prompt_instructs_never_auto_committing():
    assert "git-add or git-commit as a way to" in BASE_SYSTEM_PROMPT


def test_base_prompt_instructs_respecting_precommit_readiness():
    assert "not ready to commit" in BASE_SYSTEM_PROMPT


def test_base_prompt_mentions_all_mutating_tool_names_in_the_approval_instruction():
    for tool_name in (
        "write_file", "append_to_file", "delete_file", "create_directory",
        "move_file", "replace_in_file", "edit_file_lines",
    ):
        assert tool_name in BASE_SYSTEM_PROMPT


# --- multi-turn context instructions (resolving references, stale-context notes) ---


def test_base_prompt_instructs_resolving_references_against_recent_answer():
    assert "resolve the reference against" in BASE_SYSTEM_PROMPT
    assert "your own most recent structured answer" in BASE_SYSTEM_PROMPT


def test_base_prompt_instructs_not_rederiving_already_known_information():
    assert "Don't re-run get_workflow_state just to re-derive" in BASE_SYSTEM_PROMPT


def test_base_prompt_instructs_respecting_session_note_prefix():
    assert "[JARVIS session note]" in BASE_SYSTEM_PROMPT


def test_base_prompt_instructs_rechecking_after_a_session_note():
    assert "re-check the relevant" in BASE_SYSTEM_PROMPT
    assert "now-stale list" in BASE_SYSTEM_PROMPT


# --- TEST -> ANALYZE -> PROPOSE FIX -> APPROVAL -> APPLY -> VERIFY instructions ----


def test_base_prompt_names_the_full_sequence():
    assert "TEST -> ANALYZE -> PROPOSE FIX -> APPROVAL -> APPLY -> VERIFY" in BASE_SYSTEM_PROMPT


def test_base_prompt_instructs_running_tests_before_assuming_a_problem():
    assert "run_command('pytest')" in BASE_SYSTEM_PROMPT
    assert "before assuming it is" in BASE_SYSTEM_PROMPT


def test_base_prompt_instructs_reading_only_the_relevant_file():
    assert "read only the specific file(s)" in BASE_SYSTEM_PROMPT
    assert "don't read unrelated files 'just in case'" in BASE_SYSTEM_PROMPT


def test_base_prompt_instructs_explaining_fix_before_writing():
    assert "explain it in plain text before calling any write tool" in BASE_SYSTEM_PROMPT


def test_base_prompt_instructs_not_skipping_to_verify_before_approval():
    assert "Never skip straight to VERIFY" in BASE_SYSTEM_PROMPT


def test_base_prompt_instructs_reverifying_after_the_fix():
    assert "VERIFY: re-run the same tests" in BASE_SYSTEM_PROMPT
    assert "don't just" in BASE_SYSTEM_PROMPT.lower()


def test_base_prompt_instructs_stopping_after_one_failed_fix_attempt():
    assert "stop and explain" in BASE_SYSTEM_PROMPT
    assert "Never chain repeated fix attempts" in BASE_SYSTEM_PROMPT


def test_base_prompt_mentions_the_hard_mutation_limit():
    assert "hard limit on mutating actions per turn" in BASE_SYSTEM_PROMPT
