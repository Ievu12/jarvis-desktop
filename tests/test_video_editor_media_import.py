"""Tests for jarvis.video_editor.media_import: real video (via ffmpeg
lavfi synthetic fixtures) and real photo (via Pillow) import, probed for
actual properties. Video-dependent tests skip-guarded by
ffmpeg_available(); photo tests have no such dependency and always run.

Confirms: video and photo both import successfully with real measured
properties, an unsupported extension is rejected with a clear message,
a corrupt file with a valid-looking extension is rejected (never
silently "succeeds" with fabricated properties), and the local
SUPPORTED_VIDEO_EXTENSIONS/SUPPORTED_IMAGE_EXTENSIONS constants never
touch jarvis.video_studio.storage.SUPPORTED_EXTENSIONS at all."""

from __future__ import annotations

import subprocess

import pytest
from PIL import Image

from jarvis.video_editor import media_import, storage
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available


@pytest.fixture(autouse=True)
def _isolated_projects_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "VIDEO_EDITOR_PROJECTS_DIR", tmp_path / "video_editor_projects")


def _make_real_clip(path, *, duration_seconds: float = 2.0, width: int = 640, height: int = 360):
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=blue:s={width}x{height}:d={duration_seconds}",
            "-c:v", "libx264", "-t", str(duration_seconds), str(path),
        ],
        capture_output=True, timeout=30, check=True,
    )
    return path


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_import_real_video_reports_real_measured_properties(tmp_path):
    project = storage.create_project()
    video_path = _make_real_clip(tmp_path / "clip.mp4", duration_seconds=3.0, width=640, height=360)

    item = media_import.import_media(video_path, project, media_item_id="m1")

    assert item.kind == "video"
    assert item.width == 640
    assert item.height == 360
    assert abs(item.duration_seconds - 3.0) < 0.5
    assert item.fps is not None
    assert item.stored_path.is_file()
    assert item.stored_path.parent == project.media_dir


def test_import_real_photo_reports_real_measured_properties(tmp_path):
    project = storage.create_project()
    photo_path = tmp_path / "photo.png"
    Image.new("RGB", (1080, 1920), color=(10, 20, 30)).save(photo_path, "PNG")

    item = media_import.import_media(photo_path, project, media_item_id="m1")

    assert item.kind == "photo"
    assert item.width == 1080
    assert item.height == 1920
    assert item.duration_seconds is None
    assert item.fps is None
    assert item.stored_path.is_file()


def test_import_media_rejects_an_unsupported_extension(tmp_path):
    project = storage.create_project()
    bad_path = tmp_path / "notes.txt"
    bad_path.write_text("not media")

    with pytest.raises(media_import.MediaImportError, match="Unsupported file type"):
        media_import.import_media(bad_path, project, media_item_id="m1")


def test_import_media_rejects_a_missing_file(tmp_path):
    project = storage.create_project()
    with pytest.raises(media_import.MediaImportError, match="not found"):
        media_import.import_media(tmp_path / "ghost.mp4", project, media_item_id="m1")


def test_import_media_rejects_a_corrupt_photo_with_a_valid_extension(tmp_path):
    project = storage.create_project()
    fake_photo = tmp_path / "corrupt.jpg"
    fake_photo.write_bytes(b"this is not actually a jpeg")

    with pytest.raises(media_import.MediaImportError, match="Couldn't read"):
        media_import.import_media(fake_photo, project, media_item_id="m1")


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_import_media_rejects_a_corrupt_video_with_a_valid_extension(tmp_path):
    project = storage.create_project()
    fake_video = tmp_path / "corrupt.mp4"
    fake_video.write_bytes(b"this is not actually an mp4")

    with pytest.raises(media_import.MediaImportError, match="Couldn't read"):
        media_import.import_media(fake_video, project, media_item_id="m1")


def test_supported_extensions_are_local_and_independent_of_video_studio():
    # The real isolation guarantee: this module's own constants are a
    # SEPARATE object from jarvis.video_studio.storage's own set - never
    # the same object, never derived by mutating it.
    from jarvis.video_studio.storage import SUPPORTED_EXTENSIONS as video_studio_extensions

    assert media_import.SUPPORTED_VIDEO_EXTENSIONS is not video_studio_extensions
    assert media_import.SUPPORTED_IMAGE_EXTENSIONS  # photos are supported here, never in video_studio
    assert ".jpg" not in video_studio_extensions  # confirms video_studio truly stays video-only
