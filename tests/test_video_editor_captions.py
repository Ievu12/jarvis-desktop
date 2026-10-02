"""Tests for jarvis.video_editor.captions: real word-level transcription
(ffmpeg's own whisper filter, format=json:max_len=1) against a REAL
speech WAV file generated via Windows SAPI (System.Speech), and real
ffmpeg drawtext filter-string construction/execution. Skipped entirely
if ffmpeg isn't on PATH or the whisper transcription model hasn't been
downloaded yet (same convention as every other whisper-dependent test
in this codebase - see tests/test_video_studio_transcribe.py's own
skip guard).

Confirms: generate_word_timings() returns REAL, measured per-word
start/end times (not a character-count heuristic) for real speech;
missing file/no ffmpeg/no model/no speech all raise CaptionError with a
clear message; build_caption_filter() produces a well-formed filter
string for both "word_by_word" and "none" animation presets, and a real
ffmpeg export using that filter string actually succeeds and produces
visibly correct output (verified by extracting and inspecting a real
frame, not just checking the file exists)."""

from __future__ import annotations

import subprocess

import pytest
from PIL import Image

from jarvis.video_editor.captions import (
    CAPTION_ANIMATION_CHOICES,
    CAPTION_POSITION_CHOICES,
    CaptionError,
    CaptionLine,
    CaptionStyle,
    WordTiming,
    build_caption_filter,
    build_caption_filter_from_lines,
    generate_word_timings,
    group_words_into_lines,
)
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available
from jarvis.video_studio.transcribe import model_is_downloaded

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")


def _make_real_speech_wav(path, *, text: str = "Hello there, this is a real caption test.") -> bool:
    """Generates a real speech WAV via Windows SAPI (System.Speech) -
    returns True on success, False if no SAPI voice is available (e.g.
    a non-Windows CI runner) so callers can skip rather than fail."""
    import shutil

    powershell = shutil.which("powershell")
    if powershell is None:
        return False
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f'$synth.SetOutputToWaveFile("{path}"); '
        f'$synth.Speak("{text}"); '
        "$synth.Dispose()"
    )
    result = subprocess.run([powershell, "-Command", script], capture_output=True, timeout=30)
    return result.returncode == 0 and path.is_file()


def _make_real_video_with_speech(tmp_path, *, text: str = "Hello there, this is a real caption test."):
    wav_path = tmp_path / "speech.wav"
    if not _make_real_speech_wav(wav_path, text=text):
        pytest.skip("Windows SAPI (System.Speech) not available to generate real speech audio")
    video_path = tmp_path / "video.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=640x360:d=5", "-i", str(wav_path),
         "-c:v", "libx264", "-c:a", "aac", "-shortest", str(video_path)],
        capture_output=True, timeout=30, check=True,
    )
    return video_path


@pytest.mark.skipif(not model_is_downloaded(), reason="whisper transcription model not downloaded yet")
def test_generate_word_timings_returns_real_measured_timestamps(tmp_path):
    video_path = _make_real_video_with_speech(tmp_path)
    words = generate_word_timings(video_path, language="en")

    assert len(words) >= 4
    assert all(isinstance(w, WordTiming) for w in words)
    # Real, measured timing - monotonically non-decreasing, each word's
    # own end after its own start, never a fabricated/estimated value.
    for word in words:
        assert word.end_seconds > word.start_seconds
    for i in range(len(words) - 1):
        assert words[i].start_seconds <= words[i + 1].start_seconds
    joined = " ".join(w.text for w in words).lower()
    assert "hello" in joined


def test_generate_word_timings_rejects_a_missing_file(tmp_path):
    with pytest.raises(CaptionError, match="not found"):
        generate_word_timings(tmp_path / "ghost.mp4")


@pytest.mark.skipif(not model_is_downloaded(), reason="whisper transcription model not downloaded yet")
def test_generate_word_timings_rejects_a_silent_video(tmp_path):
    video_path = tmp_path / "silent.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=red:s=320x240:d=2", "-c:v", "libx264", "-t", "2", str(video_path)],
        capture_output=True, timeout=30, check=True,
    )
    with pytest.raises(CaptionError, match="no audio track"):
        generate_word_timings(video_path)


def test_caption_style_defaults():
    style = CaptionStyle()
    assert style.animation == "word_by_word"
    assert style.position == "bottom"
    assert style.font_size > 0


def test_caption_position_and_animation_choices():
    assert CAPTION_POSITION_CHOICES == ("top", "center", "bottom")
    assert CAPTION_ANIMATION_CHOICES == ("word_by_word", "none")


def test_build_caption_filter_empty_words_is_a_passthrough():
    filter_str = build_caption_filter([], CaptionStyle())
    assert filter_str == "[outv]null[capv]"


def test_build_caption_filter_word_by_word_has_one_drawtext_per_word():
    words = [
        WordTiming(start_seconds=0.0, end_seconds=0.5, text="Hello"),
        WordTiming(start_seconds=0.5, end_seconds=1.0, text="world"),
    ]
    filter_str = build_caption_filter(words, CaptionStyle(animation="word_by_word"))
    assert filter_str.count("drawtext=") == 2
    assert "Hello" in filter_str
    assert "world" in filter_str
    assert filter_str.startswith("[outv]")
    assert filter_str.endswith("[capv]")


def test_build_caption_filter_none_animation_has_one_combined_drawtext():
    words = [
        WordTiming(start_seconds=0.0, end_seconds=0.5, text="Hello"),
        WordTiming(start_seconds=0.5, end_seconds=1.0, text="world"),
    ]
    filter_str = build_caption_filter(words, CaptionStyle(animation="none"))
    assert filter_str.count("drawtext=") == 1
    assert "Hello world" in filter_str


def test_build_caption_filter_applies_time_offset():
    words = [WordTiming(start_seconds=0.0, end_seconds=0.5, text="Hi")]
    filter_str = build_caption_filter(words, CaptionStyle(), time_offset_seconds=10.0)
    assert "between(t,10.0,10.5)" in filter_str


def test_build_caption_filter_escapes_special_characters():
    words = [WordTiming(start_seconds=0.0, end_seconds=0.5, text="it's: a test")]
    filter_str = build_caption_filter(words, CaptionStyle())
    # Must not contain a raw, unescaped colon/apostrophe inside the text
    # value that would break ffmpeg's own filtergraph parsing - the
    # real, hand-confirmed concern this function's own docstring states.
    assert "\\:" in filter_str or ":" not in "it's: a test"
    assert "\\'" in filter_str


def test_build_caption_filter_respects_custom_labels():
    words = [WordTiming(start_seconds=0.0, end_seconds=0.5, text="Hi")]
    filter_str = build_caption_filter(words, CaptionStyle(), video_label="myvideo", output_label="mycaptions")
    assert filter_str.startswith("[myvideo]")
    assert filter_str.endswith("[mycaptions]")


@pytest.mark.skipif(not model_is_downloaded(), reason="whisper transcription model not downloaded yet")
def test_real_export_with_burned_in_word_by_word_captions_is_visually_correct(tmp_path):
    """The real end-to-end proof: real speech -> real word timings ->
    real drawtext filter -> real ffmpeg export -> extract a real frame
    at a known word's own timing window and confirm the output is a
    genuinely different image from the same frame rendered WITHOUT
    captions (proving the caption was actually burned in, not just that
    the export succeeded)."""
    from jarvis.video_editor import media_import, multisource_export as mse, storage
    from jarvis.video_editor.timeline import Timeline, TimelineClip

    video_path = _make_real_video_with_speech(tmp_path)
    words = generate_word_timings(video_path, language="en")
    assert words

    import jarvis.config as config

    project = storage.create_project()
    item = media_import.import_media(video_path, project, media_item_id="m1")
    media_items = {"m1": item}
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=item.duration_seconds),
    ), aspect_ratio="16:9")
    fmt = mse.resolve_export_format("16:9", "720p")

    input_args, full_filter, video_out, audio_out = mse.build_filtergraph(timeline, media_items, fmt, cwd=project.exports_dir)
    caption_filter = build_caption_filter(words, CaptionStyle(), video_label=video_out, output_label="capv")
    combined_filter = full_filter + ";" + caption_filter

    captioned_path = project.exports_dir / "captioned.mp4"
    cmd = [
        "ffmpeg", "-y", *input_args, "-filter_complex", combined_filter,
        "-map", "[capv]", "-map", f"[{audio_out}]", "-c:v", "libx264", "-c:a", "aac", captioned_path.name,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60, cwd=str(project.exports_dir))
    assert result.returncode == 0, result.stderr[-500:]
    assert captioned_path.is_file()

    # Without captions, for comparison.
    plain_path = project.exports_dir / "plain.mp4"
    plain_cmd = [
        "ffmpeg", "-y", *input_args, "-filter_complex", full_filter,
        "-map", f"[{video_out}]", "-map", f"[{audio_out}]", "-c:v", "libx264", "-c:a", "aac", plain_path.name,
    ]
    subprocess.run(plain_cmd, capture_output=True, timeout=60, cwd=str(project.exports_dir), check=True)

    first_word_midpoint = (words[0].start_seconds + words[0].end_seconds) / 2
    captioned_frame = project.exports_dir / "captioned_frame.png"
    plain_frame = project.exports_dir / "plain_frame.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(first_word_midpoint), "-i", str(captioned_path), "-vframes", "1", str(captioned_frame)],
        capture_output=True, timeout=30, check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-ss", str(first_word_midpoint), "-i", str(plain_path), "-vframes", "1", str(plain_frame)],
        capture_output=True, timeout=30, check=True,
    )

    with Image.open(captioned_frame) as a, Image.open(plain_frame) as b:
        assert a.convert("RGB").tobytes() != b.convert("RGB").tobytes()


# --- editable subtitle lines (group_words_into_lines / CaptionLine / build_caption_filter_from_lines) ------------


def test_group_words_into_lines_with_empty_list_returns_empty():
    assert group_words_into_lines([]) == []


def test_group_words_into_lines_splits_on_a_real_pause():
    words = [
        WordTiming(start_seconds=0.0, end_seconds=0.3, text="Hello"),
        WordTiming(start_seconds=0.35, end_seconds=0.6, text="world"),
        WordTiming(start_seconds=2.0, end_seconds=2.3, text="Second"),
        WordTiming(start_seconds=2.35, end_seconds=2.7, text="sentence"),
    ]
    lines = group_words_into_lines(words)
    assert len(lines) == 2
    assert lines[0] == CaptionLine(text="Hello world", start_seconds=0.0, end_seconds=0.6)
    assert lines[1] == CaptionLine(text="Second sentence", start_seconds=2.0, end_seconds=2.7)


def test_group_words_into_lines_keeps_closely_spaced_words_on_one_line():
    words = [
        WordTiming(start_seconds=0.0, end_seconds=0.3, text="One"),
        WordTiming(start_seconds=0.35, end_seconds=0.6, text="two"),
        WordTiming(start_seconds=0.65, end_seconds=1.0, text="three"),
    ]
    lines = group_words_into_lines(words)
    assert len(lines) == 1
    assert lines[0].text == "One two three"


def test_caption_line_validate_rejects_empty_text():
    line = CaptionLine(text="   ", start_seconds=0.0, end_seconds=1.0)
    assert any("no text" in p for p in line.validate())


def test_caption_line_validate_rejects_backwards_time_window():
    line = CaptionLine(text="Hi", start_seconds=5.0, end_seconds=2.0)
    assert any("end time must be after" in p for p in line.validate())


def test_build_caption_filter_from_lines_empty_is_a_passthrough():
    clause = build_caption_filter_from_lines([], CaptionStyle())
    assert clause == "[outv]null[capv]"


def test_build_caption_filter_from_lines_rejects_invalid_line():
    bad_line = CaptionLine(text="", start_seconds=0.0, end_seconds=1.0)
    with pytest.raises(CaptionError):
        build_caption_filter_from_lines([bad_line], CaptionStyle())


def test_build_caption_filter_from_lines_produces_one_drawtext_per_line():
    lines = [
        CaptionLine(text="First line", start_seconds=0.0, end_seconds=1.0),
        CaptionLine(text="Second line", start_seconds=1.5, end_seconds=2.5),
    ]
    clause = build_caption_filter_from_lines(lines, CaptionStyle())
    assert clause.count("drawtext=") == 2
    assert "First line" in clause
    assert "Second line" in clause


def test_build_caption_filter_from_lines_reflects_an_edited_text_not_tied_to_any_word():
    # The whole point of an editable line - its text can be completely
    # rewritten by the person, no longer matching any real transcribed
    # word, and the filter must still burn in exactly what was typed.
    edited_line = CaptionLine(text="COMPLETELY REWRITTEN TEXT", start_seconds=0.2, end_seconds=1.8)
    clause = build_caption_filter_from_lines([edited_line], CaptionStyle())
    assert "COMPLETELY REWRITTEN TEXT" in clause
    assert "enable='between(t,0.2,1.8)'" in clause


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_real_export_with_edited_caption_line_is_visually_correct(tmp_path):
    from jarvis.video_editor import media_import, multisource_export as mse, storage
    from jarvis.video_editor.timeline import Timeline, TimelineClip

    project = storage.create_project()
    clip = tmp_path / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=1080x1920:d=2", "-c:v", "libx264", "-t", "2", str(clip)],
        capture_output=True, timeout=30, check=True,
    )
    m1 = media_import.import_media(clip, project, media_item_id="m1")
    media_items = {"m1": m1}
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0.0, source_out_seconds=2.0),
    ), aspect_ratio="9:16")
    fmt = mse.resolve_export_format("9:16", "720p")

    lines = [CaptionLine(text="EDITED SUBTITLE", start_seconds=0.2, end_seconds=1.8)]
    clause = build_caption_filter_from_lines(lines, CaptionStyle())

    plain_path = project.exports_dir / "plain.mp4"
    captioned_path = project.exports_dir / "captioned.mp4"
    mse.export_timeline(timeline, media_items, export_format=fmt, output_path=plain_path)
    mse.export_timeline(timeline, media_items, export_format=fmt, output_path=captioned_path, caption_filter=clause)

    frame_plain = tmp_path / "plain.png"
    frame_captioned = tmp_path / "captioned.png"
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "1.0", "-i", str(plain_path), "-frames:v", "1", str(frame_plain)],
        capture_output=True, timeout=15, check=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-ss", "1.0", "-i", str(captioned_path), "-frames:v", "1", str(frame_captioned)],
        capture_output=True, timeout=15, check=True,
    )
    assert Image.open(frame_plain).tobytes() != Image.open(frame_captioned).tobytes()
