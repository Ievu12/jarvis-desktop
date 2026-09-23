"""Tests for jarvis.core.task_safety: pure, deterministic risk
classification for the Task Execution layer. No filesystem, network, or
subprocess access of any kind."""

from __future__ import annotations

from jarvis.core.task_safety import (
    BLOCKED_EXTERNAL_SERVICES,
    IRREVERSIBLE,
    READ_ONLY,
    REVERSIBLE,
    RiskClassification,
    classify_risk,
    find_blocked_external_service,
    mentions_shell_execution,
)


# --- find_blocked_external_service ---------------------------------------


def test_finds_known_service_case_insensitive():
    assert find_blocked_external_service("post this to Instagram") == "instagram"


def test_finds_stripe_mention():
    assert find_blocked_external_service("charge the customer via Stripe") == "stripe"


def test_returns_none_when_no_service_mentioned():
    assert find_blocked_external_service("write a README") is None


def test_every_documented_blocked_service_is_actually_detected():
    for service in BLOCKED_EXTERNAL_SERVICES:
        assert find_blocked_external_service(f"do something with {service}") == service


# --- mentions_shell_execution ----------------------------------------------


def test_detects_shell_command_phrase():
    assert mentions_shell_execution("please run a command to list files") is True


def test_detects_powershell_mention():
    assert mentions_shell_execution("use powershell to do this") is True


def test_no_false_positive_on_unrelated_text():
    assert mentions_shell_execution("write a README describing the project") is False


# --- classify_risk: precedence and levels ----------------------------------


def test_blocked_external_service_wins_and_is_irreversible():
    result = classify_risk("post an update to Facebook")
    assert isinstance(result, RiskClassification)
    assert result.is_blocked is True
    assert result.risk_level == IRREVERSIBLE
    assert "facebook" in result.blocked_reason.lower()


def test_shell_execution_is_blocked_and_irreversible():
    result = classify_risk("run a shell command to clean up")
    assert result.is_blocked is True
    assert result.risk_level == IRREVERSIBLE


def test_shell_execution_takes_priority_over_irreversible_keyword():
    # "delete" would otherwise match _IRREVERSIBLE_KEYWORDS, but the shell
    # mention must still win and mark this blocked, not just irreversible.
    result = classify_risk("run a command to delete old files")
    assert result.is_blocked is True


def test_external_service_takes_priority_over_shell_keyword():
    result = classify_risk("run a command to post to Instagram")
    assert result.is_blocked is True
    assert "instagram" in result.blocked_reason.lower()


def test_delete_keyword_is_irreversible_but_not_blocked():
    result = classify_risk("delete the old draft file")
    assert result.risk_level == IRREVERSIBLE
    assert result.is_blocked is False
    assert result.blocked_reason is None


def test_push_keyword_is_irreversible():
    result = classify_risk("push the latest commits")
    assert result.risk_level == IRREVERSIBLE


def test_write_keyword_is_reversible():
    result = classify_risk("write a new config file")
    assert result.risk_level == REVERSIBLE
    assert result.is_blocked is False


def test_commit_keyword_is_reversible():
    result = classify_risk("commit the staged changes")
    assert result.risk_level == REVERSIBLE


def test_plain_text_with_no_keywords_is_read_only():
    result = classify_risk("what does this project do")
    assert result.risk_level == READ_ONLY
    assert result.is_blocked is False


def test_irreversible_keyword_takes_priority_over_reversible_keyword():
    # "overwrite" (irreversible) and "update" (reversible) both appear;
    # irreversible must win.
    result = classify_risk("update and overwrite the existing file")
    assert result.risk_level == IRREVERSIBLE


# --- Lithuanian-language text -----------------------------------------------------


def test_lithuanian_instagram_mention_is_blocked():
    result = classify_risk("paskelbk įrašą Instagram paskyroje")
    assert result.is_blocked is True
    assert "instagram" in result.blocked_reason.lower()


def test_lithuanian_shell_command_is_blocked():
    result = classify_risk("paleisk komandą ir sutvarkyk laikinus failus")
    assert result.is_blocked is True
    assert result.risk_level == IRREVERSIBLE


def test_lithuanian_delete_is_irreversible_but_not_blocked():
    result = classify_risk("ištrink senus juodraščio failus")
    assert result.risk_level == IRREVERSIBLE
    assert result.is_blocked is False


def test_lithuanian_write_is_reversible():
    result = classify_risk("parašyk naują konfigūracijos failą")
    assert result.risk_level == REVERSIBLE
    assert result.is_blocked is False


def test_lithuanian_commit_is_reversible():
    result = classify_risk("padaryk commit'ą su pakeitimais")
    assert result.risk_level == REVERSIBLE


def test_lithuanian_plain_text_with_no_keywords_is_read_only():
    result = classify_risk("ką veikia šis projektas")
    assert result.risk_level == READ_ONLY
    assert result.is_blocked is False


# --- determinism / purity ---------------------------------------------------


def test_classify_risk_is_deterministic():
    text = "write a README and commit it"
    assert classify_risk(text) == classify_risk(text)


def test_classify_risk_never_touches_subprocess(monkeypatch):
    import subprocess

    def _boom(*a, **k):
        raise AssertionError("classify_risk must never call subprocess")

    monkeypatch.setattr(subprocess, "run", _boom)
    classify_risk("run a command to delete everything and push to Stripe")
