"""Tests for jarvis.video_editor.storage: per-project directory
management (media/exports subdirs), redirected to a per-test tmp_path -
no test touches the real VIDEO_EDITOR_PROJECTS_DIR. Confirms: a new
project gets its own real directory tree, media files are copied in
(never moved/deleted from their original location), a missing source
file raises VideoEditorStorageError, delete_project() is idempotent,
and list_project_ids() reflects real directories on disk."""

from __future__ import annotations

import pytest

from jarvis.video_editor import storage


@pytest.fixture(autouse=True)
def _isolated_projects_dir(tmp_path, monkeypatch):
    projects_dir = tmp_path / "video_editor_projects"
    monkeypatch.setattr(storage, "VIDEO_EDITOR_PROJECTS_DIR", projects_dir)
    return projects_dir


def test_create_project_creates_real_directories():
    project = storage.create_project()
    assert project.root_dir.is_dir()
    assert project.media_dir.is_dir()
    assert project.exports_dir.is_dir()


def test_create_project_generates_a_fresh_id_each_time():
    project1 = storage.create_project()
    project2 = storage.create_project()
    assert project1.project_id != project2.project_id


def test_copy_media_into_project_copies_without_touching_the_original(tmp_path):
    project = storage.create_project()
    source = tmp_path / "my_video.mp4"
    source.write_bytes(b"fake video bytes")

    destination = storage.copy_media_into_project(project, source)

    assert destination.is_file()
    assert destination.read_bytes() == b"fake video bytes"
    assert destination.parent == project.media_dir
    assert source.is_file()  # original untouched
    assert source.read_bytes() == b"fake video bytes"


def test_copy_media_into_project_avoids_filename_collisions(tmp_path):
    project = storage.create_project()
    source1 = tmp_path / "a" / "photo.jpg"
    source1.parent.mkdir()
    source1.write_bytes(b"first")
    source2 = tmp_path / "b" / "photo.jpg"
    source2.parent.mkdir()
    source2.write_bytes(b"second")

    dest1 = storage.copy_media_into_project(project, source1)
    dest2 = storage.copy_media_into_project(project, source2)

    assert dest1 != dest2
    assert dest1.read_bytes() == b"first"
    assert dest2.read_bytes() == b"second"


def test_copy_media_into_project_raises_for_missing_source(tmp_path):
    project = storage.create_project()
    with pytest.raises(storage.VideoEditorStorageError, match="not found"):
        storage.copy_media_into_project(project, tmp_path / "does_not_exist.mp4")


def test_project_paths_reconstructs_without_touching_disk():
    project = storage.create_project()
    reconstructed = storage.project_paths(project.project_id)
    assert reconstructed.project_id == project.project_id
    assert reconstructed.root_dir == project.root_dir
    assert reconstructed.media_dir == project.media_dir


def test_delete_project_removes_the_directory():
    project = storage.create_project()
    assert project.root_dir.is_dir()
    storage.delete_project(project.project_id)
    assert not project.root_dir.exists()


def test_delete_project_is_idempotent_for_a_nonexistent_project():
    storage.delete_project("does-not-exist")  # must not raise


def test_list_project_ids_reflects_real_directories():
    assert storage.list_project_ids() == []
    project1 = storage.create_project()
    project2 = storage.create_project()
    assert storage.list_project_ids() == sorted([project1.project_id, project2.project_id])


def test_list_project_ids_excludes_a_deleted_project():
    project = storage.create_project()
    storage.delete_project(project.project_id)
    assert project.project_id not in storage.list_project_ids()


# --- save_project()/load_project() (requirement 8: save and resume) -------------------------


def test_save_and_load_project_round_trips_a_real_timeline_and_media(tmp_path, monkeypatch):
    from jarvis.video_editor import db
    from jarvis.video_editor.media_import import MediaItem
    from jarvis.video_editor.timeline import Timeline, TimelineClip, TimelineStill, TransitionSpec

    db_file = tmp_path / "video_editor.db"
    monkeypatch.setattr(db, "VIDEO_EDITOR_DB_FILE", db_file)

    project = storage.create_project()
    db.create_project_record(project.project_id, "My Project")

    timeline = Timeline(items=(
        TimelineClip(
            clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=5.0, speed_factor=1.5,
            transition_out=TransitionSpec(kind="fade", duration_seconds=0.5),
        ),
        TimelineStill(clip_id="s1", media_item_id="m2", display_duration_seconds=2.0),
    ), aspect_ratio="1:1")
    media_items = {
        "m1": MediaItem(
            media_item_id="m1", original_filename="a.mp4", stored_path=tmp_path / "a.mp4",
            kind="video", duration_seconds=5.0, width=640, height=360, fps=30.0,
        ),
        "m2": MediaItem(
            media_item_id="m2", original_filename="b.jpg", stored_path=tmp_path / "b.jpg",
            kind="photo", duration_seconds=None, width=800, height=600, fps=None,
        ),
    }

    storage.save_project(project.project_id, timeline, media_items)
    loaded_timeline, loaded_media = storage.load_project(project.project_id)

    assert loaded_timeline == timeline
    assert loaded_media == media_items


def test_load_project_returns_none_and_empty_dict_for_a_brand_new_project(tmp_path, monkeypatch):
    from jarvis.video_editor import db

    db_file = tmp_path / "video_editor.db"
    monkeypatch.setattr(db, "VIDEO_EDITOR_DB_FILE", db_file)

    project = storage.create_project()
    db.create_project_record(project.project_id, "Brand New")

    loaded_timeline, loaded_media = storage.load_project(project.project_id)
    assert loaded_timeline is None
    assert loaded_media == {}


def test_load_project_returns_none_for_an_unknown_project_id(tmp_path, monkeypatch):
    from jarvis.video_editor import db

    db_file = tmp_path / "video_editor.db"
    monkeypatch.setattr(db, "VIDEO_EDITOR_DB_FILE", db_file)

    loaded_timeline, loaded_media = storage.load_project("does-not-exist")
    assert loaded_timeline is None
    assert loaded_media == {}
