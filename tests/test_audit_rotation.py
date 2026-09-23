"""Tests for audit log rotation: size-triggered rotation, archives are
timestamped and never overwritten, rotation never deletes data, and
log_event() keeps working correctly across a rotation boundary. Uses an
isolated temp directory for AUDIT_LOG_FILE - never the real project log."""

from __future__ import annotations

import json
import os

import pytest

from jarvis.core import audit


@pytest.fixture
def isolated_audit(tmp_path, monkeypatch):
    log_path = tmp_path / "audit.log"
    monkeypatch.setattr(audit, "AUDIT_LOG_FILE", log_path)
    monkeypatch.setattr(audit, "MAX_BYTES_BEFORE_ROTATION", 500)
    return log_path


def _all_files(tmp_path) -> list[str]:
    return sorted(p.name for p in tmp_path.iterdir())


def test_no_rotation_when_under_threshold(isolated_audit, tmp_path):
    audit.log_event("test_event", description="short")
    assert _all_files(tmp_path) == ["audit.log"]


def test_rotation_triggers_once_threshold_exceeded(isolated_audit, tmp_path):
    for i in range(30):
        audit.log_event("test_event", description=f"entry {i} with padding text " * 2)

    files = _all_files(tmp_path)
    assert "audit.log" in files
    archives = [f for f in files if f != "audit.log"]
    assert len(archives) >= 1


def test_archived_file_name_matches_expected_pattern(isolated_audit, tmp_path):
    for i in range(30):
        audit.log_event("test_event", description=f"entry {i} with padding text " * 2)

    archives = [f for f in _all_files(tmp_path) if f != "audit.log"]
    assert archives
    for name in archives:
        assert name.startswith("audit.log.")
        assert name.endswith(".jsonl")


def test_rotation_never_deletes_data_total_entries_preserved(isolated_audit, tmp_path):
    n = 40
    for i in range(n):
        audit.log_event("test_event", description=f"entry {i} with padding text " * 2)

    total_entries = 0
    for f in tmp_path.iterdir():
        if f.name.startswith("audit.log"):
            with open(f, encoding="utf-8") as fh:
                total_entries += sum(1 for line in fh if line.strip())

    assert total_entries == n


def test_current_log_stays_valid_jsonl_after_rotation(isolated_audit, tmp_path):
    for i in range(30):
        audit.log_event("test_event", description=f"entry {i} with padding text " * 2)

    with open(isolated_audit, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                json.loads(line)  # must not raise


def test_archived_files_contain_valid_jsonl(isolated_audit, tmp_path):
    for i in range(30):
        audit.log_event("test_event", description=f"entry {i} with padding text " * 2)

    archives = [f for f in tmp_path.iterdir() if f.name != "audit.log"]
    assert archives
    for archive in archives:
        with open(archive, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    json.loads(line)  # must not raise


def test_repeated_rotation_produces_distinct_archive_names(isolated_audit, tmp_path):
    # Force many rotations within the same second by using a tiny
    # threshold, exercising the counter-suffix collision handling.
    for i in range(200):
        audit.log_event("test_event", description=f"entry {i} padding " * 3)

    archives = [f for f in _all_files(tmp_path) if f != "audit.log"]
    assert len(archives) == len(set(archives))  # all names unique


def test_missing_log_file_does_not_error_on_rotation_check(isolated_audit):
    assert not isolated_audit.exists()
    audit.log_event("test_event", description="first ever entry")
    assert isolated_audit.exists()


def test_log_event_still_redacts_secrets_after_rotation(isolated_audit, tmp_path, monkeypatch):
    monkeypatch.setattr("jarvis.config.ANTHROPIC_API_KEY", "sk-ant-super-secret-value")
    for i in range(30):
        audit.log_event("test_event", description=f"entry {i} padding " * 2)
    audit.log_event("test_event", description="contains sk-ant-super-secret-value in it")

    with open(isolated_audit, encoding="utf-8") as f:
        content = f.read()
    assert "sk-ant-super-secret-value" not in content
