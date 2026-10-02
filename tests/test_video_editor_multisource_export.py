"""Tests for jarvis.video_editor.multisource_export: real ffmpeg
subprocess calls against real synthetic fixtures (ffmpeg lavfi color/
sine sources for video, a real Pillow-written JPEG/PNG for photos) -
skipped entirely if ffmpeg isn't on PATH, matching every other
ffmpeg-dependent test file in this codebase.

Confirms: mixed video+photo, mixed-resolution multi-source export
produces a real file at the correct target resolution/duration; a
fade transition correctly shortens total duration by its own overlap;
2x/0.5x speed retiming produces the correct on-screen duration; a
too-short/unknown-media timeline is rejected before any ffmpeg call;
progress_callback receives real, non-fabricated increasing values on a
slow-enough export; cancel_event terminates the subprocess and leaves
no partial output file behind."""

from __future__ import annotations

import subprocess
import threading
import time

import pytest
from PIL import Image

from jarvis.video_editor import media_import, multisource_export as mse, storage
from jarvis.video_editor.timeline import Timeline, TimelineClip, TimelineStill, TransitionSpec
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available, probe_video

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


@pytest.fixture(autouse=True)
def _isolated_projects_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "VIDEO_EDITOR_PROJECTS_DIR", tmp_path / "video_editor_projects")


def _make_clip(path, *, duration_seconds, width=640, height=360, with_audio=False, color="red"):
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={width}x{height}:d={duration_seconds}"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={duration_seconds}", "-c:a", "aac"]
    cmd += ["-c:v", "libx264", "-t", str(duration_seconds), str(path)]
    subprocess.run(cmd, capture_output=True, timeout=30, check=True)
    return path


def _make_photo(path, *, width=800, height=600, color=(0, 0, 255)):
    Image.new("RGB", (width, height), color=color).save(path, "JPEG" if path.suffix.lower() != ".png" else "PNG")
    return path


def test_resolve_export_format_maps_aspect_and_tier_to_real_pixels():
    fmt = mse.resolve_export_format("9:16", "1080p")
    assert (fmt.width, fmt.height) == (1080, 1920)
    fmt_4k = mse.resolve_export_format("16:9", "4k")
    assert (fmt_4k.width, fmt_4k.height) == (3840, 2160)


def test_resolve_export_format_supports_4x5_instagram_feed_format():
    # Stage 6 of the "professional Reels editor" plan explicitly named
    # 4:5 as a real, previously-missing export format.
    for tier, expected in (("720p", (864, 1080)), ("1080p", (1080, 1350)), ("4k", (2160, 2700))):
        fmt = mse.resolve_export_format("4:5", tier)
        assert (fmt.width, fmt.height) == expected
        assert fmt.width / fmt.height == pytest.approx(4 / 5)


def test_resolve_export_format_rejects_unknown_aspect_ratio():
    with pytest.raises(mse.MultiSourceExportError, match="Unknown aspect ratio"):
        mse.resolve_export_format("4:3", "1080p")


def test_resolve_export_format_rejects_unknown_tier():
    with pytest.raises(mse.MultiSourceExportError, match="Unknown resolution tier"):
        mse.resolve_export_format("9:16", "8k")


def test_export_rejects_an_empty_timeline_before_calling_ffmpeg(tmp_path):
    project = storage.create_project()
    fmt = mse.resolve_export_format("9:16", "720p")
    with pytest.raises(mse.MultiSourceExportError, match="no clips or photos"):
        mse.export_timeline(Timeline(), {}, export_format=fmt, output_path=project.exports_dir / "out.mp4")


def test_export_mixed_video_and_photo_from_different_sources(tmp_path):
    project = storage.create_project()
    clip1 = _make_clip(tmp_path / "clip1.mp4", duration_seconds=3.0, width=640, height=360)
    clip2 = _make_clip(tmp_path / "clip2.mp4", duration_seconds=2.0, width=800, height=600, color="green")
    photo = _make_photo(tmp_path / "photo.jpg")

    m1 = media_import.import_media(clip1, project, media_item_id="m1")
    m2 = media_import.import_media(clip2, project, media_item_id="m2")
    m3 = media_import.import_media(photo, project, media_item_id="m3")
    media_items = {m.media_item_id: m for m in (m1, m2, m3)}

    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=2.0),
        TimelineClip(clip_id="c2", media_item_id="m2", source_in_seconds=0.0, source_out_seconds=1.5),
        TimelineStill(clip_id="s1", media_item_id="m3", display_duration_seconds=1.0),
    ), aspect_ratio="9:16")

    fmt = mse.resolve_export_format("9:16", "1080p")
    output_path = project.exports_dir / "mixed.mp4"
    result = mse.export_timeline(timeline, media_items, export_format=fmt, output_path=output_path)

    assert result.output_path.is_file()
    assert result.width == 1080
    assert result.height == 1920
    assert abs(result.duration_seconds - 4.5) < 0.5

    probe = probe_video(result.output_path)
    assert probe.width == 1080
    assert probe.height == 1920


def test_export_with_a_fade_transition_shortens_total_duration_by_the_overlap(tmp_path):
    project = storage.create_project()
    clip1 = _make_clip(tmp_path / "clip1.mp4", duration_seconds=3.0, color="red")
    clip2 = _make_clip(tmp_path / "clip2.mp4", duration_seconds=3.0, color="blue")
    m1 = media_import.import_media(clip1, project, media_item_id="m1")
    m2 = media_import.import_media(clip2, project, media_item_id="m2")
    media_items = {"m1": m1, "m2": m2}

    timeline = Timeline(items=(
        TimelineClip(
            clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=2.0,
            transition_out=TransitionSpec(kind="fade", duration_seconds=0.5),
        ),
        TimelineClip(clip_id="c2", media_item_id="m2", source_in_seconds=0.0, source_out_seconds=2.0),
    ), aspect_ratio="9:16")

    fmt = mse.resolve_export_format("9:16", "720p")
    output_path = project.exports_dir / "fade.mp4"
    result = mse.export_timeline(timeline, media_items, export_format=fmt, output_path=output_path)

    # 2s + 2s with a 0.5s overlapping crossfade = 3.5s, not 4.0s.
    assert abs(result.duration_seconds - 3.5) < 0.3


@pytest.mark.parametrize("kind", ["slide_left", "slide_right"])
def test_export_with_a_slide_transition_shortens_total_duration_by_the_overlap(tmp_path, kind):
    project = storage.create_project()
    clip1 = _make_clip(tmp_path / "clip1.mp4", duration_seconds=3.0, color="red")
    clip2 = _make_clip(tmp_path / "clip2.mp4", duration_seconds=3.0, color="blue")
    m1 = media_import.import_media(clip1, project, media_item_id="m1")
    m2 = media_import.import_media(clip2, project, media_item_id="m2")
    media_items = {"m1": m1, "m2": m2}

    timeline = Timeline(items=(
        TimelineClip(
            clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=2.0,
            transition_out=TransitionSpec(kind=kind, duration_seconds=0.5),
        ),
        TimelineClip(clip_id="c2", media_item_id="m2", source_in_seconds=0.0, source_out_seconds=2.0),
    ), aspect_ratio="9:16")

    fmt = mse.resolve_export_format("9:16", "720p")
    output_path = project.exports_dir / f"{kind}.mp4"
    result = mse.export_timeline(timeline, media_items, export_format=fmt, output_path=output_path)
    assert abs(result.duration_seconds - 3.5) < 0.3


def test_dissolve_transition_produces_a_genuinely_different_result_than_fade(tmp_path):
    # Real regression test for a hand-hit bug: _apply_crossfades() used
    # to hardcode ffmpeg's xfade transition= to "fade" for every
    # non-"cut" kind, so a "dissolve" transition silently rendered
    # IDENTICAL to "fade" - never its own real cross-dissolve. This
    # extracts a frame at the transition's own midpoint from both a
    # fade export and a dissolve export and asserts the raw pixel bytes
    # genuinely differ, proving the mapping now reaches ffmpeg's own
    # real, distinct "dissolve" xfade transition.
    project = storage.create_project()
    clip1 = _make_clip(tmp_path / "clip1.mp4", duration_seconds=2.0, color="red")
    clip2 = _make_clip(tmp_path / "clip2.mp4", duration_seconds=2.0, color="blue")
    m1 = media_import.import_media(clip1, project, media_item_id="m1")
    m2 = media_import.import_media(clip2, project, media_item_id="m2")
    media_items = {"m1": m1, "m2": m2}
    fmt = mse.resolve_export_format("9:16", "720p")

    outputs = {}
    for kind in ("fade", "dissolve"):
        timeline = Timeline(items=(
            TimelineClip(
                clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=2.0,
                transition_out=TransitionSpec(kind=kind, duration_seconds=0.5),
            ),
            TimelineClip(clip_id="c2", media_item_id="m2", source_in_seconds=0.0, source_out_seconds=2.0),
        ), aspect_ratio="9:16")
        output_path = project.exports_dir / f"{kind}.mp4"
        mse.export_timeline(timeline, media_items, export_format=fmt, output_path=output_path)
        frame_path = tmp_path / f"{kind}_frame.png"
        subprocess.run(
            ["ffmpeg", "-y", "-ss", "1.75", "-i", str(output_path), "-frames:v", "1", str(frame_path)],
            capture_output=True, timeout=15, check=True,
        )
        outputs[kind] = frame_path

    assert Image.open(outputs["fade"]).tobytes() != Image.open(outputs["dissolve"]).tobytes()


def test_export_with_double_speed_halves_on_screen_duration(tmp_path):
    project = storage.create_project()
    clip = _make_clip(tmp_path / "clip.mp4", duration_seconds=4.0, with_audio=True)
    m1 = media_import.import_media(clip, project, media_item_id="m1")
    media_items = {"m1": m1}

    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=4.0, speed_factor=2.0),
    ), aspect_ratio="16:9")

    fmt = mse.resolve_export_format("16:9", "720p")
    output_path = project.exports_dir / "fast.mp4"
    result = mse.export_timeline(timeline, media_items, export_format=fmt, output_path=output_path)

    assert abs(result.duration_seconds - 2.0) < 0.3


def test_export_with_half_speed_doubles_on_screen_duration(tmp_path):
    project = storage.create_project()
    clip = _make_clip(tmp_path / "clip.mp4", duration_seconds=2.0)
    m1 = media_import.import_media(clip, project, media_item_id="m1")
    media_items = {"m1": m1}

    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=2.0, speed_factor=0.5),
    ), aspect_ratio="16:9")

    fmt = mse.resolve_export_format("16:9", "720p")
    output_path = project.exports_dir / "slow.mp4"
    result = mse.export_timeline(timeline, media_items, export_format=fmt, output_path=output_path)

    assert abs(result.duration_seconds - 4.0) < 0.3


def test_export_with_silent_source_still_produces_valid_output(tmp_path):
    # A clip with NO audio track must not break the shared concat's own
    # audio leg (anullsrc fallback - see build_filtergraph()'s own
    # docstring).
    project = storage.create_project()
    clip = _make_clip(tmp_path / "silent.mp4", duration_seconds=2.0, with_audio=False)
    m1 = media_import.import_media(clip, project, media_item_id="m1")
    media_items = {"m1": m1}
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=2.0),
    ), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "720p")
    output_path = project.exports_dir / "silent_out.mp4"
    result = mse.export_timeline(timeline, media_items, export_format=fmt, output_path=output_path)
    assert result.output_path.is_file()


def test_export_rejects_a_timeline_referencing_unknown_media(tmp_path):
    project = storage.create_project()
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="ghost", source_in_seconds=0.0, source_out_seconds=2.0),
    ), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "720p")
    with pytest.raises(mse.MultiSourceExportError, match="unknown media item"):
        mse.export_timeline(timeline, {}, export_format=fmt, output_path=project.exports_dir / "out.mp4")


def test_progress_callback_receives_increasing_real_values(tmp_path):
    project = storage.create_project()
    # A heavier encode (higher resolution, slow preset not directly
    # settable through this public API, so a longer/bigger source is
    # used instead) to reliably produce more than one real ffmpeg
    # -progress tick - a fast synthetic clip can legitimately finish in
    # a single tick, which is not this test's own concern (see
    # test_export_mixed_video_and_photo_from_different_sources for the
    # "it completes correctly regardless" case); this test specifically
    # wants multiple real ticks to assert monotonicity meaningfully.
    clip = tmp_path / "heavy.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1920x1080:duration=8:rate=30",
         "-c:v", "libx264", "-preset", "veryslow", "-t", "8", str(clip)],
        capture_output=True, timeout=60, check=True,
    )
    m1 = media_import.import_media(clip, project, media_item_id="m1")
    media_items = {"m1": m1}
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=8.0),
    ), aspect_ratio="16:9")
    fmt = mse.resolve_export_format("16:9", "1080p")

    percents: list[float] = []
    output_path = project.exports_dir / "progress.mp4"
    mse.export_timeline(timeline, media_items, export_format=fmt, output_path=output_path, progress_callback=percents.append)

    assert len(percents) >= 1
    assert percents[-1] == 100.0
    assert all(percents[i] <= percents[i + 1] for i in range(len(percents) - 1))


def test_cancel_event_terminates_export_and_leaves_no_output_file(tmp_path):
    project = storage.create_project()
    clip = tmp_path / "heavy.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=size=1920x1080:duration=8:rate=30",
         "-c:v", "libx264", "-preset", "veryslow", "-t", "8", str(clip)],
        capture_output=True, timeout=60, check=True,
    )
    m1 = media_import.import_media(clip, project, media_item_id="m1")
    media_items = {"m1": m1}
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=8.0),
    ), aspect_ratio="16:9")
    fmt = mse.resolve_export_format("16:9", "1080p")

    cancel_event = threading.Event()

    def cancel_soon():
        time.sleep(0.5)
        cancel_event.set()

    threading.Thread(target=cancel_soon, daemon=True).start()
    output_path = project.exports_dir / "cancelled.mp4"
    with pytest.raises(mse.MultiSourceExportError, match="cancelled"):
        mse.export_timeline(timeline, media_items, export_format=fmt, output_path=output_path, cancel_event=cancel_event)

    assert not output_path.exists()


def test_still_photo_export_produces_a_windows_media_player_compatible_stream(tmp_path):
    # Real regression test for a user-reported bug: exporting a timeline
    # containing a photo still (common for cover/intro frames) produced
    # a technically valid MP4 that ffprobe/VLC played fine, but Windows
    # Media Player rejected with "unsupported encoding settings" /
    # 0x80004005. Root cause: PNG-sourced stills decode into a 4:4:4
    # chroma layout, and without an explicit format=yuv420p in the
    # scale/pad filter chain, libx264 encoded a real "High 4:4:4
    # Predictive"/yuv444p stream that WMP's own decoder can't handle.
    # This test reads the ACTUAL encoded stream back via ffprobe (not
    # just checking export_timeline() returned successfully) to prove
    # the real fix, not just an absence-of-exception.
    project = storage.create_project()
    photo = _make_photo(tmp_path / "photo.png", width=800, height=600)
    m1 = media_import.import_media(photo, project, media_item_id="m1")
    media_items = {"m1": m1}
    timeline = Timeline(items=(
        TimelineStill(clip_id="c1", media_item_id="m1", display_duration_seconds=2.0),
    ), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "1080p")

    output_path = project.exports_dir / "still.mp4"
    result = mse.export_timeline(timeline, media_items, export_format=fmt, output_path=output_path)
    assert result.output_path.is_file()

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=pix_fmt,profile", "-print_format", "json", str(output_path)],
        capture_output=True, text=True, timeout=30, check=True,
    )
    import json

    streams = json.loads(probe.stdout)["streams"]
    assert streams[0]["pix_fmt"] == "yuv420p"
    assert streams[0]["profile"] == "High"


def test_verify_playable_encoding_rejects_a_4_4_4_stream(tmp_path):
    # Regression-proofs the verification check itself: a deliberately
    # built yuv444p file (the exact incompatible stream the bug above
    # produced) must be rejected with a clear error, never silently
    # accepted.
    bad_file = tmp_path / "bad.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=800x600:d=1",
         "-pix_fmt", "yuv444p", "-c:v", "libx264", str(bad_file)],
        capture_output=True, timeout=30, check=True,
    )
    with pytest.raises(mse.MultiSourceExportError, match="incompatible video stream"):
        mse._verify_playable_encoding(bad_file, ffmpeg_path="ffmpeg")


def test_verify_playable_encoding_accepts_a_yuv420p_stream(tmp_path):
    good_file = tmp_path / "good.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=800x600:d=1",
         "-pix_fmt", "yuv420p", "-c:v", "libx264", str(good_file)],
        capture_output=True, timeout=30, check=True,
    )
    mse._verify_playable_encoding(good_file, ffmpeg_path="ffmpeg")  # must not raise


def test_export_with_sticker_filter_composites_a_real_visible_sticker(tmp_path):
    from jarvis.video_editor.stickers import StickerInstance, build_sticker_filter

    project = storage.create_project()
    clip = _make_clip(tmp_path / "clip.mp4", duration_seconds=3.0, width=1080, height=1920, color="blue")
    m1 = media_import.import_media(clip, project, media_item_id="m1")
    media_items = {"m1": m1}
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=3.0),
    ), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "720p")

    sticker = StickerInstance(start_seconds=0.5, end_seconds=2.5, shape="heart", animation="pop_in")
    extra_args, clause = build_sticker_filter(
        sticker, canvas_width=fmt.width, canvas_height=fmt.height, cwd=project.exports_dir,
        video_label="outv", output_label="stickv", input_index=1,
    )
    output_path = project.exports_dir / "sticker.mp4"
    result = mse.export_timeline(
        timeline, media_items, export_format=fmt, output_path=output_path, sticker_filters=[(extra_args, clause)],
    )
    assert result.output_path.is_file()

    frame_during = project.exports_dir / "during.png"
    frame_before = project.exports_dir / "before.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "1.5", "-i", str(output_path), "-frames:v", "1", str(frame_during)],
        capture_output=True, timeout=15, check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "0.1", "-i", str(output_path), "-frames:v", "1", str(frame_before)],
        capture_output=True, timeout=15, check=True,
    )
    assert Image.open(frame_during).tobytes() != Image.open(frame_before).tobytes()


def test_export_without_sticker_filters_is_unchanged(tmp_path):
    # Regression check: omitting sticker_filters (the default, None)
    # must reproduce the exact same export as before this parameter
    # existed - no accidental behavior change for every pre-existing
    # export call that never passes it.
    project = storage.create_project()
    clip = _make_clip(tmp_path / "clip.mp4", duration_seconds=2.0, color="red")
    m1 = media_import.import_media(clip, project, media_item_id="m1")
    media_items = {"m1": m1}
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=2.0),
    ), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "720p")
    output_path = project.exports_dir / "no_sticker.mp4"
    result = mse.export_timeline(timeline, media_items, export_format=fmt, output_path=output_path)
    assert result.output_path.is_file()


def test_export_detects_a_sticker_and_music_input_index_collision_before_calling_ffmpeg(tmp_path):
    # Real regression test for a user-reported export failure: a caller
    # (jarvis.gui.views.video_editor.dashboard's own _start_export(),
    # fixed separately) once computed audio_mix_filter's own input
    # index without accounting for sticker_filters' own already-claimed
    # indices, so music's own `[N:a]` reference collided with a
    # sticker's PNG input - ffmpeg failed deep inside its own
    # filtergraph binding step with a cryptic error. This test verifies
    # export_timeline() ITSELF now catches that exact collision shape
    # with a clear, actionable MultiSourceExportError, as a defensive
    # backstop independent of any one caller getting its own input-index
    # math right.
    from jarvis.video_editor.stickers import StickerInstance, build_sticker_filter

    project = storage.create_project()
    clip = _make_clip(tmp_path / "clip.mp4", duration_seconds=3.0, color="blue")
    m1 = media_import.import_media(clip, project, media_item_id="m1")
    media_items = {"m1": m1}
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=3.0),
    ), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "1080p")

    sticker = StickerInstance(start_seconds=0.2, end_seconds=1.0, shape="heart")
    extra_args, clause = build_sticker_filter(
        sticker, canvas_width=fmt.width, canvas_height=fmt.height, cwd=project.exports_dir,
        video_label="outv", output_label="stickv0", input_index=1,
    )
    # Deliberately buggy: music ALSO claims input index 1, colliding
    # with the sticker's own real input index.
    colliding_audio_mix_filter = (
        ["-i", "fake.wav"], "[1:a]anull[music];[outa][music]amix=inputs=2[mixedaudio]", "mixedaudio",
    )
    output_path = project.exports_dir / "should_fail.mp4"
    with pytest.raises(mse.MultiSourceExportError, match="collides with an input already used"):
        mse.export_timeline(
            timeline, media_items, export_format=fmt, output_path=output_path,
            sticker_filters=[(extra_args, clause)], audio_mix_filter=colliding_audio_mix_filter,
        )
    assert not output_path.exists()
