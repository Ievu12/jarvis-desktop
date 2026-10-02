"""Tests for saving and reopening a whole Video Editor project
(jarvis.video_editor.storage.save_project()/load_project() +
save_overlays()/load_overlays(), and the db migration that added the
overlays_data column). Before these, reopening a project silently lost
every clip effect, text overlay, sticker, caption and music setting."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from jarvis.video_editor import db, storage
from jarvis.video_editor.audio_mixing import MusicTrack
from jarvis.video_editor.captions import CaptionLine, CaptionStyle
from jarvis.video_editor.effects import EffectSpec
from jarvis.video_editor.media_import import MediaItem
from jarvis.video_editor.stickers import StickerInstance
from jarvis.video_editor.text_overlay import TextOverlay
from jarvis.video_editor.timeline import Timeline, TimelineClip, TimelineStill, TransitionSpec


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "VIDEO_EDITOR_DB_FILE", tmp_path / "video_editor.db")
    monkeypatch.setattr(storage, "VIDEO_EDITOR_PROJECTS_DIR", tmp_path / "projects")


def _media(tmp_path) -> dict[str, MediaItem]:
    return {
        "v": MediaItem(media_item_id="v", original_filename="a.mp4", stored_path=tmp_path / "a.mp4", kind="video",
                       duration_seconds=10, width=1080, height=1920, fps=30),
        "p": MediaItem(media_item_id="p", original_filename="b.jpg", stored_path=tmp_path / "b.jpg", kind="photo",
                       duration_seconds=None, width=800, height=600, fps=None),
    }


def test_clip_and_photo_effects_survive_a_reopen(tmp_path):
    db.create_project_record("p1", "Test")
    timeline = Timeline(items=(
        TimelineClip(clip_id="c1", media_item_id="v", source_in_seconds=1, source_out_seconds=4, speed_factor=1.5,
                     transition_out=TransitionSpec(kind="fade", duration_seconds=0.5),
                     effect=EffectSpec(brightness=0.2, contrast=1.3, saturation=0.7)),
        TimelineStill(clip_id="s1", media_item_id="p", display_duration_seconds=3,
                      effect=EffectSpec(motion="zoom_in", motion_intensity=1.3, fade="fade_both", fade_seconds=0.4)),
    ), aspect_ratio="4:5")
    storage.save_project("p1", timeline, _media(tmp_path))

    loaded, media = storage.load_project("p1")
    assert loaded == timeline
    assert set(media) == {"v", "p"}


def test_overlays_round_trip(tmp_path):
    db.create_project_record("p1", "Test")
    music = tmp_path / "song.mp3"
    music.write_bytes(b"x")
    gif = tmp_path / "party.gif"
    gif.write_bytes(b"x")
    overlays = storage.ProjectOverlays(
        text_overlays=(
            TextOverlay(text="Labas ąčęėįšųūž", start_seconds=0.5, end_seconds=3, x_fraction=0.2, y_fraction=0.7,
                        font_size=72, color="#FFD700", rotation_degrees=-12.5),
            TextOverlay(text="Antras", start_seconds=1, end_seconds=2, animation="bounce", speed=1.5),
        ),
        stickers=(
            StickerInstance(start_seconds=0, end_seconds=2, shape="heart", tint=(10, 20, 30), rotation_degrees=30),
            StickerInstance(start_seconds=1, end_seconds=4, custom_path=gif, opacity=0.5, animation="spin"),
        ),
        caption_style=CaptionStyle(font_size=70, position="top", shadow_offset=3, background=False),
        caption_lines=(CaptionLine(text="Pirma eilutė", start_seconds=0, end_seconds=1.5),),
        music_track=MusicTrack(source_path=music, trim_start_seconds=2.0, volume=0.6, fade_in_seconds=0.5),
    )
    storage.save_overlays("p1", overlays)
    assert storage.load_overlays("p1") == overlays


def test_project_without_saved_overlays_loads_empty():
    db.create_project_record("p1", "Test")
    assert storage.load_overlays("p1") == storage.ProjectOverlays()
    assert storage.load_overlays("missing") == storage.ProjectOverlays()


def test_unknown_and_missing_fields_in_saved_overlays_are_tolerated():
    db.create_project_record("p1", "Test")
    db.save_overlays("p1", {
        "text_overlays": [{"text": "Old", "start_seconds": 0, "end_seconds": 1, "some_future_field": 1}],
        "stickers": [{"start_seconds": 0, "end_seconds": 1, "shape": "star"}],
    })
    loaded = storage.load_overlays("p1")
    assert loaded.text_overlays == (TextOverlay(text="Old", start_seconds=0, end_seconds=1),)
    assert loaded.stickers == (StickerInstance(start_seconds=0, end_seconds=1, shape="star"),)
    assert loaded.caption_style is None and loaded.caption_lines is None and loaded.music_track is None


def test_database_created_before_overlays_column_is_migrated(tmp_path):
    db_file = Path(db.VIDEO_EDITOR_DB_FILE)
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_file)
    conn.execute(
        "CREATE TABLE projects (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, "
        "name TEXT NOT NULL, timeline_data TEXT, media_items_data TEXT, export_format_last_used TEXT, "
        "status TEXT NOT NULL DEFAULT 'created')"
    )
    conn.execute(
        "INSERT INTO projects (id, created_at, updated_at, name, timeline_data) VALUES ('old', 'x', 'x', 'Old', ?)",
        (json.dumps({"items": [], "aspect_ratio": "1:1"}),),
    )
    conn.commit()
    conn.close()

    record = db.get_project("old")
    assert record is not None and record.overlays_data is None
    storage.save_overlays("old", storage.ProjectOverlays(
        text_overlays=(TextOverlay(text="New", start_seconds=0, end_seconds=1),),
    ))
    assert storage.load_overlays("old").text_overlays[0].text == "New"
    assert db.get_project("old").timeline_data == {"items": [], "aspect_ratio": "1:1"}
