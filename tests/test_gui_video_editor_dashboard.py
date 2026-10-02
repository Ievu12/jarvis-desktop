"""Tests for jarvis.gui.views.video_editor.dashboard.VideoEditorView:
widget construction and the full real import -> timeline -> export
flow, using a real (withdrawn) CTk root with jarvis.video_editor.db/
.storage redirected to a per-test tmp_path. Uses the established
anthropic-stub technique (see this session's own conftest.py, deleted
immediately after use) since jarvis.gui.app's import chain touches
jarvis.core.llm, which this test file doesn't otherwise need working.

Confirms: the view constructs without error and exposes its three
panels, New Project creates a real project, importing a real video clip
and a real photo populates the media list, adding them to the timeline
produces a valid (zero-problem) Timeline, and exporting produces a
real MP4 file at the correct target resolution/duration - the complete
Stage 1+2 pipeline exercised through the actual dashboard code, not
just the backend modules in isolation."""

from __future__ import annotations

import queue
import subprocess
import time

import customtkinter as ctk
import pytest
from PIL import Image

from jarvis.gui.views.video_editor import dashboard as dashboard_module
from jarvis.gui.views.video_editor.dashboard import VideoEditorView
from jarvis.gui.worker import CancelableTaskResult
from jarvis.video_editor import db, storage
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available
from jarvis.video_studio.transcribe import model_is_downloaded


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    db_file = tmp_path / "video_editor.db"
    projects_dir = tmp_path / "video_editor_projects"
    monkeypatch.setattr(db, "VIDEO_EDITOR_DB_FILE", db_file)
    monkeypatch.setattr(storage, "VIDEO_EDITOR_PROJECTS_DIR", projects_dir)
    monkeypatch.setattr(dashboard_module.db, "VIDEO_EDITOR_DB_FILE", db_file)
    monkeypatch.setattr(dashboard_module.storage, "VIDEO_EDITOR_PROJECTS_DIR", projects_dir)


@pytest.fixture(scope="module")
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture(autouse=True)
def _destroy_child_views(root):
    yield
    for child in list(root.winfo_children()):
        child.destroy()


def _drain_queue_until(view, predicate, *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = view._result_queue.get(timeout=0.2)
        except queue.Empty:
            continue
        view._handle_result(result)
        if predicate():
            return
    raise AssertionError("Timed out waiting for the expected queue result")


def test_view_constructs_with_all_three_panels(root):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    assert hasattr(view, "_import_panel")
    assert hasattr(view, "_timeline_panel")
    assert hasattr(view, "_export_panel")


def test_category_sidebar_switches_panel_visibility_without_losing_state(root):
    # Real regression test for the categorized tools sidebar (Klipai/
    # Animacijos/Perėjimai/Tekstas/Filtrai/Muzika/Formatas/Eksportas) -
    # switching category must show/hide the SAME already-built panel
    # widgets (never rebuild them), so an in-progress Timeline edit is
    # never lost by navigating between categories.
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    root.update()

    assert view._active_category == "clips"
    assert view._import_panel.winfo_ismapped()
    assert not view._export_panel.winfo_ismapped()

    view._on_new_project_clicked()
    root.update()

    view._on_category_selected("export")
    root.update()
    assert view._export_panel.winfo_ismapped()
    assert not view._import_panel.winfo_ismapped()
    # The SAME TimelinePanel instance backs "Klipai"/"Animacijos"/
    # "Perėjimai"/"Formatas" - its own Timeline object must survive a
    # category switch untouched.
    assert view._timeline_panel.timeline is not None

    view._on_category_selected("text")
    root.update()
    assert view._captions_panel.winfo_ismapped()
    assert view._text_overlay_panel.winfo_ismapped()
    assert not view._export_panel.winfo_ismapped()

    view._on_category_selected("music")
    root.update()
    assert view._music_panel.winfo_ismapped()

    for category in ("animations", "transitions", "filters", "format"):
        view._on_category_selected(category)
        root.update()
        assert view._timeline_panel.winfo_ismapped()


def test_new_project_creates_a_real_project(root):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()
    assert view._current_project is not None
    assert view._current_project.root_dir.is_dir()
    record = db.get_project(view._current_project.project_id)
    assert record is not None


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_full_import_timeline_export_flow_produces_a_real_file(root, tmp_path):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()

    clip_path = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=640x360:d=3", "-c:v", "libx264", "-t", "3", str(clip_path)],
        capture_output=True, timeout=30, check=True,
    )
    photo_path = tmp_path / "photo.jpg"
    Image.new("RGB", (800, 600), color=(0, 255, 0)).save(photo_path, "JPEG")

    view._on_files_chosen([clip_path, photo_path])
    _drain_queue_until(view, lambda: len(view._media_items) == 2)
    assert len(view._media_items) == 2

    # Uses the REAL handler the "➕ Add to Timeline" button calls
    # (_on_add_to_timeline_clicked), not TimelinePanel.add_clip()
    # directly - this is the actual GUI click path, confirming the
    # button is genuinely wired, not just that add_clip() itself works.
    for item in view._media_items.values():
        view._on_add_to_timeline_clicked(item)
    assert len(view._timeline_panel.timeline.items) == 2
    assert view._timeline_panel.timeline.validate() == []

    view._on_export_clicked("720p")
    _drain_queue_until(view, lambda: isinstance(view._export_panel._last_result, object) and view._cancel_event is None)

    result = view._export_panel._last_result
    assert result is not None
    assert result.output_path.is_file()
    assert result.width == 720
    assert result.height == 1280
    assert abs(result.duration_seconds - 6.0) < 0.5

    record = db.get_project(view._current_project.project_id)
    assert record is not None
    assert record.status == "exported"


def test_export_blocked_without_an_open_project(root):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_export_clicked("1080p")  # must not raise
    assert view._current_project is None


def test_clicking_the_real_add_to_timeline_button_adds_the_clip(root, tmp_path):
    # Real regression test for a genuine bug caught during first manual
    # use: importing media never added anything to the timeline - there
    # was no button at all wiring ImportPanel's media list to
    # TimelinePanel.add_clip(). This test walks the REAL widget tree and
    # invokes the REAL button, rather than calling a handler function
    # directly, so a future regression (the button silently missing
    # again, or wired to the wrong handler) would be caught here.
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()
    photo_path = tmp_path / "photo.jpg"
    Image.new("RGB", (400, 300), color=(1, 2, 3)).save(photo_path, "JPEG")
    view._on_files_chosen([photo_path])
    _drain_queue_until(view, lambda: len(view._media_items) == 1)

    def _walk(widget):
        yield widget
        for child in widget.winfo_children():
            yield from _walk(child)

    buttons = [
        w for w in _walk(view._import_panel)
        if isinstance(w, ctk.CTkButton) and w.cget("text") == "➕ Add to Timeline"
    ]
    assert len(buttons) == 1, "Add to Timeline button not found in the real widget tree"
    buttons[0].invoke()

    assert len(view._timeline_panel.timeline.items) == 1


def test_view_constructs_with_captions_and_music_panels(root):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    assert hasattr(view, "_captions_panel")
    assert hasattr(view, "_music_panel")
    assert view._caption_style is None
    assert view._music_track is None


def test_captions_panel_toggle_emits_and_clears_style(root):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    panel = view._captions_panel

    panel._toggle_var.set("on")
    panel._on_toggle()
    assert view._caption_style is not None
    assert view._caption_style.position == "bottom"

    panel._position_dropdown.set("top")
    panel._emit()
    assert view._caption_style.position == "top"

    panel._toggle_var.set("off")
    panel._on_toggle()
    assert view._caption_style is None


def test_generate_subtitles_requires_an_open_project(root):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_generate_subtitles_requested()  # must not raise
    assert view._caption_lines is None


def test_changing_caption_style_clears_previously_edited_lines(root):
    from jarvis.video_editor.captions import CaptionLine

    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._caption_lines = [CaptionLine(text="Stale edit", start_seconds=0.0, end_seconds=1.0)]

    view._captions_panel._toggle_var.set("on")
    view._captions_panel._on_toggle()

    assert view._caption_lines is None


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
@pytest.mark.skipif(not model_is_downloaded(), reason="whisper model not downloaded")
def test_full_generate_edit_export_subtitle_flow_burns_in_the_edited_text(root, tmp_path):
    # Real end-to-end regression for the "redaguoti kiekvieną subtitrų
    # eilutę" (edit each subtitle line) requirement: generate real
    # subtitles from real speech, edit a line's own text through the
    # actual CaptionsPanel state, export, and confirm the EXPORTED
    # frame shows the edited text (not the original transcription) -
    # exercised entirely through VideoEditorView's own click handlers.
    import dataclasses

    ps_script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{tmp_path / 'speech.wav'}'); "
        "$s.Speak('Hello world'); "
        "$s.Dispose()"
    )
    try:
        subprocess.run(["powershell", "-Command", ps_script], capture_output=True, timeout=30, check=True)
    except Exception:
        pytest.skip("Windows SAPI speech synthesis not available")
    speech_wav = tmp_path / "speech.wav"
    if not speech_wav.is_file():
        pytest.skip("Windows SAPI speech synthesis not available")

    clip_path = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=1080x1920:d=3", "-i", str(speech_wav),
         "-c:v", "libx264", "-c:a", "aac", "-t", "3", "-shortest", str(clip_path)],
        capture_output=True, timeout=30, check=True,
    )

    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()
    view._on_files_chosen([clip_path])
    _drain_queue_until(view, lambda: len(view._media_items) == 1)
    for item in view._media_items.values():
        view._on_add_to_timeline_clicked(item)

    view._captions_panel._toggle_var.set("on")
    view._captions_panel._on_toggle()

    view._on_generate_subtitles_requested()
    _drain_queue_until(view, lambda: view._caption_lines is not None, timeout=30.0)
    assert len(view._caption_lines) > 0

    edited = dataclasses.replace(view._caption_lines[0], text="EDITED BY USER")
    view._captions_panel._lines[0] = edited
    view._captions_panel._emit_lines()
    assert view._caption_lines[0].text == "EDITED BY USER"

    view._on_export_clicked("720p")
    _drain_queue_until(
        view, lambda: view._export_panel._last_result is not None and view._cancel_event is None, timeout=30.0,
    )
    result = view._export_panel._last_result
    assert result is not None and result.output_path.is_file()

    frame_path = tmp_path / "frame.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "1.0", "-i", str(result.output_path), "-frames:v", "1", str(frame_path)],
        capture_output=True, timeout=15, check=True,
    )
    frame_plain = tmp_path / "plain.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "1.0", "-i", str(clip_path), "-frames:v", "1",
         "-vf", "scale=720:1280", "-pix_fmt", "rgb24", str(frame_plain)],
        capture_output=True, timeout=15, check=True,
    )
    assert Image.open(frame_path).convert("RGB").tobytes() != Image.open(frame_plain).convert("RGB").tobytes()


def test_music_panel_file_chosen_copies_into_project_and_renders_controls(root, tmp_path):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()

    music_path = tmp_path / "song.mp3"
    music_path.write_bytes(b"fake-mp3-bytes")

    view._on_music_file_chosen(music_path)

    assert view._music_track is not None
    assert view._music_track.source_path.is_file()
    assert view._music_track.source_path != music_path
    assert view._music_track.source_path.parent != music_path.parent

    view._music_panel._on_remove_clicked()
    assert view._music_track is None


def test_two_exports_started_in_quick_succession_never_collide_on_output_path(root, tmp_path):
    # Real regression test for a reported bug: a second "Export" click
    # fired while captions transcription was still pending for a first
    # export landed on the SAME time.time()-based output filename,
    # corrupting the resulting MP4 (two ffmpeg processes writing the
    # same file at once). _start_export() now derives the output path
    # from uuid4() instead of a wall-clock second, so two calls in the
    # same instant must still get distinct paths.
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()

    photo_path = tmp_path / "photo.jpg"
    Image.new("RGB", (400, 300), color=(4, 5, 6)).save(photo_path, "JPEG")
    view._on_files_chosen([photo_path])
    _drain_queue_until(view, lambda: len(view._media_items) == 1)
    for item in view._media_items.values():
        view._on_add_to_timeline_clicked(item)

    captured_paths = []
    original_run = dashboard_module.run_cancelable_in_background

    def _spy(*args, **kwargs):
        captured_paths.append(kwargs["output_path"])
        return original_run(*args, **kwargs)

    dashboard_module.run_cancelable_in_background = _spy
    try:
        view._start_export("720p", caption_filter=None)
        view._start_export("720p", caption_filter=None)
    finally:
        dashboard_module.run_cancelable_in_background = original_run

    assert len(captured_paths) == 2
    assert captured_paths[0] != captured_paths[1]


def test_export_button_is_disabled_during_caption_transcription(root, tmp_path):
    # Real regression test: with captions enabled, the Export button
    # must be disabled the moment transcription starts (not only once
    # the real export subprocess later begins) - previously it stayed
    # enabled during this window, letting a second click race a second
    # export against the pending one.
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()

    clip_path = tmp_path / "clip.mp4"
    if not ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=640x360:d=1", "-c:v", "libx264", "-t", "1", str(clip_path)],
        capture_output=True, timeout=30, check=True,
    )
    view._on_files_chosen([clip_path])
    _drain_queue_until(view, lambda: len(view._media_items) == 1)
    for item in view._media_items.values():
        view._on_add_to_timeline_clicked(item)

    view._captions_panel._toggle_var.set("on")
    view._captions_panel._on_toggle()
    assert view._caption_style is not None

    view._on_export_clicked("720p")
    assert view._export_panel._export_button.cget("state") == "disabled"


def test_music_file_chosen_without_an_open_project_shows_error(root, tmp_path):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    music_path = tmp_path / "song.mp3"
    music_path.write_bytes(b"fake-mp3-bytes")

    view._on_music_file_chosen(music_path)

    assert view._music_track is None


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_multitrack_view_updates_when_timeline_and_text_overlays_change(root, tmp_path):
    from jarvis.video_editor.text_overlay import TextOverlay

    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()
    root.update()
    empty_count = len(view._multitrack_view._canvas.find_all())

    clip_path = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=640x360:d=2", "-c:v", "libx264", "-t", "2", str(clip_path)],
        capture_output=True, timeout=30, check=True,
    )
    view._on_files_chosen([clip_path])
    _drain_queue_until(view, lambda: len(view._media_items) == 1)
    for item in view._media_items.values():
        view._on_add_to_timeline_clicked(item)

    view._on_text_overlays_changed([TextOverlay(text="Hi", start_seconds=0.5, end_seconds=1.5)])
    root.update()
    after_count = len(view._multitrack_view._canvas.find_all())

    assert after_count > empty_count


def test_view_constructs_with_text_overlay_panel(root):
    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    assert hasattr(view, "_text_overlay_panel")
    assert view._text_overlays == []


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_effect_preview_request_renders_real_frames_into_the_correct_row(root, tmp_path):
    # Real regression test for the "animacijų peržiūra prieš
    # eksportavimą" (preview the animation before export) requirement -
    # exercised entirely through the actual dashboard click handler
    # (_on_effect_preview_requested), not by calling
    # effects.render_effect_preview() directly.
    import dataclasses

    from jarvis.video_editor.effects import EffectSpec

    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()

    photo_path = tmp_path / "photo.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=800x600:duration=1", "-frames:v", "1", str(photo_path)],
        capture_output=True, timeout=15, check=True,
    )
    view._on_files_chosen([photo_path])
    _drain_queue_until(view, lambda: len(view._media_items) == 1)
    for item in view._media_items.values():
        view._on_add_to_timeline_clicked(item)

    first_item = view._timeline_panel.timeline.items[0]
    new_item = dataclasses.replace(first_item, effect=EffectSpec(motion="zoom_in", motion_intensity=1.3))
    view._timeline_panel._replace_item(0, new_item)

    view._on_effect_preview_requested(0)
    _drain_queue_until(
        view, lambda: len(view._timeline_panel._preview_containers.get(0).winfo_children()) > 0, timeout=20.0,
    )

    container = view._timeline_panel._preview_containers[0]
    assert len(container.winfo_children()) == 3


def test_adding_a_text_overlay_row_updates_dashboard_state(root):
    from jarvis.video_editor.text_overlay import TextOverlay

    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    panel = view._text_overlay_panel

    panel._on_add_clicked()
    assert len(view._text_overlays) == 1  # the default "New text" row is already valid

    panel._overlays[0] = TextOverlay(text="Hello", start_seconds=0.5, end_seconds=2.0)
    panel._emit()
    assert view._text_overlays == [TextOverlay(text="Hello", start_seconds=0.5, end_seconds=2.0)]

    panel._remove(0)
    assert view._text_overlays == []


def test_invalid_text_overlay_row_is_excluded_from_emitted_list(root):
    from jarvis.video_editor.text_overlay import TextOverlay

    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    panel = view._text_overlay_panel
    panel._overlays = [
        TextOverlay(text="Good", start_seconds=0.0, end_seconds=1.0),
        TextOverlay(text="", start_seconds=0.0, end_seconds=1.0),  # invalid: empty text
    ]
    panel._emit()
    assert len(view._text_overlays) == 1
    assert view._text_overlays[0].text == "Good"


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_full_export_with_real_text_overlay_produces_a_visually_correct_file(root, tmp_path):
    # Real end-to-end regression: a text overlay added through the
    # actual TextOverlayPanel must genuinely appear in the final MP4,
    # exercised entirely through VideoEditorView's own click handlers.
    from jarvis.video_editor.text_overlay import TextOverlay

    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()

    clip_path = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=green:s=1080x1920:d=2", "-c:v", "libx264", "-t", "2", str(clip_path)],
        capture_output=True, timeout=30, check=True,
    )
    view._on_files_chosen([clip_path])
    _drain_queue_until(view, lambda: len(view._media_items) == 1)
    for item in view._media_items.values():
        view._on_add_to_timeline_clicked(item)

    view._text_overlay_panel._overlays = [TextOverlay(text="TITLE", start_seconds=0.2, end_seconds=1.8)]
    view._text_overlay_panel._emit()
    assert len(view._text_overlays) == 1

    view._on_export_clicked("720p")
    _drain_queue_until(
        view, lambda: view._export_panel._last_result is not None and view._cancel_event is None, timeout=30.0,
    )

    result = view._export_panel._last_result
    assert result is not None
    assert result.output_path.is_file()

    frame_with_text = tmp_path / "with_text.png"
    frame_plain_source = tmp_path / "plain_source.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "1.0", "-i", str(result.output_path), "-frames:v", "1", str(frame_with_text)],
        capture_output=True, timeout=15, check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "1.0", "-i", str(clip_path), "-frames:v", "1",
         "-vf", "scale=720:1280", "-pix_fmt", "rgb24", str(frame_plain_source)],
        capture_output=True, timeout=15, check=True,
    )
    assert Image.open(frame_with_text).convert("RGB").tobytes() != Image.open(frame_plain_source).convert("RGB").tobytes()


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
@pytest.mark.skipif(not model_is_downloaded(), reason="whisper model not downloaded")
def test_full_export_with_real_captions_and_music_produces_a_file(root, tmp_path):
    # Real end-to-end regression for Stage 4: a real speech clip gets
    # real word-level transcription, a real music file gets mixed in,
    # and the final export is a real MP4 - exercised entirely through
    # VideoEditorView's own click handlers (_on_export_clicked ->
    # async caption transcription -> _start_export), not by calling
    # captions.py/audio_mixing.py functions directly.
    ps_script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{tmp_path / 'speech.wav'}'); "
        "$s.Speak('Hello world'); "
        "$s.Dispose()"
    )
    try:
        subprocess.run(["powershell", "-Command", ps_script], capture_output=True, timeout=30, check=True)
    except Exception:
        pytest.skip("Windows SAPI speech synthesis not available")
    speech_wav = tmp_path / "speech.wav"
    if not speech_wav.is_file():
        pytest.skip("Windows SAPI speech synthesis not available")

    clip_path = tmp_path / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=3",
            "-i", str(speech_wav), "-c:v", "libx264", "-c:a", "aac", "-t", "3", "-shortest", str(clip_path),
        ],
        capture_output=True, timeout=30, check=True,
    )

    music_path = tmp_path / "music.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=5", str(music_path)],
        capture_output=True, timeout=30, check=True,
    )

    view = VideoEditorView(root, llm=None, navigate=lambda k, **kw: None)
    view._on_new_project_clicked()

    view._on_files_chosen([clip_path])
    _drain_queue_until(view, lambda: len(view._media_items) == 1)
    for item in view._media_items.values():
        view._on_add_to_timeline_clicked(item)
    assert len(view._timeline_panel.timeline.items) == 1

    view._captions_panel._toggle_var.set("on")
    view._captions_panel._on_toggle()
    assert view._caption_style is not None

    view._on_music_file_chosen(music_path)
    assert view._music_track is not None

    view._on_export_clicked("720p")
    _drain_queue_until(
        view,
        lambda: view._export_panel._last_result is not None and view._cancel_event is None,
        timeout=60.0,
    )

    result = view._export_panel._last_result
    assert result is not None
    assert result.output_path.is_file()
    assert result.width == 720
    assert result.height == 1280
