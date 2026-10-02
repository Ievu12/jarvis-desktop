"""Tests for jarvis.gui.views.video_editor.multitrack_view.MultiTrackView:
a pure, read-only rendering widget - construction and render() calls
draw real Tkinter canvas items proportional to the segments/duration
given, never reading any jarvis.video_editor state directly itself."""

from __future__ import annotations

import customtkinter as ctk
import pytest

from jarvis.gui.views.video_editor.multitrack_view import MultiTrackView


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


def test_constructs_with_an_empty_canvas(root):
    view = MultiTrackView(root)
    assert len(view._canvas.find_all()) == 0


def test_render_with_no_segments_still_draws_track_backgrounds_and_axis(root):
    view = MultiTrackView(root)
    view.render(
        video_segments=[], music_segment=None, caption_segments=[], text_segments=[],
        sticker_segments=[], total_duration_seconds=10.0,
    )
    # 5 tracks * (1 label + 1 background rect) + 5 axis ticks + 5 axis labels = 20
    assert len(view._canvas.find_all()) == 20


def test_render_with_segments_draws_additional_rectangles(root):
    view = MultiTrackView(root)
    view.render(
        video_segments=[(0.0, 2.0, "1"), (2.0, 4.0, "2")],
        music_segment=(0.0, 4.0),
        caption_segments=[(0.5, 1.5)],
        text_segments=[(1.0, 3.0)],
        sticker_segments=[(0.5, 2.0)],
        total_duration_seconds=4.0,
    )
    items_with_segments = len(view._canvas.find_all())

    view2 = MultiTrackView(root)
    view2.render(
        video_segments=[], music_segment=None, caption_segments=[], text_segments=[],
        sticker_segments=[], total_duration_seconds=4.0,
    )
    items_without_segments = len(view2._canvas.find_all())

    assert items_with_segments > items_without_segments


def test_render_clears_previous_drawing_before_redrawing(root):
    view = MultiTrackView(root)
    view.render(
        video_segments=[(0.0, 1.0, "1")], music_segment=None, caption_segments=[], text_segments=[],
        sticker_segments=[], total_duration_seconds=1.0,
    )
    first_count = len(view._canvas.find_all())

    view.render(
        video_segments=[], music_segment=None, caption_segments=[], text_segments=[],
        sticker_segments=[], total_duration_seconds=1.0,
    )
    second_count = len(view._canvas.find_all())

    assert second_count < first_count


def test_render_handles_zero_duration_without_raising(root):
    view = MultiTrackView(root)
    view.render(
        video_segments=[], music_segment=None, caption_segments=[], text_segments=[],
        sticker_segments=[], total_duration_seconds=0.0,
    )
    assert len(view._canvas.find_all()) > 0


def test_render_draws_sticker_segments(root):
    view = MultiTrackView(root)
    view.render(
        video_segments=[], music_segment=None, caption_segments=[], text_segments=[],
        sticker_segments=[], total_duration_seconds=4.0,
    )
    without_stickers = len(view._canvas.find_all())

    view2 = MultiTrackView(root)
    view2.render(
        video_segments=[], music_segment=None, caption_segments=[], text_segments=[],
        sticker_segments=[(0.5, 2.0), (2.5, 3.5)], total_duration_seconds=4.0,
    )
    with_stickers = len(view2._canvas.find_all())

    assert with_stickers > without_stickers
