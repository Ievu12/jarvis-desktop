"""Instagram Reels mode, stage 2: pop-up text cards and picture/video
inserts - the model, their animations, drawing, timeline lanes, saving,
and an export compared against the preview's own renderer."""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from jarvis.video_editor import multisource_export as mse
from jarvis.video_editor import reels, reels_elements, reels_export, reels_media
from jarvis.video_editor import reels_render as rr
from jarvis.video_editor import track_layout as tl
from jarvis.video_editor.editor_state import EditorState
from jarvis.video_editor.media_import import MediaItem
from jarvis.video_editor.timeline import Timeline, TimelineClip

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _card(**changes) -> reels.TextCard:
    return dataclasses.replace(reels.TextCard(text="Storytelling", start_seconds=1.0, end_seconds=3.0), **changes)


def _photo(tmp_path: Path, color=(220, 40, 90), size=(400, 300)) -> Path:
    path = tmp_path / "photo.png"
    Image.new("RGB", size, color).save(path)
    return path


def _insert(path: Path, **changes) -> reels.MediaInsert:
    insert = reels.MediaInsert(path=str(path), kind=reels.insert_kind_for(str(path)), start_seconds=0.5,
                               end_seconds=2.5, aspect_ratio=4 / 3)
    return dataclasses.replace(insert, **changes)


def _video(tmp_path: Path, name: str, color: str, seconds: int = 3, size: str = "320x240") -> Path:
    path = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=30:d={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


# --- model ------------------------------------------------------------------------------------------------


def test_card_validation_and_uppercase():
    assert _card().validate() == []
    assert _card().shown_text == "STORYTELLING"
    assert _card(uppercase=False).shown_text == "Storytelling"
    assert _card(text="  ").validate()
    assert _card(end_seconds=1.0).validate()
    assert _card(design="nope").validate()
    assert _card(enter_animation="nope").validate()


def test_card_design_sets_its_colors_but_keeps_text_place_and_timing():
    card = _card(x_fraction=0.3, y_fraction=0.6)
    neon = reels.apply_card_design(card, "neon")
    assert neon.design == "neon"
    assert (neon.text, neon.x_fraction, neon.y_fraction, neon.start_seconds) == ("Storytelling", 0.3, 0.6, 1.0)
    assert (neon.text_color, neon.background_color) != (card.text_color, card.background_color)


def test_insert_kind_and_validation(tmp_path):
    assert reels.insert_kind_for("a/b.JPG") == "image"
    assert reels.insert_kind_for("clip.mov") == "video"
    assert reels.insert_kind_for("notes.txt") is None
    insert = _insert(_photo(tmp_path))
    assert insert.validate() == [] and insert.name == "photo.png"
    assert dataclasses.replace(insert, width_fraction=0).validate()
    assert dataclasses.replace(insert, enter_animation="typewriter").validate()  # text-only animation


def test_cards_and_inserts_round_trip_through_a_dict(tmp_path):
    layers = reels.ReelsLayers(cards=(_card(), _card(text="Jarvis", design="tag")),
                               inserts=(_insert(_photo(tmp_path)),))
    assert not layers.is_empty
    assert reels.from_dict(reels.to_dict(layers)) == layers
    assert reels.ReelsLayers().is_empty


# --- animations -------------------------------------------------------------------------------------------


def test_enter_and_exit_animations_run_at_the_right_time():
    card = _card(enter_animation="slide_left", exit_animation="fade", enter_seconds=0.4, exit_seconds=0.4)
    assert reels_elements.motion(card, 0.99, 1080, 1920) is None
    entering = reels_elements.motion(card, 1.1, 1080, 1920)
    assert entering.dx < 0  # still coming in from the left
    settled = reels_elements.motion(card, 2.0, 1080, 1920)
    assert (settled.dx, settled.dy, settled.scale, settled.alpha) == (0, 0, 1, 1)
    leaving = reels_elements.motion(card, 2.8, 1080, 1920)
    assert 0 < leaving.alpha < 1
    assert reels_elements.motion(card, 3.0, 1080, 1920) is None


def test_typewriter_reveals_the_text_letter_by_letter():
    card = _card(enter_animation="typewriter", enter_seconds=1.0)
    early = reels_elements.plan_card(card, 1.3, 1080, 1920, 1.0)
    assert 0 < len(early.text) < len("STORYTELLING")
    assert reels_elements.plan_card(card, 2.2, 1080, 1920, 1.0).text == "STORYTELLING"


def test_opacity_scales_the_animation_alpha():
    assert reels_elements.motion(_card(opacity=0.5), 2.0, 1080, 1920).alpha == pytest.approx(0.5)


# --- drawing ----------------------------------------------------------------------------------------------


def test_every_card_design_and_animation_draws():
    for design in reels.CARD_DESIGN_CHOICES:
        for animation in reels.ENTER_ANIMATION_CHOICES:
            card = reels.apply_card_design(_card(enter_animation=animation), design)
            for t in (1.05, 2.0, 2.95):
                layer = rr.render_frame(reels.ReelsLayers(cards=(card,)), t=t, width=540, height=960, scale=0.5)
                assert layer.getbbox() is not None, (design, animation, t)


def test_card_is_drawn_where_it_is_placed():
    layers = reels.ReelsLayers(cards=(_card(x_fraction=0.25, y_fraction=0.75, enter_animation="none"),))
    box = rr.render_frame(layers, t=2.0, width=1080, height=1920, scale=1.0).getbbox()
    center = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    assert center[0] == pytest.approx(270, abs=12) and center[1] == pytest.approx(1440, abs=12)
    assert rr.render_frame(layers, t=0.5, width=1080, height=1920, scale=1.0).getbbox() is None


def test_photo_insert_has_rounded_corners_and_the_photo_inside(tmp_path):
    insert = _insert(_photo(tmp_path), x_fraction=0.5, y_fraction=0.5, width_fraction=0.5, corner_radius=0.2,
                     shadow=0, enter_animation="none")
    layer = rr.render_frame(reels.ReelsLayers(inserts=(insert,)), t=1.0, width=1080, height=1920, scale=1.0)
    left, top, right, bottom = layer.getbbox()
    assert right - left == pytest.approx(540, abs=3)
    assert bottom - top == pytest.approx(405, abs=3)  # 4:3 photo
    assert layer.getpixel((left + 1, top + 1))[3] == 0  # rounded corner is see-through
    assert layer.getpixel((540, 960))[:3] == (220, 40, 90)


@needs_ffmpeg
def test_video_insert_plays_the_video_from_its_start_offset(tmp_path):
    clip = _video(tmp_path, "red.mp4", "red")
    insert = reels.MediaInsert(path=str(clip), kind="video", start_seconds=1.0, end_seconds=2.5,
                               aspect_ratio=4 / 3, x_fraction=0.5, y_fraction=0.5, shadow=0,
                               enter_animation="none", exit_animation="none", source_start_seconds=1.0)
    op = rr.plan(reels.ReelsLayers(inserts=(insert,)), t=1.5, frame_width=540, frame_height=960, scale=0.5)[0]
    assert op.source_time == pytest.approx(1.5)
    frames = reels_media.VideoFrames()
    try:
        layer = rr.render_frame(reels.ReelsLayers(inserts=(insert,)), t=1.5, width=540, height=960, scale=0.5,
                                frames=frames)
        red, green, blue, alpha = layer.getpixel((270, 480))
        assert alpha == 255 and red > 200 and green < 60 and blue < 60
    finally:
        frames.close()


def test_element_places_follow_position_size_and_rotation(tmp_path):
    layers = reels.ReelsLayers(
        cards=(_card(x_fraction=0.5, y_fraction=0.1, rotation_degrees=10),),
        inserts=(_insert(_photo(tmp_path), x_fraction=0.7, y_fraction=0.3, width_fraction=0.4),),
    )
    places = rr.element_places(layers, t=2.0, frame_width=540, frame_height=960, scale=0.5)
    assert [(p.kind, p.index) for p in places] == [("reels_insert", 0), ("reels_card", 0)]
    insert_place, card_place = places
    assert (insert_place.center_x, insert_place.center_y) == pytest.approx((378, 288))
    assert insert_place.width == pytest.approx(216, abs=1)
    assert card_place.rotation_degrees == 10
    assert rr.element_places(layers, t=2.9, frame_width=540, frame_height=960, scale=0.5)[0].kind == "reels_card"  # insert ended at 2.5


def test_preview_offers_cards_and_inserts_for_dragging(tmp_path):
    from jarvis.video_editor import preview_compositor as pc

    scene = pc.Scene(reels=reels.ReelsLayers(cards=(_card(),), inserts=(_insert(_photo(tmp_path)),)))
    boxes = pc.element_boxes(scene, t=2.0, frame_width=360, frame_height=640, canvas_width=1080)
    kinds = [(b.kind, b.index) for b in boxes]
    assert ("reels_card", 0) in kinds and ("reels_insert", 0) in kinds
    card_box = next(b for b in boxes if b.kind == "reels_card")
    assert pc.hit_test(boxes, card_box.center_x, card_box.center_y).kind == "reels_card"


# --- timeline lanes and saving ----------------------------------------------------------------------------


def _state(tmp_path) -> EditorState:
    return EditorState(reels=reels.ReelsLayers(
        cards=(_card(), _card(text="Reels", start_seconds=4.0, end_seconds=5.0)),
        inserts=(_insert(_photo(tmp_path)),),
    ))


def test_cards_and_inserts_have_their_own_lanes(tmp_path):
    bars = tl.build_track_bars(_state(tmp_path), {})
    assert [(b.start, b.end) for b in bars["reels_cards"]] == [(1.0, 3.0), (4.0, 5.0)]
    assert [(b.start, b.end) for b in bars["reels_inserts"]] == [(0.5, 2.5)]
    assert tl.TRACK_LABELS["reels_cards"] and tl.TRACK_LABELS["reels_inserts"]


def test_cards_and_inserts_move_trim_delete_and_duplicate_on_the_timeline(tmp_path):
    state = _state(tmp_path)
    moved = tl.move_overlay(state, "reels_cards", 0, 2.0, total=10).reels.cards[0]
    assert (moved.start_seconds, moved.end_seconds) == (2.0, 4.0)
    trimmed = tl.trim_overlay(state, "reels_inserts", 0, end=1.5, total=10).reels.inserts[0]
    assert (trimmed.start_seconds, trimmed.end_seconds) == (0.5, 1.5)
    assert [c.text for c in tl.delete_element(state, "reels_cards", 0).reels.cards] == ["Reels"]
    result = tl.duplicate_element(state, "reels_inserts", 0, new_clip_id="x", total=10)
    assert result is not None and len(result[0].reels.inserts) == 2
    # The other Reels layers are never touched.
    assert tl.delete_element(state, "reels_cards", 0).reels.inserts == state.reels.inserts


def test_cards_and_inserts_survive_a_reopen(tmp_path, monkeypatch):
    from jarvis.video_editor import db, storage

    monkeypatch.setattr(db, "VIDEO_EDITOR_DB_FILE", tmp_path / "e.db")
    monkeypatch.setattr(storage, "VIDEO_EDITOR_PROJECTS_DIR", tmp_path / "p")
    project = storage.create_project()
    db.create_project_record(project.project_id, "Reels")
    layers = _state(tmp_path).reels
    storage.save_overlays(project.project_id, storage.ProjectOverlays(reels=layers))
    assert storage.load_overlays(project.project_id).reels == layers


# --- export -----------------------------------------------------------------------------------------------


def _frame_at(video: Path, t: float, out: Path) -> Image.Image:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t}", "-i", str(video), "-frames:v", "1",
                    str(out)], check=True)
    with Image.open(out) as image:
        return image.convert("RGB")


@needs_ffmpeg
def test_exported_mp4_with_a_card_and_a_video_insert_matches_the_preview_drawing(tmp_path):
    seconds = 4
    clip_path = tmp_path / "talk.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x406080:s=540x960:r=30:d={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(clip_path)],
        check=True,
    )
    media = {"v": MediaItem(media_item_id="v", original_filename="talk.mp4", stored_path=clip_path, kind="video",
                            duration_seconds=seconds, width=540, height=960, fps=30)}
    timeline = Timeline(items=(TimelineClip(clip_id="c", media_item_id="v", source_in_seconds=0,
                                            source_out_seconds=seconds),), aspect_ratio="9:16")
    insert_clip = _video(tmp_path, "yellow.mp4", "yellow", seconds=3)
    layers = reels.ReelsLayers(
        cards=(_card(design="neon", start_seconds=0.5, end_seconds=3.5),),
        inserts=(reels.MediaInsert(path=str(insert_clip), kind="video", start_seconds=1.0, end_seconds=3.0,
                                   aspect_ratio=4 / 3, corner_radius=0.15),),
    )
    fmt = mse.resolve_export_format("9:16", "720p")
    exports = tmp_path / "exports"
    exports.mkdir()
    overlay_args, clause = reels_export.build_overlay_filter("_reels.mov", video_label="outv", input_index=1)
    result = reels_export.export_timeline_with_reels(
        reels_layers=layers, overlay_path=exports / "_reels.mov", progress_callback=None,
        timeline=timeline, media_items=media, export_format=fmt, output_path=exports / "out.mp4",
        sticker_filters=[(overlay_args, clause)],
    )
    plain = tmp_path / "plain.mp4"
    mse.export_timeline(timeline, media, export_format=fmt, output_path=plain)

    t = 2.0  # card settled, video insert playing
    exported = _frame_at(result.output_path, t, tmp_path / "e.png")
    expected = _frame_at(plain, t, tmp_path / "b.png").convert("RGBA")
    frames = reels_media.VideoFrames()
    try:
        expected.alpha_composite(rr.render_frame(layers, t=t, width=fmt.width, height=fmt.height,
                                                 scale=reels_export.overlay_scale(fmt.width, fmt.height),
                                                 frames=frames))
    finally:
        frames.close()
    expected = expected.convert("RGB")
    diff = ImageChops.difference(exported, expected).convert("L")
    mean = sum(i * n for i, n in enumerate(diff.histogram())) / (diff.width * diff.height)
    assert mean < 1.5
    assert diff.point(lambda v: 255 if v > 60 else 0).getbbox() is None
    # The yellow video really is in the export, top right by default.
    r, g, b = exported.getpixel((round(0.73 * fmt.width), round(0.36 * fmt.height)))
    assert r > 180 and g > 180 and b < 90


def test_insert_name_hides_the_id_added_when_copied_into_the_project():
    insert = reels.MediaInsert(path="/p/media/893339e041dd4f6094fe1fb07063e0b1_produktas.png", kind="image",
                               start_seconds=0, end_seconds=1)
    assert insert.name == "produktas.png"
