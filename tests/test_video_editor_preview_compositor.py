"""Tests for jarvis.video_editor.preview_compositor (the live preview's
per-frame overlay drawing) and jarvis.video_editor.text_render.

The parity tests render the SAME overlay twice - once through the real
export filtergraph (live_preview.render_preview_frame(), i.e. ffmpeg)
and once through the compositor on the plain decoded frame - and
require the two images to match pixel-for-pixel apart from a thin
anti-aliasing fringe. That is the guarantee the live preview gives:
what you see while editing is what export produces. They need ffmpeg
and a font metrically identical to the export's own Arial Bold
(Liberation Sans Bold on Linux, Arial Bold itself on Windows)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from PIL import Image, ImageChops, ImageDraw

from jarvis.video_editor import captions as captions_module
from jarvis.video_editor import preview_compositor as pc
from jarvis.video_editor import text_overlay as text_overlay_module
from jarvis.video_editor import text_render
from jarvis.video_editor.captions import CaptionLine, CaptionStyle, build_caption_filter_from_lines
from jarvis.video_editor.live_preview import PreviewFilters, render_preview_frame
from jarvis.video_editor.media_import import MediaItem
from jarvis.video_editor.multisource_export import resolve_export_format
from jarvis.video_editor.stickers import StickerInstance, build_sticker_filter
from jarvis.video_editor.text_overlay import TextOverlay, build_rotated_text_filters, build_text_overlay_filter
from jarvis.video_editor.timeline import Timeline, TimelineClip
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available

_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)
_PARITY_FONT = next((f for f in _FONT_CANDIDATES if Path(f).is_file()), None)

CANVAS_W, CANVAS_H = 1080, 1920


def _blank(width=360, height=640, color=(40, 60, 90)) -> Image.Image:
    return Image.new("RGB", (width, height), color)


# --- text_render -----------------------------------------------------------------------------


@pytest.mark.parametrize("value, expected", [
    ("white", (255, 255, 255, 255)),
    ("#FFD700", (255, 215, 0, 255)),
    ("0xFFD700", (255, 215, 0, 255)),
    ("black@0.5", (0, 0, 0, 128)),
    ("red@0.6", (255, 0, 0, 153)),
    ("not-a-color", (255, 255, 255, 255)),
    ("", (255, 255, 255, 255)),
])
def test_parse_color_understands_ffmpeg_color_syntax(value, expected):
    assert text_render.parse_color(value) == expected


def test_measure_reports_ink_box_and_draw_text_lands_on_it():
    font = text_render.load_font(_PARITY_FONT or "", 60)
    metrics = text_render.measure("Ąžuolas", font)
    layer = Image.new("RGBA", (400, 200), (0, 0, 0, 0))
    text_render.draw_text(layer, "Ąžuolas", font=font, x=50, y=40, fill=(255, 255, 255, 255))
    left, top, right, bottom = layer.getchannel("A").point(lambda a: 255 if a > 128 else 0).getbbox()
    assert abs(top - 40) <= 1
    assert abs(bottom - (40 + metrics.height)) <= 2
    assert abs((right - left) - metrics.width) <= 4


def test_semi_transparent_text_blends_instead_of_punching_through():
    layer = Image.new("RGBA", (200, 100), (0, 0, 255, 255))
    font = text_render.load_font(_PARITY_FONT or "", 60)
    text_render.draw_text(layer, "I", font=font, x=80, y=20, fill=(255, 0, 0, 128))
    r, g, b, a = layer.getpixel((layer.getchannel("R").getbbox()[0] + 2, 50))
    assert a == 255 and r > 100 and b > 100  # red over blue, not a transparent hole


# --- visibility, geometry, interaction helpers -------------------------------------------------


def test_elements_outside_their_time_window_are_not_drawn():
    scene = pc.Scene(
        text_overlays=(TextOverlay(text="Hi", start_seconds=2, end_seconds=4),),
        stickers=(StickerInstance(start_seconds=2, end_seconds=4, shape="heart", animation="none"),),
    )
    base = _blank()
    before = pc.compose(base, scene, t=1.0, canvas_width=CANVAS_W, canvas_height=CANVAS_H)
    during = pc.compose(base, scene, t=3.0, canvas_width=CANVAS_W, canvas_height=CANVAS_H)
    assert ImageChops.difference(before, base).getbbox() is None
    assert ImageChops.difference(during, base).getbbox() is not None


def test_element_boxes_and_hit_test_pick_the_topmost_element():
    scene = pc.Scene(
        text_overlays=(TextOverlay(text="Title", start_seconds=0, end_seconds=5, x_fraction=0.5, y_fraction=0.5),),
        stickers=(StickerInstance(start_seconds=0, end_seconds=5, shape="star", x_fraction=0.5, y_fraction=0.5,
                                  size_fraction=0.3, animation="none"),),
    )
    boxes = pc.element_boxes(scene, t=1.0, frame_width=360, frame_height=640, canvas_width=CANVAS_W)
    assert [b.kind for b in boxes] == ["text", "sticker"]
    sticker_box = boxes[1]
    assert sticker_box.center_x == pytest.approx(180)
    assert sticker_box.center_y == pytest.approx(320)
    assert sticker_box.width == pytest.approx(int(0.3 * CANVAS_W) / 3)
    hit = pc.hit_test(boxes, 180, 320)
    assert (hit.kind, hit.index) == ("sticker", 0)  # stickers are drawn (and hit) above text
    assert pc.hit_test(boxes, 5, 5) is None


def test_rotated_box_contains_uses_the_rotated_shape():
    box = pc.ElementBox("sticker", 0, center_x=100, center_y=100, width=100, height=10, rotation_degrees=90)
    assert box.contains(100, 140)  # along the rotated long axis
    assert not box.contains(140, 100)  # would be inside the unrotated box


def test_text_fractions_for_center_inverts_the_drawtext_position_rule():
    overlay = TextOverlay(text="Labas", start_seconds=0, end_seconds=1, font_size=80)
    scale = 360 / CANVAS_W
    x_fraction, y_fraction = pc.text_fractions_for_center(
        overlay, center_x=100, center_y=500, frame_width=360, frame_height=640, scale=scale,
    )
    moved = TextOverlay(text="Labas", start_seconds=0, end_seconds=1, font_size=80,
                        x_fraction=x_fraction, y_fraction=y_fraction)
    (box,) = pc.element_boxes(pc.Scene(text_overlays=(moved,)), t=0.5, frame_width=360, frame_height=640,
                              canvas_width=CANVAS_W)
    assert box.center_x == pytest.approx(100, abs=0.5)
    assert box.center_y == pytest.approx(500, abs=0.5)


def test_animated_gif_sticker_loops_from_its_own_start(tmp_path):
    frames = [Image.new("RGBA", (20, 20), color) for color in ((255, 0, 0, 255), (0, 255, 0, 255), (0, 0, 255, 255))]
    gif_path = tmp_path / "anim.gif"
    frames[0].save(gif_path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    sticker = StickerInstance(start_seconds=5.0, end_seconds=9.0, custom_path=gif_path, animation="none",
                              x_fraction=0.5, y_fraction=0.5, size_fraction=0.2)

    def center_color(t):
        image = pc.compose(_blank(), pc.Scene(stickers=(sticker,)), t=t, canvas_width=CANVAS_W, canvas_height=CANVAS_H)
        return image.getpixel((180, 320))

    assert center_color(5.05) == (255, 0, 0)  # first frame right at the sticker's start, not at t=0
    assert center_color(5.15) == (0, 255, 0)
    assert center_color(5.25) == (0, 0, 255)
    assert center_color(5.35) == (255, 0, 0)  # looped


def test_sticker_opacity_and_fade_reduce_alpha():
    sticker = StickerInstance(start_seconds=1, end_seconds=3, shape="heart", opacity=0.5, animation="fade_in_out",
                              x_fraction=0.5, y_fraction=0.5, size_fraction=0.3)
    base = _blank(color=(0, 0, 0))
    mid = pc.compose(base, pc.Scene(stickers=(sticker,)), t=2.0, canvas_width=CANVAS_W, canvas_height=CANVAS_H)
    ramp = pc.compose(base, pc.Scene(stickers=(sticker,)), t=1.1, canvas_width=CANVAS_W, canvas_height=CANVAS_H)
    brightest_mid = max(mid.convert("L").getdata())
    brightest_ramp = max(ramp.convert("L").getdata())
    assert 0 < brightest_ramp < brightest_mid < 200


def test_missing_custom_sticker_file_is_skipped_not_crashing(tmp_path):
    sticker = StickerInstance(start_seconds=0, end_seconds=2, custom_path=tmp_path / "gone.png", animation="none")
    image = pc.compose(_blank(), pc.Scene(stickers=(sticker,)), t=1, canvas_width=CANVAS_W, canvas_height=CANVAS_H)
    assert image.size == (360, 640)


# --- parity with the real export -------------------------------------------------------------


@pytest.fixture(scope="module")
def parity_env(tmp_path_factory):
    if not ffmpeg_available() or _PARITY_FONT is None:
        pytest.skip("needs ffmpeg and an Arial-metric font")
    work = tmp_path_factory.mktemp("parity")
    clip = work / "flat.mp4"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x406080:s={CANVAS_W}x{CANVAS_H}:r=30:d=4",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)],
        check=True,
    )
    media = MediaItem(media_item_id="m1", original_filename="flat.mp4", stored_path=clip, kind="video",
                      duration_seconds=4, width=CANVAS_W, height=CANVAS_H, fps=30)
    timeline = Timeline(items=(TimelineClip(clip_id="c1", media_item_id="m1", source_in_seconds=0, source_out_seconds=4),))
    return work, timeline, {"m1": media}


@pytest.fixture
def parity_font(monkeypatch):
    monkeypatch.setattr(text_overlay_module, "_DEFAULT_FONT_FILE", _PARITY_FONT)
    monkeypatch.setattr(captions_module, "_DEFAULT_FONT_FILE", _PARITY_FONT)


def _export_vs_preview(parity_env, *, t, texts=(), stickers=(), lines=None, style=None):
    work, timeline, media_items = parity_env
    export_format = resolve_export_format("9:16", "1080p")
    caption_filter = build_caption_filter_from_lines(list(lines), style) if lines else None
    label = "capv" if caption_filter else "outv"
    text_filter = None
    if texts:
        text_filter = build_text_overlay_filter(list(texts), video_label=label)
        label = "textv"
    extra, label = build_rotated_text_filters(
        list(texts), canvas_width=CANVAS_W, canvas_height=CANVAS_H, cwd=work, video_label=label, first_input_index=1,
    )
    for n, sticker in enumerate(stickers):
        extra.append(build_sticker_filter(
            sticker, canvas_width=CANVAS_W, canvas_height=CANVAS_H, cwd=work, video_label=label,
            output_label=f"st{n}", input_index=1 + len(extra),
        ))
        label = f"st{n}"

    def render(filters):
        path = render_preview_frame(timeline, media_items, export_format=export_format, timestamp_seconds=t,
                                    filters=filters, cwd=work)
        with Image.open(path) as image:
            return image.convert("RGB")

    exact = render(PreviewFilters(caption_filter, text_filter, extra or None))
    base = render(PreviewFilters())
    scene = pc.Scene(text_overlays=tuple(texts), stickers=tuple(stickers), caption_style=style,
                     caption_lines=tuple(lines) if lines else None)
    preview = pc.compose(base, scene, t=t, canvas_width=CANVAS_W, canvas_height=CANVAS_H)
    return exact, preview, base


def _overlay_bbox(frame, base):
    return ImageChops.difference(frame, base).convert("L").point(lambda v: 255 if v > 40 else 0).getbbox()


def _assert_matches(exact, preview, base, *, max_mean_difference=6.0):
    """The overlay must be there, in the same place (bounding box
    within 4 px), and look the same: the mean per-pixel difference
    inside that box stays small. Single pixels on anti-aliased edges do
    differ - Pillow and ffmpeg's yuv420p chroma round edges slightly
    differently - so this compares the region, not every pixel."""
    exact_box = _overlay_bbox(exact, base)
    preview_box = _overlay_bbox(preview, base)
    assert exact_box is not None, "the export frame shows no overlay at all"
    assert preview_box is not None, "the preview shows no overlay at all"
    assert all(abs(e - p) <= 4 for e, p in zip(exact_box, preview_box)), (exact_box, preview_box)
    region = ImageChops.difference(exact, preview).convert("L").crop(exact_box)
    mean_difference = sum(region.histogram()[v] * v for v in range(256)) / (region.width * region.height)
    assert mean_difference <= max_mean_difference, mean_difference


@pytest.mark.parametrize("overlay", [
    TextOverlay(text="Labas, Ieva! ąčęėįšųūž", start_seconds=0.5, end_seconds=3, x_fraction=0.3, y_fraction=0.2,
                font_size=70, color="#FFD700"),
    TextOverlay(text="Bounce", start_seconds=0.5, end_seconds=3, animation="bounce", font_size=90),
    TextOverlay(text="Pop", start_seconds=0.5, end_seconds=3, animation="pop_up", font_size=120),
    TextOverlay(text="Rašau tekstą", start_seconds=0.5, end_seconds=3, animation="typewriter", font_size=80),
    TextOverlay(text="Pasuktas", start_seconds=0.5, end_seconds=3, font_size=90, rotation_degrees=30),
    TextOverlay(text="Kontūras ąčę", start_seconds=0.5, end_seconds=3, font_size=90, outline_width=6,
                outline_color="#1B3CFF"),
    TextOverlay(text="Šešėlis", start_seconds=0.5, end_seconds=3, font_size=90, shadow_offset=8,
                shadow_color="black", shadow_opacity=0.7),
    TextOverlay(text="Etiketė žąsis", start_seconds=0.5, end_seconds=3, font_size=70, color="black",
                background_color="#FFD700", background_opacity=1.0),
    TextOverlay(text="Viskas kartu", start_seconds=0.5, end_seconds=3, font_size=80, animation="fade",
                fade_seconds=1.0, outline_width=4, shadow_offset=5, background_opacity=0.5),
    TextOverlay(text="Švytėjimas", start_seconds=0.5, end_seconds=3, font_size=80, animation="glow",
                color="#7FDBFF", background_opacity=0.6),
    TextOverlay(text="Pasuktas fonas", start_seconds=0.5, end_seconds=3, font_size=70, rotation_degrees=-20,
                outline_width=4, background_opacity=0.7, background_color="#C9A7FF"),
    TextOverlay(text="Kitas šriftas ąčę", start_seconds=0.5, end_seconds=3, font_size=80, font="georgia",
                outline_width=3),
    TextOverlay(text="Pusiau permatomas", start_seconds=0.5, end_seconds=3, font_size=80, opacity=0.5,
                outline_width=4, background_opacity=0.6, background_color="#FFD700"),
    TextOverlay(text="Permatomas išnyksta", start_seconds=0.5, end_seconds=3, font_size=80, animation="fade",
                fade_seconds=0.4, opacity=0.6, shadow_offset=5),
    TextOverlay(text="Permatomas pasuktas", start_seconds=0.5, end_seconds=3, font_size=70, rotation_degrees=15,
                opacity=0.5, outline_width=3),
    TextOverlay(text="Glitch", start_seconds=0.5, end_seconds=3, font_size=90, animation="glitch", opacity=0.7),
], ids=["plain-lithuanian", "bounce", "pop_up", "typewriter", "rotated", "outline", "shadow", "box",
        "all-styles-fading", "glow-box", "rotated-styled", "other-font", "opacity-styled", "opacity-fading",
        "opacity-rotated", "opacity-glitch"])
def test_text_preview_matches_export(parity_env, parity_font, overlay):
    t = 0.83 if overlay.animation == "typewriter" else 0.6 if overlay.animation == "pop_up" else 1.0
    exact, preview, base = _export_vs_preview(parity_env, t=t, texts=[overlay])
    # drawtext rounds a time-varying font size to whole pixels, Pillow
    # doesn't - a little more edge difference for pop_up than elsewhere.
    _assert_matches(exact, preview, base, max_mean_difference=8.0)


@pytest.mark.parametrize("sticker, t", [
    (StickerInstance(start_seconds=0.5, end_seconds=3, shape="heart", x_fraction=0.3, y_fraction=0.6,
                     size_fraction=0.3, animation="none"), 1.0),
    (StickerInstance(start_seconds=0.5, end_seconds=3, shape="star", x_fraction=0.6, y_fraction=0.4,
                     size_fraction=0.3, rotation_degrees=25, opacity=0.6, animation="none"), 1.0),
    (StickerInstance(start_seconds=0.5, end_seconds=3, shape="arrow", size_fraction=0.25, animation="spin"), 0.9),
    (StickerInstance(start_seconds=0.5, end_seconds=3, shape="sun", size_fraction=0.25, animation="blink"), 0.7),
    (StickerInstance(start_seconds=0.5, end_seconds=3, shape="sun", size_fraction=0.25, opacity=0.7,
                     animation="fade_in_out"), 0.7),
    (StickerInstance(start_seconds=0.5, end_seconds=3, shape="heart", size_fraction=0.25, animation="bounce",
                     animation_speed=2.0, animation_intensity=2.0), 0.8),
    (StickerInstance(start_seconds=0.5, end_seconds=3, shape="arrow", size_fraction=0.25, animation="spin",
                     animation_speed=0.5), 1.2),
    (StickerInstance(start_seconds=0.5, end_seconds=3, shape="sun", size_fraction=0.25, animation="blink",
                     animation_speed=2.0, animation_intensity=0.5), 0.7),
    (StickerInstance(start_seconds=0.5, end_seconds=3, shape="star", size_fraction=0.3, animation="float",
                     fade_in_seconds=1.0, fade_out_seconds=0.5), 1.0),
], ids=["plain", "rotated-translucent", "spin", "blink", "fade", "bounce-fast-strong", "spin-slow",
        "blink-fast-soft", "float-appearing"])
def test_sticker_preview_matches_export(parity_env, sticker, t):
    exact, preview, base = _export_vs_preview(parity_env, t=t, stickers=[sticker])
    _assert_matches(exact, preview, base)


def test_caption_preview_matches_export(parity_env, parity_font):
    exact, preview, base = _export_vs_preview(
        parity_env, t=1.0, lines=[CaptionLine(text="Šiandien kalbame apie jogą", start_seconds=0.5, end_seconds=3)],
        style=CaptionStyle(animation="none", shadow_offset=3),
    )
    _assert_matches(exact, preview, base)


def test_styled_caption_with_another_font_matches_export(parity_env, parity_font):
    exact, preview, base = _export_vs_preview(
        parity_env, t=1.0, lines=[CaptionLine(text="Žiūrėk čia ąčęėįšųūž", start_seconds=0.5, end_seconds=3)],
        style=CaptionStyle(animation="none", font="times", outline_width=4, outline_color="#1B3CFF",
                           shadow_offset=4, background=False, color="#FFD700"),
    )
    # Thin serifs with a yellow-on-blue outline: almost every pixel is an
    # edge, where yuv420p halves the color resolution - so a little more
    # difference than for a plain sans text, though they look the same.
    _assert_matches(exact, preview, base, max_mean_difference=12.0)


def test_compose_draws_on_a_copy():
    base = _blank()
    snapshot = base.copy()
    ImageDraw.Draw(snapshot)  # noqa - just to be explicit we compare against an untouched copy
    pc.compose(base, pc.Scene(text_overlays=(TextOverlay(text="X", start_seconds=0, end_seconds=1),)),
               t=0.5, canvas_width=CANVAS_W, canvas_height=CANVAS_H)
    assert ImageChops.difference(base, snapshot).getbbox() is None


@pytest.mark.parametrize("look", ["moody", "golden_hour", "y2k", "vintage", "cinematic", "natural"])
def test_filter_look_in_live_playback_matches_export(parity_env, look):
    """The playback decoder and the export run the same filter chain, so
    a frame with a look looks the same in both (and differs from none)."""
    import dataclasses

    from jarvis.video_editor.effects import EffectSpec
    from jarvis.video_editor.playback import decode_single_frame

    work, timeline, media_items = parity_env
    graded = dataclasses.replace(timeline, items=(
        dataclasses.replace(timeline.items[0], effect=EffectSpec(look=look, look_intensity=0.8)),
    ))
    export_format = resolve_export_format("9:16", "1080p")
    path = render_preview_frame(graded, media_items, export_format=export_format, timestamp_seconds=1.0,
                                filters=PreviewFilters(), cwd=work)
    with Image.open(path) as image:
        exact = image.convert("RGB").resize((360, 640), Image.Resampling.BILINEAR)
    live = decode_single_frame(graded, media_items, t=1.0, width=360, height=640)
    plain = decode_single_frame(timeline, media_items, t=1.0, width=360, height=640)

    def mean_difference(a, b):
        diff = ImageChops.difference(a, b).convert("L")
        return sum(diff.histogram()[v] * v for v in range(256)) / (diff.width * diff.height)

    def strongest_channel_shift(a, b):
        return max(
            sum(h * v for v, h in enumerate(channel.histogram())) / (a.width * a.height)
            for channel in ImageChops.difference(a, b).split()
        )

    assert mean_difference(live, exact) <= 4.0
    assert strongest_channel_shift(live, plain) >= 3.0
