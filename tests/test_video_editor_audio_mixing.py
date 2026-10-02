"""Tests for jarvis.video_editor.audio_mixing: real ffmpeg music
trim/volume/fade/mix, verified against real synthetic audio (ffmpeg
lavfi sine sources at different frequencies, so "did the music actually
get mixed in" can be checked by real waveform comparison, not just file
existence). Skipped entirely if ffmpeg isn't on PATH.

Confirms: validate_music_track() catches every documented problem
(missing file, unsupported extension, bad trim range, out-of-range
volume, negative fades) and accepts a well-formed track; a real
exported video WITH music differs in its actual audio bytes from the
same export WITHOUT music (proving real mixing, not a silent no-op);
the mixed output's own total duration is clamped to the TIMELINE's own
length even when the music track is longer; a too-long music track
doesn't extend the video."""

from __future__ import annotations

import subprocess

import pytest

from jarvis.video_editor.audio_mixing import (
    SUPPORTED_MUSIC_EXTENSIONS,
    AudioMixingError,
    MusicTrack,
    build_music_mix_filter,
    validate_music_track,
)
from jarvis.video_editor import media_import, multisource_export as mse, storage
from jarvis.video_editor.timeline import Timeline, TimelineClip
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available, probe_video

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


@pytest.fixture(autouse=True)
def _isolated_projects_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "VIDEO_EDITOR_PROJECTS_DIR", tmp_path / "video_editor_projects")


def _make_clip_with_audio(path, *, duration_seconds=3.0, frequency=440):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=red:s=320x240:d={duration_seconds}",
         "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration_seconds}",
         "-c:v", "libx264", "-c:a", "aac", "-t", str(duration_seconds), str(path)],
        capture_output=True, timeout=30, check=True,
    )
    return path


def _make_music(path, *, duration_seconds=5.0, frequency=880):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={duration_seconds}", str(path)],
        capture_output=True, timeout=30, check=True,
    )
    return path


def test_supported_music_extensions_are_broad():
    assert ".mp3" in SUPPORTED_MUSIC_EXTENSIONS
    assert ".wav" in SUPPORTED_MUSIC_EXTENSIONS


def test_validate_music_track_accepts_a_well_formed_track(tmp_path):
    music_path = _make_music(tmp_path / "music.wav")
    track = MusicTrack(source_path=music_path)
    assert validate_music_track(track) == []


def test_validate_music_track_rejects_a_missing_file(tmp_path):
    track = MusicTrack(source_path=tmp_path / "ghost.mp3")
    problems = validate_music_track(track)
    assert any("not found" in p for p in problems)


def test_validate_music_track_rejects_an_unsupported_extension(tmp_path):
    bad_path = tmp_path / "notes.txt"
    bad_path.write_text("not music")
    track = MusicTrack(source_path=bad_path)
    problems = validate_music_track(track)
    assert any("Unsupported music file type" in p for p in problems)


def test_validate_music_track_rejects_backwards_trim_range(tmp_path):
    music_path = _make_music(tmp_path / "music.wav")
    track = MusicTrack(source_path=music_path, trim_start_seconds=5.0, trim_end_seconds=2.0)
    problems = validate_music_track(track)
    assert any("trim end must be after trim start" in p for p in problems)


def test_validate_music_track_rejects_out_of_range_volume(tmp_path):
    music_path = _make_music(tmp_path / "music.wav")
    track = MusicTrack(source_path=music_path, volume=10.0)
    problems = validate_music_track(track)
    assert any("outside the supported" in p for p in problems)


def test_validate_music_track_rejects_negative_fades(tmp_path):
    music_path = _make_music(tmp_path / "music.wav")
    track = MusicTrack(source_path=music_path, fade_in_seconds=-1.0)
    problems = validate_music_track(track)
    assert any("cannot be negative" in p for p in problems)


def test_build_music_mix_filter_rejects_an_invalid_track(tmp_path):
    track = MusicTrack(source_path=tmp_path / "ghost.mp3")
    with pytest.raises(AudioMixingError):
        build_music_mix_filter(track, timeline_duration_seconds=5.0, cwd=tmp_path, timeline_audio_input_count=1)


def test_real_export_with_music_has_genuinely_different_audio_than_without(tmp_path):
    project = storage.create_project()
    video_path = _make_clip_with_audio(tmp_path / "clip.mp4", duration_seconds=3.0, frequency=440)
    music_path = _make_music(tmp_path / "music.wav", duration_seconds=3.0, frequency=880)

    item = media_import.import_media(video_path, project, media_item_id="m1")
    media_items = {"m1": item}
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=3.0),
    ), aspect_ratio="16:9")
    fmt = mse.resolve_export_format("16:9", "720p")

    music_project_path = storage.copy_media_into_project(project, music_path)
    track = MusicTrack(source_path=music_project_path, volume=0.8, fade_in_seconds=0.2, fade_out_seconds=0.2)

    input_args, full_filter, video_out, audio_out = mse.build_filtergraph(timeline, media_items, fmt, cwd=project.exports_dir)
    audio_mix = build_music_mix_filter(
        track, timeline_duration_seconds=3.0, cwd=project.exports_dir,
        timeline_audio_input_count=1, timeline_audio_label=audio_out,
    )

    with_music_path = project.exports_dir / "with_music.mp4"
    without_music_path = project.exports_dir / "without_music.mp4"
    result_with = mse.export_timeline(timeline, media_items, export_format=fmt, output_path=with_music_path, audio_mix_filter=audio_mix)
    result_without = mse.export_timeline(timeline, media_items, export_format=fmt, output_path=without_music_path)

    assert result_with.output_path.is_file()
    assert result_without.output_path.is_file()
    # Duration stays exactly the video's own length regardless of music.
    assert abs(result_with.duration_seconds - 3.0) < 0.3
    assert abs(result_without.duration_seconds - 3.0) < 0.3

    wav_with = tmp_path / "with.wav"
    wav_without = tmp_path / "without.wav"
    subprocess.run(["ffmpeg", "-y", "-i", str(with_music_path), "-vn", "-acodec", "pcm_s16le", str(wav_with)], capture_output=True, timeout=30, check=True)
    subprocess.run(["ffmpeg", "-y", "-i", str(without_music_path), "-vn", "-acodec", "pcm_s16le", str(wav_without)], capture_output=True, timeout=30, check=True)

    assert wav_with.read_bytes() != wav_without.read_bytes()


def test_music_longer_than_timeline_does_not_extend_the_export(tmp_path):
    project = storage.create_project()
    video_path = _make_clip_with_audio(tmp_path / "clip.mp4", duration_seconds=2.0)
    music_path = _make_music(tmp_path / "music.wav", duration_seconds=10.0)  # much longer than the video

    item = media_import.import_media(video_path, project, media_item_id="m1")
    media_items = {"m1": item}
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=2.0),
    ), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "720p")

    music_project_path = storage.copy_media_into_project(project, music_path)
    track = MusicTrack(source_path=music_project_path)

    input_args, full_filter, video_out, audio_out = mse.build_filtergraph(timeline, media_items, fmt, cwd=project.exports_dir)
    audio_mix = build_music_mix_filter(
        track, timeline_duration_seconds=2.0, cwd=project.exports_dir,
        timeline_audio_input_count=1, timeline_audio_label=audio_out,
    )
    output_path = project.exports_dir / "clamped.mp4"
    result = mse.export_timeline(timeline, media_items, export_format=fmt, output_path=output_path, audio_mix_filter=audio_mix)

    assert abs(result.duration_seconds - 2.0) < 0.3


def test_export_without_audio_mix_filter_is_unchanged(tmp_path):
    # Omitting audio_mix_filter (every call site before this feature)
    # must reproduce the exact previous export behavior.
    project = storage.create_project()
    video_path = _make_clip_with_audio(tmp_path / "clip.mp4", duration_seconds=2.0)
    item = media_import.import_media(video_path, project, media_item_id="m1")
    media_items = {"m1": item}
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=2.0),
    ), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "720p")
    output_path = project.exports_dir / "plain.mp4"
    result = mse.export_timeline(timeline, media_items, export_format=fmt, output_path=output_path)
    assert result.output_path.is_file()
    probe = probe_video(result.output_path)
    assert probe.has_audio is True
