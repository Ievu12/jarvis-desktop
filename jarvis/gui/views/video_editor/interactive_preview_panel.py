"""Interactive live preview: the big video window at the center of the
Video Editor. Shows the current frame with every overlay drawn on top
in real time (jarvis.video_editor.preview_compositor), plays/pauses the
timeline, and lets the person click a text or sticker right in the
picture to select it, drag it to move it, drag the corner handle to
resize it and drag the round handle to rotate it.

This panel owns no project state. The owning dashboard holds the
timeline, overlays and the PlaybackEngine; it hands this panel frames
(show_base()/show_exact()) and the current Scene (update_scene()), and
receives the person's edits back through the callbacks - the same
"panel collects a choice, dashboard applies it" split every other
Video Editor panel follows.

Two kinds of frame:
  - a BASE frame (decoded video only, no overlays) - overlays are
    composited here with Pillow on every redraw, so a drag or a slider
    change shows up immediately, during playback too;
  - an EXACT frame - the real export render of this moment from ffmpeg
    (jarvis.video_editor.live_preview). Shown when the preview has been
    paused and still for a moment, marked "✓ Tikslus eksporto kadras";
    any edit switches back to the base+overlay view."""

from __future__ import annotations

import dataclasses
import math
import tkinter as tk
from pathlib import Path
from typing import Callable

import customtkinter as ctk
from PIL import Image, ImageTk

from jarvis.gui import theme
from jarvis.gui.widgets import Card
from jarvis.video_editor import preview_compositor as pc
from jarvis.video_editor.stickers import StickerInstance
from jarvis.video_editor.text_overlay import ROTATABLE_TEXT_ANIMATIONS, TextOverlay

DISPLAY_MAX_WIDTH = 640
DISPLAY_MAX_HEIGHT = 520
# The size before the panel knows how much room it has; after that the
# picture grows/shrinks with the window, up to FIT_MAX_SIDE.
FIT_MAX_SIDE = 1000
_MIN_FIT_SIDE = 120

_HANDLE_SIZE = 10
_ROTATE_HANDLE_DISTANCE = 26
_ROTATION_SNAP_DEGREES = 4.0
_MIN_STICKER_SIZE = 0.02
_MIN_FONT_SIZE, _MAX_FONT_SIZE = 8, 400

ElementRef = tuple[str, int]
"""("text" | "sticker", index into the scene's list)."""


def format_timecode(seconds: float) -> str:
    seconds = max(0.0, seconds)
    minutes, secs = divmod(seconds, 60)
    return f"{int(minutes):02d}:{secs:04.1f}"


def display_size_for(
    frame_size: tuple[int, int], available: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """The on-screen picture size: `frame_size`'s aspect ratio, as big
    as fits `available` (or the default box when that's unknown)."""
    width, height = frame_size
    if available is None:
        max_width, max_height = DISPLAY_MAX_WIDTH, DISPLAY_MAX_HEIGHT
    else:
        max_width = max(_MIN_FIT_SIDE, min(FIT_MAX_SIDE, available[0]))
        max_height = max(_MIN_FIT_SIDE, min(FIT_MAX_SIDE, available[1]))
    ratio = min(max_width / width, max_height / height)
    return max(1, round(width * ratio)), max(1, round(height * ratio))


class InteractivePreviewPanel(ctk.CTkFrame):
    def __init__(
        self, master, *,
        on_play_toggled: Callable[[], None],
        on_seek: Callable[[float], None],
        on_selection_changed: Callable[[ElementRef | None], None],
        on_element_edited: Callable[[str, int, object, bool], None],
        on_delete_requested: Callable[[str, int], None],
        **kwargs,
    ) -> None:
        """`on_element_edited(kind, index, new_element, final)` fires
        continuously while dragging (final=False) and once on release
        (final=True) - the dashboard updates its state on every call
        but only saves/syncs the side panels on the final one."""
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_play_toggled = on_play_toggled
        self._on_seek = on_seek
        self._on_selection_changed = on_selection_changed
        self._on_element_edited = on_element_edited
        self._on_delete_requested = on_delete_requested

        self._scene = pc.Scene()
        self._canvas_size = (1080, 1920)
        self._frame_size = (360, 640)
        self._placeholder_message = ""
        self._available: tuple[int, int] | None = None
        self._display_size = display_size_for(self._frame_size)
        self._base_frame: Image.Image | None = None
        self._exact_frame: Image.Image | None = None
        self._time = 0.0
        self._duration = 0.0
        self._boxes: list[pc.ElementBox] = []
        self._selected: ElementRef | None = None
        self._drag: dict | None = None
        self._photo: ImageTk.PhotoImage | None = None

        card = Card(self)
        card.pack(fill="both", expand=True)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        header = ctk.CTkFrame(inner, fg_color="transparent")
        header.pack(fill="x", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            header, text="🖥️ PERŽIŪRA",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(side="left")
        self._mode_label = ctk.CTkLabel(
            header, text="", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="e",
        )
        self._mode_label.pack(side="right")

        # Packed bottom-up so the picture area gets whatever height is
        # left and the transport row never gets pushed out of view.
        self._hint_label = ctk.CTkLabel(
            inner, text="Spauskite tekstą ar lipduką, kad jį perkeltumėte, keistumėte dydį ar pasuktumėte.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w", wraplength=self._display_size[0], justify="left",
        )
        self._hint_label.pack(side="bottom", anchor="w", pady=(theme.SPACE_XS, 0))
        transport = ctk.CTkFrame(inner, fg_color="transparent")
        transport.pack(side="bottom", fill="x", pady=(theme.SPACE_SM, 0))

        self._stage = tk.Frame(inner, bg=theme.BG_CARD, highlightthickness=0)
        self._stage.pack(fill="both", expand=True)
        self._stage.bind("<Configure>", self._on_stage_resized)
        self._canvas = tk.Canvas(
            self._stage, width=self._display_size[0], height=self._display_size[1], bg="#000000",
            highlightthickness=0, cursor="hand2",
        )
        self._canvas.place(relx=0.5, rely=0.5, anchor="center")
        self._canvas.bind("<ButtonPress-1>", self._on_press)
        self._canvas.bind("<B1-Motion>", self._on_motion)
        self._canvas.bind("<ButtonRelease-1>", self._on_release)
        self._canvas.bind("<Delete>", self._on_delete_key)
        self._canvas.bind("<BackSpace>", self._on_delete_key)
        self._draw_placeholder("Sukurkite projektą ir pridėkite klipą ar nuotrauką.")

        ctk.CTkButton(
            transport, text="⏮", width=36, command=lambda: self._on_seek(0.0),
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_XS))
        self._play_button = ctk.CTkButton(transport, text="▶", width=48, command=self._on_play_toggled)
        self._play_button.pack(side="left", padx=(0, theme.SPACE_SM))
        self._time_label = ctk.CTkLabel(
            transport, text="00:00.0 / 00:00.0", width=120,
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        )
        self._time_label.pack(side="left", padx=(0, theme.SPACE_SM))
        self._slider = ctk.CTkSlider(transport, from_=0.0, to=1.0, number_of_steps=2000, command=self._on_slider_moved)
        self._slider.set(0.0)
        self._slider.pack(side="left", fill="x", expand=True)
        self._slider.configure(state="disabled")

    # --- dashboard-facing API ----------------------------------------------------------------

    def configure_canvas(self, *, frame_size: tuple[int, int], canvas_size: tuple[int, int]) -> None:
        """`frame_size` is the decoded preview frame size, `canvas_size`
        the export canvas overlays are measured against (1080p of the
        timeline's aspect ratio)."""
        self._canvas_size = canvas_size
        if frame_size != self._frame_size:
            self._frame_size = frame_size
            self._base_frame = None
            self._exact_frame = None
        self._apply_display_size()

    def _on_stage_resized(self, event) -> None:
        available = (event.width - 4, event.height - 4)
        if available[0] < 20 or available[1] < 20 or available == self._available:
            return
        self._available = available
        self._apply_display_size()

    def _apply_display_size(self) -> None:
        new_display = display_size_for(self._frame_size, self._available)
        if new_display == self._display_size:
            return
        self._display_size = new_display
        self._canvas.configure(width=new_display[0], height=new_display[1])
        self._hint_label.configure(wraplength=max(200, new_display[0]))
        # The decoded frames are kept: they are scaled to the new size on redraw.
        if self._base_frame is None and self._exact_frame is None:
            self._draw_placeholder(self._placeholder_message)
        else:
            self._redraw()

    def set_time(self, t: float, duration: float) -> None:
        self._time = t
        self._duration = max(0.0, duration)
        self._slider.configure(
            from_=0.0, to=max(0.01, self._duration), state="normal" if self._duration > 0 else "disabled",
        )
        self._slider.set(min(t, max(0.01, self._duration)))
        self._time_label.configure(text=f"{format_timecode(t)} / {format_timecode(self._duration)}")
        if self._duration <= 0:
            self._base_frame = self._exact_frame = None
            self._draw_placeholder("Pridėkite klipą ar nuotrauką į laiko juostą.")

    def set_playing(self, playing: bool) -> None:
        self._play_button.configure(text="⏸" if playing else "▶")
        self._exact_frame = None
        if playing:
            self._mode_label.configure(text="▶ Groja (sumažinta raiška)", text_color=theme.TEXT_MUTED)
        else:
            self._mode_label.configure(text="")

    def update_scene(self, scene: pc.Scene, *, keep_exact: bool = False) -> None:
        """New overlays - redraws immediately from the base frame. The
        exact frame (if shown) no longer matches, so it's dropped unless
        the caller knows nothing visible changed."""
        self._scene = scene
        if self._selected is not None and not self._selection_exists(self._selected):
            self._set_selected(None, notify=True)
        if not keep_exact:
            self._exact_frame = None
        self._redraw()

    def show_base(self, frame: Image.Image, t: float) -> None:
        """A decoded video frame (no overlays) for timeline time `t`."""
        self._base_frame = frame
        self._time = t
        if self._exact_frame is not None:
            self._exact_frame = None
            self._mode_label.configure(text="")
        self._redraw()

    def show_exact(self, frame_path: Path) -> None:
        """The real export render of the current moment."""
        if self._drag is not None:
            return
        try:
            with Image.open(frame_path) as image:
                self._exact_frame = image.convert("RGB")
        except OSError:
            return
        self._mode_label.configure(text="✓ Tikslus eksporto kadras", text_color=theme.SUCCESS)
        self._redraw()

    def show_error(self, message: str) -> None:
        self._mode_label.configure(text=f"⚠️ {message[:80]}", text_color=theme.DANGER)

    def select(self, ref: ElementRef | None) -> None:
        """Selects an element from outside (e.g. right after it's added)."""
        self._set_selected(ref, notify=False)
        self._redraw()

    @property
    def selected(self) -> ElementRef | None:
        return self._selected

    @property
    def display_size(self) -> tuple[int, int]:
        return self._display_size

    def fraction_at_root(self, x_root: int, y_root: int) -> tuple[float, float] | None:
        """Where screen point (x_root, y_root) falls on the video, as
        fractions of its width/height - None when it's outside it
        (used when a sticker is dragged in from the library)."""
        x = x_root - self._canvas.winfo_rootx()
        y = y_root - self._canvas.winfo_rooty()
        width, height = self._display_size
        if not (0 <= x < width and 0 <= y < height):
            return None
        return round(x / width, 4), round(y / height, 4)

    def show_drop_target(self, active: bool) -> None:
        """A dashed frame around the video while something is being
        dragged over it."""
        self._canvas.delete("drop_target")
        if active:
            width, height = self._display_size
            self._canvas.create_rectangle(
                3, 3, width - 3, height - 3, outline=theme.ACCENT_PRIMARY, width=3, dash=(8, 4), tags=("drop_target",),
            )

    @property
    def boxes(self) -> list[pc.ElementBox]:
        return list(self._boxes)

    # --- drawing ------------------------------------------------------------------------------

    def _draw_placeholder(self, message: str) -> None:
        self._placeholder_message = message
        self._canvas.delete("all")
        self._boxes = []
        width, height = self._display_size
        self._canvas.create_text(
            width / 2, height / 2, text=message, fill=theme.TEXT_MUTED, width=width - 40, justify="center",
        )

    def _redraw(self) -> None:
        if self._base_frame is None and self._exact_frame is None:
            return
        width, height = self._display_size
        if self._exact_frame is not None:
            image = self._exact_frame.resize((width, height), Image.Resampling.BILINEAR)
        else:
            base = self._base_frame.resize((width, height), Image.Resampling.BILINEAR)
            image = pc.compose(base, self._scene, t=self._time, canvas_width=self._canvas_size[0], canvas_height=self._canvas_size[1])
        self._boxes = pc.element_boxes(
            self._scene, t=self._time, frame_width=width, frame_height=height, canvas_width=self._canvas_size[0],
        )
        self._photo = ImageTk.PhotoImage(image)
        self._canvas.delete("all")
        self._canvas.create_image(0, 0, image=self._photo, anchor="nw")
        self._draw_selection()

    def _selected_box(self) -> pc.ElementBox | None:
        if self._selected is None:
            return None
        kind, index = self._selected
        for box in self._boxes:
            if box.kind == kind and box.index == index:
                return box
        return None

    def _rotate_handle_position(self, box: pc.ElementBox) -> tuple[float, float]:
        angle = math.radians(box.rotation_degrees)
        distance = box.height / 2 + _ROTATE_HANDLE_DISTANCE
        return box.center_x + math.sin(angle) * distance, box.center_y - math.cos(angle) * distance

    def _can_rotate(self, kind: str, index: int) -> bool:
        if kind == "sticker":
            return self._scene.stickers[index].animation != "spin"
        return self._scene.text_overlays[index].animation in ROTATABLE_TEXT_ANIMATIONS

    def _draw_selection(self) -> None:
        box = self._selected_box()
        if box is None:
            return
        corners = box.corners()
        flat = [coordinate for corner in corners for coordinate in corner]
        self._canvas.create_polygon(*flat, outline=theme.ACCENT_PRIMARY, fill="", width=2, dash=(5, 3))
        handle_x, handle_y = corners[2]
        half = _HANDLE_SIZE / 2
        self._canvas.create_rectangle(
            handle_x - half, handle_y - half, handle_x + half, handle_y + half,
            fill=theme.ACCENT_PRIMARY, outline="white",
        )
        if self._can_rotate(box.kind, box.index):
            rotate_x, rotate_y = self._rotate_handle_position(box)
            top_mid = ((corners[0][0] + corners[1][0]) / 2, (corners[0][1] + corners[1][1]) / 2)
            self._canvas.create_line(*top_mid, rotate_x, rotate_y, fill=theme.ACCENT_PRIMARY, width=1)
            self._canvas.create_oval(
                rotate_x - half, rotate_y - half, rotate_x + half, rotate_y + half,
                fill="white", outline=theme.ACCENT_PRIMARY, width=2,
            )

    # --- selection & dragging ------------------------------------------------------------------

    def _selection_exists(self, ref: ElementRef) -> bool:
        kind, index = ref
        items = self._scene.text_overlays if kind == "text" else self._scene.stickers
        return 0 <= index < len(items)

    def _set_selected(self, ref: ElementRef | None, *, notify: bool) -> None:
        if ref == self._selected:
            return
        self._selected = ref
        if notify:
            self._on_selection_changed(ref)

    def _element(self, kind: str, index: int):
        return (self._scene.text_overlays if kind == "text" else self._scene.stickers)[index]

    def _on_press(self, event) -> None:
        self._canvas.focus_set()
        if not self._boxes and self._base_frame is None:
            return
        box = self._selected_box()
        if box is not None:
            corner_x, corner_y = box.corners()[2]
            if math.hypot(event.x - corner_x, event.y - corner_y) <= _HANDLE_SIZE:
                self._start_drag("resize", box, event)
                return
            if self._can_rotate(box.kind, box.index):
                rotate_x, rotate_y = self._rotate_handle_position(box)
                if math.hypot(event.x - rotate_x, event.y - rotate_y) <= _HANDLE_SIZE:
                    self._start_drag("rotate", box, event)
                    return
        hit = pc.hit_test(self._boxes, event.x, event.y)
        if hit is None:
            self._set_selected(None, notify=True)
            self._redraw()
            return
        self._set_selected((hit.kind, hit.index), notify=True)
        self._start_drag("move", hit, event)
        self._redraw()

    def _start_drag(self, mode: str, box: pc.ElementBox, event) -> None:
        self._drag = {
            "mode": mode, "kind": box.kind, "index": box.index, "box": box,
            "element": self._element(box.kind, box.index),
            "start_x": event.x, "start_y": event.y, "moved": False,
        }
        self._exact_frame = None

    def _on_motion(self, event) -> None:
        drag = self._drag
        if drag is None:
            return
        new_element = self._dragged_element(drag, event.x, event.y)
        if new_element is None:
            return
        drag["moved"] = True
        self._on_element_edited(drag["kind"], drag["index"], new_element, False)

    def _on_release(self, event) -> None:
        drag, self._drag = self._drag, None
        if drag is None or not drag["moved"]:
            return
        new_element = self._dragged_element(drag, event.x, event.y)
        if new_element is not None:
            self._on_element_edited(drag["kind"], drag["index"], new_element, True)

    def _on_delete_key(self, _event=None) -> None:
        if self._selected is not None:
            kind, index = self._selected
            self._set_selected(None, notify=True)
            self._on_delete_requested(kind, index)

    def _dragged_element(self, drag: dict, x: float, y: float):
        """The element as it would be with the pointer at (x, y)."""
        box: pc.ElementBox = drag["box"]
        element = drag["element"]
        width, height = self._display_size
        scale = width / self._canvas_size[0]
        mode = drag["mode"]

        if mode == "move":
            center_x = box.center_x + (x - drag["start_x"])
            center_y = box.center_y + (y - drag["start_y"])
            if isinstance(element, StickerInstance):
                return dataclasses.replace(
                    element, x_fraction=round(_clamp(center_x / width, 0.0, 1.0), 4),
                    y_fraction=round(_clamp(center_y / height, 0.0, 1.0), 4),
                )
            x_fraction, y_fraction = pc.text_fractions_for_center(
                element, center_x=center_x, center_y=center_y, frame_width=width, frame_height=height, scale=scale,
            )
            return dataclasses.replace(element, x_fraction=round(x_fraction, 4), y_fraction=round(y_fraction, 4))

        if mode == "resize":
            start_distance = max(1.0, math.hypot(drag["start_x"] - box.center_x, drag["start_y"] - box.center_y))
            ratio = math.hypot(x - box.center_x, y - box.center_y) / start_distance
            if isinstance(element, StickerInstance):
                return dataclasses.replace(element, size_fraction=round(_clamp(element.size_fraction * ratio, _MIN_STICKER_SIZE, 1.0), 4))
            assert isinstance(element, TextOverlay)
            new_size = round(_clamp(element.font_size * ratio, _MIN_FONT_SIZE, _MAX_FONT_SIZE))
            new_element = dataclasses.replace(element, font_size=new_size)
            # Keep the text's center where it was while it grows/shrinks.
            x_fraction, y_fraction = pc.text_fractions_for_center(
                new_element, center_x=box.center_x, center_y=box.center_y, frame_width=width, frame_height=height, scale=scale,
            )
            return dataclasses.replace(new_element, x_fraction=round(x_fraction, 4), y_fraction=round(y_fraction, 4))

        # rotate
        angle = math.degrees(math.atan2(x - box.center_x, -(y - box.center_y)))
        for snap in (-180.0, -90.0, 0.0, 90.0, 180.0):
            if abs(angle - snap) <= _ROTATION_SNAP_DEGREES:
                angle = snap
        angle = round(angle, 1)
        if angle == -180.0:
            angle = 180.0
        return dataclasses.replace(element, rotation_degrees=angle)

    # --- transport ----------------------------------------------------------------------------

    def _on_slider_moved(self, value: float) -> None:
        self._time_label.configure(text=f"{format_timecode(float(value))} / {format_timecode(self._duration)}")
        self._on_seek(float(value))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
