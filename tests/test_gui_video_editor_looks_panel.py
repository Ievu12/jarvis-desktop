"""Stage 3 GUI tests: the library's Filters and Transitions panels, the
settings panel's filter/transition/sticker-animation controls, and
dragging a sticker out of the library."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import customtkinter as ctk
import pytest
from PIL import Image

from jarvis.gui.views.video_editor.element_inspector_panel import ElementInspectorPanel
from jarvis.gui.views.video_editor.looks_panel import FiltersPanel, TransitionsPanel
from jarvis.gui.views.video_editor.stickers_panel import StickersPanel
from jarvis.video_editor.captions import CaptionStyle
from jarvis.video_editor.effects import EffectSpec
from jarvis.video_editor.stickers import StickerInstance
from jarvis.video_editor.timeline import TimelineStill, TransitionSpec


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


def _wait(root, predicate, timeout_s: float = 20.0) -> None:
    import time

    deadline = time.monotonic() + timeout_s
    while not predicate() and time.monotonic() < deadline:
        root.update()
        time.sleep(0.02)


def test_filters_panel_targets_a_clip_and_reports_choices(root, tmp_path):
    looks, intensities, apply_all = [], [], []
    rendered = []

    def fake_render(image, looks_, *, intensity):
        rendered.append((image.size, looks_))
        return {look: image for look in looks_}

    panel = FiltersPanel(
        root, on_look_chosen=looks.append, on_intensity_changed=lambda v, final: intensities.append((v, final)),
        on_apply_all=lambda: apply_all.append(True), render_previews=fake_render,
    )
    panel.pack()
    panel.show_target(None, None, None)
    assert panel.look_buttons["moody"].cget("state") == "disabled"
    panel.look_buttons["moody"].invoke()
    assert looks == []

    source = tmp_path / "frame.png"
    Image.new("RGB", (320, 180), "orange").save(source)
    panel.show_target("klipas.mp4", EffectSpec(look="vintage", look_intensity=0.4), source)
    _wait(root, lambda: panel.look_buttons["y2k"].cget("image") is not None)
    assert rendered and rendered[0][0] == (72, 72)
    assert panel.look_buttons["vintage"].cget("border_color") != panel.look_buttons["moody"].cget("border_color")

    panel.look_buttons["cinematic"].invoke()
    assert looks == ["cinematic"]
    panel._intensity._moved(75)
    assert intensities == [(0.75, False)]
    _wait(root, lambda: intensities[-1][1])
    assert intensities[-1] == (0.75, True)
    panel._apply_all_button.invoke()
    assert apply_all == [True]


def test_transitions_panel_is_off_for_the_last_clip(root):
    kinds, durations, apply_all = [], [], []
    panel = TransitionsPanel(
        root, on_transition_chosen=kinds.append, on_duration_changed=lambda v, final: durations.append((v, final)),
        on_apply_all=lambda: apply_all.append(True),
    )
    panel.show_target("paskutinis.mp4", TransitionSpec(), None)
    assert panel.kind_buttons["fade"].cget("state") == "disabled"

    panel.show_target("pirmas.mp4", TransitionSpec(), 1.5)
    assert panel.duration() == 0.5
    panel.kind_buttons["dissolve"].invoke()
    assert kinds == ["dissolve"]
    panel._duration._moved(1.2)
    assert durations == []  # a cut has no duration

    panel.show_target("pirmas.mp4", TransitionSpec("dissolve", 1.0), 1.5)
    panel._duration._moved(1.2)
    assert durations == [(1.2, False)]
    panel._apply_all_button.invoke()
    assert apply_all == [True]


def _inspector(root):
    edits = []
    inspector = ElementInspectorPanel(
        root, on_element_edited=lambda kind, index, new, final: edits.append((kind, index, new, final)),
        on_delete_requested=lambda kind, index: None, on_duplicate_requested=lambda kind, index: None,
    )
    inspector.pack()
    return inspector, edits


def _dropdowns(widget) -> list:
    found = []
    for child in widget.winfo_children():
        if isinstance(child, ctk.CTkOptionMenu):
            found.append(child)
        found.extend(_dropdowns(child))
    return found


def test_inspector_clip_filter_and_transition(root):
    inspector, edits = _inspector(root)
    photo = TimelineStill(clip_id="s", media_item_id="p", display_duration_seconds=3)

    inspector.show_element("clip", 1, photo, title="foto.jpg", max_transition=None)
    assert len(_dropdowns(inspector)) == 1  # the last clip: filter only, no transition
    inspector.show_element("clip", 0, photo, title="foto.jpg", max_transition=1.8)
    look_menu, transition_menu = _dropdowns(inspector)

    look_menu._command("Golden Hour")
    assert edits[-1][2].effect.look == "golden_hour" and edits[-1][3]
    transition_menu._command("Užtemimas")
    assert edits[-1][2].transition_out == TransitionSpec("fade", 0.5)
    transition_menu._command("Be perėjimo")
    assert edits[-1][2].transition_out == TransitionSpec()


def test_inspector_sticker_animation_settings(root):
    inspector, edits = _inspector(root)
    sticker = StickerInstance(start_seconds=0, end_seconds=4, shape="star", animation="bounce")
    inspector.show_element("sticker", 0, sticker)
    inspector._edit(final=True, animation_speed=2.0, animation_intensity=1.5, fade_in_seconds=0.5)
    assert edits[-1][2] == dataclasses.replace(
        sticker, animation_speed=2.0, animation_intensity=1.5, fade_in_seconds=0.5,
    )
    labels = []

    def collect(widget):
        for child in widget.winfo_children():
            if isinstance(child, ctk.CTkLabel):
                labels.append(child.cget("text"))
            collect(child)

    collect(inspector)
    for expected in ("Animacijos greitis (x)", "Animacijos stiprumas", "Atsiradimas (s)", "Dingimas (s)"):
        assert expected in labels


def test_dragging_a_library_sticker_reports_moves_and_the_drop(root):
    drags = []
    panel = StickersPanel(root, on_stickers_changed=lambda s: None,
                          on_shape_dragged=lambda shape, x, y, dropped: drags.append((shape, x, y, dropped)))
    panel._on_drag_press("star", SimpleNamespace(x_root=100, y_root=100))
    panel._on_drag_motion(SimpleNamespace(x_root=103, y_root=101))
    assert drags == []  # a tiny wobble is still a click
    panel._on_drag_motion(SimpleNamespace(x_root=300, y_root=200))
    panel._on_drag_release(SimpleNamespace(x_root=320, y_root=210))
    assert drags == [("star", 300, 200, False), ("star", 320, 210, True)]
    assert panel._stickers == []  # the drop itself adds nothing here: the dashboard places it

    panel._on_drag_press("heart", SimpleNamespace(x_root=5, y_root=5))
    panel._on_drag_release(SimpleNamespace(x_root=5, y_root=5))
    assert len(drags) == 2  # a plain click is left to the button


# --- stage 4: text and subtitle styles -------------------------------------------------------------


def test_inspector_text_style_controls(root):
    from jarvis.video_editor.text_overlay import TextOverlay

    inspector, edits = _inspector(root)
    text = TextOverlay(text="Labas ąčę", start_seconds=0, end_seconds=3, x_fraction=0.3, animation="fade")
    inspector.show_element("text", 0, text)

    buttons = {}

    def collect(widget):
        for child in widget.winfo_children():
            if isinstance(child, ctk.CTkButton) and child.cget("text"):
                buttons[child.cget("text")] = child
            collect(child)

    collect(inspector)
    buttons["TikTok"].invoke()
    styled = edits[-1][2]
    assert (styled.font, styled.outline_width, styled.x_fraction, styled.animation) == ("impact", 6, 0.3, "fade")
    assert edits[-1][3]

    inspector.refresh_values(styled)  # what the dashboard does after an edit
    font_menu = next(m for m in _dropdowns(inspector) if m.get() == "Impact")
    font_menu._command("Georgia Bold")
    assert edits[-1][2].font == "georgia"

    inspector._edit(final=True, background_opacity=0.7, shadow_offset=5)
    assert (edits[-1][2].background_opacity, edits[-1][2].shadow_offset) == (0.7, 5)


def test_captions_panel_style_presets_and_font(root):
    from jarvis.gui.views.video_editor.captions_panel import CaptionsPanel

    styles = []
    panel = CaptionsPanel(root, on_style_changed=styles.append)
    panel.apply_style(CaptionStyle(font_size=70, position="top", outline_color="#1B3CFF"))
    panel.preset_buttons["TikTok"].invoke()
    style = styles[-1]
    assert (style.font, style.outline_width, style.background, style.font_size, style.position) == (
        "impact", 5, False, 70, "top",
    )
    panel._font_dropdown.set("Times New Roman Bold")
    panel._emit()
    assert styles[-1].font == "times" and styles[-1].outline_width == 5
    panel.preset_buttons["Klasikinis"].invoke()
    assert styles[-1].outline_color == "black" and styles[-1].background


def test_text_panel_edit_keeps_style_fields(root):
    from jarvis.gui.views.video_editor.text_overlay_panel import TextOverlayPanel
    from jarvis.video_editor.text_overlay import TextOverlay

    emitted = []
    panel = TextOverlayPanel(root, on_overlays_changed=emitted.append)
    panel.pack()
    styled = TextOverlay(text="Sveiki", start_seconds=0, end_seconds=2, font="georgia", outline_width=4,
                         background_opacity=0.5, rotation_degrees=15)
    panel.set_overlays([styled])
    entries = []

    def collect(widget):
        for child in widget.winfo_children():
            if isinstance(child, ctk.CTkEntry):
                entries.append(child)
            collect(child)

    collect(panel)
    text_entry = next(e for e in entries if e.get() == "Sveiki")
    text_entry.delete(0, "end")
    text_entry.insert(0, "Sveiki visi")
    root.deiconify()
    text_entry._entry.focus_force()
    root.update()
    text_entry._entry.event_generate("<Return>")
    root.withdraw()
    root.update()
    assert emitted[-1] == [dataclasses.replace(styled, text="Sveiki visi")]
