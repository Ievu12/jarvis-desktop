"""Tests for jarvis.gui.views.video_studio.dashboard.VideoStudioView:
the top-level view shown for the sidebar's "🎬 AI Video Studio" nav
item. Uses a real (withdrawn) CTk root, with jarvis.video_studio.db/
.storage redirected to a per-test tmp_path (see the fixtures below) so
no test touches the real .jarvis/video_studio.db or
.jarvis/video_studio/projects/ directory.

The upload/analysis tests use a REAL small ffmpeg-generated test video
and run the actual background upload+analyze pipeline to completion
(pumping the Tk event loop) rather than mocking
jarvis.video_studio.analysis/.storage - this view's own logic (routing
GenerationTaskResults by the (kind, self) source tuple, rendering
VideoAnalysis fields, handling the upload-error string case) is exactly
what a mock of those modules would let a bug slip through un-tested
(see this file's fix for the "unpacking a str as (project, analysis)"
bug, found by this same real-pipeline testing approach during
development). Skipped entirely if ffmpeg/ffprobe aren't on PATH.
"""

from __future__ import annotations

import subprocess
import time
from unittest.mock import MagicMock

import customtkinter as ctk
import pytest

from jarvis.gui.views.video_studio import dashboard as dashboard_module
from jarvis.gui.views.video_studio.dashboard import VideoStudioView
from jarvis.video_studio import db, storage
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available

requires_ffmpeg = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg/ffprobe not on PATH")


@pytest.fixture(autouse=True)
def _isolated_video_studio_storage(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    db_file = tmp_path / "video_studio.db"
    monkeypatch.setattr(storage, "VIDEO_STUDIO_PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(db, "VIDEO_STUDIO_DB_FILE", db_file)
    monkeypatch.setattr(dashboard_module.storage, "VIDEO_STUDIO_PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(dashboard_module.db, "VIDEO_STUDIO_DB_FILE", db_file)


@pytest.fixture(scope="module")
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


def _pump(root, predicate, *, timeout=15.0):
    """Drives the Tk event loop until `predicate()` is true or
    `timeout` elapses - mirrors this view's own .after() polling."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        root.update()
        if predicate():
            return
        time.sleep(0.05)


@pytest.fixture(scope="module")
def test_video(tmp_path_factory):
    if not ffmpeg_available():
        pytest.skip("ffmpeg/ffprobe not on PATH")
    out_dir = tmp_path_factory.mktemp("video_studio_gui")
    video_path = out_dir / "test.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=320x240:d=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-c:v", "libx264", "-c:a", "aac", "-t", "2", str(video_path),
        ],
        capture_output=True, timeout=30, check=True,
    )
    return video_path


# --- construction ------------------------------------------------------------------------


def test_builds_with_llm(root):
    view = VideoStudioView(root, llm=MagicMock())
    assert isinstance(view, ctk.CTkFrame)


def test_builds_without_llm(root):
    view = VideoStudioView(root, llm=None)
    assert isinstance(view, ctk.CTkFrame)


def test_refresh_does_not_raise(root):
    view = VideoStudioView(root, llm=MagicMock())
    view.refresh()


def test_has_transcript_reel_creator_and_cover_panels(root):
    view = VideoStudioView(root, llm=MagicMock())
    assert view._transcript_panel is not None
    assert view._reel_creator_panel is not None
    assert view._cover_panel is not None


def test_transcript_panel_highlights_callback_is_wired_to_reel_creator_panel(root):
    # Regression test for a real bug found during development: without
    # this wiring, ReelCreatorPanel never learned about highlights
    # found in the SAME session (only ones already saved before the
    # project was opened) - see TranscriptPanel.__init__'s
    # on_highlights_updated docstring and ReelCreatorPanel
    # .set_candidates()'s docstring for the full story.
    view = VideoStudioView(root, llm=MagicMock())
    assert view._transcript_panel._on_highlights_updated == view._reel_creator_panel.set_candidates


def test_transcript_panel_transcript_callback_fans_out_to_reel_creator_and_cover_panels(root):
    # Regression test for a real bug found by full end-to-end hand
    # testing (upload -> transcribe -> ... -> cover -> hand-off, all in
    # one session): without this wiring, ReelCreatorPanel's hand-off
    # and CoverPanel's auto-suggested cover text both silently used an
    # EMPTY transcript right after transcribing, since each panel only
    # ever saw a transcript that existed BEFORE the project was opened
    # (from load_project()) - see TranscriptPanel
    # .set_on_transcript_updated()'s own docstring for the full story.
    view = VideoStudioView(root, llm=MagicMock())
    assert view._transcript_panel._on_transcript_updated is not None
    view._transcript_panel._on_transcript_updated("some transcript text")
    assert view._reel_creator_panel._transcript_text == "some transcript text"
    assert view._cover_panel._transcript_text == "some transcript text"


def test_cover_panel_render_callback_is_wired_to_reel_creator_panel(root):
    view = VideoStudioView(root, llm=MagicMock())
    assert view._cover_panel._on_cover_rendered is not None
    view._cover_panel._on_cover_rendered("/tmp/cover.jpg")
    assert view._reel_creator_panel._cover_path == "/tmp/cover.jpg"


def test_shows_no_projects_message_when_empty(root):
    view = VideoStudioView(root, llm=MagicMock())
    texts = _collect_texts(view._recent_projects_container)
    assert any("No projects yet" in t for t in texts)


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


# --- upload + analysis (real ffmpeg pipeline) ---------------------------------------------


@requires_ffmpeg
def test_upload_creates_project_and_renders_analysis(root, test_video):
    view = VideoStudioView(root, llm=MagicMock())
    view._start_upload(test_video)
    _pump(root, lambda: view._current_project is not None)

    assert view._current_project is not None
    assert view._current_project.original_path.is_file()
    # The original file the person picked must be untouched.
    assert test_video.is_file()

    texts = _collect_texts(view._analysis_container)
    assert any("VIDEO ANALYSIS" in t for t in texts)
    assert any("YES" in t for t in texts)  # audio detected


@requires_ffmpeg
def test_upload_never_modifies_or_deletes_the_original(root, test_video):
    original_bytes = test_video.read_bytes()
    view = VideoStudioView(root, llm=MagicMock())
    view._start_upload(test_video)
    _pump(root, lambda: view._current_project is not None)
    assert test_video.read_bytes() == original_bytes


def test_upload_unsupported_format_shows_error_not_crash(root, tmp_path):
    bad_file = tmp_path / "notes.txt"
    bad_file.write_text("hello")
    view = VideoStudioView(root, llm=MagicMock())
    view._start_upload(bad_file)
    _pump(root, lambda: any("Unsupported file type" in t for t in _collect_texts(view._status_container)))
    texts = _collect_texts(view._status_container)
    assert any("Unsupported file type" in t for t in texts)
    # Must not have crashed the view or created a project.
    assert view._current_project is None


@requires_ffmpeg
def test_uploaded_project_appears_in_recent_projects(root, test_video):
    view = VideoStudioView(root, llm=MagicMock())
    view._start_upload(test_video)
    _pump(root, lambda: view._current_project is not None)
    # _handle_upload_result() calls _refresh_recent_projects() itself.
    texts = _collect_texts(view._recent_projects_container)
    assert any(test_video.name in t for t in texts)


@requires_ffmpeg
def test_reopening_a_project_uses_cached_analysis_not_reanalyzing(root, test_video, monkeypatch):
    view = VideoStudioView(root, llm=MagicMock())
    view._start_upload(test_video)
    _pump(root, lambda: view._current_project is not None)
    assert view._current_project is not None
    project_id = view._current_project.project_id

    view2 = VideoStudioView(root, llm=MagicMock())
    with monkeypatch.context() as m:
        m.setattr(dashboard_module, "analyze_video", MagicMock(side_effect=AssertionError("should not re-analyze")))
        view2._open_project(project_id)
        root.update()
    texts = _collect_texts(view2._analysis_container)
    assert any("VIDEO ANALYSIS" in t for t in texts)


def test_open_nonexistent_project_shows_error(root):
    view = VideoStudioView(root, llm=MagicMock())
    view._open_project("does-not-exist")
    root.update()
    texts = _collect_texts(view._status_container)
    assert any("could no longer be found" in t for t in texts)
