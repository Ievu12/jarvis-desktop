"""Tests for jarvis.gui.dashboard_data: the read-only aggregator behind
the dashboard's Home/Tasks/Activity/Integrations/Automations panels.
Confirms each function wraps its existing source correctly (no new
business logic invented here), degrades gracefully on missing/malformed
data, and never raises out to a UI caller. subprocess (PowerShell) and
the audit log file are isolated per test - no test depends on this
machine's real Task Scheduler state or real audit log contents, except
the explicit real-Task-Scheduler smoke test which is skipped if
Task Scheduler/PowerShell isn't reachable."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from jarvis.gui import dashboard_data as dd


# --- get_integration_statuses(): delegates to IntegrationsManager ------------------


def test_get_integration_statuses_returns_a_list():
    statuses = dd.get_integration_statuses()
    assert isinstance(statuses, list)
    assert len(statuses) > 0


def test_get_integration_statuses_includes_known_services():
    statuses = dd.get_integration_statuses()
    names = {s.service_name for s in statuses}
    assert "instagram" in names
    assert "gmail" in names
    assert "stripe" in names


def test_get_integration_statuses_never_includes_a_credential_value():
    # IntegrationStatusInfo.detail is already masked by
    # jarvis.integrations.credentials.format_credential_status() -
    # this just confirms nothing here adds a raw value on top.
    statuses = dd.get_integration_statuses()
    for s in statuses:
        assert "Bearer " not in s.detail


# --- get_tasks(): delegates to jarvis.session.tasks.load_tasks() -------------------


def test_get_tasks_delegates_to_load_tasks():
    fake_tasks = [MagicMock()]
    with patch("jarvis.gui.dashboard_data.load_tasks", return_value=fake_tasks) as mock_load:
        result = dd.get_tasks()
    assert result is fake_tasks
    mock_load.assert_called_once()


# --- get_recent_activity(): reads the audit log, never raises ----------------------


@pytest.fixture
def isolated_audit_log(tmp_path, monkeypatch):
    log_file = tmp_path / "audit.log"
    monkeypatch.setattr(dd, "AUDIT_LOG_FILE", log_file)
    return log_file


def test_get_recent_activity_missing_file_returns_empty_list(isolated_audit_log):
    assert dd.get_recent_activity() == []


def test_get_recent_activity_parses_tool_call_entries(isolated_audit_log):
    entry = {"ts": "2026-01-01T00:00:00", "event": "tool_call", "tool": "read_file", "ok": True}
    isolated_audit_log.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    result = dd.get_recent_activity()
    assert len(result) == 1
    assert result[0].event_type == "tool_call"
    assert "read_file" in result[0].summary
    assert result[0].ok is True


def test_get_recent_activity_parses_external_action_entries(isolated_audit_log):
    entry = {
        "ts": "2026-01-01T00:00:00", "event": "external_action_confirmation",
        "service": "instagram", "action": "get_profile", "approved": True,
    }
    isolated_audit_log.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    result = dd.get_recent_activity()
    assert result[0].summary == "instagram.get_profile"
    assert result[0].ok is True


def test_get_recent_activity_returns_newest_first(isolated_audit_log):
    lines = [
        json.dumps({"ts": f"2026-01-0{i}T00:00:00", "event": "tool_call", "tool": f"tool{i}", "ok": True})
        for i in range(1, 4)
    ]
    isolated_audit_log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = dd.get_recent_activity()
    assert [r.timestamp for r in result] == ["2026-01-03T00:00:00", "2026-01-02T00:00:00", "2026-01-01T00:00:00"]


def test_get_recent_activity_respects_limit(isolated_audit_log):
    lines = [
        json.dumps({"ts": f"2026-01-{i:02d}T00:00:00", "event": "tool_call", "tool": "x", "ok": True})
        for i in range(1, 30)
    ]
    isolated_audit_log.write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = dd.get_recent_activity(limit=5)
    assert len(result) == 5


def test_get_recent_activity_skips_malformed_lines_without_crashing(isolated_audit_log):
    good_entry = json.dumps({"ts": "2026-01-01T00:00:00", "event": "tool_call", "tool": "x", "ok": True})
    isolated_audit_log.write_text(f"{{not valid json\n{good_entry}\n", encoding="utf-8")

    result = dd.get_recent_activity()
    assert len(result) == 1


def test_get_recent_activity_truncates_a_long_multiline_summary(isolated_audit_log):
    long_description = "A" * 200 + "\nsecond line that must never appear"
    entry = {
        "ts": "2026-01-01T00:00:00", "event": "side_effect_confirmation",
        "description": long_description, "approved": True,
    }
    isolated_audit_log.write_text(json.dumps(entry) + "\n", encoding="utf-8")

    result = dd.get_recent_activity()
    assert "\n" not in result[0].summary
    assert "second line" not in result[0].summary
    assert len(result[0].summary) <= 80


def test_get_recent_activity_unreadable_file_returns_empty_list_not_raise(isolated_audit_log):
    isolated_audit_log.write_text("x", encoding="utf-8")
    with patch("builtins.open", side_effect=OSError("permission denied")):
        result = dd.get_recent_activity()  # must not raise
    assert result == []


# --- get_automations(): reads Windows Task Scheduler, read-only --------------------


def _mock_powershell_result(stdout: str):
    result = MagicMock()
    result.stdout = stdout
    return result


def test_query_scheduled_task_parses_dotnet_date_format():
    # 1790219400000 ms since epoch = 2026-09-24 06:10:00 in some zone -
    # this only checks it parses to SOME non-None, non-raw value.
    payload = json.dumps({"Enabled": True, "NextRun": "/Date(1790219400000)/", "LastRun": None})
    with patch("subprocess.run", return_value=_mock_powershell_result(payload)):
        info = dd._query_scheduled_task("JARVIS Morning Briefing - Weekday")
    assert info is not None
    assert info.enabled is True
    assert info.next_run is not None
    assert "/Date(" not in info.next_run


def test_query_scheduled_task_treats_never_run_sentinel_as_none():
    # 943912800000ms = 1999-11-30, Task Scheduler's "never run" placeholder.
    payload = json.dumps({"Enabled": True, "NextRun": "/Date(1790219400000)/", "LastRun": "/Date(943912800000)/"})
    with patch("subprocess.run", return_value=_mock_powershell_result(payload)):
        info = dd._query_scheduled_task("JARVIS Morning Briefing - Weekday")
    assert info.last_run is None


def test_query_scheduled_task_missing_task_returns_none():
    with patch("subprocess.run", return_value=_mock_powershell_result("")):
        info = dd._query_scheduled_task("Nonexistent Task")
    assert info is None


def test_query_scheduled_task_malformed_json_returns_none_not_raise():
    with patch("subprocess.run", return_value=_mock_powershell_result("{not valid json")):
        info = dd._query_scheduled_task("JARVIS Morning Briefing - Weekday")  # must not raise
    assert info is None


def test_query_scheduled_task_powershell_unavailable_returns_none():
    with patch("subprocess.run", side_effect=OSError("powershell.exe not found")):
        info = dd._query_scheduled_task("JARVIS Morning Briefing - Weekday")  # must not raise
    assert info is None


def test_query_scheduled_task_timeout_returns_none():
    import subprocess as sp

    with patch("subprocess.run", side_effect=sp.TimeoutExpired(cmd="powershell", timeout=10)):
        info = dd._query_scheduled_task("JARVIS Morning Briefing - Weekday")  # must not raise
    assert info is None


def test_get_automations_only_queries_known_task_names():
    with patch("jarvis.gui.dashboard_data._query_scheduled_task", return_value=None) as mock_query:
        dd.get_automations()
    queried_names = {c.args[0] for c in mock_query.call_args_list}
    assert queried_names == set(dd._KNOWN_AUTOMATION_TASK_NAMES)


def test_get_automations_omits_tasks_that_cannot_be_queried():
    with patch("jarvis.gui.dashboard_data._query_scheduled_task", return_value=None):
        result = dd.get_automations()
    assert result == []


def test_get_automations_never_calls_register_or_unregister_cmdlets():
    # A read-only guarantee: confirm the PowerShell script text built
    # for a real query never contains a mutating cmdlet name.
    with patch("subprocess.run", return_value=_mock_powershell_result("")) as mock_run:
        dd._query_scheduled_task("JARVIS Morning Briefing - Weekday")
    script = mock_run.call_args.args[0][-1]
    for forbidden in ("Register-ScheduledTask", "Unregister-ScheduledTask", "Set-ScheduledTask", "Disable-ScheduledTask"):
        assert forbidden not in script


# --- get_automations(): real smoke test against this machine's actual tasks --------


def test_get_automations_real_smoke_test():
    # Not mocked - a real, read-only query against whatever JARVIS
    # scheduled tasks exist on this machine (created in earlier work).
    # Skipped rather than failed if PowerShell/Task Scheduler can't be
    # reached at all (e.g. a locked-down CI runner), since that's an
    # environment limitation, not a code defect.
    try:
        import subprocess as sp
        sp.run(["powershell.exe", "-Command", "exit 0"], capture_output=True, timeout=5)
    except (OSError, sp.TimeoutExpired):
        pytest.skip("PowerShell not reachable in this environment")

    result = dd.get_automations()  # must not raise
    assert isinstance(result, list)


# --- time_of_day_greeting(): pure function of the clock hour -----------------------


def test_greeting_before_noon_is_morning():
    assert dd.time_of_day_greeting(datetime(2026, 1, 1, 9, 0)) == "Good morning"


def test_greeting_afternoon():
    assert dd.time_of_day_greeting(datetime(2026, 1, 1, 14, 0)) == "Good afternoon"


def test_greeting_evening():
    assert dd.time_of_day_greeting(datetime(2026, 1, 1, 20, 0)) == "Good evening"


def test_greeting_boundary_noon_is_afternoon():
    assert dd.time_of_day_greeting(datetime(2026, 1, 1, 12, 0)) == "Good afternoon"


def test_greeting_boundary_six_pm_is_evening():
    assert dd.time_of_day_greeting(datetime(2026, 1, 1, 18, 0)) == "Good evening"
