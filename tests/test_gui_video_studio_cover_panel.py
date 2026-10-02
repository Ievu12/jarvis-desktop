"""Tests for jarvis.gui.views.video_studio.cover_panel.CoverPanel:
widget wiring and rendering, using a real (withdrawn) CTk root with
jarvis.video_studio.db redirected to a per-test tmp_path.
jarvis.video_studio.cover.extract_cover_candidates/generate_cover_text/
render_cover are mocked for these tests (no real ffmpeg/LLM call) - the
real, unmocked pipeline (frame extraction, LLM-generated cover text,
FFmpeg text-overlay rendering across every template) is covered by
tests/test_video_studio_cover.py's own real-call/real-ffmpeg tests and
by manual end-to-end testing during development (verified real
1080x1920 JPEG output for every template).

Confirms: button enable/disable states, routing GenerationTaskResults
by (kind, self), frame selection triggers cover-text auto-suggestion,
on_cover_rendered callback fires (the same session-local-callback
pattern already established for TranscriptPanel/ReelCreatorPanel's
highlights wiring - see reel_creator_panel.py's set_cover_path()
docstring for why), and a rendered cover is saved via
jarvis.video_studio.db.save_cover()."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import MagicMock

import customtkinter as ctk
import pytest

from jarvis.gui.views.video_studio import cover_panel as panel_module
from jarvis.gui.views.video_studio.cover_panel import CoverPanel
from jarvis.video_studio import db, storage
from jarvis.video_studio.cover import CoverError


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    db_file = tmp_path / "video_studio.db"
    monkeypatch.setattr(db, "VIDEO_STUDIO_DB_FILE", db_file)
    monkeypatch.setattr(panel_module.db, "VIDEO_STUDIO_DB_FILE", db_file)


@pytest.fixture(scope="module")
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


def _pump(root, predicate, *, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(0.02)


def _fake_project(tmp_path, project_id="proj1"):
    video_path = tmp_path / "original" / "video.mp4"
    video_path.parent.mkdir(parents=True)
    video_path.write_bytes(b"fake video bytes")
    (tmp_path / "cover").mkdir()
    return storage.VideoProject(project_id=project_id, root_dir=tmp_path, original_path=video_path)


def _get_project(project_id: str) -> db.ProjectRecord:
    record = db.get_project(project_id)
    assert record is not None
    return record


def _real_test_frame(tmp_path) -> Path:
    """A tiny real JPEG (not a video frame) - only needs to be openable
    by Image.open() for the thumbnail-rendering code path; content
    doesn't matter since extraction itself is mocked in these tests."""
    from PIL import Image

    path = tmp_path / "frame.jpg"
    Image.new("RGB", (10, 10), color="blue").save(path)
    return path


def _collect_texts(widget) -> list[str]:
    texts = []

    def _walk(w):
        try:
            t = w.cget("text")
            if t:
                texts.append(t)
        except Exception:
            pass
        for c in w.winfo_children():
            _walk(c)

    _walk(widget)
    return texts


# --- construction and button states -------------------------------------------------------


def test_builds_with_extract_button_disabled(root):
    panel = CoverPanel(root, llm=MagicMock())
    assert panel._extract_button.cget("state") == "disabled"


def test_loading_project_enables_extract_button(root, tmp_path):
    panel = CoverPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    panel.load_project(project, _get_project(project.project_id))
    assert panel._extract_button.cget("state") == "normal"


def test_loading_project_with_saved_cover_shows_preview(root, tmp_path):
    panel = CoverPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    cover_path = _real_test_frame(tmp_path)
    db.save_cover(project.project_id, str(cover_path))
    panel.load_project(project, _get_project(project.project_id))
    assert len(panel._preview_container.winfo_children()) > 0


# --- extract frames flow (mocked) -----------------------------------------------------------


def test_extract_click_renders_thumbnails(root, tmp_path, monkeypatch):
    panel = CoverPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    panel.load_project(project, _get_project(project.project_id))

    fake_frames = [_real_test_frame(tmp_path)]
    with monkeypatch.context() as m:
        m.setattr(panel_module, "extract_cover_candidates", MagicMock(return_value=fake_frames))
        m.setattr(panel_module, "_probe_duration", MagicMock(return_value=10.0))
        panel._on_extract_clicked()
        _pump(root, lambda: len(panel._candidate_frames) > 0)

    assert panel._candidate_frames == fake_frames
    assert panel._change_frame_button.cget("state") == "normal"


def test_extract_failure_shows_error(root, tmp_path, monkeypatch):
    panel = CoverPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    panel.load_project(project, _get_project(project.project_id))

    with monkeypatch.context() as m:
        m.setattr(panel_module, "extract_cover_candidates", MagicMock(side_effect=CoverError("no frames")))
        m.setattr(panel_module, "_probe_duration", MagicMock(return_value=10.0))
        panel._on_extract_clicked()
        _pump(root, lambda: any("no frames" in t for t in _collect_texts(panel._status_container)))

    texts = _collect_texts(panel._status_container)
    assert any("no frames" in t for t in texts)


def test_selecting_a_frame_enables_generate_button(root, tmp_path):
    panel = CoverPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    panel.load_project(project, _get_project(project.project_id))

    frame = _real_test_frame(tmp_path)
    panel._candidate_frames = [frame]
    panel._on_frame_selected(frame)
    assert panel._selected_frame == frame
    assert panel._generate_button.cget("state") == "normal"


# --- generate cover flow (mocked) -----------------------------------------------------------


def test_generate_click_renders_cover_and_saves_it(root, tmp_path, monkeypatch):
    panel = CoverPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    panel.load_project(project, _get_project(project.project_id))

    frame = _real_test_frame(tmp_path)
    panel._selected_frame = frame
    panel._text_entry.insert(0, "My Cover Title")

    output_cover = _real_test_frame(tmp_path)  # reuse a real openable image as the "rendered" output
    with monkeypatch.context() as m:
        m.setattr(panel_module, "render_cover", MagicMock(return_value=output_cover))
        panel._on_generate_cover_clicked()
        _pump(root, lambda: panel._rendered_cover_path is not None)

    assert panel._rendered_cover_path == output_cover
    saved = _get_project(project.project_id)
    assert saved.cover_path == str(output_cover)


def test_generate_without_text_shows_error(root, tmp_path):
    panel = CoverPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    panel.load_project(project, _get_project(project.project_id))

    frame = _real_test_frame(tmp_path)
    panel._selected_frame = frame
    panel._on_generate_cover_clicked()
    texts = _collect_texts(panel._status_container)
    assert any("Enter cover text" in t for t in texts)


def test_on_cover_rendered_callback_fires(root, tmp_path, monkeypatch):
    callback = MagicMock()
    panel = CoverPanel(root, llm=MagicMock(), on_cover_rendered=callback)
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    panel.load_project(project, _get_project(project.project_id))

    frame = _real_test_frame(tmp_path)
    panel._selected_frame = frame
    panel._text_entry.insert(0, "My Cover Title")

    output_cover = _real_test_frame(tmp_path)
    with monkeypatch.context() as m:
        m.setattr(panel_module, "render_cover", MagicMock(return_value=output_cover))
        panel._on_generate_cover_clicked()
        _pump(root, lambda: callback.called)

    callback.assert_called_once_with(str(output_cover))


def test_generate_failure_shows_error(root, tmp_path, monkeypatch):
    panel = CoverPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    panel.load_project(project, _get_project(project.project_id))

    frame = _real_test_frame(tmp_path)
    panel._selected_frame = frame
    panel._text_entry.insert(0, "My Cover Title")

    with monkeypatch.context() as m:
        m.setattr(panel_module, "render_cover", MagicMock(side_effect=CoverError("render boom")))
        panel._on_generate_cover_clicked()
        _pump(root, lambda: any("render boom" in t for t in _collect_texts(panel._status_container)))

    texts = _collect_texts(panel._status_container)
    assert any("render boom" in t for t in texts)


# --- cover text auto-suggestion (mocked) ---------------------------------------------------


def test_selecting_frame_triggers_text_suggestion_when_transcript_available(root, tmp_path, monkeypatch):
    panel = CoverPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    from jarvis.video_studio.transcribe import TranscriptionResult, TranscriptSegment
    import dataclasses

    transcription = TranscriptionResult(
        segments=[TranscriptSegment(0.0, 3.0, "Hello world.")], full_text="Hello world.",
        language=None, error=None,
    )
    db.save_transcript(project.project_id, dataclasses.asdict(transcription))
    panel.load_project(project, _get_project(project.project_id))

    frame = _real_test_frame(tmp_path)
    panel._candidate_frames = [frame]
    with monkeypatch.context() as m:
        m.setattr(panel_module, "generate_cover_text", MagicMock(return_value="Generated Title"))
        panel._on_frame_selected(frame)
        _pump(root, lambda: panel._text_entry.get() == "Generated Title")

    assert panel._text_entry.get() == "Generated Title"
