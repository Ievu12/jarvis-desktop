"""Settings panel for whichever text or sticker is selected in the
interactive preview, or clip/photo selected on the track timeline
(right of the video window). Every control applies
immediately: sliders and typing update the preview on each change, and
the edit is committed (saved, synced into the Text/Stickers panels)
once the person stops for a moment.

Like every Video Editor panel it owns no state of its own - it shows
the element the dashboard gives it and reports edits back through
`on_element_edited(kind, index, new_element, final)`."""

from __future__ import annotations

import dataclasses
from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.views.video_editor.common import LabeledDropdown
from jarvis.gui.widgets import Card
from jarvis.video_editor.stickers import STICKER_ANIMATION_CHOICES, StickerInstance
from jarvis.video_editor.text_overlay import ROTATABLE_TEXT_ANIMATIONS, TEXT_ANIMATION_CHOICES, TextOverlay
from jarvis.video_editor.timeline import TimelineClip, TimelineStill

_COMMIT_DELAY_MS = 500
_SWATCHES = ("white", "black", "#FFD700", "#FF6B9D", "#7FDBFF", "#B8F2A0", "#C9A7FF", "#FF7A45")


class ElementInspectorPanel(ctk.CTkFrame):
    def __init__(
        self, master, *,
        on_element_edited: Callable[[str, int, object, bool], None],
        on_delete_requested: Callable[[str, int], None],
        on_duplicate_requested: Callable[[str, int], None],
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_element_edited = on_element_edited
        self._on_delete_requested = on_delete_requested
        self._on_duplicate_requested = on_duplicate_requested
        self._kind: str | None = None
        self._index = -1
        self._element: TextOverlay | StickerInstance | None = None
        self._commit_after_id: str | None = None
        self._refreshing = False
        self._value_setters: list[Callable[[object], None]] = []

        card = Card(self)
        card.pack(fill="both", expand=True)
        self._inner = ctk.CTkFrame(card, fg_color="transparent")
        self._inner.pack(fill="both", expand=True, padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        ctk.CTkLabel(
            self._inner, text="⚙️ NUSTATYMAI",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        self._body = ctk.CTkFrame(self._inner, fg_color="transparent")
        self._body.pack(fill="both", expand=True)
        self.show_nothing()

    # --- dashboard-facing API ----------------------------------------------------------------

    @property
    def shown(self) -> tuple[str, int] | None:
        return (self._kind, self._index) if self._kind is not None else None

    def show_nothing(self) -> None:
        self._flush_commit()
        self._kind, self._index, self._element = None, -1, None
        self._clear_body()
        ctk.CTkLabel(
            self._body, text="Paspauskite tekstą ar lipduką peržiūros lange arba bet kurį elementą laiko juostoje - čia atsiras jo nustatymai.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_MUTED, anchor="w", wraplength=240, justify="left",
        ).pack(anchor="w")

    def show_element(
        self, kind: str, index: int, element: TextOverlay | StickerInstance | TimelineClip | TimelineStill,
        *, title: str = "",
    ) -> None:
        """`kind` is "text", "sticker" or "clip" (a video clip or photo
        on the timeline, `title` being its file name)."""
        if (kind, index) == (self._kind, self._index) and type(element) is type(self._element):
            self.refresh_values(element)
            return
        self._flush_commit()
        self._kind, self._index, self._element = kind, index, element
        self._clear_body()
        if isinstance(element, TextOverlay):
            self._build_text_controls(element)
        elif isinstance(element, StickerInstance):
            self._build_sticker_controls(element)
        else:
            self._build_clip_controls(element, title)

    def refresh_values(self, element: TextOverlay | StickerInstance) -> None:
        """Updates the shown values after an edit made elsewhere (e.g.
        dragging in the preview) without firing any callback."""
        self._element = element
        self._refreshing = True
        try:
            for setter in self._value_setters:
                setter(element)
        finally:
            self._refreshing = False

    # --- building --------------------------------------------------------------------------

    def _clear_body(self) -> None:
        self._value_setters = []
        for child in self._body.winfo_children():
            child.destroy()

    def _label(self, text: str) -> None:
        ctk.CTkLabel(
            self._body, text=text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(theme.SPACE_SM, 0))

    def _slider(self, label: str, field: str, low: float, high: float, *, steps: int, fmt: str, cast=float) -> ctk.CTkSlider:
        self._label(label)
        row = ctk.CTkFrame(self._body, fg_color="transparent")
        row.pack(fill="x")
        value_label = ctk.CTkLabel(row, text="", width=48, anchor="e",
                                   font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION))

        def on_move(value: float) -> None:
            value_label.configure(text=fmt.format(cast(value)))
            if not self._refreshing:
                self._edit(**{field: cast(value)})

        slider = ctk.CTkSlider(row, from_=low, to=high, number_of_steps=steps, command=on_move, width=180)
        slider.pack(side="left", fill="x", expand=True)
        value_label.pack(side="left", padx=(theme.SPACE_XS, 0))

        def set_value(element) -> None:
            value = getattr(element, field)
            slider.set(max(low, min(high, value)))
            value_label.configure(text=fmt.format(value))

        set_value(self._element)
        self._value_setters.append(set_value)
        return slider

    def _entry(self, parent, field: str, *, width: int, parse, fmt=str, live: bool = False) -> ctk.CTkEntry:
        entry = ctk.CTkEntry(parent, width=width)

        def on_change(_event=None, *, final: bool = True) -> None:
            try:
                value = parse(entry.get())
            except ValueError:
                return
            if value != getattr(self._element, field):
                self._edit(final=final, **{field: value})

        entry.bind("<Return>", on_change)
        entry.bind("<FocusOut>", on_change)
        if live:
            entry.bind("<KeyRelease>", lambda e: on_change(e, final=False))

        def set_value(element) -> None:
            text = fmt(getattr(element, field))
            if entry.get() != text and entry.focus_get() is not entry:
                entry.delete(0, "end")
                entry.insert(0, text)

        set_value(self._element)
        self._value_setters.append(set_value)
        return entry

    def _timing_row(self) -> None:
        self._label("Rodoma (s): nuo - iki")
        row = ctk.CTkFrame(self._body, fg_color="transparent")
        row.pack(fill="x")
        self._entry(row, "start_seconds", width=70, parse=float, fmt=lambda v: f"{v:g}").pack(side="left")
        ctk.CTkLabel(row, text=" - ").pack(side="left")
        self._entry(row, "end_seconds", width=70, parse=float, fmt=lambda v: f"{v:g}").pack(side="left")

    def _animation_dropdown(self, choices: tuple[str, ...]) -> LabeledDropdown:
        dropdown = LabeledDropdown(self._body, "Animacija:", choices)
        dropdown.pack(fill="x", pady=(theme.SPACE_SM, 0))

        def on_pick(value: str) -> None:
            if self._refreshing:
                return
            changes = {"animation": value}
            # Rotation only exports with a plain/fade text animation (see
            # text_overlay.ROTATABLE_TEXT_ANIMATIONS) - picking another
            # one straightens the text rather than making it invalid.
            if isinstance(self._element, TextOverlay) and value not in ROTATABLE_TEXT_ANIMATIONS:
                changes["rotation_degrees"] = 0.0
            self._edit(final=True, **changes)

        dropdown.dropdown.configure(command=on_pick)
        dropdown.set(self._element.animation)
        self._value_setters.append(lambda element: dropdown.set(element.animation))
        return dropdown

    def _action_buttons(self) -> None:
        row = ctk.CTkFrame(self._body, fg_color="transparent")
        row.pack(fill="x", pady=(theme.SPACE_MD, 0))
        ctk.CTkButton(
            row, text="📋 Kopijuoti", width=100, height=28,
            command=lambda: self._on_duplicate_requested(self._kind, self._index),
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(0, theme.SPACE_XS))
        ctk.CTkButton(
            row, text="🗑 Ištrinti", width=100, height=28, command=self._on_delete_clicked,
            fg_color=theme.BG_CARD, hover_color=theme.DANGER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(side="left")

    def _build_text_controls(self, overlay: TextOverlay) -> None:
        self._label("Tekstas")
        self._entry(self._body, "text", width=240, parse=_non_empty, live=True).pack(fill="x")
        self._slider("Šrifto dydis", "font_size", 8, 300, steps=292, fmt="{:d}", cast=lambda v: int(round(v)))

        self._label("Spalva")
        color_row = ctk.CTkFrame(self._body, fg_color="transparent")
        color_row.pack(fill="x")
        self._entry(color_row, "color", width=90, parse=_non_empty).pack(side="left", padx=(0, theme.SPACE_XS))
        for color in _SWATCHES:
            ctk.CTkButton(
                color_row, text="", width=18, height=18, fg_color=_swatch_hex(color), hover_color=_swatch_hex(color),
                border_width=1, border_color=theme.BORDER_SUBTLE,
                command=lambda c=color: self._edit(final=True, color=c),
            ).pack(side="left", padx=(0, 2))

        rotation_slider = self._slider("Pasukimas (°)", "rotation_degrees", -180, 180, steps=360, fmt="{:.0f}")
        rotation_note = ctk.CTkLabel(
            self._body, text="", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w", wraplength=240, justify="left",
        )
        rotation_note.pack(anchor="w")

        def set_rotation_state(element: TextOverlay) -> None:
            rotatable = element.animation in ROTATABLE_TEXT_ANIMATIONS
            rotation_slider.configure(state="normal" if rotatable else "disabled")
            rotation_note.configure(text="" if rotatable else "Pasukti galima tik su „none“ arba „fade“ animacija.")

        set_rotation_state(overlay)
        self._value_setters.append(set_rotation_state)
        self._animation_dropdown(TEXT_ANIMATION_CHOICES)
        self._timing_row()
        self._action_buttons()

    def _build_sticker_controls(self, sticker: StickerInstance) -> None:
        name = sticker.custom_path.name if sticker.custom_path is not None else sticker.shape
        ctk.CTkLabel(
            self._body, text=f"🖼️ {name}", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w")
        self._slider("Dydis", "size_fraction", 0.02, 1.0, steps=98, fmt="{:.2f}")
        self._slider("Pasukimas (°)", "rotation_degrees", -180, 180, steps=360, fmt="{:.0f}")
        self._slider("Permatomumas", "opacity", 0.0, 1.0, steps=100, fmt="{:.2f}")
        self._animation_dropdown(STICKER_ANIMATION_CHOICES)
        self._timing_row()
        self._action_buttons()

    def _build_clip_controls(self, item: TimelineClip | TimelineStill, title: str) -> None:
        ctk.CTkLabel(
            self._body, text=f"🎬 {title}" if title else "🎬 Klipas",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w", wraplength=240, justify="left",
        ).pack(anchor="w")
        if isinstance(item, TimelineStill):
            self._slider("Trukmė (s)", "display_duration_seconds", 0.5, 30, steps=295, fmt="{:.1f}",
                         cast=lambda v: round(v, 1))
        else:
            self._slider("Greitis (x)", "speed_factor", 0.25, 4.0, steps=75, fmt="{:.2f}",
                         cast=lambda v: round(v, 2))
            self._label("Iškarpa iš originalo (s): nuo - iki")
            row = ctk.CTkFrame(self._body, fg_color="transparent")
            row.pack(fill="x")
            self._entry(row, "source_in_seconds", width=70, parse=float, fmt=lambda v: f"{v:g}").pack(side="left")
            ctk.CTkLabel(row, text=" - ").pack(side="left")
            self._entry(row, "source_out_seconds", width=70, parse=float, fmt=lambda v: f"{v:g}").pack(side="left")
        ctk.CTkLabel(
            self._body, text="Kraštus galite tempti ir laiko juostoje. Efektai ir perėjimai: kairėje, 🎨 Filtrai.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w", wraplength=240, justify="left",
        ).pack(anchor="w", pady=(theme.SPACE_SM, 0))
        self._action_buttons()

    # --- editing -----------------------------------------------------------------------------

    def _edit(self, *, final: bool = False, **changes) -> None:
        if self._element is None or self._kind is None:
            return
        self._element = dataclasses.replace(self._element, **changes)
        self._on_element_edited(self._kind, self._index, self._element, final)
        if final:
            self._cancel_commit()
        else:
            self._cancel_commit()
            self._commit_after_id = self.after(_COMMIT_DELAY_MS, self._commit)

    def _commit(self) -> None:
        self._commit_after_id = None
        if self._element is not None and self._kind is not None:
            self._on_element_edited(self._kind, self._index, self._element, True)

    def _cancel_commit(self) -> None:
        if self._commit_after_id is not None:
            self.after_cancel(self._commit_after_id)
            self._commit_after_id = None

    def _flush_commit(self) -> None:
        """A pending (debounced) commit for the element being left is
        sent now, so switching selection never drops an edit."""
        if self._commit_after_id is not None:
            self._cancel_commit()
            self._commit()

    def _on_delete_clicked(self) -> None:
        if self._kind is None:
            return
        kind, index = self._kind, self._index
        self._cancel_commit()
        self._kind, self._index, self._element = None, -1, None
        self._on_delete_requested(kind, index)


def _non_empty(value: str) -> str:
    if not value.strip():
        raise ValueError("empty")
    return value


def _swatch_hex(color: str) -> str:
    return {"white": "#FFFFFF", "black": "#000000"}.get(color, color)
