"""Tests for jarvis.gui.worker's new, ADDITIVE cancelable/progress-
reporting infrastructure (run_cancelable_in_background/ProgressResult/
CancelableTaskResult) - exercised against a REAL
jarvis.video_editor.multisource_export.export_timeline() call over a
real, heavy-enough-to-take-measurable-time synthetic ffmpeg export, not
a mock, so this confirms the real relay between export_timeline()'s own
progress_callback/cancel_event contract and the GUI-facing queue.
Skipped entirely if ffmpeg isn't on PATH.

Confirms: a successful cancelable export posts increasing
ProgressResult values followed by one terminal CancelableTaskResult
with cancelled=False and a real ExportResult; a cancelled export posts
a terminal CancelableTaskResult with cancelled=True, error=None,
value=None; `source` is carried through on every posted result,
matching GenerationTaskResult's own established routing convention."""

from __future__ import annotations

import queue
import subprocess
import threading
import time

import pytest

from jarvis.gui.worker import CancelableTaskResult, ProgressResult, run_cancelable_in_background
from jarvis.video_editor import media_import, multisource_export as mse, storage
from jarvis.video_editor.timeline import Timeline, TimelineClip
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


@pytest.fixture(autouse=True)
def _isolated_projects_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "VIDEO_EDITOR_PROJECTS_DIR", tmp_path / "video_editor_projects")


def _make_heavy_clip(path, *, duration_seconds=8.0):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc=size=1920x1080:duration={duration_seconds}:rate=30",
         "-c:v", "libx264", "-preset", "veryslow", "-t", str(duration_seconds), str(path)],
        capture_output=True, timeout=60, check=True,
    )
    return path


def _drain_queue(q: "queue.Queue", *, timeout: float = 60.0) -> list:
    """Polls the queue until a CancelableTaskResult (the terminal
    result) arrives or the timeout elapses - mirrors the real
    `.after()` polling loop jarvis.gui.app/every dashboard already
    uses, just synchronous for test purposes."""
    results = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            item = q.get(timeout=0.1)
        except queue.Empty:
            continue
        results.append(item)
        if isinstance(item, CancelableTaskResult):
            return results
    raise AssertionError("Timed out waiting for a terminal CancelableTaskResult")


def test_successful_cancelable_export_posts_progress_then_terminal_result(tmp_path):
    project = storage.create_project()
    clip = _make_heavy_clip(tmp_path / "heavy.mp4")
    item = media_import.import_media(clip, project, media_item_id="m1")
    media_items = {"m1": item}
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=8.0),
    ), aspect_ratio="16:9")
    fmt = mse.resolve_export_format("16:9", "1080p")

    result_queue: "queue.Queue" = queue.Queue()
    cancel_event = threading.Event()
    output_path = project.exports_dir / "out.mp4"

    run_cancelable_in_background(
        mse.export_timeline, result_queue, cancel_event=cancel_event, source="panel-A",
        timeline=timeline, media_items=media_items, export_format=fmt, output_path=output_path,
    )

    results = _drain_queue(result_queue)
    progress_results = [r for r in results if isinstance(r, ProgressResult)]
    terminal = results[-1]

    assert isinstance(terminal, CancelableTaskResult)
    assert terminal.cancelled is False
    assert terminal.error is None
    assert terminal.value is not None
    assert terminal.value.output_path.is_file()
    assert terminal.source == "panel-A"
    assert all(r.source == "panel-A" for r in progress_results)
    if len(progress_results) > 1:
        assert all(progress_results[i].percent <= progress_results[i + 1].percent for i in range(len(progress_results) - 1))


def test_cancelled_export_posts_a_cancelled_terminal_result(tmp_path):
    project = storage.create_project()
    clip = _make_heavy_clip(tmp_path / "heavy.mp4")
    item = media_import.import_media(clip, project, media_item_id="m1")
    media_items = {"m1": item}
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=8.0),
    ), aspect_ratio="16:9")
    fmt = mse.resolve_export_format("16:9", "1080p")

    result_queue: "queue.Queue" = queue.Queue()
    cancel_event = threading.Event()
    output_path = project.exports_dir / "cancelled.mp4"

    run_cancelable_in_background(
        mse.export_timeline, result_queue, cancel_event=cancel_event, source="panel-B",
        timeline=timeline, media_items=media_items, export_format=fmt, output_path=output_path,
    )
    time.sleep(0.5)
    cancel_event.set()

    results = _drain_queue(result_queue)
    terminal = results[-1]

    assert isinstance(terminal, CancelableTaskResult)
    assert terminal.cancelled is True
    assert terminal.error is None
    assert terminal.value is None
    assert terminal.source == "panel-B"
    assert not output_path.exists()


def test_existing_generation_in_background_is_completely_unaffected():
    # Regression check: the pre-existing, already-depended-upon
    # run_generation_in_background() must still behave exactly as
    # before this module's new additions - a plain function call, one
    # terminal GenerationTaskResult, no progress/cancel concept at all.
    from jarvis.gui.worker import GenerationTaskResult, run_generation_in_background

    result_queue: "queue.Queue" = queue.Queue()
    run_generation_in_background(lambda: 42, result_queue, source="x")
    result = result_queue.get(timeout=5.0)
    assert isinstance(result, GenerationTaskResult)
    assert result.value == 42
    assert result.error is None
    assert result.source == "x"
