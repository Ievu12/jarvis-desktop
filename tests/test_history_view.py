"""Tests for jarvis.core.history_view: formatting the audit log into a
human-readable summary. Read-only - operates against an isolated copy of
AUDIT_LOG_FILE, never the real project's log."""

from __future__ import annotations

import json

import pytest

from jarvis.core import history_view


@pytest.fixture
def isolated_audit_log(tmp_path, monkeypatch):
    log_path = tmp_path / "audit.log"
    monkeypatch.setattr(history_view, "AUDIT_LOG_FILE", log_path)
    return log_path


def _write_entries(log_path, entries: list[dict]) -> None:
    with open(log_path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry) + "\n")


def test_no_log_file_reports_no_history(isolated_audit_log):
    result = history_view.format_history()
    assert "no audit history" in result.lower()


def test_empty_log_file_reports_no_history(isolated_audit_log):
    isolated_audit_log.write_text("", encoding="utf-8")
    result = history_view.format_history()
    assert "no audit history" in result.lower()


def test_approved_entry_shown_as_approved(isolated_audit_log):
    _write_entries(
        isolated_audit_log,
        [{"ts": "2026-01-01T00:00:00", "event": "side_effect_confirmation",
          "description": "write x.txt", "approved": True}],
    )
    result = history_view.format_history()
    assert "APPROVED" in result
    assert "write x.txt" in result


def test_denied_entry_shown_as_denied(isolated_audit_log):
    _write_entries(
        isolated_audit_log,
        [{"ts": "2026-01-01T00:00:00", "event": "side_effect_confirmation",
          "description": "delete x.txt", "approved": False}],
    )
    result = history_view.format_history()
    assert "DENIED" in result
    assert "INTERRUPTED" not in result


def test_interrupted_entry_shown_distinctly(isolated_audit_log):
    _write_entries(
        isolated_audit_log,
        [{"ts": "2026-01-01T00:00:00", "event": "side_effect_confirmation",
          "description": "delete x.txt", "approved": False, "interrupted": True}],
    )
    result = history_view.format_history()
    assert "INTERRUPTED" in result


def test_outside_sandbox_event_labeled_distinctly(isolated_audit_log):
    _write_entries(
        isolated_audit_log,
        [{"ts": "2026-01-01T00:00:00", "event": "outside_sandbox_request",
          "path": "C:\\outside\\file.txt", "approved": True}],
    )
    result = history_view.format_history()
    assert "outside-sandbox access" in result
    assert "C:\\outside\\file.txt" in result


def test_limit_shows_only_most_recent_entries(isolated_audit_log):
    entries = [
        {"ts": f"2026-01-01T00:00:{i:02d}", "event": "side_effect_confirmation",
         "description": f"action {i}", "approved": True}
        for i in range(30)
    ]
    _write_entries(isolated_audit_log, entries)

    result = history_view.format_history(limit=5)
    assert "action 29" in result  # most recent
    assert "action 0" not in result  # oldest, beyond the limit
    assert "Showing 5 of 30" in result


def test_no_truncation_note_when_under_limit(isolated_audit_log):
    _write_entries(
        isolated_audit_log,
        [{"ts": "2026-01-01T00:00:00", "event": "side_effect_confirmation",
          "description": "one thing", "approved": True}],
    )
    result = history_view.format_history(limit=20)
    assert "earlier entries not shown" not in result


def test_malformed_line_is_skipped_not_fatal(isolated_audit_log):
    with open(isolated_audit_log, "w", encoding="utf-8") as f:
        f.write("not valid json\n")
        f.write(json.dumps({"ts": "2026-01-01T00:00:00", "event": "side_effect_confirmation",
                             "description": "valid one", "approved": True}) + "\n")

    result = history_view.format_history()
    assert "valid one" in result


def test_output_never_mentions_this_being_session_scoped(isolated_audit_log):
    _write_entries(
        isolated_audit_log,
        [{"ts": "2026-01-01T00:00:00", "event": "side_effect_confirmation",
          "description": "x", "approved": True}],
    )
    result = history_view.format_history()
    assert "all sessions" in result.lower()


# --- tool_call entries (jarvis.core.agent's tool-call audit logging) --------------


def test_tool_call_entry_ok_shown_as_ok(isolated_audit_log):
    _write_entries(
        isolated_audit_log,
        [{"ts": "2026-01-01T00:00:00", "event": "tool_call",
          "tool": "get_workflow_state", "input": {"stage": "scan"}, "ok": True}],
    )
    result = history_view.format_history()
    assert "OK" in result
    assert "tool call" in result
    assert "get_workflow_state" in result


def test_tool_call_entry_not_ok_shown_as_error(isolated_audit_log):
    _write_entries(
        isolated_audit_log,
        [{"ts": "2026-01-01T00:00:00", "event": "tool_call",
          "tool": "does_not_exist", "input": {}, "ok": False}],
    )
    result = history_view.format_history()
    assert "ERROR" in result
    assert "does_not_exist" in result


def test_tool_call_entry_never_labeled_approved_or_denied(isolated_audit_log):
    # tool_call entries use their own OK/ERROR vocabulary, distinct from
    # APPROVED/DENIED - a read-only tool call was never actually presented
    # to the user for a yes/no decision, so it must not look like one was.
    _write_entries(
        isolated_audit_log,
        [{"ts": "2026-01-01T00:00:00", "event": "tool_call",
          "tool": "get_workflow_state", "input": {"stage": "plan"}, "ok": True}],
    )
    result = history_view.format_history()
    assert "APPROVED" not in result
    assert "DENIED" not in result


def test_tool_call_entry_shows_input_arguments(isolated_audit_log):
    _write_entries(
        isolated_audit_log,
        [{"ts": "2026-01-01T00:00:00", "event": "tool_call",
          "tool": "read_file", "input": {"path": "README.md"}, "ok": True}],
    )
    result = history_view.format_history()
    assert "README.md" in result


def test_tool_call_entry_with_empty_input_omits_parens(isolated_audit_log):
    _write_entries(
        isolated_audit_log,
        [{"ts": "2026-01-01T00:00:00", "event": "tool_call",
          "tool": "manage_tasks", "input": {}, "ok": True}],
    )
    result = history_view.format_history()
    assert "manage_tasks" in result
    assert "manage_tasks({})" not in result


def test_tool_call_and_approval_entries_coexist_in_same_log(isolated_audit_log):
    _write_entries(
        isolated_audit_log,
        [
            {"ts": "2026-01-01T00:00:00", "event": "tool_call",
             "tool": "get_workflow_state", "input": {"stage": "review"}, "ok": True},
            {"ts": "2026-01-01T00:00:01", "event": "side_effect_confirmation",
             "description": "write x.txt", "approved": True},
        ],
    )
    result = history_view.format_history()
    assert "OK" in result and "tool call" in result
    assert "APPROVED" in result and "side effect" in result


# --- external_action_confirmation entries (jarvis.integrations.approval) ------------


def test_external_action_entry_approved_shown_distinctly(isolated_audit_log):
    _write_entries(
        isolated_audit_log,
        [{"ts": "2026-01-01T00:00:00", "event": "external_action_confirmation",
          "service": "stripe", "action": "charge_customer", "risk_level": "irreversible_write",
          "approved": True}],
    )
    result = history_view.format_history()
    assert "APPROVED" in result
    assert "external action" in result
    assert "stripe.charge_customer" in result
    assert "irreversible_write" in result


def test_external_action_entry_denied_shown_distinctly(isolated_audit_log):
    _write_entries(
        isolated_audit_log,
        [{"ts": "2026-01-01T00:00:00", "event": "external_action_confirmation",
          "service": "email", "action": "send_email", "risk_level": "irreversible_write",
          "approved": False}],
    )
    result = history_view.format_history()
    assert "DENIED" in result
    assert "email.send_email" in result


def test_external_action_entry_interrupted_shown_distinctly(isolated_audit_log):
    _write_entries(
        isolated_audit_log,
        [{"ts": "2026-01-01T00:00:00", "event": "external_action_confirmation",
          "service": "email", "action": "send_email", "risk_level": "irreversible_write",
          "approved": False, "interrupted": True}],
    )
    result = history_view.format_history()
    assert "INTERRUPTED" in result


def test_external_action_entry_never_labeled_as_plain_side_effect(isolated_audit_log):
    # External actions must be visually distinguishable from local
    # side_effect_confirmation entries, not lumped into the same label.
    _write_entries(
        isolated_audit_log,
        [{"ts": "2026-01-01T00:00:00", "event": "external_action_confirmation",
          "service": "stripe", "action": "refund_charge", "risk_level": "irreversible_write",
          "approved": True}],
    )
    result = history_view.format_history()
    lines = [line for line in result.splitlines() if "stripe.refund_charge" in line]
    assert len(lines) == 1
    assert "external action" in lines[0]
    assert lines[0].count("side effect") == 0


def test_external_action_and_tool_call_and_side_effect_all_coexist(isolated_audit_log):
    _write_entries(
        isolated_audit_log,
        [
            {"ts": "2026-01-01T00:00:00", "event": "tool_call",
             "tool": "get_workflow_state", "input": {"stage": "scan"}, "ok": True},
            {"ts": "2026-01-01T00:00:01", "event": "side_effect_confirmation",
             "description": "write x.txt", "approved": True},
            {"ts": "2026-01-01T00:00:02", "event": "external_action_confirmation",
             "service": "email", "action": "send_email", "risk_level": "irreversible_write",
             "approved": True},
        ],
    )
    result = history_view.format_history()
    assert "tool call" in result
    assert "side effect" in result
    assert "external action" in result
