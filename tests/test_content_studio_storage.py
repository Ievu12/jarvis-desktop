"""Tests for jarvis.content_studio.storage: Content Studio project
directory management. Redirected to a per-test tmp_path directory - no
test touches the real .jarvis/content_studio/projects/ directory."""

from __future__ import annotations

import pytest

from jarvis.content_studio import storage


@pytest.fixture(autouse=True)
def _isolated_projects_dir(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    monkeypatch.setattr(storage, "CONTENT_STUDIO_PROJECTS_DIR", projects_dir)
    return projects_dir


def test_create_project_creates_pdf_subdirectory():
    project = storage.create_project()
    assert project.pdf_dir.is_dir()


def test_create_project_generates_unique_ids():
    p1 = storage.create_project()
    p2 = storage.create_project()
    assert p1.project_id != p2.project_id
    assert p1.root_dir != p2.root_dir


def test_project_paths_reconstructs_without_touching_disk():
    project = storage.project_paths("abc123")
    assert project.project_id == "abc123"
    assert not project.root_dir.exists()


def test_delete_project_removes_directory():
    project = storage.create_project()
    assert project.root_dir.is_dir()
    storage.delete_project(project.project_id)
    assert not project.root_dir.exists()


def test_delete_project_nonexistent_does_not_raise():
    storage.delete_project("does-not-exist")  # must not raise


def test_list_project_ids_empty_when_no_projects():
    assert storage.list_project_ids() == []


def test_list_project_ids_returns_created_projects():
    p1 = storage.create_project()
    p2 = storage.create_project()
    ids = storage.list_project_ids()
    assert set(ids) == {p1.project_id, p2.project_id}
