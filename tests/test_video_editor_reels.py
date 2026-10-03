"""Instagram Reels mode, stage 1: the Reels subtitle model, its one
renderer, and the export overlay - checked against real ffmpeg output."""

from __future__ import annotations

import dataclasses
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageChops

from jarvis.video_editor import multisource_export as mse
from jarvis.video_editor import reels
from jarvis.video_editor import reels_export
from jarvis.video_editor import reels_render as rr
from jarvis.video_editor.captions import WordTiming
from jarvis.video_editor.media_import import MediaItem
from jarvis.video_editor.timeline import Timeline, TimelineClip

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _words(*spec) -> tuple[reels.ReelsWord, ...]:
    return tuple(reels.ReelsWord(text, start, end) for text, start, end in spec)


WORDS = _words(
    ("Labas,", 0.0, 0.4), ("čia", 0.4, 0.6), ("Jarvis.", 0.6, 1.2),
    ("Šiandien", 2.0, 2.5), ("kalbėsime", 2.5, 3.0), ("apie", 3.0, 3.2), ("automatizaciją", 3.2, 4.0),
)


# --- phrases ----------------------------------------------------------------------------------------


def test_phrases_split_at_sentence_end_pause_and_word_limit():
    style = reels.ReelsCaptionStyle(max_words=3)
    phrases = reels.build_phrases(WORDS, style)
    assert [(p.first, p.last) for p in phrases] == [(0, 2), (3, 5), (6, 6)]
    # The first phrase holds a moment after its last word (the next one starts much later).
    assert phrases[0].end_seconds == pytest.approx(1.2 + style.hold_seconds)
    # Back-to-back phrases hand over exactly when the next one starts.
    assert phrases[1].end_seconds == pytest.approx(3.2)


def test_line_break_after_forces_a_new_phrase():
    words = WORDS[:1] + (dataclasses.replace(WORDS[1], line_break_after=True),) + WORDS[2:]
    phrases = reels.build_phrases(words, reels.ReelsCaptionStyle(max_words=6))
    assert (phrases[0].first, phrases[0].last) == (0, 1)


def test_phrase_at_finds_the_phrase_on_screen():
    phrases = reels.build_phrases(WORDS, reels.ReelsCaptionStyle(max_words=3))
    assert reels.phrase_at(phrases, 0.5).first == 0
    assert reels.phrase_at(phrases, 1.9) is None  # silence after the hold
    assert reels.phrase_at(phrases, 3.5).first == 6


def test_words_from_timings_applies_the_clip_offset_and_skips_blanks():
    words = reels.words_from_timings([WordTiming(1.0, 1.4, " labas"), WordTiming(1.4, 1.4, "x"), WordTiming(2, 3, " ")],
                                     offset_seconds=2.0)
    assert [(w.text, w.start_seconds) for w in words] == [("labas", 3.0), ("x", 3.4)]
    assert words[1].end_seconds > words[1].start_seconds


def test_retext_phrase_keeps_word_timing_when_the_word_count_matches():
    phrases = reels.build_phrases(WORDS, reels.ReelsCaptionStyle(max_words=3))
    fixed = reels.retext_phrase(WORDS, phrases[0], "Labas, čia JARVIS.")
    assert [w.text for w in fixed[:3]] == ["Labas,", "čia", "JARVIS."]
    assert [w.start_seconds for w in fixed] == [w.start_seconds for w in WORDS]


def test_retext_phrase_spreads_a_different_word_count_over_the_phrase():
    phrases = reels.build_phrases(WORDS, reels.ReelsCaptionStyle(max_words=3))
    changed = reels.retext_phrase(WORDS, phrases[0], "Sveiki")
    assert changed[0].text == "Sveiki"
    assert (changed[0].start_seconds, changed[0].end_seconds) == (0.0, 1.2)
    assert len(changed) == len(WORDS) - 2


def test_shift_and_retime_phrase():
    phrases = reels.build_phrases(WORDS, reels.ReelsCaptionStyle(max_words=3))
    shifted = reels.shift_phrase(WORDS, phrases[1], 0.5)
    assert shifted[3].start_seconds == 2.5 and shifted[5].end_seconds == 3.7
    assert shifted[0] == WORDS[0]
    assert reels.shift_phrase(WORDS, phrases[0], -5)[0].start_seconds == 0.0  # never before 0
    stretched = reels.retime_phrase(WORDS, phrases[1], 2.0, 4.4)  # twice as long
    assert stretched[4].start_seconds == pytest.approx(3.0) and stretched[5].end_seconds == pytest.approx(4.4)


def test_emphasize_matching_finds_lithuanian_word_forms():
    marked = reels.emphasize_matching(WORDS, ["JARVIS", "automatizacij"])
    assert [w.text for w in marked if w.emphasized] == ["Jarvis.", "automatizaciją"]


def test_presets_change_the_look_but_keep_position():
    style = reels.ReelsCaptionStyle(y_fraction=0.3, font_size=60)
    for name in reels.REELS_CAPTION_PRESETS:
        applied = reels.apply_caption_preset(style, name)
        assert applied.y_fraction == 0.3
        assert applied.validate() == []
    assert reels.apply_caption_preset(style, "Reels klasika").font_size == reels.ReelsCaptionStyle().font_size


def test_layers_round_trip_through_a_dict_and_ignore_unknown_fields():
    layers = reels.ReelsLayers(captions=reels.ReelsCaptions(
        words=reels.emphasize_matching(WORDS, ["jarvis"]), style=reels.ReelsCaptionStyle(active_effect="box"),
    ))
    data = reels.to_dict(layers)
    data["captions"]["style"]["from_the_future"] = 1
    assert reels.from_dict(data) == layers
    assert reels.from_dict(None) is None


# --- renderer ----------------------------------------------------------------------------------------


def _layers(**style_changes) -> reels.ReelsLayers:
    return reels.ReelsLayers(captions=reels.ReelsCaptions(
        words=WORDS, style=dataclasses.replace(reels.ReelsCaptionStyle(), **style_changes),
    ))


def test_nothing_is_drawn_between_phrases():
    layer = rr.render_frame(_layers(), t=1.8, width=270, height=480, scale=0.25)
    assert layer.getbbox() is None
    assert rr.plan(_layers(), t=1.8, frame_width=270, frame_height=480, scale=0.25) == ()


def test_subtitles_sit_where_the_style_says():
    for y_fraction in (0.2, 0.5, 0.8):
        layer = rr.render_frame(_layers(y_fraction=y_fraction, animation="none"), t=0.5, width=540, height=960,
                                scale=0.5)
        top, bottom = layer.getbbox()[1], layer.getbbox()[3]
        assert abs((top + bottom) / 2 - 960 * y_fraction) < 25


def test_active_word_and_key_words_get_their_colors():
    style = dict(animation="none", outline_width=0, shadow_offset=0, shadow_blur=0, active_color="#00FF00",
                 highlight_color="#FF0000", max_words=1)
    words = reels.emphasize_matching(WORDS, ["jarvis"])
    layers = reels.ReelsLayers(captions=reels.ReelsCaptions(
        words=words, style=dataclasses.replace(reels.ReelsCaptionStyle(), **style)))

    def colors_at(t):
        layer = rr.render_frame(layers, t=t, width=540, height=960, scale=0.5)
        return {c for _, c in layer.getcolors(100000) if c[3] == 255}

    assert (0, 255, 0, 255) in colors_at(0.2)  # "Labas," is being spoken
    assert (255, 0, 0, 255) in colors_at(1.1) or (0, 255, 0, 255) in colors_at(1.1)
    assert (255, 0, 0, 255) in colors_at(1.3)  # "Jarvis." after it was spoken: the key-word color


def test_word_reveal_shows_only_words_already_spoken():
    layers = _layers(reveal="word", animation="none")
    early = rr.plan(layers, t=2.1, frame_width=540, frame_height=960, scale=0.5)
    later = rr.plan(layers, t=3.1, frame_width=540, frame_height=960, scale=0.5)
    assert [op.text for op in early if isinstance(op, rr.WordOp)] == ["ŠIANDIEN"]
    assert [op.text for op in later if isinstance(op, rr.WordOp)] == ["ŠIANDIEN", "KALBĖSIME", "APIE"]


def test_pop_animation_grows_the_phrase_then_settles():
    layers = _layers(animation="pop")
    scales = [
        next(op for op in rr.plan(layers, t=2.0 + dt, frame_width=540, frame_height=960, scale=0.5)
             if isinstance(op, rr.WordOp)).scale
        for dt in (0.03, 0.15, 0.4)
    ]
    assert scales[0] < 0.9 and scales[1] > 1.0 and scales[2] == 1.0


def test_every_option_draws_without_errors():
    for effect in reels.ACTIVE_EFFECT_CHOICES:
        for animation in reels.PHRASE_ANIMATION_CHOICES:
            for reveal in reels.WORD_REVEAL_CHOICES:
                for background in reels.PHRASE_BACKGROUND_CHOICES:
                    layers = _layers(active_effect=effect, animation=animation, reveal=reveal, background=background,
                                     weight=2)
                    for t in (0.05, 0.5, 2.6):
                        rr.render_frame(layers, t=t, width=108, height=192, scale=0.1)


def test_lithuanian_letters_and_long_words_fit_the_frame():
    long_words = _words(("Ąžuolų", 0, 1), ("šviesūs", 1, 2), ("nebeprisikiškiakopūstaudavote", 2, 3))
    layers = reels.ReelsLayers(captions=reels.ReelsCaptions(
        words=long_words, style=reels.ReelsCaptionStyle(max_words=3, animation="none")))
    box = rr.caption_box(layers, t=2.5, frame_width=540, frame_height=960, scale=0.5)
    assert box is not None and box.height > 80  # wrapped onto more than one line
    layer = rr.render_frame(layers, t=0.5, width=540, height=960, scale=0.5)
    assert layer.getbbox() is not None


def test_equal_plans_mean_equal_pixels():
    layers = _layers(animation="none", active_effect="none")
    a = rr.plan(layers, t=2.6, frame_width=540, frame_height=960, scale=0.5)
    b = rr.plan(layers, t=2.9, frame_width=540, frame_height=960, scale=0.5)
    assert a == b


# --- export -----------------------------------------------------------------------------------------


def test_overlay_filter_clause_chains_like_a_sticker():
    args, clause = reels_export.build_overlay_filter("o.mov", video_label="stickv1", input_index=4)
    assert args == ["-i", "o.mov"]
    assert clause.startswith("[stickv1][4:v]overlay=0:0") and clause.endswith("[reelsv]")
    assert mse.extract_output_label(clause) == "reelsv"


@needs_ffmpeg
def test_overlay_video_has_alpha_and_the_right_length(tmp_path):
    path = reels_export.render_overlay_video(
        _layers(), duration_seconds=1.0, width=108, height=192, output_path=tmp_path / "o.mov",
    )
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
         "stream=nb_read_frames,pix_fmt,width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert probe.split(",") == ["108", "192", "argb", "30"]


@needs_ffmpeg
def test_overlay_render_can_be_cancelled(tmp_path):
    import threading

    cancel = threading.Event()
    cancel.set()
    with pytest.raises(reels_export.ReelsExportError):
        reels_export.render_overlay_video(_layers(), duration_seconds=2, width=108, height=192,
                                          output_path=tmp_path / "o.mov", cancel_event=cancel)
    assert not (tmp_path / "o.mov").exists()


def _gray_clip(tmp_path: Path, seconds: int = 5) -> tuple[Timeline, dict]:
    clip_path = tmp_path / "talk.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x406080:s=540x960:r=30:d={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-shortest", str(clip_path)],
        check=True,
    )
    media = {"v": MediaItem(media_item_id="v", original_filename="talk.mp4", stored_path=clip_path, kind="video",
                            duration_seconds=seconds, width=540, height=960, fps=30)}
    clip = TimelineClip(clip_id="c", media_item_id="v", source_in_seconds=0, source_out_seconds=seconds)
    return Timeline(items=(clip,), aspect_ratio="9:16"), media


def _frame_at(video: Path, t: float, out: Path) -> Image.Image:
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-ss", f"{t}", "-i", str(video), "-frames:v", "1",
                    str(out)], check=True)
    with Image.open(out) as image:
        return image.convert("RGB")


@needs_ffmpeg
def test_exported_mp4_matches_the_preview_drawing(tmp_path):
    """The MP4 frame equals the base video with the Reels layer drawn by
    the preview's own renderer on top (apart from video compression)."""
    timeline, media = _gray_clip(tmp_path)
    layers = reels.ReelsLayers(captions=reels.ReelsCaptions(
        words=reels.emphasize_matching(WORDS, ["automatizacij"]),
        style=reels.ReelsCaptionStyle(active_effect="box", animation="none"),
    ))
    fmt = mse.resolve_export_format("9:16", "720p")
    exports = tmp_path / "exports"
    exports.mkdir()
    overlay_args, clause = reels_export.build_overlay_filter("_reels.mov", video_label="outv", input_index=1)
    progress: list[float] = []
    result = reels_export.export_timeline_with_reels(
        reels_layers=layers, overlay_path=exports / "_reels.mov", progress_callback=progress.append,
        timeline=timeline, media_items=media, export_format=fmt, output_path=exports / "out.mp4",
        sticker_filters=[(overlay_args, clause)],
    )
    assert result.width == fmt.width and result.height == fmt.height
    assert not (exports / "_reels.mov").exists()  # the temporary overlay is cleaned up
    assert progress == sorted(progress) and progress[-1] > 99

    plain = tmp_path / "plain.mp4"
    mse.export_timeline(timeline, media, export_format=fmt, output_path=plain)
    t = 3.5  # "automatizaciją" is being spoken: key word + purple box
    exported = _frame_at(result.output_path, t, tmp_path / "e.png")
    expected = _frame_at(plain, t, tmp_path / "b.png").convert("RGBA")
    expected.alpha_composite(rr.render_frame(layers, t=t, width=fmt.width, height=fmt.height,
                                             scale=reels_export.overlay_scale(fmt.width, fmt.height)))
    expected = expected.convert("RGB")

    diff = ImageChops.difference(exported, expected).convert("L")
    assert ImageChops.difference(exported, _frame_at(plain, t, tmp_path / "p2.png")).getbbox() is not None
    mean = sum(i * n for i, n in enumerate(diff.histogram())) / (diff.width * diff.height)
    assert mean < 1.5
    assert diff.point(lambda v: 255 if v > 60 else 0).getbbox() is None


@needs_ffmpeg
def test_compose_onto_frame_draws_the_layers_onto_an_exact_frame(tmp_path):
    frame_path = tmp_path / "f.png"
    Image.new("RGB", (720, 1280), (64, 96, 128)).save(frame_path)
    reels_export.compose_onto_frame(frame_path, _layers(animation="none"), t=0.5)
    with Image.open(frame_path) as image:
        assert ImageChops.difference(image.convert("RGB"), Image.new("RGB", (720, 1280), (64, 96, 128))).getbbox()


# --- key word suggestions ------------------------------------------------------------------------------


def test_suggest_keywords_skips_filler_and_prefers_long_words_numbers_and_names():
    from jarvis.video_editor.reels_keywords import suggest_keywords

    words = reels.words_from_text(
        "Šiandien aš parodysiu kaip Jarvis per 5 minutes sukuria įrašą ir automatizacija tai labai paprasta. "
        "Automatizacija sutaupo laiko!",
        start_seconds=0, end_seconds=10,
    )
    suggested = suggest_keywords(words, max_count=5)
    lowered = [s.lower() for s in suggested]
    assert "jarvis" in lowered and "5" in lowered and "automatizacija" in lowered
    assert not {"aš", "kaip", "ir", "tai", "labai"} & set(lowered)
    assert len(suggested) <= 5
    # In the order they are said.
    positions = [next(i for i, w in enumerate(words) if reels.normalize_word(w.text) == reels.normalize_word(s))
                 for s in suggested]
    assert positions == sorted(positions)


def test_own_keywords_always_win():
    from jarvis.video_editor.reels_keywords import suggest_keywords

    words = reels.words_from_text("mes kuriame reels kiekvieną dieną", start_seconds=0, end_seconds=3)
    assert "reels" in [s.lower() for s in suggest_keywords(words, max_count=1, own_words=["Reels"])]


# --- timeline lane and saving ----------------------------------------------------------------------------


def _state_with_words():
    from jarvis.video_editor.editor_state import EditorState

    return EditorState(reels=reels.ReelsLayers(captions=reels.ReelsCaptions(
        words=WORDS, style=reels.ReelsCaptionStyle(max_words=3))))


def test_each_phrase_is_a_bar_on_the_reels_lane():
    from jarvis.video_editor import track_layout as tl

    bars = tl.build_track_bars(_state_with_words(), {})["reels_captions"]
    assert [(b.start, b.end) for b in bars] == [(0.0, 1.2), (2.0, 3.2), (3.2, 4.0)]
    assert bars[0].label == "Labas, čia Jarvis."
    assert "reels_captions" in tl.TRACKS and tl.TRACK_LABELS["reels_captions"]


def test_moving_trimming_and_deleting_a_phrase_bar_edits_its_words():
    from jarvis.video_editor import track_layout as tl

    state = _state_with_words()
    moved = tl.move_overlay(state, "reels_captions", 1, 2.5, total=10).reels.captions.words
    assert (moved[3].start_seconds, moved[5].end_seconds) == (2.5, 3.7)
    trimmed = tl.trim_overlay(state, "reels_captions", 0, end=2.4, total=10).reels.captions.words
    assert trimmed[2].end_seconds == pytest.approx(2.4) and trimmed[0].start_seconds == 0
    deleted = tl.delete_element(state, "reels_captions", 0).reels.captions.words
    assert [w.text for w in deleted] == [w.text for w in WORDS[3:]]
    assert tl.move_overlay(state, "reels_captions", 9, 1.0, total=10) == state  # unknown phrase: no change


def test_reels_layers_survive_a_reopen(tmp_path, monkeypatch):
    from jarvis.video_editor import db, storage

    monkeypatch.setattr(db, "VIDEO_EDITOR_DB_FILE", tmp_path / "e.db")
    monkeypatch.setattr(storage, "VIDEO_EDITOR_PROJECTS_DIR", tmp_path / "p")
    project = storage.create_project()
    db.create_project_record(project.project_id, "Reels")
    layers = _state_with_words().reels
    storage.save_overlays(project.project_id, storage.ProjectOverlays(reels=layers))
    assert storage.load_overlays(project.project_id).reels == layers
    storage.save_overlays(project.project_id, storage.ProjectOverlays())
    assert storage.load_overlays(project.project_id).reels is None


def test_preview_scene_draws_the_reels_layer_and_offers_it_for_dragging():
    from jarvis.video_editor import preview_compositor as pc

    scene = pc.Scene(reels=_layers(animation="none"))
    base = Image.new("RGB", (360, 640), (40, 40, 40))
    shown = pc.compose(base, scene, t=0.5, canvas_width=1080, canvas_height=1920)
    assert ImageChops.difference(shown, base).getbbox() is not None
    boxes = pc.element_boxes(scene, t=0.5, frame_width=360, frame_height=640, canvas_width=1080)
    assert [(b.kind, b.index) for b in boxes] == [("reels_caption", 0)]
    assert pc.hit_test(boxes, boxes[0].center_x, boxes[0].center_y).kind == "reels_caption"
    assert pc.element_boxes(scene, t=1.9, frame_width=360, frame_height=640, canvas_width=1080) == []
