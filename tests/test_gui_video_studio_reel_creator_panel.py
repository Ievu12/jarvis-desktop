"""Tests for jarvis.gui.views.video_studio.reel_creator_panel
.ReelCreatorPanel: widget wiring and rendering, using a real (withdrawn)
CTk root with jarvis.video_studio.db redirected to a per-test tmp_path.
jarvis.video_studio.reel.generate_reel_edit and jarvis.video_studio
.export.export_reel are mocked for these tests (no real LLM/ffmpeg
call) - the real, unmocked pipeline is covered end-to-end by manual
testing during development (upload -> transcribe -> find highlights ->
create reel -> export, verified against a real exported, playable
1080x1920 MP4) and by tests/test_video_studio_reel.py/
test_video_studio_export.py's own real-call/real-ffmpeg tests.

Confirms: button enable/disable states, routing GenerationTaskResults
by (kind, self), set_candidates() (the fix for a real bug found during
development - see its own docstring in reel_creator_panel.py - where
ReelCreatorPanel never learned about highlights found in the SAME
session, only ones already saved before the project was opened), and
the export result panel's four buttons (Open File/Create Another/
Create Caption/Open Instagram Manager)."""

from __future__ import annotations

import dataclasses
import time
from pathlib import Path
from unittest.mock import MagicMock

import customtkinter as ctk
import pytest

from jarvis.gui.views.video_studio import reel_creator_panel as panel_module
from jarvis.gui.views.video_studio.reel_creator_panel import ReelCreatorPanel
from jarvis.video_studio import db, storage
from jarvis.video_studio.export import ExportResult
from jarvis.video_studio.highlights import HighlightCandidate, HighlightResult
from jarvis.video_studio.reel import PlannedClip, ReelEditPlan
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
    exports_dir = tmp_path / "exports"
    exports_dir.mkdir()
    return storage.VideoProject(project_id=project_id, root_dir=tmp_path, original_path=video_path)


def _get_project(project_id: str) -> db.ProjectRecord:
    record = db.get_project(project_id)
    assert record is not None
    return record


def _transcription() -> TranscriptionResult:
    return TranscriptionResult(
        segments=[TranscriptSegment(0.0, 5.0, "Hello world.")],
        full_text="Hello world.", language=None, error=None,
    )


def _highlights() -> HighlightResult:
    return HighlightResult(
        candidates=[
            HighlightCandidate(
                start_seconds=0.0, end_seconds=5.0, transcript_text="Hello world.",
                reason="Strong opener.", suggested_hook="Did you know...", confidence="high",
            )
        ],
        insufficient_data=False, message=None,
    )


def _plan() -> ReelEditPlan:
    return ReelEditPlan(
        clips=[PlannedClip(0.0, 5.0, "Hello world.", "Caption here")],
        hook="Big hook", cta="Follow now", target_duration_seconds=30, style="Educational",
        pacing="Normal", total_duration_seconds=5.0, silence_removal_suggested=False,
        zoom_crop_suggested=True, insufficient_data=False, message=None,
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


def test_builds_with_create_button_disabled(root):
    panel = ReelCreatorPanel(root, llm=MagicMock())
    assert panel._create_button.cget("state") == "disabled"


def test_loading_project_without_highlights_keeps_create_disabled(root, tmp_path):
    panel = ReelCreatorPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    panel.load_project(project, _get_project(project.project_id))
    assert panel._create_button.cget("state") == "disabled"


def test_loading_project_with_highlights_enables_create(root, tmp_path):
    panel = ReelCreatorPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_highlights(project.project_id, dataclasses.asdict(_highlights()))
    panel.load_project(project, _get_project(project.project_id))
    assert panel._create_button.cget("state") == "normal"


def test_loading_project_without_llm_keeps_create_disabled(root, tmp_path):
    panel = ReelCreatorPanel(root, llm=None)
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_highlights(project.project_id, dataclasses.asdict(_highlights()))
    panel.load_project(project, _get_project(project.project_id))
    assert panel._create_button.cget("state") == "disabled"


def test_set_candidates_enables_create_without_full_reload(root, tmp_path):
    # Regression test for the real bug found during development: before
    # ReelCreatorPanel.set_candidates() existed, finding highlights in
    # the SAME session as opening a project never updated this panel's
    # candidate list (only load_project() did, and that only runs on
    # upload/(re)open) - Create Reel stayed disabled even with valid
    # highlights just found.
    panel = ReelCreatorPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    panel.load_project(project, _get_project(project.project_id))
    assert panel._create_button.cget("state") == "disabled"

    panel.set_candidates(_highlights())
    assert panel._create_button.cget("state") == "normal"
    assert len(panel._candidates) == 1


def test_loading_project_with_saved_plan_renders_it(root, tmp_path):
    panel = ReelCreatorPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_reel_plan(project.project_id, dataclasses.asdict(_plan()))
    panel.load_project(project, _get_project(project.project_id))
    texts = _collect_texts(panel._plan_container)
    assert any("Big hook" in t for t in texts)


# --- create reel flow (mocked) --------------------------------------------------------------


def test_create_reel_click_renders_plan_and_saves_it(root, tmp_path, monkeypatch):
    panel = ReelCreatorPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_highlights(project.project_id, dataclasses.asdict(_highlights()))
    panel.load_project(project, _get_project(project.project_id))

    with monkeypatch.context() as m:
        m.setattr(panel_module, "generate_reel_edit", MagicMock(return_value=_plan()))
        panel._on_create_clicked()
        _pump(root, lambda: panel._plan is not None)

    assert panel._plan is not None
    assert panel._plan.hook == "Big hook"
    saved = _get_project(project.project_id)
    assert saved.reel_plan_data is not None
    assert saved.status == "reel_created"


def test_create_reel_insufficient_data_shows_message(root, tmp_path, monkeypatch):
    panel = ReelCreatorPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_highlights(project.project_id, dataclasses.asdict(_highlights()))
    panel.load_project(project, _get_project(project.project_id))

    insufficient = ReelEditPlan(
        clips=[], hook="", cta="", target_duration_seconds=30, style="s", pacing="p",
        total_duration_seconds=0.0, silence_removal_suggested=False, zoom_crop_suggested=False,
        insufficient_data=True, message="Nothing fits.",
    )
    with monkeypatch.context() as m:
        m.setattr(panel_module, "generate_reel_edit", MagicMock(return_value=insufficient))
        panel._on_create_clicked()
        _pump(root, lambda: any("Nothing fits." in t for t in _collect_texts(panel._plan_container)))

    texts = _collect_texts(panel._plan_container)
    assert any("Nothing fits." in t for t in texts)


# --- export flow (mocked) ----------------------------------------------------------------


def test_export_click_renders_result_and_saves_record(root, tmp_path, monkeypatch):
    panel = ReelCreatorPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_highlights(project.project_id, dataclasses.asdict(_highlights()))
    db.save_reel_plan(project.project_id, dataclasses.asdict(_plan()))
    panel.load_project(project, _get_project(project.project_id))

    fake_result = ExportResult(
        output_path=Path("exported.mp4"), export_format="instagram_reel", duration_seconds=5.0,
        width=1080, height=1920, file_size_bytes=12345,
    )
    with monkeypatch.context() as m:
        m.setattr(panel_module, "export_reel", MagicMock(return_value=fake_result))
        panel._on_export_clicked()
        _pump(root, lambda: len(panel._export_result_container.winfo_children()) > 0)

    texts = _collect_texts(panel._export_result_container)
    assert any("exported.mp4" in t for t in texts)
    saved_exports = db.list_exports(project.project_id)
    assert len(saved_exports) == 1
    assert saved_exports[0].export_format == "instagram_reel"

    updated_record = _get_project(project.project_id)
    assert updated_record.status == "exported"


def test_export_failure_shows_error_not_crash(root, tmp_path, monkeypatch):
    panel = ReelCreatorPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_highlights(project.project_id, dataclasses.asdict(_highlights()))
    db.save_reel_plan(project.project_id, dataclasses.asdict(_plan()))
    panel.load_project(project, _get_project(project.project_id))

    with monkeypatch.context() as m:
        m.setattr(panel_module, "export_reel", MagicMock(side_effect=RuntimeError("ffmpeg boom")))
        panel._on_export_clicked()
        _pump(root, lambda: any("ffmpeg boom" in t for t in _collect_texts(panel._status_container)))

    texts = _collect_texts(panel._status_container)
    assert any("ffmpeg boom" in t for t in texts)


def test_export_result_has_all_four_buttons(root, tmp_path, monkeypatch):
    panel = ReelCreatorPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_highlights(project.project_id, dataclasses.asdict(_highlights()))
    db.save_reel_plan(project.project_id, dataclasses.asdict(_plan()))
    panel.load_project(project, _get_project(project.project_id))

    fake_result = ExportResult(
        output_path=Path("exported.mp4"), export_format="instagram_reel", duration_seconds=5.0,
        width=1080, height=1920, file_size_bytes=12345,
    )
    with monkeypatch.context() as m:
        m.setattr(panel_module, "export_reel", MagicMock(return_value=fake_result))
        panel._on_export_clicked()
        _pump(root, lambda: len(panel._export_result_container.winfo_children()) > 0)

    def find_buttons(w, acc):
        if isinstance(w, ctk.CTkButton):
            acc.append(w)
        for c in w.winfo_children():
            find_buttons(c, acc)
        return acc

    button_texts = {b.cget("text") for b in find_buttons(panel._export_result_container, [])}
    assert button_texts == {"Open File", "Create Another", "Create Caption", "Open Instagram Manager"}


# --- Instagram AI Manager hand-off (mocked) --------------------------------------------


def test_create_caption_click_sends_content_and_stays_on_view(root, tmp_path, monkeypatch):
    navigate = MagicMock()
    panel = ReelCreatorPanel(root, llm=MagicMock(), navigate=navigate)
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_transcript(project.project_id, dataclasses.asdict(_transcription()))
    db.save_highlights(project.project_id, dataclasses.asdict(_highlights()))
    db.save_reel_plan(project.project_id, dataclasses.asdict(_plan()))
    panel.load_project(project, _get_project(project.project_id))

    fake_handoff = panel_module.HandoffResult(
        hook_set_id=1, caption_id=2, cta_set_id=3, hashtag_set_id=4, cover_path=None,
        suggested_posting_time="Friday around 14:00 (high confidence)", insufficient_data=False, message=None,
    )
    with monkeypatch.context() as m:
        m.setattr(panel_module, "send_to_instagram_manager", MagicMock(return_value=fake_handoff))
        panel._on_handoff_clicked(navigate_after=False)
        _pump(root, lambda: any("Sent" in t for t in _collect_texts(panel._status_container)))

    texts = _collect_texts(panel._status_container)
    assert any("hooks" in t and "caption" in t for t in texts)
    navigate.assert_not_called()
    saved = _get_project(project.project_id)
    assert saved.handoff_data == {
        "hook_set_id": 1, "caption_id": 2, "cta_set_id": 3, "hashtag_set_id": 4, "cover_path": None,
    }


def test_open_instagram_manager_click_navigates_after_success(root, tmp_path, monkeypatch):
    navigate = MagicMock()
    panel = ReelCreatorPanel(root, llm=MagicMock(), navigate=navigate)
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_transcript(project.project_id, dataclasses.asdict(_transcription()))
    db.save_highlights(project.project_id, dataclasses.asdict(_highlights()))
    db.save_reel_plan(project.project_id, dataclasses.asdict(_plan()))
    panel.load_project(project, _get_project(project.project_id))

    fake_handoff = panel_module.HandoffResult(
        hook_set_id=1, caption_id=None, cta_set_id=None, hashtag_set_id=None, cover_path=None,
        suggested_posting_time=None, insufficient_data=False, message=None,
    )
    with monkeypatch.context() as m:
        m.setattr(panel_module, "send_to_instagram_manager", MagicMock(return_value=fake_handoff))
        panel._on_handoff_clicked(navigate_after=True)
        _pump(root, lambda: navigate.called)

    navigate.assert_called_once_with("instagram_ai_manager")


def test_handoff_without_transcript_or_plan_shows_error_without_calling_llm(root, tmp_path):
    panel = ReelCreatorPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    panel.load_project(project, _get_project(project.project_id))

    panel._on_handoff_clicked(navigate_after=False)
    texts = _collect_texts(panel._status_container)
    assert any("Transcribe the video" in t for t in texts)


def test_handoff_insufficient_data_shows_message(root, tmp_path, monkeypatch):
    panel = ReelCreatorPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_transcript(project.project_id, dataclasses.asdict(_transcription()))
    panel.load_project(project, _get_project(project.project_id))

    insufficient = panel_module.HandoffResult(
        hook_set_id=None, caption_id=None, cta_set_id=None, hashtag_set_id=None, cover_path=None,
        suggested_posting_time=None, insufficient_data=True, message="AI generation failed for every content type.",
    )
    with monkeypatch.context() as m:
        m.setattr(panel_module, "send_to_instagram_manager", MagicMock(return_value=insufficient))
        panel._on_handoff_clicked(navigate_after=False)
        _pump(root, lambda: any("AI generation failed" in t for t in _collect_texts(panel._status_container)))

    texts = _collect_texts(panel._status_container)
    assert any("AI generation failed" in t for t in texts)


def test_set_cover_path_updates_context_for_handoff(root, tmp_path, monkeypatch):
    panel = ReelCreatorPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_transcript(project.project_id, dataclasses.asdict(_transcription()))
    panel.load_project(project, _get_project(project.project_id))

    panel.set_cover_path("/tmp/new_cover.jpg")
    assert panel._cover_path == "/tmp/new_cover.jpg"


# --- Timeline Editor: delete/reorder clips (module brief, section 11) ---------------------


def _multi_clip_plan() -> ReelEditPlan:
    return ReelEditPlan(
        clips=[
            PlannedClip(0.0, 5.0, "a", "Clip A"),
            PlannedClip(10.0, 15.0, "b", "Clip B"),
            PlannedClip(20.0, 25.0, "c", "Clip C"),
        ],
        hook="Big hook", cta="Follow now", target_duration_seconds=30, style="Educational",
        pacing="Normal", total_duration_seconds=15.0, silence_removal_suggested=False,
        zoom_crop_suggested=True, insufficient_data=False, message=None,
    )


def test_move_clip_down_swaps_order(root, tmp_path):
    panel = ReelCreatorPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_reel_plan(project.project_id, dataclasses.asdict(_multi_clip_plan()))
    panel.load_project(project, _get_project(project.project_id))

    panel._move_clip(0, 1)
    assert panel._plan is not None
    assert [c.caption_text for c in panel._plan.clips] == ["Clip B", "Clip A", "Clip C"]


def test_move_clip_out_of_range_does_nothing(root, tmp_path):
    panel = ReelCreatorPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_reel_plan(project.project_id, dataclasses.asdict(_multi_clip_plan()))
    panel.load_project(project, _get_project(project.project_id))

    panel._move_clip(0, -1)  # can't move the first clip up
    assert panel._plan is not None
    assert [c.caption_text for c in panel._plan.clips] == ["Clip A", "Clip B", "Clip C"]


def test_delete_clip_removes_it_and_recomputes_duration(root, tmp_path):
    panel = ReelCreatorPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_reel_plan(project.project_id, dataclasses.asdict(_multi_clip_plan()))
    panel.load_project(project, _get_project(project.project_id))

    panel._delete_clip(1)  # remove "Clip B"
    assert panel._plan is not None
    assert [c.caption_text for c in panel._plan.clips] == ["Clip A", "Clip C"]
    assert panel._plan.total_duration_seconds == 10.0  # 5.0 + 5.0


def test_delete_last_remaining_clip_is_refused(root, tmp_path):
    panel = ReelCreatorPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    single_clip_plan = ReelEditPlan(
        clips=[PlannedClip(0.0, 5.0, "a", "Only Clip")],
        hook="h", cta="c", target_duration_seconds=30, style="s", pacing="p",
        total_duration_seconds=5.0, silence_removal_suggested=False, zoom_crop_suggested=False,
        insufficient_data=False, message=None,
    )
    db.save_reel_plan(project.project_id, dataclasses.asdict(single_clip_plan))
    panel.load_project(project, _get_project(project.project_id))

    panel._delete_clip(0)
    assert panel._plan is not None
    assert len(panel._plan.clips) == 1


def test_edited_plan_is_persisted(root, tmp_path):
    panel = ReelCreatorPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_reel_plan(project.project_id, dataclasses.asdict(_multi_clip_plan()))
    panel.load_project(project, _get_project(project.project_id))

    panel._delete_clip(0)
    saved = _get_project(project.project_id)
    assert saved.reel_plan_data is not None
    saved_plan = ReelEditPlan.from_dict(saved.reel_plan_data)
    assert [c.caption_text for c in saved_plan.clips] == ["Clip B", "Clip C"]


def test_edited_plan_re_renders_the_timeline(root, tmp_path):
    panel = ReelCreatorPanel(root, llm=MagicMock())
    project = _fake_project(tmp_path)
    db.create_project_record(project.project_id, "video.mp4")
    db.save_reel_plan(project.project_id, dataclasses.asdict(_multi_clip_plan()))
    panel.load_project(project, _get_project(project.project_id))

    panel._delete_clip(0)
    texts = _collect_texts(panel._plan_container)
    assert any("Clip B" in t for t in texts)
    assert not any('"Clip A"' in t for t in texts)
