"""Live Preview panel - Stage 1: shows a real, fully-composited frame of
the current timeline at a chosen timestamp, refreshed whenever the
person edits anything (timeline, text overlays, stickers, captions).

Same "no new in-app video decoder, extract a real frame via ffmpeg"
convention as jarvis.video_editor.live_preview's own docstring and
jarvis.gui.views.video_editor.timeline_panel's established precedent -
this panel never decodes video itself, it only displays whatever real
PNG the owning dashboard's background render produces.

This panel never calls jarvis.video_editor.live_preview
.render_preview_frame() itself (a real, possibly-slow ffmpeg call) - it
only exposes the chosen timestamp (via a slider) and shows whatever
frame the owning dashboard hands it via show_frame()/show_error(),
mirroring every other panel's "collect a choice, dashboard renders it"
division of responsibility (see ImportPanel's own docstring)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import customtkinter as ctk
from PIL import Image

from jarvis.gui import theme
from jarvis.gui.views.video_editor.common import format_duration, status_label
from jarvis.gui.widgets import Card

_PREVIEW_MAX_HEIGHT = 420


class LivePreviewPanel(ctk.CTkFrame):
    def __init__(self, master, *, on_timestamp_changed: Callable[[float], None], **kwargs) -> None:
        """`on_timestamp_changed(seconds)` fires whenever the person
        drags the scrub slider - the owning dashboard debounces/renders
        a new preview frame for that timestamp in the background."""
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_timestamp_changed = on_timestamp_changed
        self._total_duration_seconds = 0.0
        self._current_timestamp_seconds = 0.0
        self._image_label: ctk.CTkLabel | None = None

        card = Card(self)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        ctk.CTkLabel(
            inner, text="🖥️ LIVE PREVIEW",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            inner,
            text="A real, composited frame of your current edit - updates automatically as you add "
                 "text, stickers, captions, or change the timeline.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w", wraplength=600, justify="left",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        self._frame_container = ctk.CTkFrame(inner, fg_color=theme.BG_CARD, corner_radius=8)
        self._frame_container.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._placeholder_label = ctk.CTkLabel(
            self._frame_container, text="Add a clip or photo to see a live preview.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_MUTED,
        )
        self._placeholder_label.pack(pady=theme.SPACE_LG * 2)

        self._timestamp_label = ctk.CTkLabel(
            inner, text="00:00 / 00:00",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        )
        self._timestamp_label.pack(anchor="w")

        self._scrub_slider = ctk.CTkSlider(
            inner, from_=0.0, to=1.0, number_of_steps=1000, command=self._on_slider_moved,
        )
        self._scrub_slider.set(0.0)
        self._scrub_slider.pack(fill="x", pady=(theme.SPACE_XS, 0))
        self._scrub_slider.configure(state="disabled")

        self._status = status_label(inner, "", kind="muted")

    def set_total_duration(self, total_duration_seconds: float) -> None:
        """Called by the owning dashboard whenever the timeline's own
        total duration changes - rescales the slider's own range and
        clamps the current timestamp into the new range (e.g. after a
        clip is trimmed shorter than the previously-selected preview
        timestamp)."""
        self._total_duration_seconds = max(0.0, total_duration_seconds)
        self._scrub_slider.configure(
            from_=0.0, to=max(0.01, self._total_duration_seconds),
            state="normal" if self._total_duration_seconds > 0 else "disabled",
        )
        self._current_timestamp_seconds = min(self._current_timestamp_seconds, self._total_duration_seconds)
        self._scrub_slider.set(self._current_timestamp_seconds)
        self._update_timestamp_label()
        if self._total_duration_seconds <= 0:
            self._show_placeholder("Add a clip or photo to see a live preview.")

    def current_timestamp_seconds(self) -> float:
        return self._current_timestamp_seconds

    def _on_slider_moved(self, value: float) -> None:
        self._current_timestamp_seconds = float(value)
        self._update_timestamp_label()
        self._on_timestamp_changed(self._current_timestamp_seconds)

    def _update_timestamp_label(self) -> None:
        self._timestamp_label.configure(
            text=f"{format_duration(self._current_timestamp_seconds)} / {format_duration(self._total_duration_seconds)}",
        )

    def set_rendering_state(self, *, rendering: bool) -> None:
        if rendering:
            self._status.configure(text="Rendering preview...", text_color=theme.ACCENT_PRIMARY)
            self._status.pack(anchor="w", pady=(theme.SPACE_XS, 0))
        else:
            self._status.pack_forget()

    def show_frame(self, frame_path: Path) -> None:
        """Displays a real rendered frame - called by the owning
        dashboard once jarvis.video_editor.live_preview
        .render_preview_frame() has produced a real PNG."""
        self._status.pack_forget()
        if not frame_path.is_file():
            self.show_error("The preview frame could not be found.")
            return
        try:
            pil_image = Image.open(frame_path)
            pil_image.load()
        except Exception:
            self.show_error("The preview frame could not be opened.")
            return

        display_height = min(_PREVIEW_MAX_HEIGHT, pil_image.height)
        display_width = int(display_height * pil_image.width / pil_image.height) if pil_image.height else display_height
        ctk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(display_width, display_height))

        self._placeholder_label.pack_forget()
        if self._image_label is None or not self._image_label.winfo_exists():
            self._image_label = ctk.CTkLabel(self._frame_container, text="")
            self._image_label.pack(pady=theme.SPACE_SM)
        self._image_label.configure(image=ctk_image, text="")
        self._image_label.image = ctk_image  # keep a real reference - CTkImage is garbage-collected otherwise

    def show_error(self, message: str) -> None:
        self._status.configure(text=f"⚠️ {message}", text_color=theme.DANGER)
        self._status.pack(anchor="w", pady=(theme.SPACE_XS, 0))

    def _show_placeholder(self, message: str) -> None:
        if self._image_label is not None and self._image_label.winfo_exists():
            self._image_label.pack_forget()
        self._placeholder_label.configure(text=message)
        self._placeholder_label.pack(pady=theme.SPACE_LG * 2)
