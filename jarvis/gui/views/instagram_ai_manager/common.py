"""Shared UI building blocks for the Content Studio tools (Reel Ideas,
Hook Generator, Caption Generator, CTA Generator, Hashtag Assistant,
Story Builder, Weekly Content Plan) - a labeled text input row, a
loading/empty/error state label, and a copy-to-clipboard button. Pure
UI, no calls into jarvis.instagram_ai_manager - each tool module wires
these to its own generate/save logic.
"""

from __future__ import annotations

from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.widgets import Card


class LabeledEntry(ctk.CTkFrame):
    """A label above a single-line text entry - the "Topic:"/"Goal:"
    style input the module's brief asks for repeatedly."""

    def __init__(self, master, label: str, *, placeholder: str = "", **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        ctk.CTkLabel(
            self, text=label, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        self.entry = ctk.CTkEntry(self, placeholder_text=placeholder)
        self.entry.pack(fill="x")

    def get(self) -> str:
        return self.entry.get().strip()

    def set(self, value: str) -> None:
        self.entry.delete(0, "end")
        self.entry.insert(0, value)


class LabeledDropdown(ctk.CTkFrame):
    """A label above a dropdown (CTkOptionMenu) - used for tone/category
    selections (e.g. caption tone, difficulty)."""

    def __init__(self, master, label: str, values: tuple[str, ...], **kwargs) -> None:
        super().__init__(master, fg_color="transparent", **kwargs)
        ctk.CTkLabel(
            self, text=label, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
            text_color=theme.TEXT_SECONDARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))
        self._var = ctk.StringVar(value=values[0] if values else "")
        self.dropdown = ctk.CTkOptionMenu(
            self, values=list(values), variable=self._var,
            fg_color=theme.BG_CARD, button_color=theme.ACCENT_PRIMARY,
            button_hover_color=theme.ACCENT_PRIMARY_HOVER,
        )
        self.dropdown.pack(fill="x")

    def get(self) -> str:
        return self._var.get()


class GeneratorActionBar(ctk.CTkFrame):
    """The [Generate]/[Regenerate]/[Save]/[Copy] button row every
    Content Studio tool has, per the module's brief. `on_generate` is
    required; the others are optional (a tool without a meaningful
    "Copy" action, for instance, simply doesn't pass one) - buttons for
    omitted callbacks aren't created at all, rather than shown disabled,
    to avoid a UI trying to explain "why is this greyed out" per
    element."""

    def __init__(
        self, master, *, on_generate: Callable[[], None],
        on_regenerate: Callable[[], None] | None = None,
        on_save: Callable[[], None] | None = None,
        on_copy: Callable[[], None] | None = None,
        generate_label: str = "Generate",
    ) -> None:
        super().__init__(master, fg_color="transparent")
        self.generate_button = ctk.CTkButton(self, text=generate_label, command=on_generate)
        self.generate_button.pack(side="left", padx=(0, theme.SPACE_SM))

        self.regenerate_button = None
        if on_regenerate is not None:
            self.regenerate_button = ctk.CTkButton(
                self, text="Regenerate", command=on_regenerate,
                fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
                border_color=theme.BORDER_SUBTLE,
            )
            self.regenerate_button.pack(side="left", padx=(0, theme.SPACE_SM))

        self.save_button = None
        if on_save is not None:
            self.save_button = ctk.CTkButton(
                self, text="Save", command=on_save,
                fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
                border_color=theme.BORDER_SUBTLE,
            )
            self.save_button.pack(side="left", padx=(0, theme.SPACE_SM))

        self.copy_button = None
        if on_copy is not None:
            self.copy_button = ctk.CTkButton(
                self, text="Copy", command=on_copy,
                fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1,
                border_color=theme.BORDER_SUBTLE,
            )
            self.copy_button.pack(side="left")

    def set_busy(self, busy: bool) -> None:
        """Disables the Generate/Regenerate buttons while a generation
        request is in flight, so a person can't fire a second overlapping
        request by clicking again - Save/Copy stay enabled since they act
        on whatever result is already shown, independent of a new
        generation being in progress."""
        state = "disabled" if busy else "normal"
        self.generate_button.configure(state=state)
        if self.regenerate_button is not None:
            self.regenerate_button.configure(state=state)


def copy_to_clipboard(widget, text: str) -> None:
    """Copies `text` to the system clipboard via Tkinter's own
    clipboard_clear()/clipboard_append() - `widget` is any widget
    belonging to the same Tk root (clipboard ownership is per-root, not
    per-widget). No jarvis.instagram_ai_manager involvement - a pure
    Tkinter mechanism."""
    widget.clipboard_clear()
    widget.clipboard_append(text)


def status_label(master, text: str, *, kind: str = "muted") -> ctk.CTkLabel:
    """A one-line status message for a generator panel: 'loading',
    'error', or 'muted' (empty-state/help text) - kind picks the text
    color. Not a persistent widget the caller keeps updating in place;
    callers destroy and recreate this label as state changes (simpler
    than tracking a single label's .configure() calls across every
    tool, given how small these labels are)."""
    color = {
        "loading": theme.ACCENT_PRIMARY, "error": theme.DANGER, "muted": theme.TEXT_MUTED,
    }.get(kind, theme.TEXT_MUTED)
    return ctk.CTkLabel(
        master, text=text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
        text_color=color, anchor="w",
    )


class ResultCard(Card):
    """A single generated item's display card (one Reel idea, one hook
    category, etc.) - a title/heading plus a scrollable text body.
    Content Studio tools build a list of these inside their results
    container after a successful generation."""

    def __init__(self, master, title: str, body_lines: list[str], **kwargs) -> None:
        super().__init__(master, **kwargs)
        ctk.CTkLabel(
            self, text=title, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_BODY, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w", justify="left", wraplength=560,
        ).pack(anchor="w", padx=theme.SPACE_MD, pady=(theme.SPACE_MD, theme.SPACE_XS))
        for line in body_lines:
            ctk.CTkLabel(
                self, text=line, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL),
                text_color=theme.TEXT_SECONDARY, anchor="w", justify="left", wraplength=560,
            ).pack(anchor="w", padx=theme.SPACE_MD, pady=(0, theme.SPACE_XS))
        # bottom padding
        ctk.CTkFrame(self, fg_color="transparent", height=theme.SPACE_SM).pack()
