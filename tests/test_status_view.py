"""Tests for jarvis.core.status_view.format_status: combines JARVIS.md
presence, task/plan state, and git change summary into one view with a
suggested next step. Pure composition of existing checks - verifies no
new persistence is introduced and every workflow state produces the
expected suggestion. Uses monkeypatch to isolate JARVIS_ROOT/TASKS_FILE
across status_view, review_view, and session.tasks - never the real
project."""

from __future__ import annotations

import subprocess

import pytest

from jarvis.core import review_view, status_view
from jarvis.session import tasks as tasks_module


def _init_git_repo(path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True)


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setattr(status_view, "JARVIS_ROOT", tmp_path)
    monkeypatch.setattr(review_view, "JARVIS_ROOT", tmp_path)
    monkeypatch.setattr(tasks_module, "TASKS_FILE", tmp_path / ".jarvis" / "tasks.json")
    return tmp_path


def test_no_notes_no_plan_no_git(isolated_env):
    result = status_view.format_status()
    assert "no JARVIS.md yet" in result
    assert "no active plan" in result
    assert "not a repository yet" in result
    assert "scan" in result.lower()  # suggested next step


def test_has_notes_no_plan_git_repo_clean(isolated_env):
    _init_git_repo(isolated_env)
    (isolated_env / "JARVIS.md").write_text("Project notes.", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=isolated_env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=isolated_env, check=True)

    result = status_view.format_status()
    assert "JARVIS.md loaded" in result
    assert "no active plan" in result
    assert "working tree clean" in result
    assert "nothing pending" in result.lower()


def test_plan_with_pending_steps_suggests_tasks(isolated_env):
    _init_git_repo(isolated_env)
    (isolated_env / "JARVIS.md").write_text("notes", encoding="utf-8")
    tasks_module.create_plan(["step A", "step B"])

    result = status_view.format_status()
    assert "0/2 step(s) done" in result
    assert "2 step(s) remaining" in result
    assert "tasks" in result.lower()


def test_plan_partially_done(isolated_env):
    _init_git_repo(isolated_env)
    (isolated_env / "JARVIS.md").write_text("notes", encoding="utf-8")
    tasks_module.create_plan(["step A", "step B", "step C"])
    tasks_module.complete_task(1)

    result = status_view.format_status()
    assert "1/3 step(s) done" in result
    assert "2 step(s) remaining" in result


def test_plan_complete_with_uncommitted_changes_suggests_review(isolated_env):
    _init_git_repo(isolated_env)
    (isolated_env / "JARVIS.md").write_text("notes", encoding="utf-8")
    tasks_module.create_plan(["step A"])
    tasks_module.complete_task(1)
    (isolated_env / "new_file.txt").write_text("content", encoding="utf-8")

    result = status_view.format_status()
    assert "1/1 step(s) done" in result
    assert "file(s) with uncommitted changes" in result
    assert "review" in result.lower() and "precommit" in result.lower()


def test_plan_complete_clean_tree_reports_nothing_pending(isolated_env):
    _init_git_repo(isolated_env)
    (isolated_env / "JARVIS.md").write_text("notes", encoding="utf-8")
    f = isolated_env / "tracked.txt"
    f.write_text("content", encoding="utf-8")

    tasks_module.create_plan(["done step"])
    tasks_module.complete_task(1)

    # Commit everything, including the task file this created, so the
    # tree is genuinely clean for this assertion - status_view only
    # reports on git state, it has no special-case awareness of .jarvis/.
    subprocess.run(["git", "add", "."], cwd=isolated_env, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial"], cwd=isolated_env, check=True)

    result = status_view.format_status()
    assert "working tree clean" in result
    assert "nothing pending" in result.lower()


def test_not_a_git_repo_suggests_git_init(isolated_env):
    (isolated_env / "JARVIS.md").write_text("notes", encoding="utf-8")
    result = status_view.format_status()
    assert "not a repository yet" in result
    assert "git-init" in result.lower()


def test_status_never_writes_anything(isolated_env):
    _init_git_repo(isolated_env)
    (isolated_env / "JARVIS.md").write_text("notes", encoding="utf-8")
    tasks_module.create_plan(["a step"])

    files_before = sorted(p.name for p in isolated_env.rglob("*") if p.is_file())
    status_view.format_status()
    status_view.format_status()
    files_after = sorted(p.name for p in isolated_env.rglob("*") if p.is_file())

    assert files_before == files_after


def test_status_never_raises_in_any_state(isolated_env):
    # No git repo, no notes, no tasks at all - the emptiest possible state.
    status_view.format_status()  # must not raise
