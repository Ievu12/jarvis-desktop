"""Tests for jarvis.core.task_intent.parse_task_intent: deterministic,
rule-based classification of a free-text task request. No filesystem,
git, network, or LLM access of any kind."""

from __future__ import annotations

from jarvis.core.task_intent import (
    EXTERNAL_INTEGRATION,
    FILE_CHANGE,
    GIT_OPERATION,
    SHELL_EXECUTION,
    TEST_RUN,
    UNKNOWN,
    TaskIntent,
    parse_task_intent,
)


# --- empty / blank input -----------------------------------------------------


def test_empty_string_is_unknown_and_unsupported():
    intent = parse_task_intent("")
    assert intent.action_category == UNKNOWN
    assert intent.is_supported is False


def test_whitespace_only_is_unknown_and_unsupported():
    intent = parse_task_intent("   \n\t  ")
    assert intent.action_category == UNKNOWN
    assert intent.is_supported is False


def test_returns_taskintent_dataclass():
    assert isinstance(parse_task_intent("write a README"), TaskIntent)


# --- external integration -----------------------------------------------------


def test_instagram_mention_is_external_integration():
    intent = parse_task_intent("post an update to Instagram")
    assert intent.action_category == EXTERNAL_INTEGRATION
    assert intent.mentions_external_service is True
    assert intent.external_service_name == "instagram"
    assert intent.is_supported is False


def test_gmail_mention_is_external_integration():
    intent = parse_task_intent("send an email via Gmail")
    assert intent.action_category == EXTERNAL_INTEGRATION


def test_stripe_mention_is_external_integration():
    intent = parse_task_intent("issue a refund through Stripe")
    assert intent.action_category == EXTERNAL_INTEGRATION


def test_external_integration_wins_over_git_keywords():
    intent = parse_task_intent("commit this and post it to Facebook")
    assert intent.action_category == EXTERNAL_INTEGRATION


# --- shell execution -----------------------------------------------------


def test_shell_command_request_is_shell_execution_category():
    intent = parse_task_intent("run a shell command to list files")
    assert intent.action_category == SHELL_EXECUTION
    assert intent.mentions_shell_execution is True
    assert intent.is_supported is False


def test_shell_execution_does_not_set_external_service_fields():
    intent = parse_task_intent("run a powershell command")
    assert intent.mentions_external_service is False
    assert intent.external_service_name is None


# --- git operation -----------------------------------------------------


def test_commit_request_is_git_operation():
    intent = parse_task_intent("commit the staged changes")
    assert intent.action_category == GIT_OPERATION
    assert intent.is_supported is True


def test_branch_mention_is_git_operation():
    intent = parse_task_intent("create a new branch for this feature")
    assert intent.action_category == GIT_OPERATION


def test_repository_mention_is_git_operation():
    intent = parse_task_intent("initialize the repository")
    assert intent.action_category == GIT_OPERATION


# --- test run -----------------------------------------------------


def test_run_tests_request_is_test_run():
    intent = parse_task_intent("run the test suite")
    assert intent.action_category == TEST_RUN
    assert intent.is_supported is True


def test_pytest_mention_is_test_run():
    intent = parse_task_intent("run pytest on this project")
    assert intent.action_category == TEST_RUN


# --- file change -----------------------------------------------------


def test_readme_request_is_file_change():
    intent = parse_task_intent("write a README describing the project")
    assert intent.action_category == FILE_CHANGE
    assert intent.is_supported is True


def test_create_file_request_is_file_change():
    intent = parse_task_intent("create a config file")
    assert intent.action_category == FILE_CHANGE


def test_delete_file_request_is_file_change():
    intent = parse_task_intent("delete the old draft file")
    assert intent.action_category == FILE_CHANGE


# --- unknown -----------------------------------------------------


def test_unrecognized_request_is_unknown():
    intent = parse_task_intent("make the project better somehow")
    assert intent.action_category == UNKNOWN
    assert intent.is_supported is False


# --- Lithuanian-language requests -----------------------------------------------------


def test_lithuanian_readme_request_is_file_change():
    intent = parse_task_intent("parašyk README failą aprašantį projektą")
    assert intent.action_category == FILE_CHANGE
    assert intent.is_supported is True


def test_lithuanian_delete_request_is_file_change():
    intent = parse_task_intent("ištrink seną juodraščio failą")
    assert intent.action_category == FILE_CHANGE


def test_lithuanian_commit_request_is_git_operation():
    intent = parse_task_intent("padaryk commit'ą su pakeitimais")
    assert intent.action_category == GIT_OPERATION
    assert intent.is_supported is True


def test_lithuanian_branch_request_is_git_operation():
    intent = parse_task_intent("sukurk naują šaką šitai funkcijai")
    assert intent.action_category == GIT_OPERATION


def test_lithuanian_test_run_request_is_test_run():
    intent = parse_task_intent("paleisk testus")
    assert intent.action_category == TEST_RUN
    assert intent.is_supported is True


def test_lithuanian_instagram_mention_is_external_integration():
    intent = parse_task_intent("paskelbk įrašą Instagram")
    assert intent.action_category == EXTERNAL_INTEGRATION
    assert intent.mentions_external_service is True
    assert intent.external_service_name == "instagram"


def test_lithuanian_shell_command_request_is_shell_execution():
    intent = parse_task_intent("paleisk komandą ir išvalyk laikinus failus")
    assert intent.action_category == SHELL_EXECUTION
    assert intent.mentions_shell_execution is True


# --- purity / determinism -----------------------------------------------------


def test_parse_task_intent_is_deterministic():
    text = "write a README and commit it"
    assert parse_task_intent(text) == parse_task_intent(text)


def test_parse_task_intent_never_touches_subprocess(monkeypatch):
    import subprocess

    def _boom(*a, **k):
        raise AssertionError("parse_task_intent must never call subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)
    parse_task_intent("run a command to delete everything and push to Stripe")


def test_raw_text_preserved_unstripped():
    intent = parse_task_intent("  write a README  ")
    assert intent.raw_text == "  write a README  "
