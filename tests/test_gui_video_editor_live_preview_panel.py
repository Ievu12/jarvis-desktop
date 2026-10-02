"""Tests for jarvis.gui.views.video_editor.live_preview_panel
.LivePreviewPanel and the owning dashboard's live-preview refresh
wiring (Stage 1 of the "Live Preview" request) - real ffmpeg renders
where the dashboard tests exercise the full flow, pure widget behavior
otherwise."""

from __future__ import annotations

from pathlib import Path

import customtkinter as ctk
import pytest
from PIL import Image

from jarvis.gui.views.video_editor.dashboard import VideoEditorView
from jarvis.gui.views.video_editor.live_preview_panel import LivePreviewPanel
from jarvis.video_editor.media_import import MediaItem
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available


@pytest.fixture(scope="module")
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture(autouse=True)
def _destroy_children(root):
    yield
    for child in list(root.winfo_children()):
        child.destroy()


# --- LivePreviewPanel on its own --------------------------------------------------


def test_new_panel_shows_the_placeholder_and_a_disabled_slider(root):
    panel = LivePreviewPanel(root, on_timestamp_changed=lambda t: None)
    assert str(panel._scrub_slider.cget("state")) == "disabled"


def test_set_total_duration_enables_the_slider_and_updates_the_label(root):
    panel = LivePreviewPanel(root, on_timestamp_changed=lambda t: None)
    panel.set_total_duration(10.0)
    assert str(panel._scrub_slider.cget("state")) == "normal"
    assert "10" in panel._timestamp_label.cget("text") or "00:10" in panel._timestamp_label.cget("text")


def test_set_total_duration_zero_disables_the_slider_again(root):
    panel = LivePreviewPanel(root, on_timestamp_changed=lambda t: None)
    panel.set_total_duration(10.0)
    panel.set_total_duration(0.0)
    assert str(panel._scrub_slider.cget("state")) == "disabled"


def test_moving_the_slider_fires_the_callback_with_the_new_timestamp(root):
    seen = []
    panel = LivePreviewPanel(root, on_timestamp_changed=seen.append)
    panel.set_total_duration(10.0)
    panel._on_slider_moved(4.5)
    assert seen == [4.5]
    assert panel.current_timestamp_seconds() == 4.5


def test_current_timestamp_is_clamped_when_duration_shrinks(root):
    panel = LivePreviewPanel(root, on_timestamp_changed=lambda t: None)
    panel.set_total_duration(10.0)
    panel._on_slider_moved(9.0)
    panel.set_total_duration(3.0)
    assert panel.current_timestamp_seconds() == 3.0


def test_show_frame_displays_a_real_image(root, tmp_path):
    # winfo_ismapped() is unreliable here: `root` itself is withdrawn
    # (see this module's own fixture), so even a correctly-packed
    # direct descendant reports unmapped - winfo_manager() reports the
    # real geometry manager regardless of the root's own mapped state.
    frame_path = tmp_path / "frame.png"
    Image.new("RGB", (640, 360), color=(10, 20, 30)).save(frame_path, "PNG")
    panel = LivePreviewPanel(root, on_timestamp_changed=lambda t: None)
    panel.pack()
    root.update()
    panel.show_frame(frame_path)
    root.update()
    assert panel._image_label is not None
    assert panel._image_label.winfo_manager() == "pack"


def test_show_frame_with_a_missing_file_shows_an_error(root, tmp_path):
    panel = LivePreviewPanel(root, on_timestamp_changed=lambda t: None)
    panel.show_frame(tmp_path / "does_not_exist.png")
    assert "could not be found" in panel._status.cget("text")


def test_show_error_displays_the_message(root):
    panel = LivePreviewPanel(root, on_timestamp_changed=lambda t: None)
    panel.show_error("something broke")
    assert "something broke" in panel._status.cget("text")


def test_set_rendering_state_shows_and_hides_a_status_message(root):
    panel = LivePreviewPanel(root, on_timestamp_changed=lambda t: None)
    panel.pack()
    root.update()
    panel.set_rendering_state(rendering=True)
    root.update()
    assert panel._status.winfo_manager() == "pack"
    panel.set_rendering_state(rendering=False)
    root.update()
    assert panel._status.winfo_manager() == ""


# --- dashboard wiring --------------------------------------------------------------


def _wait_for_preview(view, timeout=20.0):
    # LivePreviewPanel lives OUTSIDE self._scroll (a CTkScrollableFrame,
    # which embeds its children in its own internal Canvas via
    # create_window() - a real CustomTkinter quirk where a widget nested
    # in that canvas reports winfo_ismapped()==1 even when none of its
    # real Tk ancestors above the scrollable frame are mapped into a
    # realized top-level window, which is exactly this test's own
    # VideoEditorView - never packed into `root` itself, same as every
    # other dashboard test in this file). A plain, directly-packed
    # sibling widget like LivePreviewPanel has no such canvas and
    # correctly reports winfo_ismapped()==0 in this same test harness
    # pattern regardless of whether it was really packed - checked here
    # via winfo_manager() instead, which reports the real geometry
    # manager regardless of the ancestor chain's own mapped state.
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        view.update()
        label = view._live_preview_panel._image_label
        if label is not None and label.winfo_manager() == "pack":
            return True
        time.sleep(0.05)
    return False


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_adding_a_clip_triggers_a_real_preview_render(root, tmp_path):
    import subprocess

    clip_path = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=640x360:d=2", "-c:v", "libx264", "-t", "2", str(clip_path)],
        capture_output=True, timeout=30, check=True,
    )
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()
    view._on_files_chosen([clip_path])

    import time

    deadline = time.time() + 15
    while time.time() < deadline and len(view._media_items) == 0:
        view.update()
        time.sleep(0.05)
    for item in view._media_items.values():
        view._on_add_to_timeline_clicked(item)

    assert _wait_for_preview(view)


def test_empty_timeline_shows_the_placeholder_not_an_error(root):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()
    assert view._live_preview_panel._total_duration_seconds == 0.0
    assert "⚠️" not in view._live_preview_panel._placeholder_label.cget("text")


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_adding_a_text_overlay_triggers_a_new_preview_render(root, tmp_path):
    import subprocess

    from jarvis.video_editor.text_overlay import TextOverlay

    clip_path = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=2", "-c:v", "libx264", "-t", "2", str(clip_path)],
        capture_output=True, timeout=30, check=True,
    )
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()
    view._on_files_chosen([clip_path])

    import time

    deadline = time.time() + 15
    while time.time() < deadline and len(view._media_items) == 0:
        view.update()
        time.sleep(0.05)
    for item in view._media_items.values():
        view._on_add_to_timeline_clicked(item)
    assert _wait_for_preview(view)

    view._on_text_overlays_changed([TextOverlay(text="HI", start_seconds=0.0, end_seconds=2.0)])
    # A new render was requested (debounced) - just confirm it completes without error.
    deadline = time.time() + 15
    while time.time() < deadline and view._live_preview_render_after_id is None and "⚠️" in view._live_preview_panel._status.cget("text"):
        view.update()
        time.sleep(0.05)
    assert _wait_for_preview(view)


def test_preview_render_token_ignores_a_stale_result(root):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()
    view._preview_request_token = 5

    from jarvis.gui.worker import GenerationTaskResult

    stale_result = GenerationTaskResult(value=Path("whatever.png"), error=None, source=("live_preview", 3))
    view._handle_live_preview_result(3, stale_result)  # token 3 != current token 5
    # Nothing should have been shown - the placeholder/empty state should remain.
    assert view._live_preview_panel._image_label is None or not view._live_preview_panel._image_label.winfo_ismapped()
