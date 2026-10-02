"""Tests for jarvis.gui.views.video_studio.transcript_panel
.TranscriptPanel: widget wiring and rendering, using a real (withdrawn)
CTk root with jarvis.video_studio.db redirected to a per-test tmp_path.
jarvis.video_studio.transcribe.transcribe_video and
jarvis.video_studio.highlights.find_highlights are mocked for the
button-click/result-routing tests (no real ffmpeg/whisper/LLM call) -
the real, unmocked pipeline is already covered end-to-end by manual
testing during development and by
tests/test_video_studio_transcribe.py/test_video_studio_highlights.py's
own real-call tests; this file's job is the VIEW's own logic: button
enable/disable states, routing GenerationTaskResults by (kind, self),
rendering candidate cards, and the from_dict() reconstruction bug this
module's own development caught (see the "real project data" tests
below, which exercise the actual dataclasses.asdict() round-trip
through jarvis.video_studio.db rather than hand-built dicts, so a
regression in TranscriptionResult.from_dict()/HighlightResult.from_dict()
would be caught here too, not just in their own unit tests)."""

from __future__ import annotations

import dataclasses
import time
from unittest.mock import MagicMock

import customtkinter as ctk
import pytest

from jarvis.gui.views.video_studio import transcript_panel as panel_module
from jarvis.gui.views.video_studio.transcript_panel import TranscriptPanel
from jarvis.video_studio import db, storage
from jarvis.video_studio.analysis import VideoAnalysis
from jarvis.video_studio.highlights import HighlightCandidate, HighlightResult
from jarvis.video_studio.transcribe import TranscriptionResult, TranscriptSegment


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
    return storage.VideoProject(project_id=project_id, root_dir=tmp_path, original_path=video_path)


def _get_project(project_id: str) -> db.ProjectRecord:
    """db.get_project() -> ProjectRecord | None; every call site in
    this file has just created the project, so it is always present -
    this narrows the type for load_project() without a bare `assert`
    at every call site."""
    record = db.get_project(project_id)
    assert record is not None
    return record


def _analysis() -> VideoAnalysis:
    return VideoAnalysis(
        duration_seconds=10.0, width=640, height=360, aspect_ratio="16:9", fps=25.0,
        has_audio=True, file_size_bytes=1000, video_codec="h264", audio_codec="aac",
        scene_changes=[], silence_gaps=[], error=None,
    )


def _transcription() -> TranscriptionResult:
    return TranscriptionResult(
        segments=[TranscriptSegment(0.0, 3.0, "Hello world this is a test.")],
        full_text="Hello world this is a test.", language=None, error=None,
    )


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


def test_builds_with_all_buttons_disabled_initially(root):
    panel = TranscriptPanel(root, llm=MagicMock())
    assert panel._transcribe_button.cget("state") == "disabled"
    assert panel._regenerate_button.cget("state") == "disabled"
    assert panel._find_highlights_button.cget("state") == "disabled"


def test_loading_project_without_transcript_enables_transcribe_only(root, tmp_path):
    panel = TranscriptPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    record = _get_project(project.project_id)
    panel.load_project(project, record)
    assert panel._transcribe_button.cget("state") == "normal"
    assert panel._find_highlights_button.cget("state") == "disabled"


def test_loading_project_with_saved_transcript_enables_find_highlights(root, tmp_path):
    panel = TranscriptPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_transcript(project.project_id, dataclasses.asdict(_transcription()))
    record = _get_project(project.project_id)
    panel.load_project(project, record)
    assert panel._find_highlights_button.cget("state") == "normal"
    textbox = next(
        w for w in panel._transcript_container.winfo_children()[0].winfo_children()
        if isinstance(w, ctk.CTkTextbox)
    )
    assert "Hello world" in textbox.get("1.0", "end")


def test_loading_project_without_llm_keeps_find_highlights_disabled(root, tmp_path):
    panel = TranscriptPanel(root, llm=None)
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_transcript(project.project_id, dataclasses.asdict(_transcription()))
    record = _get_project(project.project_id)
    panel.load_project(project, record)
    assert panel._find_highlights_button.cget("state") == "disabled"


def test_loading_project_with_saved_highlights_renders_candidate_cards(root, tmp_path):
    panel = TranscriptPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_transcript(project.project_id, dataclasses.asdict(_transcription()))
    highlights = HighlightResult(
        candidates=[
            HighlightCandidate(
                start_seconds=0.0, end_seconds=3.0, transcript_text="Hello world.",
                reason="Strong opener.", suggested_hook="Did you know...", confidence="high",
            )
        ],
        insufficient_data=False, message=None,
    )
    db.save_highlights(project.project_id, dataclasses.asdict(highlights))
    record = _get_project(project.project_id)
    panel.load_project(project, record)
    texts = _collect_texts(panel._highlights_container)
    assert any("Strong opener." in t for t in texts)
    assert any("Did you know" in t for t in texts)


# --- transcribe flow (mocked) --------------------------------------------------------------


def test_transcribe_click_calls_transcribe_video_and_renders_result(root, tmp_path, monkeypatch):
    panel = TranscriptPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    record = _get_project(project.project_id)
    panel.load_project(project, record)

    with monkeypatch.context() as m:
        m.setattr(panel_module, "transcribe_video", MagicMock(return_value=_transcription()))
        panel._on_transcribe_clicked()
        _pump(root, lambda: panel._transcription is not None)

    assert panel._transcription is not None
    assert panel._transcription.full_text == "Hello world this is a test."
    saved = _get_project(project.project_id)
    assert saved.transcript_data is not None


def test_transcribe_failure_shows_error_and_does_not_crash(root, tmp_path, monkeypatch):
    panel = TranscriptPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    record = _get_project(project.project_id)
    panel.load_project(project, record)

    with monkeypatch.context() as m:
        m.setattr(panel_module, "transcribe_video", MagicMock(side_effect=RuntimeError("boom")))
        panel._on_transcribe_clicked()
        _pump(root, lambda: any("boom" in t for t in _collect_texts(panel._status_container)))

    texts = _collect_texts(panel._status_container)
    assert any("boom" in t for t in texts)


# --- find highlights flow (mocked) ----------------------------------------------------------


def test_find_highlights_click_renders_candidates(root, tmp_path, monkeypatch):
    panel = TranscriptPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_analysis(project.project_id, dataclasses.asdict(_analysis()))
    db.save_transcript(project.project_id, dataclasses.asdict(_transcription()))
    record = _get_project(project.project_id)
    panel.load_project(project, record)

    fake_result = HighlightResult(
        candidates=[
            HighlightCandidate(
                start_seconds=0.0, end_seconds=3.0, transcript_text="Hello world.",
                reason="Good hook.", suggested_hook="Hook here", confidence="medium",
            )
        ],
        insufficient_data=False, message=None,
    )
    with monkeypatch.context() as m:
        m.setattr(panel_module, "find_highlights", MagicMock(return_value=fake_result))
        panel._on_find_highlights_clicked()
        _pump(root, lambda: len(panel._highlights_container.winfo_children()) > 0)

    texts = _collect_texts(panel._highlights_container)
    assert any("Good hook." in t for t in texts)
    saved = _get_project(project.project_id)
    assert saved.highlights_data is not None


def test_find_highlights_insufficient_data_shows_message(root, tmp_path, monkeypatch):
    panel = TranscriptPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_analysis(project.project_id, dataclasses.asdict(_analysis()))
    db.save_transcript(project.project_id, dataclasses.asdict(_transcription()))
    record = _get_project(project.project_id)
    panel.load_project(project, record)

    insufficient = HighlightResult(candidates=[], insufficient_data=True, message="Nothing stood out.")
    with monkeypatch.context() as m:
        m.setattr(panel_module, "find_highlights", MagicMock(return_value=insufficient))
        panel._on_find_highlights_clicked()
        _pump(root, lambda: any("Nothing stood out." in t for t in _collect_texts(panel._highlights_container)))

    texts = _collect_texts(panel._highlights_container)
    assert any("Nothing stood out." in t for t in texts)


# --- reject / preview buttons ----------------------------------------------------------------


def test_reject_removes_candidate_from_view_but_not_from_db(root, tmp_path):
    panel = TranscriptPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_transcript(project.project_id, dataclasses.asdict(_transcription()))
    candidate = HighlightCandidate(
        start_seconds=0.0, end_seconds=3.0, transcript_text="Hello world.",
        reason="r", suggested_hook="h", confidence="low",
    )
    highlights = HighlightResult(candidates=[candidate], insufficient_data=False, message=None)
    db.save_highlights(project.project_id, dataclasses.asdict(highlights))
    record = _get_project(project.project_id)
    panel.load_project(project, record)

    assert len(panel._highlights_container.winfo_children()) == 1
    panel._on_reject_clicked(candidate)
    assert len(panel._highlights_container.winfo_children()) == 0

    # The database's saved highlights are untouched - re-loading the
    # project shows the candidate again (Reject is session-only).
    record2 = _get_project(project.project_id)
    panel.load_project(project, record2)
    assert len(panel._highlights_container.winfo_children()) == 1


def test_preview_use_clip_trim_show_not_yet_available_message(root, tmp_path):
    panel = TranscriptPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_transcript(project.project_id, dataclasses.asdict(_transcription()))
    candidate = HighlightCandidate(
        start_seconds=0.0, end_seconds=3.0, transcript_text="Hello world.",
        reason="r", suggested_hook="h", confidence="low",
    )
    highlights = HighlightResult(candidates=[candidate], insufficient_data=False, message=None)
    db.save_highlights(project.project_id, dataclasses.asdict(highlights))
    record = _get_project(project.project_id)
    panel.load_project(project, record)

    handler = panel._make_not_yet_available("Preview")
    handler()
    texts = _collect_texts(panel._status_container)
    assert any("coming in a later update" in t for t in texts)
