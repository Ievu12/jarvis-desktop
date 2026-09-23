"""Tests for jarvis.integrations.approval.confirm_external_action: the
stricter approval gate for external-service actions. REVERSIBLE_WRITE
(and READ_ONLY, if it ever reaches this function) uses a plain y/N
prompt; IRREVERSIBLE_WRITE requires typing the service name back exactly
(case-insensitive) - deliberately higher friction than any local approval
prompt, since these actions cannot be undone by JARVIS itself. Every
outcome is logged as its own external_action_confirmation audit event.
No real network access - mirrors test_keyboard_interrupt.py's approach
to jarvis.core.approval."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from jarvis.integrations.approval import confirm_external_action
from jarvis.integrations.base import RiskLevel


def _interrupting_input(*a, **k):
    raise KeyboardInterrupt


# --- REVERSIBLE_WRITE: plain y/N -----------------------------------------------------


def test_reversible_write_approved_with_y():
    with patch("builtins.input", return_value="y"):
        result = confirm_external_action(
            "fake_service", "do_thing", RiskLevel.REVERSIBLE_WRITE, "details here"
        )
    assert result is True


def test_reversible_write_denied_with_n():
    with patch("builtins.input", return_value="n"):
        result = confirm_external_action(
            "fake_service", "do_thing", RiskLevel.REVERSIBLE_WRITE, "details here"
        )
    assert result is False


def test_reversible_write_denied_by_default_on_blank_input():
    with patch("builtins.input", return_value=""):
        result = confirm_external_action(
            "fake_service", "do_thing", RiskLevel.REVERSIBLE_WRITE, "details"
        )
    assert result is False


def test_reversible_write_denied_on_typing_service_name():
    # Typing the service name is only meaningful for IRREVERSIBLE_WRITE -
    # for REVERSIBLE_WRITE it's just an arbitrary non-'y' string, denied.
    with patch("builtins.input", return_value="fake_service"):
        result = confirm_external_action(
            "fake_service", "do_thing", RiskLevel.REVERSIBLE_WRITE, "details"
        )
    assert result is False


# --- IRREVERSIBLE_WRITE: must type the service name ---------------------------------


def test_irreversible_write_approved_by_typing_exact_service_name():
    with patch("builtins.input", return_value="fake_service"):
        result = confirm_external_action(
            "fake_service", "send_email", RiskLevel.IRREVERSIBLE_WRITE, "to: someone@example.com"
        )
    assert result is True


def test_irreversible_write_approved_case_insensitively():
    with patch("builtins.input", return_value="FAKE_SERVICE"):
        result = confirm_external_action(
            "fake_service", "send_email", RiskLevel.IRREVERSIBLE_WRITE, "details"
        )
    assert result is True


def test_irreversible_write_denied_with_plain_y():
    # 'y' alone is NOT enough for the highest risk tier - must be the
    # exact service name.
    with patch("builtins.input", return_value="y"):
        result = confirm_external_action(
            "fake_service", "send_email", RiskLevel.IRREVERSIBLE_WRITE, "details"
        )
    assert result is False


def test_irreversible_write_denied_on_blank_input():
    with patch("builtins.input", return_value=""):
        result = confirm_external_action(
            "fake_service", "send_email", RiskLevel.IRREVERSIBLE_WRITE, "details"
        )
    assert result is False


def test_irreversible_write_denied_on_wrong_service_name():
    with patch("builtins.input", return_value="wrong_service"):
        result = confirm_external_action(
            "fake_service", "send_email", RiskLevel.IRREVERSIBLE_WRITE, "details"
        )
    assert result is False


def test_irreversible_write_prompt_mentions_cannot_be_undone():
    captured = []

    def _capture(prompt=""):
        captured.append(prompt)
        return "fake_service"

    with patch("builtins.input", side_effect=_capture):
        confirm_external_action(
            "fake_service", "send_email", RiskLevel.IRREVERSIBLE_WRITE, "details"
        )
    assert "cannot be undone" in captured[0].lower()


# --- prompt content --------------------------------------------------------------------


def test_prompt_includes_service_action_and_description():
    captured = []

    def _capture(prompt=""):
        captured.append(prompt)
        return "y"

    with patch("builtins.input", side_effect=_capture):
        confirm_external_action(
            "stripe", "list_charges", RiskLevel.REVERSIBLE_WRITE, "showing last 10 charges"
        )
    assert "stripe" in captured[0]
    assert "list_charges" in captured[0]
    assert "showing last 10 charges" in captured[0]


# --- audit logging -----------------------------------------------------------------------


def test_approval_logged_with_service_action_and_risk_level(tmp_path, monkeypatch):
    from jarvis.core import audit

    log_path = tmp_path / "audit.log"
    monkeypatch.setattr(audit, "AUDIT_LOG_FILE", log_path)

    with patch("builtins.input", return_value="y"):
        confirm_external_action(
            "fake_service", "do_thing", RiskLevel.REVERSIBLE_WRITE, "details"
        )

    import json
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    entry = entries[0]
    assert entry["event"] == "external_action_confirmation"
    assert entry["service"] == "fake_service"
    assert entry["action"] == "do_thing"
    assert entry["risk_level"] == "reversible_write"
    assert entry["approved"] is True


def test_denial_logged_as_not_approved(tmp_path, monkeypatch):
    from jarvis.core import audit

    log_path = tmp_path / "audit.log"
    monkeypatch.setattr(audit, "AUDIT_LOG_FILE", log_path)

    with patch("builtins.input", return_value="n"):
        confirm_external_action(
            "fake_service", "do_thing", RiskLevel.REVERSIBLE_WRITE, "details"
        )

    import json
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert entries[0]["approved"] is False


# --- interrupt safety ------------------------------------------------------------------


def test_keyboard_interrupt_propagates_not_treated_as_approval():
    with patch("builtins.input", side_effect=_interrupting_input):
        with pytest.raises(KeyboardInterrupt):
            confirm_external_action(
                "fake_service", "do_thing", RiskLevel.IRREVERSIBLE_WRITE, "details"
            )


def test_keyboard_interrupt_logged_as_denied_and_interrupted(tmp_path, monkeypatch):
    from jarvis.core import audit

    log_path = tmp_path / "audit.log"
    monkeypatch.setattr(audit, "AUDIT_LOG_FILE", log_path)

    with patch("builtins.input", side_effect=_interrupting_input):
        with pytest.raises(KeyboardInterrupt):
            confirm_external_action(
                "fake_service", "do_thing", RiskLevel.REVERSIBLE_WRITE, "details"
            )

    import json
    entries = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    assert entries[0]["approved"] is False
    assert entries[0]["interrupted"] is True
