"""Text overlay panel: add/edit/remove free-typed text elements (titles,
callouts) - distinct from CaptionsPanel (transcribed speech only). Each
overlay gets its own row with text/timing/position/font/color controls,
mirroring MusicPanel's own add-then-configure flow but supporting
MULTIPLE entries at once (a music track is singular; a project
routinely wants several titles/callouts at different times)."""

from __future__ import annotations

import dataclasses
from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.views.video_editor.common import LabeledDropdown, status_label
from jarvis.gui.widgets import Card
from jarvis.video_editor.text_overlay import TEXT_ANIMATION_CHOICES, TEXT_SLIDE_DIRECTION_CHOICES, TextOverlay
from jarvis.video_editor.text_templates import COLOR_PALETTES, TEXT_TEMPLATE_NAMES, get_color_palette, get_text_template


class TextOverlayPanel(ctk.CTkFrame):
    def __init__(self, master, *, on_overlays_changed: Callable[[list[TextOverlay]], None], **kwargs) -> None:
        """`on_overlays_changed(overlays)` fires with the full, current
        list of valid TextOverlay entries whenever any row changes - an
        invalid row (caught by its own validate()) is excluded from the
        emitted list rather than blocking every other row's own valid
        overlay, same "one bad entry doesn't break everything else"
        spirit as MusicPanel's own single-track validation, generalized
        to a list."""
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_overlays_changed = on_overlays_changed
        self._overlays: list[TextOverlay] = []

        card = Card(self)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        ctk.CTkLabel(
            inner, text="🔤 TEXT OVERLAYS",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            inner, text="Add titles or callouts anywhere on screen, shown during a chosen time window.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w", wraplength=600, justify="left",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        self._rows_container = ctk.CTkFrame(inner, fg_color="transparent")
        self._rows_container.pack(fill="x")

        ctk.CTkButton(inner, text="➕ Add Text", command=self._on_add_clicked, width=140).pack(
            anchor="w", pady=(theme.SPACE_SM, 0),
        )

    def _on_add_clicked(self) -> None:
        self.add_overlay(TextOverlay(text="New text", start_seconds=0.0, end_seconds=2.0))

    # --- owning-dashboard API (edits made in the interactive preview) ------------------------

    def add_overlay(self, overlay: TextOverlay) -> None:
        self._overlays.append(overlay)
        self._render()
        self._emit()

    def set_overlays(self, overlays: list[TextOverlay]) -> None:
        """Replaces every row (e.g. when a saved project is reopened)
        without emitting - the caller already holds this list."""
        self._overlays = list(overlays)
        self._render()

    def replace_overlay(self, old: TextOverlay, new: TextOverlay) -> None:
        """Swaps the first row equal to `old` for `new` without emitting
        - used after the person moved/resized/restyled `old` in the
        preview, where the dashboard already applied the change."""
        for index, overlay in enumerate(self._overlays):
            if overlay == old:
                self._overlays[index] = new
                self._render()
                return

    def remove_overlay(self, overlay: TextOverlay) -> None:
        if overlay in self._overlays:
            self._overlays.remove(overlay)
            self._render()
            self._emit()

    def _render(self) -> None:
        for child in self._rows_container.winfo_children():
            child.destroy()
        for index, overlay in enumerate(self._overlays):
            self._render_row(index, overlay)

    def _render_row(self, index: int, overlay: TextOverlay) -> None:
        row = Card(self._rows_container)
        row.pack(fill="x", pady=(0, theme.SPACE_XS))
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_SM, pady=theme.SPACE_SM)

        text_entry = ctk.CTkEntry(inner, width=200)
        text_entry.insert(0, overlay.text)
        text_entry.pack(fill="x", pady=(0, theme.SPACE_XS))

        row1 = ctk.CTkFrame(inner, fg_color="transparent")
        row1.pack(fill="x", pady=(0, theme.SPACE_XS))
        start_entry = ctk.CTkEntry(row1, width=60)
        start_entry.insert(0, f"{overlay.start_seconds:g}")
        ctk.CTkLabel(row1, text="Start (s):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        start_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        end_entry = ctk.CTkEntry(row1, width=60)
        end_entry.insert(0, f"{overlay.end_seconds:g}")
        ctk.CTkLabel(row1, text="End (s):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        end_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        size_entry = ctk.CTkEntry(row1, width=55)
        size_entry.insert(0, str(overlay.font_size))
        ctk.CTkLabel(row1, text="Size:", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        size_entry.pack(side="left")

        row2 = ctk.CTkFrame(inner, fg_color="transparent")
        row2.pack(fill="x", pady=(0, theme.SPACE_XS))
        x_entry = ctk.CTkEntry(row2, width=55)
        x_entry.insert(0, f"{overlay.x_fraction:g}")
        ctk.CTkLabel(row2, text="X (0-1):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        x_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        y_entry = ctk.CTkEntry(row2, width=55)
        y_entry.insert(0, f"{overlay.y_fraction:g}")
        ctk.CTkLabel(row2, text="Y (0-1):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        y_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        color_entry = ctk.CTkEntry(row2, width=70)
        color_entry.insert(0, overlay.color)
        ctk.CTkLabel(row2, text="Color:", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        color_entry.pack(side="left")

        row3 = ctk.CTkFrame(inner, fg_color="transparent")
        row3.pack(fill="x", pady=(0, theme.SPACE_XS))
        anim_dropdown = LabeledDropdown(row3, "Animation:", TEXT_ANIMATION_CHOICES)
        anim_dropdown.set(overlay.animation)
        anim_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))

        direction_dropdown = LabeledDropdown(row3, "Direction:", TEXT_SLIDE_DIRECTION_CHOICES)
        direction_dropdown.set(overlay.direction)
        direction_dropdown.pack(side="left", padx=(0, theme.SPACE_MD))

        speed_entry = ctk.CTkEntry(row3, width=50)
        speed_entry.insert(0, f"{overlay.speed:g}")
        ctk.CTkLabel(row3, text="Speed:", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        speed_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        intensity_entry = ctk.CTkEntry(row3, width=50)
        intensity_entry.insert(0, f"{overlay.intensity:g}")
        ctk.CTkLabel(row3, text="Intensity:", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        intensity_entry.pack(side="left")

        error_label = status_label(inner, "", kind="error")

        def on_change(_event=None) -> None:
            try:
                new_overlay = TextOverlay(
                    text=text_entry.get(), start_seconds=float(start_entry.get()), end_seconds=float(end_entry.get()),
                    x_fraction=float(x_entry.get()), y_fraction=float(y_entry.get()),
                    font_size=int(size_entry.get()), color=color_entry.get().strip() or "white",
                    animation=anim_dropdown.get(), speed=float(speed_entry.get()), intensity=float(intensity_entry.get()),
                    direction=direction_dropdown.get(), rotation_degrees=overlay.rotation_degrees,
                    fade_seconds=overlay.fade_seconds,
                )
            except ValueError:
                return
            problems = new_overlay.validate()
            if problems:
                error_label.configure(text=f"⚠️ {problems[0]}")
                error_label.pack(anchor="w", pady=(theme.SPACE_XS, 0))
            else:
                error_label.pack_forget()
            self._overlays[index] = new_overlay
            self._emit()

        for entry in (text_entry, start_entry, end_entry, size_entry, x_entry, y_entry, color_entry, speed_entry, intensity_entry):
            entry.bind("<FocusOut>", on_change)
            entry.bind("<Return>", on_change)
        anim_dropdown.dropdown.configure(command=lambda _v: on_change())
        direction_dropdown.dropdown.configure(command=lambda _v: on_change())

        row4 = ctk.CTkFrame(inner, fg_color="transparent")
        row4.pack(fill="x", pady=(0, theme.SPACE_XS))
        template_dropdown = LabeledDropdown(row4, "Template:", TEXT_TEMPLATE_NAMES)
        template_dropdown.pack(side="left", padx=(0, theme.SPACE_SM))

        def on_apply_template() -> None:
            template = get_text_template(template_dropdown.get())
            if template is None:
                return
            size_entry.delete(0, "end")
            size_entry.insert(0, str(template.font_size))
            color_entry.delete(0, "end")
            color_entry.insert(0, template.color)
            anim_dropdown.set(template.animation)
            direction_dropdown.set(template.direction)
            speed_entry.delete(0, "end")
            speed_entry.insert(0, f"{template.speed:g}")
            intensity_entry.delete(0, "end")
            intensity_entry.insert(0, f"{template.intensity:g}")
            on_change()

        ctk.CTkButton(row4, text="✨ Apply", width=70, height=24, command=on_apply_template).pack(
            side="left", padx=(0, theme.SPACE_MD),
        )

        palette_dropdown = LabeledDropdown(row4, "Palette:", tuple(p.name for p in COLOR_PALETTES))
        palette_dropdown.pack(side="left", padx=(0, theme.SPACE_SM))

        def on_pick_palette_color(color: str) -> None:
            color_entry.delete(0, "end")
            color_entry.insert(0, color)
            on_change()

        def on_show_palette() -> None:
            palette = get_color_palette(palette_dropdown.get())
            if palette is None:
                return
            for swatch in list(swatches_row.winfo_children()):
                swatch.destroy()
            for color in palette.colors:
                ctk.CTkButton(
                    swatches_row, text="", width=22, height=22, fg_color=color, hover_color=color,
                    border_width=1, border_color=theme.BORDER_SUBTLE,
                    command=lambda c=color: on_pick_palette_color(c),
                ).pack(side="left", padx=(0, 4))

        ctk.CTkButton(row4, text="🎨 Show", width=70, height=24, command=on_show_palette).pack(side="left")

        swatches_row = ctk.CTkFrame(inner, fg_color="transparent")
        swatches_row.pack(fill="x", pady=(0, theme.SPACE_XS))

        ctk.CTkButton(
            inner, text="🗑 Remove", width=90, height=24, command=lambda i=index: self._remove(i),
            fg_color=theme.BG_CARD, hover_color=theme.DANGER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(anchor="w", pady=(theme.SPACE_XS, 0))

    def _remove(self, index: int) -> None:
        del self._overlays[index]
        self._render()
        self._emit()

    def _emit(self) -> None:
        valid = [o for o in self._overlays if not o.validate()]
        self._on_overlays_changed(valid)
