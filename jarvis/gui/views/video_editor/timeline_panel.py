"""Timeline panel: the ordered strip of TimelineClip/TimelineStill
entries, with per-entry trim/retime/transition controls and a real
thumbnail per entry. No in-app video DECODING for preview - this
codebase's own established convention (see jarvis.gui.views
.video_studio.reel_creator_panel's own docstring, and
jarvis.gui.views.reel_generator.dashboard's own _open_video() for the
identical pattern applied to a different feature): a thumbnail is a
REAL frame extracted from the real source file
(jarvis.video_studio.ffmpeg_utils.extract_frame() for video, a direct
Pillow open+thumbnail for a photo) - never a fake placeholder - and
full-motion preview opens the real file in the OS's own default player
via os.startfile(), not a new in-app decoder.

Deliberately simple drag-free reordering for this first version (Move
Up/Move Down buttons per entry) rather than real mouse-drag reordering -
native Tkinter drag-and-drop between frames inside a scrollable
container is non-trivial and error-prone to get pixel-accurate; Move
Up/Down is a real, fully-functional reordering mechanism with zero risk
of a half-finished drag leaving the timeline in an inconsistent state,
and can be upgraded to real drag-and-drop in a later pass without
changing this panel's own public contract (`render()`/`on_change`)."""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.views.video_editor.common import LabeledDropdown, format_duration
from jarvis.gui.widgets import Card
from jarvis.video_editor.effects import FADE_CHOICES, PHOTO_MOTION_CHOICES, EffectSpec
from jarvis.video_editor.media_import import MediaItem
from jarvis.video_editor.timeline import (
    ASPECT_RATIO_CHOICES,
    TRANSITION_KIND_CHOICES,
    Timeline,
    TimelineClip,
    TimelineStill,
    TransitionSpec,
)
from jarvis.video_editor.timeline_history import TimelineClipboard, TimelineHistory

_THUMBNAIL_HEIGHT = 120


class TimelinePanel(ctk.CTkFrame):
    def __init__(
        self, master, *, on_timeline_changed: Callable[[Timeline], None],
        get_thumbnail: Callable[[MediaItem], Path | None],
        on_preview_requested: Callable[[int], None] | None = None, **kwargs,
    ) -> None:
        """`get_thumbnail` is supplied by the owning dashboard (which
        has access to the project's own thumbnail cache directory) -
        this panel never decides WHERE a thumbnail file lives, it only
        asks for one and displays whatever real path comes back (or
        renders nothing for that entry if None - a missing thumbnail is
        a cosmetic gap, never a reason to break the whole panel, same
        convention jarvis.gui.views.reel_generator.dashboard's own
        _reel_preview_thumbnail_path() callers already establish).

        `on_preview_requested(index)`, if given, is called with an
        item's own index when its "👁 Preview Animation" button is
        clicked - rendering a real preview is a possibly-slow ffmpeg
        call, so this panel never runs it directly; the owning
        dashboard runs it through run_generation_in_background() (the
        same established background-thread convention every other
        possibly-slow action in this package already uses) and calls
        show_preview_frames() below once real frames are ready."""
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_timeline_changed = on_timeline_changed
        self._get_thumbnail = get_thumbnail
        self._on_preview_requested = on_preview_requested
        self._timeline = Timeline()
        self._media_items: dict[str, MediaItem] = {}
        self._preview_containers: dict[int, ctk.CTkFrame] = {}
        self._history = TimelineHistory()
        self._clipboard = TimelineClipboard()

        header_row = ctk.CTkFrame(self, fg_color="transparent")
        header_row.pack(fill="x")
        ctk.CTkLabel(
            header_row, text="🎬 TIMELINE",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(side="left")

        self._aspect_dropdown = LabeledDropdown(header_row, "Format:", ASPECT_RATIO_CHOICES)
        self._aspect_dropdown.dropdown.configure(command=lambda _v: self._on_aspect_changed())
        self._aspect_dropdown.pack(side="right")

        history_row = ctk.CTkFrame(self, fg_color="transparent")
        history_row.pack(fill="x", pady=(theme.SPACE_XS, 0))
        self._undo_button = ctk.CTkButton(
            history_row, text="↩ Undo", width=80, height=24, command=self._on_undo_clicked,
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        )
        self._undo_button.pack(side="left", padx=(0, theme.SPACE_XS))
        self._redo_button = ctk.CTkButton(
            history_row, text="↪ Redo", width=80, height=24, command=self._on_redo_clicked,
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        )
        self._redo_button.pack(side="left")
        self._update_history_buttons()

        self._items_container = ctk.CTkFrame(self, fg_color="transparent")
        self._items_container.pack(fill="x", pady=(theme.SPACE_SM, 0))

        self._validation_label = ctk.CTkLabel(
            self, text="", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.DANGER, anchor="w", wraplength=650, justify="left",
        )
        self._validation_label.pack(anchor="w", pady=(theme.SPACE_XS, 0))

    @property
    def timeline(self) -> Timeline:
        return self._timeline

    def _commit(self, new_timeline: Timeline) -> None:
        """The ONE place every real edit in this panel ends up: updates
        `self._timeline`, records the change on the undo/redo history,
        re-renders, and notifies the owning dashboard - replaces the
        repeated "set _timeline, render, notify" triplet every mutation
        method below used to do inline, so undo/redo wiring needed one
        new call site, not seven rewritten ones."""
        self._history.push(new_timeline)
        self._timeline = new_timeline
        self._update_history_buttons()
        self._rerender(self._timeline)
        self._on_timeline_changed(self._timeline)

    def _update_history_buttons(self) -> None:
        self._undo_button.configure(state="normal" if self._history.can_undo() else "disabled")
        self._redo_button.configure(state="normal" if self._history.can_redo() else "disabled")

    def _on_undo_clicked(self) -> None:
        restored = self._history.undo()
        self._timeline = restored
        self._update_history_buttons()
        self._rerender(self._timeline)
        self._on_timeline_changed(self._timeline)

    def _on_redo_clicked(self) -> None:
        restored = self._history.redo()
        self._timeline = restored
        self._update_history_buttons()
        self._rerender(self._timeline)
        self._on_timeline_changed(self._timeline)

    def _on_copy_clicked(self, index: int) -> None:
        self._clipboard.copy(self._timeline.items[index])

    def _on_paste_clicked(self, index: int) -> None:
        pasted = self._clipboard.paste(new_clip_id=_new_clip_id())
        if pasted is None:
            return
        items = list(self._timeline.items)
        items.insert(index + 1, pasted)
        self._commit(dataclasses.replace(self._timeline, items=tuple(items)))

    def add_clip(self, media_item: MediaItem) -> None:
        """Appends `media_item` to the end of the timeline - a video
        clip defaults to its FULL real duration (in=0, out=real
        duration), a photo defaults to a 3-second display duration (a
        reasonable default the person can immediately change via this
        panel's own duration field)."""
        if media_item.kind == "video":
            item = TimelineClip(
                clip_id=_new_clip_id(), media_item_id=media_item.media_item_id,
                source_in_seconds=0.0, source_out_seconds=media_item.duration_seconds or 0.0,
            )
        else:
            item = TimelineStill(
                clip_id=_new_clip_id(), media_item_id=media_item.media_item_id, display_duration_seconds=3.0,
            )
        self._media_items[media_item.media_item_id] = media_item
        self._commit(dataclasses.replace(self._timeline, items=(*self._timeline.items, item)))

    def apply_timeline(self, new_timeline: Timeline) -> None:
        """Replaces the current timeline with `new_timeline` through
        the SAME `_commit()` path every other edit in this panel uses -
        unlike `render()` (the project-OPEN entry point, which always
        resets the undo/redo history), this is a real, undoable EDIT:
        used by the owning dashboard's "Apply Reel Template" action
        (jarvis.video_editor.reel_templates.apply_template() already
        returns a new Timeline built from the current one, never a
        fresh/unrelated one), so applying a template is itself a single
        Undo away from being reverted."""
        self._commit(new_timeline)

    def render(self, timeline: Timeline, media_items: dict[str, MediaItem]) -> None:
        """The EXTERNAL entry point the owning dashboard calls on
        project open/load (see dashboard.py's own single call site) -
        always resets the undo/redo history, since a freshly-loaded
        project's own edit history never existed in this running GUI
        session (same reasoning TimelineHistory.reset()'s own docstring
        gives). Internal re-renders after an edit (_commit()/undo/redo)
        call `_rerender()` directly instead, so they never wipe the
        history they just updated."""
        self._media_items = media_items
        self._history.reset(timeline)
        self._timeline = timeline
        self._update_history_buttons()
        self._rerender(timeline)

    def _rerender(self, timeline: Timeline) -> None:
        self._aspect_dropdown.set(timeline.aspect_ratio)

        for child in self._items_container.winfo_children():
            child.destroy()
        self._preview_containers.clear()

        for index, item in enumerate(timeline.items):
            self._render_item_row(index, item)

        problems = timeline.validate()
        self._validation_label.configure(text="\n".join(f"⚠️ {p}" for p in problems) if problems else "")

    def _render_item_row(self, index: int, item) -> None:
        media = self._media_items.get(item.media_item_id)
        row = Card(self._items_container)
        row.pack(fill="x", pady=(0, theme.SPACE_XS))
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_SM, pady=theme.SPACE_SM)

        top_row = ctk.CTkFrame(inner, fg_color="transparent")
        top_row.pack(fill="x")

        thumb_path = self._get_thumbnail(media) if media is not None else None
        if thumb_path is not None and thumb_path.is_file():
            try:
                from PIL import Image

                pil_image = Image.open(thumb_path)
                pil_image.load()
                thumb_width = int(_THUMBNAIL_HEIGHT * pil_image.width / pil_image.height) if pil_image.height else 68
                ctk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(thumb_width, _THUMBNAIL_HEIGHT))
                ctk.CTkLabel(top_row, image=ctk_image, text="").pack(side="left", padx=(0, theme.SPACE_SM))
            except Exception:
                pass  # a missing/corrupt thumbnail is cosmetic - never breaks this row

        label_text = media.original_filename if media is not None else "(missing media)"
        kind_label = "🎬 Clip" if isinstance(item, TimelineClip) else "🖼️ Photo"
        ctk.CTkLabel(
            top_row, text=f"{index + 1}. {kind_label} - {label_text}",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(side="left", anchor="n")

        controls_row = ctk.CTkFrame(inner, fg_color="transparent")
        controls_row.pack(fill="x", pady=(theme.SPACE_XS, 0))

        if isinstance(item, TimelineClip):
            self._render_clip_controls(controls_row, index, item, media)
        else:
            self._render_still_controls(controls_row, index, item)

        self._render_effect_controls(inner, index, item)
        self._render_transition_controls(inner, index, item)
        self._render_reorder_remove_controls(inner, index)

    def _render_clip_controls(self, row, index: int, item: TimelineClip, media: MediaItem | None) -> None:
        max_duration = media.duration_seconds if media is not None and media.duration_seconds is not None else item.source_out_seconds

        in_entry = ctk.CTkEntry(row, width=70)
        in_entry.insert(0, f"{item.source_in_seconds:.1f}")
        ctk.CTkLabel(row, text="In (s):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        in_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        out_entry = ctk.CTkEntry(row, width=70)
        out_entry.insert(0, f"{item.source_out_seconds:.1f}")
        ctk.CTkLabel(row, text="Out (s):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        out_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        speed_entry = ctk.CTkEntry(row, width=60)
        speed_entry.insert(0, f"{item.speed_factor:g}")
        ctk.CTkLabel(row, text="Speed:", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        speed_entry.pack(side="left")

        def on_change(_event=None) -> None:
            try:
                new_in = float(in_entry.get())
                new_out = min(float(out_entry.get()), max_duration)
                new_speed = float(speed_entry.get())
            except ValueError:
                return
            self._replace_item(index, dataclasses.replace(
                item, source_in_seconds=new_in, source_out_seconds=new_out, speed_factor=new_speed,
            ))

        for entry in (in_entry, out_entry, speed_entry):
            entry.bind("<FocusOut>", on_change)
            entry.bind("<Return>", on_change)

    def _render_still_controls(self, row, index: int, item: TimelineStill) -> None:
        duration_entry = ctk.CTkEntry(row, width=70)
        duration_entry.insert(0, f"{item.display_duration_seconds:.1f}")
        ctk.CTkLabel(row, text="Duration (s):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        duration_entry.pack(side="left")

        def on_change(_event=None) -> None:
            try:
                new_duration = float(duration_entry.get())
            except ValueError:
                return
            self._replace_item(index, dataclasses.replace(item, display_duration_seconds=new_duration))

        duration_entry.bind("<FocusOut>", on_change)
        duration_entry.bind("<Return>", on_change)

    def _render_effect_controls(self, inner, index: int, item) -> None:
        """Ken Burns motion (zoom/pan) + fade + color filter controls
        for THIS item only - lives inline in the item's own row, same
        placement/on_change('<FocusOut>'/'<Return>', dropdown command)
        convention _render_transition_controls() already establishes
        right below this, rather than a separate selected-clip panel
        (jarvis.video_editor.effects.EffectSpec is per-item, so there is
        no single "the" effect to show outside of a specific row)."""
        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x", pady=(theme.SPACE_XS, 0))
        effect = item.effect

        motion_dropdown = LabeledDropdown(row, "Motion:", PHOTO_MOTION_CHOICES)
        motion_dropdown.set(effect.motion)
        motion_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))

        intensity_entry = ctk.CTkEntry(row, width=55)
        intensity_entry.insert(0, f"{effect.motion_intensity:g}")
        ctk.CTkLabel(row, text="Intensity:", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        intensity_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        fade_dropdown = LabeledDropdown(row, "Fade:", FADE_CHOICES)
        fade_dropdown.set(effect.fade)
        fade_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))

        fade_seconds_entry = ctk.CTkEntry(row, width=55)
        fade_seconds_entry.insert(0, f"{effect.fade_seconds:g}")
        ctk.CTkLabel(row, text="Fade (s):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        fade_seconds_entry.pack(side="left")

        row2 = ctk.CTkFrame(inner, fg_color="transparent")
        row2.pack(fill="x", pady=(theme.SPACE_XS, 0))

        brightness_entry = ctk.CTkEntry(row2, width=55)
        brightness_entry.insert(0, f"{effect.brightness:g}")
        ctk.CTkLabel(row2, text="Brightness:", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        brightness_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        contrast_entry = ctk.CTkEntry(row2, width=55)
        contrast_entry.insert(0, f"{effect.contrast:g}")
        ctk.CTkLabel(row2, text="Contrast:", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        contrast_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        saturation_entry = ctk.CTkEntry(row2, width=55)
        saturation_entry.insert(0, f"{effect.saturation:g}")
        ctk.CTkLabel(row2, text="Saturation:", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        saturation_entry.pack(side="left")

        error_label = ctk.CTkLabel(
            inner, text="", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.DANGER, anchor="w",
        )

        def on_change(_event=None) -> None:
            try:
                new_effect = EffectSpec(
                    motion=motion_dropdown.get(), motion_intensity=float(intensity_entry.get()),
                    fade=fade_dropdown.get(), fade_seconds=float(fade_seconds_entry.get()),
                    brightness=float(brightness_entry.get()), contrast=float(contrast_entry.get()),
                    saturation=float(saturation_entry.get()),
                )
            except ValueError:
                return
            problems = new_effect.validate()
            if problems:
                error_label.configure(text=f"⚠️ {problems[0]}")
                error_label.pack(anchor="w", pady=(theme.SPACE_XS, 0))
                return
            error_label.pack_forget()
            self._replace_item(index, dataclasses.replace(item, effect=new_effect))

        motion_dropdown.dropdown.configure(command=lambda _v: on_change())
        fade_dropdown.dropdown.configure(command=lambda _v: on_change())
        for entry in (intensity_entry, fade_seconds_entry, brightness_entry, contrast_entry, saturation_entry):
            entry.bind("<FocusOut>", on_change)
            entry.bind("<Return>", on_change)

        if self._on_preview_requested is not None:
            ctk.CTkButton(
                inner, text="👁 Preview Animation", width=160, height=24,
                command=lambda i=index: self._on_preview_requested(i),
                fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
            ).pack(anchor="w", pady=(theme.SPACE_XS, 0))
            preview_container = ctk.CTkFrame(inner, fg_color="transparent")
            preview_container.pack(fill="x", pady=(theme.SPACE_XS, 0))
            self._preview_containers[index] = preview_container

    def show_preview_frames(self, index: int, frame_paths: list[Path]) -> None:
        """Called by the owning dashboard once
        jarvis.video_editor.effects.render_effect_preview() has
        produced real start/mid/end frames for the item at `index` -
        renders them as a small thumbnail row directly in that item's
        own row, the real "animacijų peržiūra prieš eksportavimą"
        (preview the animation before export) requirement, without a
        new in-app video decoder (this package's own established
        convention - see this class's own docstring)."""
        container = self._preview_containers.get(index)
        if container is None or not container.winfo_exists():
            return
        for child in container.winfo_children():
            child.destroy()
        from PIL import Image

        for frame_path in frame_paths:
            if not frame_path.is_file():
                continue
            try:
                pil_image = Image.open(frame_path)
                pil_image.load()
                thumb_width = int(80 * pil_image.width / pil_image.height) if pil_image.height else 45
                ctk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(thumb_width, 80))
                ctk.CTkLabel(container, image=ctk_image, text="").pack(side="left", padx=(0, theme.SPACE_XS))
            except Exception:
                pass  # a corrupt/missing preview frame is cosmetic - never breaks this row

    def show_preview_error(self, index: int, message: str) -> None:
        container = self._preview_containers.get(index)
        if container is None or not container.winfo_exists():
            return
        for child in container.winfo_children():
            child.destroy()
        status_label_widget = ctk.CTkLabel(
            container, text=f"⚠️ {message}", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.DANGER, anchor="w",
        )
        status_label_widget.pack(anchor="w")

    def _render_transition_controls(self, inner, index: int, item) -> None:
        is_last = index == len(self._timeline.items) - 1
        if is_last:
            return  # the last item has no "next clip" to transition into (Timeline.validate()'s own rule)

        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x", pady=(theme.SPACE_XS, 0))
        transition_dropdown = LabeledDropdown(row, "Transition out:", TRANSITION_KIND_CHOICES)
        transition_dropdown.set(item.transition_out.kind)
        transition_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))

        duration_entry = ctk.CTkEntry(row, width=60)
        duration_entry.insert(0, f"{item.transition_out.duration_seconds:g}")
        ctk.CTkLabel(row, text="Duration (s):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        duration_entry.pack(side="left")

        def on_change(_event=None) -> None:
            kind = transition_dropdown.get()
            try:
                duration = float(duration_entry.get()) if kind != "cut" else 0.0
            except ValueError:
                return
            self._replace_item(index, dataclasses.replace(
                item, transition_out=TransitionSpec(kind=kind, duration_seconds=duration),
            ))

        transition_dropdown.dropdown.configure(command=lambda _v: on_change())
        duration_entry.bind("<FocusOut>", on_change)
        duration_entry.bind("<Return>", on_change)

    def _render_reorder_remove_controls(self, inner, index: int) -> None:
        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x", pady=(theme.SPACE_XS, 0))
        ctk.CTkButton(
            row, text="⬆ Move Up", width=90, height=24, command=lambda i=index: self._move_item(i, -1),
            state="disabled" if index == 0 else "normal",
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_XS))
        ctk.CTkButton(
            row, text="⬇ Move Down", width=100, height=24, command=lambda i=index: self._move_item(i, 1),
            state="disabled" if index == len(self._timeline.items) - 1 else "normal",
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_XS))
        ctk.CTkButton(
            row, text="🗑 Remove", width=90, height=24, command=lambda i=index: self._remove_item(i),
            fg_color=theme.BG_CARD, hover_color=theme.DANGER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_XS))
        ctk.CTkButton(
            row, text="📋 Copy", width=80, height=24, command=lambda i=index: self._on_copy_clicked(i),
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_XS))
        ctk.CTkButton(
            row, text="📎 Paste After", width=100, height=24, command=lambda i=index: self._on_paste_clicked(i),
            state="normal" if self._clipboard.has_item() else "disabled",
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(side="left")

    def _replace_item(self, index: int, new_item) -> None:
        items = list(self._timeline.items)
        items[index] = new_item
        self._commit(dataclasses.replace(self._timeline, items=tuple(items)))

    def _move_item(self, index: int, direction: int) -> None:
        items = list(self._timeline.items)
        target = index + direction
        if not (0 <= target < len(items)):
            return
        items[index], items[target] = items[target], items[index]
        self._commit(dataclasses.replace(self._timeline, items=tuple(items)))

    def _remove_item(self, index: int) -> None:
        items = list(self._timeline.items)
        del items[index]
        self._commit(dataclasses.replace(self._timeline, items=tuple(items)))

    def _on_aspect_changed(self) -> None:
        self._commit(dataclasses.replace(self._timeline, aspect_ratio=self._aspect_dropdown.get()))


_next_clip_id = 0


def _new_clip_id() -> str:
    """A simple, process-local incrementing id - unique enough within
    one running GUI session (the only scope a Timeline's own clip_id
    values ever need to be unique within, since they're never compared
    across different projects/sessions)."""
    global _next_clip_id
    _next_clip_id += 1
    return f"clip-{_next_clip_id}"
