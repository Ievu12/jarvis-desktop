"""Stage 5: clip sound (volume, mute, fades) in the preview and the
export, the clip-sound and music lanes, text opacity, layer order,
SRT import and the Lithuanian animation categories."""

from __future__ import annotations

import dataclasses
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from jarvis.video_editor import animation_catalog as catalog
from jarvis.video_editor import multisource_export as mse
from jarvis.video_editor import track_layout as tl
from jarvis.video_editor.captions import CaptionError, CaptionLine, import_srt, parse_srt
from jarvis.video_editor.editor_state import EditorState
from jarvis.video_editor.media_import import MediaItem
from jarvis.video_editor.playback import render_preview_audio
from jarvis.video_editor.stickers import STICKER_ANIMATION_CHOICES, StickerInstance
from jarvis.video_editor.text_overlay import TEXT_ANIMATION_CHOICES, TextOverlay, build_text_overlay_filter
from jarvis.video_editor.timeline import Timeline, TimelineClip, TimelineStill

MEDIA = {
    "v": MediaItem(media_item_id="v", original_filename="klipas.mp4", stored_path=Path("a.mp4"), kind="video",
                   duration_seconds=10, width=1080, height=1920, fps=30),
    "p": MediaItem(media_item_id="p", original_filename="foto.jpg", stored_path=Path("b.jpg"), kind="photo",
                   duration_seconds=None, width=800, height=600, fps=None),
}
CLIP = TimelineClip(clip_id="c1", media_item_id="v", source_in_seconds=2, source_out_seconds=6)
STILL = TimelineStill(clip_id="s1", media_item_id="p", display_duration_seconds=3)


# --- clip sound -------------------------------------------------------------------------------------


def test_clip_sound_lane_shows_volume_and_only_real_clips():
    state = EditorState(timeline=Timeline(items=(CLIP, STILL, dataclasses.replace(CLIP, clip_id="c2", volume=0.0))))
    bars = tl.build_track_bars(state, MEDIA)
    assert [(b.index, b.label) for b in bars["sound"]] == [(0, "🔊 100%"), (2, "🔇 Nutildyta")]
    assert not any(b.can_move or b.can_trim_end for b in bars["sound"])
    assert tl.TRACK_LABELS["audio"] == "🎵 Muzika"


def test_set_clip_sound_and_deleting_a_sound_bar_mutes():
    state = EditorState(timeline=Timeline(items=(CLIP, STILL, dataclasses.replace(CLIP, clip_id="c2"))))
    louder = tl.set_clip_sound(state, 0, volume=1.5, fade_in=0.5)
    assert (louder.timeline.items[0].volume, louder.timeline.items[0].audio_fade_in_seconds) == (1.5, 0.5)
    assert louder.timeline.items[2].volume == 1.0
    everywhere = tl.set_clip_sound(state, None, volume=9.0, fade_out=-1)
    assert [getattr(i, "volume", None) for i in everywhere.timeline.items] == [2.0, None, 2.0]  # clamped, photo skipped
    muted = tl.delete_element(state, "sound", 2)
    assert muted.timeline.items[2].volume == 0.0 and muted.timeline.items[0] == CLIP


def test_clip_volume_is_validated():
    assert Timeline(items=(dataclasses.replace(CLIP, volume=3.0),)).validate()
    assert Timeline(items=(dataclasses.replace(CLIP, audio_fade_in_seconds=-1),)).validate()
    assert Timeline(items=(dataclasses.replace(CLIP, volume=0.0, audio_fade_out_seconds=2),)).validate() == []


def test_clip_sound_chain_goes_after_retiming():
    clip = dataclasses.replace(CLIP, speed_factor=2.0, volume=0.5, audio_fade_in_seconds=0.5, audio_fade_out_seconds=9)
    chain = mse._clip_sound_chain(clip)
    # 4 s of source at 2x is 2 s on screen; a fade longer than that is clamped to it
    assert chain == ",volume=0.500,afade=t=in:st=0:d=0.500,afade=t=out:st=0.000:d=2.000"
    assert mse._clip_sound_chain(CLIP) == ""


def test_clip_sound_survives_a_reopen(tmp_path, monkeypatch):
    from jarvis.video_editor import db, storage

    monkeypatch.setattr(db, "VIDEO_EDITOR_DB_FILE", tmp_path / "ve.db")
    monkeypatch.setattr(storage, "VIDEO_EDITOR_PROJECTS_DIR", tmp_path / "projects")
    db.create_project_record("p1", "Test")
    timeline = Timeline(items=(dataclasses.replace(CLIP, volume=0.3, audio_fade_in_seconds=1, audio_fade_out_seconds=2),))
    storage.save_project("p1", timeline, {"v": MEDIA["v"]})
    assert storage.load_project("p1")[0] == timeline


def _mean_volume(path: Path) -> float:
    result = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostdin", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True, check=True,
    )
    match = re.search(r"mean_volume: (-?[\d.]+|-inf) dB", result.stderr)
    return float(match.group(1)) if match and match.group(1) != "-inf" else -200.0


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")
def test_clip_volume_is_heard_the_same_in_preview_and_export(tmp_path):
    clip_path = tmp_path / "tone.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=320x568:r=30:d=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(clip_path)],
        check=True,
    )
    media = {"v": MediaItem(media_item_id="v", original_filename="tone.mp4", stored_path=clip_path, kind="video",
                            duration_seconds=2, width=320, height=568, fps=30)}
    clip = TimelineClip(clip_id="c", media_item_id="v", source_in_seconds=0, source_out_seconds=2)
    fmt = mse.resolve_export_format("9:16", "720p")
    levels = {}
    for name, volume in (("full", 1.0), ("half", 0.5), ("muted", 0.0)):
        timeline = Timeline(items=(dataclasses.replace(clip, volume=volume),))
        preview = render_preview_audio(timeline, media, output_path=tmp_path / f"{name}.wav", cwd=tmp_path)
        exported = tmp_path / f"{name}.mp4"
        mse.export_timeline(timeline, media, export_format=fmt, output_path=exported)
        levels[name] = (_mean_volume(preview), _mean_volume(exported))
    for preview_db, export_db in levels.values():
        assert abs(preview_db - export_db) < 1.5
    assert abs((levels["full"][1] - levels["half"][1]) - 6.0) < 1.0  # half the amplitude is -6 dB
    assert levels["muted"][1] < -80


# --- text opacity and layer order -------------------------------------------------------------------


def test_text_opacity_scales_every_drawtext_alpha():
    text = TextOverlay(text="Labas", start_seconds=0, end_seconds=2, opacity=0.5)
    assert "alpha=0.500:enable=" in build_text_overlay_filter([text])
    fade = build_text_overlay_filter([dataclasses.replace(text, animation="fade")])
    assert "alpha='0.500*if(lt(t,0)" in fade
    glitch = build_text_overlay_filter([dataclasses.replace(text, animation="glitch")])
    assert glitch.count("alpha=0.500") == 3
    typewriter = build_text_overlay_filter([dataclasses.replace(text, animation="typewriter")])
    assert typewriter.count("alpha=0.500") == typewriter.count("drawtext=") == 5
    assert "alpha" not in build_text_overlay_filter([dataclasses.replace(text, opacity=1.0)])
    assert dataclasses.replace(text, opacity=1.2).validate() == ["Text opacity must be between 0.0 and 1.0."]


def test_reorder_overlay_moves_one_layer_and_stops_at_the_ends():
    a, b, c = (TextOverlay(text=n, start_seconds=0, end_seconds=1) for n in "abc")
    state = EditorState(text_overlays=(a, b, c))
    up, index = tl.reorder_overlay(state, "text", 0, +1)
    assert [o.text for o in up.text_overlays] == ["b", "a", "c"] and index == 1
    same, index = tl.reorder_overlay(state, "text", 2, +1)
    assert same is state and index == 2
    stickers = EditorState(stickers=(StickerInstance(0, 1, shape="heart"), StickerInstance(0, 1, shape="star")))
    down, index = tl.reorder_overlay(stickers, "stickers", 1, -1)
    assert [s.shape for s in down.stickers] == ["star", "heart"] and index == 0


# --- SRT ------------------------------------------------------------------------------------------


def test_parse_srt_handles_real_world_files():
    text = (
        "﻿1\r\n00:00:01,000 --> 00:00:02,500\r\nLabas, <i>Ieva</i>!\r\nKaip sekasi?\r\n\r\n"
        "00:00:03.2 --> 00:00:04.75\nBe numerio ąčęėįšųūž\n\n"
        "3\n00:00:05,000 --> 00:00:04,000\nBloga eilutė\n\n"
        "4\n00:00:00,100 --> 00:00:00,900\n\n"
        "5\n00:00:00,000 --> 00:00:00,800\nPirma\n"
    )
    assert parse_srt(text) == [
        CaptionLine(text="Pirma", start_seconds=0.0, end_seconds=0.8),
        CaptionLine(text="Labas, Ieva! Kaip sekasi?", start_seconds=1.0, end_seconds=2.5),
        CaptionLine(text="Be numerio ąčęėįšųūž", start_seconds=3.2, end_seconds=4.75),
    ]


def test_import_srt_reads_lithuanian_in_every_common_encoding(tmp_path):
    from jarvis.video_editor.captions import export_srt

    lines = [CaptionLine(text="Šiandien žąsys skrenda į pietus", start_seconds=0.5, end_seconds=2.25)]
    utf8 = tmp_path / "utf8.srt"
    export_srt(lines, utf8)
    assert import_srt(utf8) == lines
    baltic = tmp_path / "baltic.srt"
    baltic.write_bytes(utf8.read_text(encoding="utf-8").encode("cp1257"))
    assert import_srt(baltic) == lines
    utf16 = tmp_path / "utf16.srt"
    utf16.write_text(utf8.read_text(encoding="utf-8"), encoding="utf-16")
    assert import_srt(utf16) == lines
    empty = tmp_path / "empty.srt"
    empty.write_text("not subtitles", encoding="utf-8")
    with pytest.raises(CaptionError):
        import_srt(empty)


# --- animation catalog ------------------------------------------------------------------------------


def test_every_animation_has_a_lithuanian_name_and_a_category():
    for kind, choices in (("text", TEXT_ANIMATION_CHOICES), ("sticker", STICKER_ANIMATION_CHOICES)):
        assert catalog.animations_in(kind, "all") == choices
        for animation in choices:
            label = catalog.animation_label(kind, animation)
            assert label != animation and catalog.animation_from_label(kind, label) == animation
            category = catalog.category_of(kind, animation)
            assert animation in catalog.animations_in(kind, category)
        for category in catalog.ANIMATION_CATEGORIES:
            listed = catalog.animations_in(kind, category)
            assert listed[0] == "none" and set(listed) <= set(choices)
    assert catalog.animations_in("text", "slide") == ("none", "slide_in", "slide_out")
    assert set(catalog.CATEGORY_LABELS.values()) >= {"Įėjimo", "Išėjimo", "Judėjimo", "Mastelio", "Išnykimo",
                                                      "Šokinėjimo", "Slinkimo"}
