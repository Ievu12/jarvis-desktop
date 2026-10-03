"""The library's 🎨 Filtrai and ⇄ Perėjimai panels: one-click filter
looks (thumbnails rendered from the clip itself) with an intensity
slider, and transitions with a duration. Both work on ONE target clip
the dashboard chooses (the selected clip, else the one at the
playhead) and can apply the same choice to every clip.

Like every Video Editor panel they own no project state - the
dashboard shows the target with `show_target(...)` and applies what
the callbacks report (through its undo history)."""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.widgets import Card
from jarvis.video_editor.effects import LOOK_CHOICES, LOOK_LABELS, EffectSpec
from jarvis.video_editor.timeline import TRANSITION_KIND_CHOICES, TransitionSpec
from jarvis.video_editor.track_layout import MIN_TRANSITION_SECONDS, TRANSITION_LABELS

THUMB_SIZE = 72
_COMMIT_DELAY_MS = 400
_POLL_MS = 60
_TRANSITION_ICONS = {"cut": "✂", "fade": "◐", "dissolve": "░", "slide_left": "⇠", "slide_right": "⇢"}


def _title(parent, text: str) -> None:
    ctk.CTkLabel(
        parent, text=text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
        text_color=theme.ACCENT_PRIMARY, anchor="w",
    ).pack(anchor="w", pady=(0, theme.SPACE_XS))


def _note(parent, text: str = "") -> ctk.CTkLabel:
    label = ctk.CTkLabel(
        parent, text=text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
        text_color=theme.TEXT_MUTED, anchor="w", wraplength=330, justify="left",
    )
    label.pack(anchor="w", pady=(0, theme.SPACE_XS))
    return label


class _DebouncedSlider(ctk.CTkFrame):
    """A labeled slider reporting `on_change(value, final)`: live on
    every move, final once the person stops for a moment."""

    def __init__(self, master, label: str, low: float, high: float, *, steps: int, fmt: str,
                 on_change: Callable[[float, bool], None]) -> None:
        super().__init__(master, fg_color="transparent")
        self._fmt = fmt
        self._on_change = on_change
        self._after_id: str | None = None
        self._quiet = False
        ctk.CTkLabel(
            self, text=label, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w")
        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(fill="x")
        self.slider = ctk.CTkSlider(row, from_=low, to=high, number_of_steps=steps, command=self._moved)
        self.slider.pack(side="left", fill="x", expand=True)
        self._value_label = ctk.CTkLabel(row, text="", width=48, anchor="e")
        self._value_label.pack(side="left")

    def set(self, value: float) -> None:
        self._quiet = True
        try:
            self.slider.set(value)
            self._value_label.configure(text=self._fmt.format(value))
        finally:
            self._quiet = False

    def configure_range(self, low: float, high: float, steps: int) -> None:
        self.slider.configure(from_=low, to=high, number_of_steps=max(1, steps))

    def set_enabled(self, enabled: bool) -> None:
        self.slider.configure(state="normal" if enabled else "disabled")

    def _moved(self, value: float) -> None:
        self._value_label.configure(text=self._fmt.format(value))
        if self._quiet:
            return
        self._on_change(value, False)
        if self._after_id is not None:
            self.after_cancel(self._after_id)
        self._after_id = self.after(_COMMIT_DELAY_MS, lambda: self._commit(value))

    def _commit(self, value: float) -> None:
        self._after_id = None
        self._on_change(value, True)


class FiltersPanel(ctk.CTkFrame):
    def __init__(
        self, master, *,
        on_look_chosen: Callable[[str], None],
        on_intensity_changed: Callable[[float, bool], None],
        on_apply_all: Callable[[], None],
        render_previews: Callable[..., dict] | None = None,
        **kwargs,
    ) -> None:
        """`render_previews(image, looks, intensity=)` grades a PIL
        image by each look (playback.render_look_previews); without it
        the look buttons show only their names."""
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_look_chosen = on_look_chosen
        self._on_intensity_changed = on_intensity_changed
        self._on_apply_all = on_apply_all
        self._render_previews = render_previews
        self._effect: EffectSpec | None = None
        self._source: Path | None = None
        self._thumbs: dict[str, ctk.CTkImage] = {}
        self._thumb_cache: dict[Path, dict[str, ctk.CTkImage]] = {}
        self._results: "queue.Queue[tuple[Path, dict]]" = queue.Queue()
        self._polling = False

        card = Card(self)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        _title(inner, "🎨 FILTRAI")
        self._target_label = _note(inner)

        self._grid = ctk.CTkFrame(inner, fg_color="transparent")
        self._grid.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._buttons: dict[str, ctk.CTkButton] = {}
        for n, look in enumerate(LOOK_CHOICES):
            button = ctk.CTkButton(
                self._grid, text=LOOK_LABELS[look], width=THUMB_SIZE + 8, height=THUMB_SIZE + 26,
                compound="top", fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER,
                border_width=2, border_color=theme.BG_CARD,
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                command=lambda lk=look: self._on_look_clicked(lk),
            )
            button.grid(row=n // 4, column=n % 4, padx=(0, theme.SPACE_XS), pady=(0, theme.SPACE_XS))
            self._buttons[look] = button

        self._intensity = _DebouncedSlider(
            inner, "Intensyvumas (%)", 0, 100, steps=20, fmt="{:.0f}", on_change=self._on_intensity_moved,
        )
        self._intensity.pack(fill="x")
        self._apply_all_button = ctk.CTkButton(
            inner, text="Taikyti visiems klipams", height=28, command=lambda: self._on_apply_all(),
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        )
        self._apply_all_button.pack(anchor="w", pady=(theme.SPACE_SM, 0))
        _note(inner, "Judesys, išblukimas, ryškumas, kontrastas ir sodrumas: kiekvieno klipo eilutėje žemiau.")
        self.show_target(None, None, None)

    # --- dashboard-facing API ----------------------------------------------------------------

    @property
    def look_buttons(self) -> dict[str, ctk.CTkButton]:
        return self._buttons

    def show_target(self, title: str | None, effect: EffectSpec | None, source: Path | None) -> None:
        """`title`/`effect` of the clip the panel applies to (None when
        the timeline is empty); `source` is an image of that clip for
        the look thumbnails."""
        self._effect = effect
        enabled = effect is not None
        self._target_label.configure(
            text=f"Taikoma klipui: {title}" if enabled else "Pridėkite klipą į laiko juostą, kad galėtumėte taikyti filtrą.",
        )
        for look, button in self._buttons.items():
            selected = enabled and effect.look == look
            button.configure(
                state="normal" if enabled else "disabled",
                border_color=theme.ACCENT_PRIMARY if selected else theme.BG_CARD,
            )
        self._intensity.set(round((effect.look_intensity if enabled else 1.0) * 100))
        self._intensity.set_enabled(enabled and effect.look != "none")
        self._apply_all_button.configure(state="normal" if enabled else "disabled")
        if source != self._source:
            self._source = source
            self._show_thumbs(self._thumb_cache.get(source, {}) if source is not None else {})
            if source is not None and source not in self._thumb_cache:
                self._start_render(source)

    # --- thumbnails --------------------------------------------------------------------------

    def _show_thumbs(self, thumbs: dict[str, ctk.CTkImage]) -> None:
        self._thumbs = thumbs
        for look, button in self._buttons.items():
            button.configure(image=thumbs.get(look))

    def _start_render(self, source: Path) -> None:
        if self._render_previews is None:
            return

        def work() -> None:
            try:
                from PIL import Image, ImageOps

                with Image.open(source) as image:
                    small = ImageOps.fit(image.convert("RGB"), (THUMB_SIZE, THUMB_SIZE))
                previews = self._render_previews(small, LOOK_CHOICES, intensity=1.0)
            except Exception:
                previews = {}
            self._results.put((source, previews))

        threading.Thread(target=work, daemon=True).start()
        if not self._polling:
            self._polling = True
            self.after(_POLL_MS, self._poll)

    def _poll(self) -> None:
        try:
            while True:
                source, previews = self._results.get_nowait()
                thumbs = {
                    look: ctk.CTkImage(light_image=image, dark_image=image, size=(THUMB_SIZE, THUMB_SIZE))
                    for look, image in previews.items()
                }
                self._thumb_cache[source] = thumbs
                if source == self._source:
                    self._show_thumbs(thumbs)
        except queue.Empty:
            pass
        except Exception:
            return  # the widget is gone
        self.after(_POLL_MS, self._poll)

    # --- events ------------------------------------------------------------------------------

    def _on_look_clicked(self, look: str) -> None:
        if self._effect is not None:
            self._on_look_chosen(look)

    def _on_intensity_moved(self, value: float, final: bool) -> None:
        if self._effect is not None:
            self._on_intensity_changed(round(value) / 100, final)


class TransitionsPanel(ctk.CTkFrame):
    def __init__(
        self, master, *,
        on_transition_chosen: Callable[[str], None],
        on_duration_changed: Callable[[float, bool], None],
        on_apply_all: Callable[[], None],
        **kwargs,
    ) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_transition_chosen = on_transition_chosen
        self._on_duration_changed = on_duration_changed
        self._on_apply_all = on_apply_all
        self._transition: TransitionSpec | None = None

        card = Card(self)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)
        _title(inner, "⇄ PERĖJIMAI")
        self._target_label = _note(inner)
        grid = ctk.CTkFrame(inner, fg_color="transparent")
        grid.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._buttons: dict[str, ctk.CTkButton] = {}
        for n, kind in enumerate(TRANSITION_KIND_CHOICES):
            button = ctk.CTkButton(
                grid, text=f"{_TRANSITION_ICONS[kind]}  {TRANSITION_LABELS[kind]}", width=150, height=30, anchor="w",
                fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=2, border_color=theme.BG_CARD,
                command=lambda k=kind: self._on_kind_clicked(k),
            )
            button.grid(row=n // 2, column=n % 2, padx=(0, theme.SPACE_XS), pady=(0, theme.SPACE_XS), sticky="w")
            self._buttons[kind] = button
        self._duration = _DebouncedSlider(
            inner, "Trukmė (s)", MIN_TRANSITION_SECONDS, 2.0, steps=18, fmt="{:.1f}", on_change=self._on_duration_moved,
        )
        self._duration.pack(fill="x")
        self._apply_all_button = ctk.CTkButton(
            inner, text="Taikyti visiems klipams", height=28, command=lambda: self._on_apply_all(),
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        )
        self._apply_all_button.pack(anchor="w", pady=(theme.SPACE_SM, 0))
        _note(inner, "Perėjimas matomas peržiūroje grojant ir laiko juostoje (⇄ ženklas tarp klipų).")
        self.show_target(None, None, None)

    @property
    def kind_buttons(self) -> dict[str, ctk.CTkButton]:
        return self._buttons

    def show_target(self, title: str | None, transition: TransitionSpec | None, limit: float | None) -> None:
        """The transition out of clip `title` into the next one;
        `limit` is the longest one both clips allow (None: there's no
        clip to show, or it's the last one)."""
        usable = transition is not None and limit is not None and limit >= MIN_TRANSITION_SECONDS
        self._transition = transition if usable else None
        if title is None:
            text = "Pridėkite bent du klipus į laiko juostą."
        elif limit is None:
            text = f"„{title}“ yra paskutinis klipas: pasirinkite ankstesnį klipą."
        elif not usable:
            text = f"„{title}“ ir kitas klipas per trumpi perėjimui."
        else:
            text = f"Perėjimas po klipo: {title}"
        self._target_label.configure(text=text)
        for kind, button in self._buttons.items():
            selected = usable and transition.kind == kind
            button.configure(
                state="normal" if usable else "disabled",
                border_color=theme.ACCENT_PRIMARY if selected else theme.BG_CARD,
            )
        if usable:
            steps = int(round((limit - MIN_TRANSITION_SECONDS) * 10))
            self._duration.configure_range(MIN_TRANSITION_SECONDS, max(limit, MIN_TRANSITION_SECONDS + 0.1), steps)
            self._duration.set(transition.duration_seconds if transition.kind != "cut" else min(0.5, limit))
        self._duration.set_enabled(usable and transition.kind != "cut")
        self._apply_all_button.configure(state="normal" if usable else "disabled")

    def duration(self) -> float:
        return round(self._duration.slider.get(), 1)

    def _on_kind_clicked(self, kind: str) -> None:
        if self._transition is not None:
            self._on_transition_chosen(kind)

    def _on_duration_moved(self, value: float, final: bool) -> None:
        if self._transition is not None and self._transition.kind != "cut":
            self._on_duration_changed(round(value, 1), final)
