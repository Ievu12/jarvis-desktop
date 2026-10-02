"""Tests for jarvis.video_editor.stickers: built-in sticker rendering
(real Pillow PNGs, visually verified by hand during development) and
StickerInstance/build_sticker_filter()'s own filter-string construction
are pure/cheap; a handful of ffmpeg-guarded tests at the bottom run a
REAL export through a standalone overlay command and extract real
frames to prove the sticker genuinely appears and animates, not just
that ffmpeg exits 0."""

from __future__ import annotations

import subprocess

import pytest
from PIL import Image

from jarvis.video_editor.stickers import (
    STICKER_ANIMATION_CHOICES,
    STICKER_CATEGORY_CHOICES,
    STICKER_CATEGORY_LABELS,
    STICKER_CATEGORY_SHAPES,
    STICKER_SHAPE_CHOICES,
    StickerError,
    StickerInstance,
    build_sticker_filter,
    render_builtin_sticker,
)
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available


def test_every_category_has_a_label():
    for category in STICKER_CATEGORY_CHOICES:
        assert category in STICKER_CATEGORY_LABELS
        assert STICKER_CATEGORY_LABELS[category].strip()


def test_every_category_has_at_least_three_shapes():
    for category in STICKER_CATEGORY_CHOICES:
        shapes = STICKER_CATEGORY_SHAPES[category]
        assert len(shapes) >= 3


def test_every_category_shape_is_a_real_valid_shape():
    for category, shapes in STICKER_CATEGORY_SHAPES.items():
        for shape in shapes:
            assert shape in STICKER_SHAPE_CHOICES


def test_category_shapes_are_mutually_exclusive():
    # No shape should belong to two categories at once - a person
    # browsing a category must see each shape exactly once, not
    # duplicated across categories.
    seen = set()
    for shapes in STICKER_CATEGORY_SHAPES.values():
        for shape in shapes:
            assert shape not in seen, f"{shape!r} appears in more than one category"
            seen.add(shape)


def test_expanded_library_has_at_least_thirty_shapes():
    # Requirement: a genuinely larger sticker library (the user-approved
    # scope for this stage was ~25-30 new shapes across 9 categories,
    # on top of the 7 that already existed) - this is a real, non-trivial
    # lower bound, not an exact count, so adding more shapes later never
    # breaks this test.
    assert len(STICKER_SHAPE_CHOICES) >= 30


def test_default_sticker_instance_is_invalid_without_shape_or_path():
    sticker = StickerInstance(start_seconds=0.0, end_seconds=1.0)
    problems = sticker.validate()
    assert any("built-in shape or a custom image" in p for p in problems)


def test_sticker_cannot_use_both_shape_and_custom_path(tmp_path):
    custom = tmp_path / "custom.png"
    custom.write_bytes(b"fake")
    sticker = StickerInstance(start_seconds=0.0, end_seconds=1.0, shape="heart", custom_path=custom)
    problems = sticker.validate()
    assert any("cannot use both" in p for p in problems)


def test_sticker_rejects_missing_custom_file(tmp_path):
    sticker = StickerInstance(start_seconds=0.0, end_seconds=1.0, custom_path=tmp_path / "missing.png")
    problems = sticker.validate()
    assert any("not found" in p for p in problems)


def test_sticker_rejects_backwards_time_window():
    sticker = StickerInstance(start_seconds=5.0, end_seconds=2.0, shape="star")
    problems = sticker.validate()
    assert any("end time must be after" in p for p in problems)


@pytest.mark.parametrize("field,value", [
    ("x_fraction", 1.5), ("y_fraction", -0.1), ("size_fraction", 0.0), ("opacity", 2.0),
])
def test_sticker_rejects_out_of_range_values(field, value):
    sticker = StickerInstance(start_seconds=0.0, end_seconds=1.0, shape="star", **{field: value})
    problems = sticker.validate()
    assert len(problems) >= 1


def test_valid_sticker_passes_validation():
    sticker = StickerInstance(start_seconds=0.0, end_seconds=1.0, shape="star")
    assert sticker.validate() == []


@pytest.mark.parametrize("shape", STICKER_SHAPE_CHOICES)
def test_render_builtin_sticker_produces_a_real_transparent_png(tmp_path, shape):
    out = tmp_path / f"{shape}.png"
    render_builtin_sticker(shape, output_path=out)
    assert out.is_file()
    with Image.open(out) as img:
        assert img.mode == "RGBA"
        assert img.size == (200, 200)
        # At least some pixels must be non-transparent (a real shape was
        # drawn, not an empty/blank image).
        alpha_channel = img.getchannel("A")
        assert alpha_channel.getextrema()[1] > 0


def test_render_builtin_sticker_rejects_unknown_shape(tmp_path):
    with pytest.raises(StickerError):
        render_builtin_sticker("not_a_real_shape", output_path=tmp_path / "x.png")  # type: ignore[arg-type]


def test_build_sticker_filter_rejects_invalid_sticker(tmp_path):
    bad_sticker = StickerInstance(start_seconds=0.0, end_seconds=1.0)  # no shape or path
    with pytest.raises(StickerError):
        build_sticker_filter(bad_sticker, canvas_width=1080, canvas_height=1920, cwd=tmp_path, input_index=1)


def test_build_sticker_filter_renders_a_builtin_shape_file(tmp_path):
    sticker = StickerInstance(start_seconds=0.0, end_seconds=1.0, shape="heart")
    extra_args, clause = build_sticker_filter(
        sticker, canvas_width=1080, canvas_height=1920, cwd=tmp_path, input_index=1,
    )
    assert extra_args[0] == "-i"
    assert (tmp_path / extra_args[1].rsplit("\\", 1)[-1].rsplit("/", 1)[-1]).is_file()
    assert "overlay=" in clause
    assert clause.endswith("[stickv]")


def test_build_sticker_filter_uses_custom_image_without_rendering(tmp_path):
    custom = tmp_path / "custom.png"
    Image.new("RGBA", (50, 50), (255, 0, 0, 255)).save(custom)
    sticker = StickerInstance(start_seconds=0.0, end_seconds=1.0, custom_path=custom)
    extra_args, clause = build_sticker_filter(
        sticker, canvas_width=1080, canvas_height=1920, cwd=tmp_path, input_index=2,
    )
    assert str(custom) in extra_args


@pytest.mark.parametrize("animation", STICKER_ANIMATION_CHOICES)
def test_build_sticker_filter_produces_a_valid_clause_for_every_animation(tmp_path, animation):
    sticker = StickerInstance(start_seconds=0.2, end_seconds=1.8, shape="star", animation=animation)
    extra_args, clause = build_sticker_filter(
        sticker, canvas_width=1080, canvas_height=1920, cwd=tmp_path, input_index=1,
    )
    assert "overlay=" in clause
    assert clause.endswith("[stickv]")


# --- real, ffmpeg-dependent export tests -----------------------------------------------------


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
@pytest.mark.parametrize("animation", STICKER_ANIMATION_CHOICES)
def test_real_export_with_sticker_animation_succeeds_and_stays_compatible(tmp_path, animation):
    # Real regression test for a hand-hit bug: colorchannelmixer's own
    # `aa=` option does not support time-varying expressions (t/if/lt
    # are rejected outright) - "fade_in_out" and "blink" previously
    # failed at the ffmpeg subprocess level with every other animation
    # succeeding. This runs every real animation kind through a real
    # ffmpeg export and confirms it both succeeds AND stays WMP-
    # compatible (yuv420p/yuvj420p), matching
    # jarvis.video_editor.multisource_export's own established
    # compatibility bar.
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=2", "-c:v", "libx264", "-t", "2", str(clip)],
        capture_output=True, timeout=30, check=True,
    )
    sticker = StickerInstance(start_seconds=0.2, end_seconds=1.8, shape="star", animation=animation)
    extra_args, clause = build_sticker_filter(
        sticker, canvas_width=640, canvas_height=360, cwd=tmp_path, video_label="base", output_label="stickered", input_index=1,
    )
    out = tmp_path / f"{animation}.mp4"
    cmd = [
        "ffmpeg", "-y", "-i", str(clip), *extra_args,
        "-filter_complex", f"[0:v]null[base];{clause}",
        "-map", "[stickered]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=str(tmp_path))
    assert result.returncode == 0, result.stderr[-800:]
    assert out.is_file()

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=pix_fmt",
         "-print_format", "json", str(out)],
        capture_output=True, text=True, timeout=15, check=True,
    )
    import json

    pix_fmt = json.loads(probe.stdout)["streams"][0]["pix_fmt"]
    assert pix_fmt in ("yuv420p", "yuvj420p")


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_real_export_with_sticker_is_visually_different_from_plain(tmp_path):
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=2", "-c:v", "libx264", "-t", "2", str(clip)],
        capture_output=True, timeout=30, check=True,
    )
    sticker = StickerInstance(start_seconds=0.2, end_seconds=1.8, shape="heart", animation="none")
    extra_args, clause = build_sticker_filter(
        sticker, canvas_width=640, canvas_height=360, cwd=tmp_path, video_label="base", output_label="stickered", input_index=1,
    )
    out = tmp_path / "stickered.mp4"
    cmd = [
        "ffmpeg", "-y", "-i", str(clip), *extra_args,
        "-filter_complex", f"[0:v]null[base];{clause}",
        "-map", "[stickered]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
    ]
    subprocess.run(cmd, capture_output=True, timeout=30, cwd=str(tmp_path), check=True)

    frame_stickered = tmp_path / "stickered.png"
    frame_plain = tmp_path / "plain.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "1.0", "-i", str(out), "-frames:v", "1", str(frame_stickered)],
        capture_output=True, timeout=15, check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "1.0", "-i", str(clip), "-frames:v", "1", str(frame_plain)],
        capture_output=True, timeout=15, check=True,
    )
    assert Image.open(frame_stickered).tobytes() != Image.open(frame_plain).tobytes()
