"""Tests for jarvis.design_studio.storage: design project directory
management. Redirected to a per-test tmp_path directory - no test
touches the real .jarvis/design_studio/projects/ directory."""

from __future__ import annotations

import pytest

from jarvis.design_studio import storage


@pytest.fixture(autouse=True)
def _isolated_projects_dir(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    monkeypatch.setattr(storage, "DESIGN_STUDIO_PROJECTS_DIR", projects_dir)
    return projects_dir


def test_create_project_creates_all_subdirectories():
    project = storage.create_project()
    assert project.variants_dir.is_dir()
    assert project.uploads_dir.is_dir()
    assert project.exports_dir.is_dir()


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


# --- save_uploaded_asset ----------------------------------------------------------------


@pytest.fixture
def source_image(tmp_path):
    src = tmp_path / "logo.png"
    src.write_bytes(b"fake image bytes")
    return src


def test_save_uploaded_asset_copies_file_and_never_touches_source(source_image):
    project = storage.create_project()
    saved_path = storage.save_uploaded_asset(project, source_image)
    assert saved_path.is_file()
    assert saved_path.read_bytes() == b"fake image bytes"
    assert saved_path.parent == project.uploads_dir
    # The original the person picked must still exist, unmodified.
    assert source_image.is_file()
    assert source_image.read_bytes() == b"fake image bytes"


@pytest.mark.parametrize("ext", [".png", ".jpg", ".jpeg", ".webp", ".PNG", ".JPG"])
def test_save_uploaded_asset_accepts_supported_formats(tmp_path, ext):
    project = storage.create_project()
    src = tmp_path / f"image{ext}"
    src.write_bytes(b"x")
    saved_path = storage.save_uploaded_asset(project, src)
    assert saved_path.is_file()


def test_save_uploaded_asset_rejects_unsupported_format(tmp_path):
    project = storage.create_project()
    src = tmp_path / "document.txt"
    src.write_bytes(b"x")
    with pytest.raises(storage.StorageError, match="Unsupported file type"):
        storage.save_uploaded_asset(project, src)


def test_save_uploaded_asset_missing_source_raises(tmp_path):
    project = storage.create_project()
    with pytest.raises(storage.StorageError, match="not found"):
        storage.save_uploaded_asset(project, tmp_path / "does_not_exist.png")
