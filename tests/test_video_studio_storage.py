"""Tests for jarvis.video_studio.storage: project directory management.
Redirected to a per-test tmp_path directory - no test touches the real
.jarvis/video_studio/projects/ directory."""

from __future__ import annotations

import pytest

from jarvis.video_studio import storage


@pytest.fixture(autouse=True)
def _isolated_projects_dir(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    monkeypatch.setattr(storage, "VIDEO_STUDIO_PROJECTS_DIR", projects_dir)
    return projects_dir


@pytest.fixture
def source_video(tmp_path):
    src = tmp_path / "my_video.mp4"
    src.write_bytes(b"fake video bytes")
    return src


def test_create_project_copies_file_and_never_touches_source(source_video):
    project = storage.create_project(source_video)
    assert project.original_path.is_file()
    assert project.original_path.read_bytes() == b"fake video bytes"
    # The original the person picked must still exist, unmodified.
    assert source_video.is_file()
    assert source_video.read_bytes() == b"fake video bytes"


def test_create_project_creates_all_subdirectories(source_video):
    project = storage.create_project(source_video)
    assert project.clips_dir.is_dir()
    assert project.audio_dir.is_dir()
    assert project.subtitles_dir.is_dir()
    assert project.cover_dir.is_dir()
    assert project.exports_dir.is_dir()


def test_create_project_generates_unique_ids(source_video):
    p1 = storage.create_project(source_video)
    p2 = storage.create_project(source_video)
    assert p1.project_id != p2.project_id
    assert p1.root_dir != p2.root_dir


@pytest.mark.parametrize("ext", [".mp4", ".mov", ".m4v", ".webm", ".MP4", ".MOV"])
def test_create_project_accepts_supported_formats(tmp_path, ext):
    src = tmp_path / f"video{ext}"
    src.write_bytes(b"x")
    project = storage.create_project(src)
    assert project.original_path.is_file()


def test_create_project_rejects_unsupported_format(tmp_path):
    src = tmp_path / "document.txt"
    src.write_bytes(b"x")
    with pytest.raises(storage.StorageError, match="Unsupported file type"):
        storage.create_project(src)


def test_create_project_missing_source_raises(tmp_path):
    with pytest.raises(storage.StorageError, match="not found"):
        storage.create_project(tmp_path / "does_not_exist.mp4")


def test_project_paths_reconstructs_without_touching_disk():
    project = storage.project_paths("abc123", "video.mp4")
    assert project.project_id == "abc123"
    assert project.original_path.name == "video.mp4"
    assert not project.original_path.exists()


def test_delete_project_removes_directory(source_video):
    project = storage.create_project(source_video)
    assert project.root_dir.is_dir()
    storage.delete_project(project.project_id)
    assert not project.root_dir.exists()


def test_delete_project_nonexistent_does_not_raise():
    storage.delete_project("does-not-exist")  # must not raise


def test_list_project_ids_empty_when_no_projects():
    assert storage.list_project_ids() == []


def test_list_project_ids_returns_created_projects(source_video):
    p1 = storage.create_project(source_video)
    p2 = storage.create_project(source_video)
    ids = storage.list_project_ids()
    assert set(ids) == {p1.project_id, p2.project_id}
