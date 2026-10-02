"""Stickers/GIF/emoji panel: browse a categorized library (requirement:
"patogią integruotą lipdukų biblioteką su kategorijomis" - a convenient
integrated sticker library with categories), search by name, favorite/
un-favorite a shape, add a built-in shape from the library or upload a
custom PNG/GIF with transparency, each placed sticker with its own
position/size/rotation/opacity/animation/timing - mirrors
TextOverlayPanel's own multi-entry add/edit/remove flow for the PLACED
stickers list (a project can have several stickers at once, unlike
MusicPanel's single track); the library BROWSER above it is a separate
concern (jarvis.video_editor.sticker_library's own favorites/
collections), never confused with the placed-stickers list itself."""

from __future__ import annotations

import dataclasses
import tkinter as tk
from pathlib import Path
from tkinter import filedialog
from typing import Callable

import customtkinter as ctk

from jarvis.gui import theme
from jarvis.gui.views.video_editor.common import LabeledDropdown, status_label
from jarvis.gui.widgets import Card
from jarvis.video_editor import sticker_library as sl
from jarvis.video_editor.stickers import (
    STICKER_ANIMATION_CHOICES,
    STICKER_CATEGORY_CHOICES,
    STICKER_CATEGORY_LABELS,
    STICKER_CATEGORY_SHAPES,
    STICKER_SHAPE_CHOICES,
    StickerInstance,
    render_builtin_sticker,
)

_FILETYPES = (
    ("Image/GIF files", "*.png *.gif *.webp"),
    ("All files", "*.*"),
)

_LIBRARY_CATEGORY_LABELS = ("⭐ Favorites", "🔍 Search results") + tuple(
    STICKER_CATEGORY_LABELS[c] for c in STICKER_CATEGORY_CHOICES
)
_THUMB_SIZE = 56


class StickersPanel(ctk.CTkFrame):
    def __init__(
        self, master, *, on_stickers_changed: Callable[[list[StickerInstance]], None],
        on_shape_dragged: Callable[[str, int, int, bool], None] | None = None, **kwargs,
    ) -> None:
        """`on_stickers_changed(stickers)` fires with the current,
        valid-only sticker list whenever any row changes - same "one bad
        entry doesn't break everything else" validation spirit as
        TextOverlayPanel's own on_overlays_changed.

        `on_shape_dragged(shape, x_root, y_root, dropped)` follows a
        library sticker being dragged out of the grid (e.g. onto the
        preview): `dropped` is False while moving, True on release."""
        super().__init__(master, fg_color="transparent", **kwargs)
        self._on_stickers_changed = on_stickers_changed
        self._on_shape_dragged = on_shape_dragged
        self._drag: dict | None = None
        self._stickers: list[StickerInstance] = []
        self._library_state = sl.load_library()
        self._thumb_cache: dict[str, object] = {}
        self._preview_dir = Path.home() / ".jarvis_sticker_preview_cache"

        card = Card(self)
        card.pack(fill="x")
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_MD, pady=theme.SPACE_MD)

        ctk.CTkLabel(
            inner, text="✨ STICKERS, GIF & EMOJI",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.ACCENT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))
        ctk.CTkLabel(
            inner, text="Browse the sticker library by category, search by name, or upload your own PNG/GIF.",
            font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
            text_color=theme.TEXT_MUTED, anchor="w", wraplength=600, justify="left",
        ).pack(anchor="w", pady=(0, theme.SPACE_SM))

        # --- library browser (search + categories + favorites) ---------------------------
        browser_row = ctk.CTkFrame(inner, fg_color="transparent")
        browser_row.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._search_entry = ctk.CTkEntry(browser_row, width=160, placeholder_text="Search stickers...")
        self._search_entry.pack(side="left", padx=(0, theme.SPACE_SM))
        self._search_entry.bind("<KeyRelease>", lambda _e: self._render_library_grid())

        self._category_dropdown = LabeledDropdown(browser_row, "", _LIBRARY_CATEGORY_LABELS)
        self._category_dropdown.dropdown.configure(command=lambda _v: self._render_library_grid())
        self._category_dropdown.pack(side="left")
        if not self._library_state.favorite_shapes:
            # Nothing favorited yet: open on the first real category, not an empty list.
            self._category_dropdown.set(_LIBRARY_CATEGORY_LABELS[2])
        if on_shape_dragged is not None:
            ctk.CTkLabel(
                inner, text="Tempkite lipduką ant vaizdo peržiūroje: jis atsiras toje vietoje.",
                font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION),
                text_color=theme.TEXT_MUTED, anchor="w",
            ).pack(anchor="w", pady=(0, theme.SPACE_XS))

        self._library_grid = ctk.CTkFrame(inner, fg_color="transparent")
        self._library_grid.pack(fill="x", pady=(0, theme.SPACE_SM))
        self._render_library_grid()

        self._rows_container = ctk.CTkFrame(inner, fg_color="transparent")
        self._rows_container.pack(fill="x")

        buttons_row = ctk.CTkFrame(inner, fg_color="transparent")
        buttons_row.pack(fill="x", pady=(theme.SPACE_SM, 0))
        ctk.CTkButton(buttons_row, text="📁 Upload PNG/GIF", command=self._on_upload_clicked, width=150).pack(
            side="left", padx=(0, theme.SPACE_SM),
        )
        ctk.CTkButton(
            buttons_row, text="💾 Save Current as Collection", command=self._on_save_collection_clicked, width=200,
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(side="left")

        self._collections_container = ctk.CTkFrame(inner, fg_color="transparent")
        self._collections_container.pack(fill="x", pady=(theme.SPACE_SM, 0))
        self._render_collections()

    # --- library browser --------------------------------------------------------------------

    def _current_library_shapes(self) -> list[str]:
        query = self._search_entry.get().strip().lower()
        if query:
            return [s for s in STICKER_SHAPE_CHOICES if query in s.replace("_", " ")]
        label = self._category_dropdown.get()
        if label == "⭐ Favorites":
            return list(self._library_state.favorite_shapes)
        if label == "🔍 Search results":
            return []
        for category, category_label in STICKER_CATEGORY_LABELS.items():
            if category_label == label:
                return list(STICKER_CATEGORY_SHAPES[category])
        return list(STICKER_SHAPE_CHOICES)

    def _get_thumb_image(self, shape: str):
        if shape in self._thumb_cache:
            return self._thumb_cache[shape]
        from PIL import Image

        self._preview_dir.mkdir(parents=True, exist_ok=True)
        thumb_path = self._preview_dir / f"{shape}.png"
        if not thumb_path.is_file():
            try:
                render_builtin_sticker(shape, output_path=thumb_path)
            except Exception:
                return None
        try:
            pil_image = Image.open(thumb_path)
            pil_image.load()
            ctk_image = ctk.CTkImage(light_image=pil_image, dark_image=pil_image, size=(_THUMB_SIZE, _THUMB_SIZE))
        except Exception:
            return None
        self._thumb_cache[shape] = ctk_image
        return ctk_image

    def _render_library_grid(self) -> None:
        for child in self._library_grid.winfo_children():
            child.destroy()
        shapes = self._current_library_shapes()
        if not shapes:
            status_label(self._library_grid, "No stickers match - try a different search or category.", kind="muted").pack(anchor="w")
            return

        row = None
        for i, shape in enumerate(shapes):
            if i % 8 == 0:
                row = ctk.CTkFrame(self._library_grid, fg_color="transparent")
                row.pack(fill="x", pady=(0, theme.SPACE_XS))
            cell = ctk.CTkFrame(row, fg_color="transparent")
            cell.pack(side="left", padx=(0, theme.SPACE_XS))
            thumb = self._get_thumb_image(shape)
            is_favorite = shape in self._library_state.favorite_shapes
            btn = ctk.CTkButton(
                cell, text="" if thumb is not None else shape[:3], image=thumb, width=_THUMB_SIZE, height=_THUMB_SIZE,
                command=lambda s=shape: self._on_library_shape_clicked(s),
                fg_color=theme.ACCENT_PRIMARY if is_favorite else theme.BG_CARD,
            )
            btn.pack()
            if self._on_shape_dragged is not None:
                btn.bind("<ButtonPress-1>", lambda e, s=shape: self._on_drag_press(s, e), add="+")
                btn.bind("<B1-Motion>", self._on_drag_motion, add="+")
                btn.bind("<ButtonRelease-1>", self._on_drag_release, add="+")
            fav_btn = ctk.CTkButton(
                cell, text=("★" if is_favorite else "☆"), width=_THUMB_SIZE, height=16,
                command=lambda s=shape: self._on_toggle_favorite_clicked(s),
                fg_color="transparent", hover_color=theme.BG_CARD_HOVER, text_color=theme.TEXT_SECONDARY,
            )
            fav_btn.pack()

    def _on_library_shape_clicked(self, shape: str) -> None:
        self._stickers.append(StickerInstance(start_seconds=0.0, end_seconds=2.0, shape=shape))
        self._render()
        self._emit()

    # --- dragging a library sticker out (onto the preview) ------------------------------------

    def _on_drag_press(self, shape: str, event) -> None:
        self._drag = {"shape": shape, "x": event.x_root, "y": event.y_root, "ghost": None}

    def _on_drag_motion(self, event) -> None:
        drag = self._drag
        if drag is None:
            return
        if drag["ghost"] is None:
            if abs(event.x_root - drag["x"]) + abs(event.y_root - drag["y"]) < 8:
                return
            ghost = tk.Toplevel(self)
            ghost.overrideredirect(True)
            try:
                ghost.attributes("-topmost", True)
            except tk.TclError:
                pass
            tk.Label(
                ghost, text=f"✨ {drag['shape']}", bg=theme.ACCENT_PRIMARY, fg="white", padx=8, pady=4,
            ).pack()
            drag["ghost"] = ghost
        drag["ghost"].geometry(f"+{event.x_root + 12}+{event.y_root + 12}")
        self._on_shape_dragged(drag["shape"], event.x_root, event.y_root, False)

    def _on_drag_release(self, event) -> None:
        drag, self._drag = self._drag, None
        if drag is None or drag["ghost"] is None:
            return  # a plain click: the button's own command adds the sticker
        drag["ghost"].destroy()
        self._on_shape_dragged(drag["shape"], event.x_root, event.y_root, True)

    def _on_toggle_favorite_clicked(self, shape: str) -> None:
        self._library_state = sl.toggle_favorite(shape)
        self._render_library_grid()

    # --- saved collections -------------------------------------------------------------------

    def _on_save_collection_clicked(self) -> None:
        if not self._stickers:
            return
        dialog = ctk.CTkInputDialog(text="Name this collection:", title="Save Sticker Collection")
        name = dialog.get_input()
        if not name:
            return
        presets = [sl.StickerPreset.from_sticker_instance(s) for s in self._stickers]
        self._library_state = sl.save_collection(name, presets)
        self._render_collections()

    def _render_collections(self) -> None:
        for child in self._collections_container.winfo_children():
            child.destroy()
        if not self._library_state.collections:
            return
        status_label(self._collections_container, "Saved collections:", kind="muted").pack(anchor="w")
        for collection in self._library_state.collections:
            row = ctk.CTkFrame(self._collections_container, fg_color="transparent")
            row.pack(fill="x", pady=(theme.SPACE_XS, 0))
            ctk.CTkButton(
                row, text=f"📂 {collection.name} ({len(collection.presets)})", height=24,
                command=lambda c=collection: self._on_apply_collection_clicked(c),
                fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
            ).pack(side="left", padx=(0, theme.SPACE_XS))
            ctk.CTkButton(
                row, text="🗑", width=30, height=24, command=lambda n=collection.name: self._on_delete_collection_clicked(n),
                fg_color=theme.BG_CARD, hover_color=theme.DANGER, border_width=1, border_color=theme.BORDER_SUBTLE,
            ).pack(side="left")

    def _on_apply_collection_clicked(self, collection: sl.SavedCollection) -> None:
        self.apply_presets(collection.presets)

    def apply_presets(self, presets: tuple[sl.StickerPreset, ...]) -> None:
        """Appends one placed sticker per preset, each a 2-second
        default placement the person can then retime - same behavior
        _on_apply_collection_clicked() above already gives a saved
        collection, now also reachable from the owning dashboard's
        "Apply Reel Template" action
        (jarvis.video_editor.reel_templates.ReelTemplate.sticker_presets)."""
        for preset in presets:
            self._stickers.append(preset.to_sticker_instance(start_seconds=0.0, end_seconds=2.0))
        self._render()
        self._emit()

    def _on_delete_collection_clicked(self, name: str) -> None:
        self._library_state = sl.delete_collection(name)
        self._render_collections()

    # --- owning-dashboard API (edits made in the interactive preview) ------------------------

    def add_sticker(self, sticker: StickerInstance) -> None:
        self._stickers.append(sticker)
        self._render()
        self._emit()

    def set_stickers(self, stickers: list[StickerInstance]) -> None:
        """Replaces every row without emitting (see
        TextOverlayPanel.set_overlays())."""
        self._stickers = list(stickers)
        self._render()

    def replace_sticker(self, old: StickerInstance, new: StickerInstance) -> None:
        for index, sticker in enumerate(self._stickers):
            if sticker == old:
                self._stickers[index] = new
                self._render()
                return

    def remove_sticker(self, sticker: StickerInstance) -> None:
        if sticker in self._stickers:
            self._stickers.remove(sticker)
            self._render()
            self._emit()

    def _on_add_builtin_clicked(self) -> None:
        self._stickers.append(StickerInstance(start_seconds=0.0, end_seconds=2.0, shape="heart"))
        self._render()
        self._emit()

    def _on_upload_clicked(self) -> None:
        path = filedialog.askopenfilename(title="Add sticker image", filetypes=_FILETYPES)
        if not path:
            return
        self._stickers.append(StickerInstance(start_seconds=0.0, end_seconds=2.0, custom_path=Path(path)))
        self._render()
        self._emit()

    def _render(self) -> None:
        for child in self._rows_container.winfo_children():
            child.destroy()
        for index, sticker in enumerate(self._stickers):
            self._render_row(index, sticker)

    def _render_row(self, index: int, sticker: StickerInstance) -> None:
        row = Card(self._rows_container)
        row.pack(fill="x", pady=(0, theme.SPACE_XS))
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.pack(fill="x", padx=theme.SPACE_SM, pady=theme.SPACE_SM)

        label_text = f"🖼️ {sticker.custom_path.name}" if sticker.custom_path is not None else f"Shape: {sticker.shape}"
        ctk.CTkLabel(
            inner, text=label_text, font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_SMALL, weight="bold"),
            text_color=theme.TEXT_PRIMARY, anchor="w",
        ).pack(anchor="w", pady=(0, theme.SPACE_XS))

        shape_dropdown = None
        if sticker.custom_path is None:
            shape_dropdown = LabeledDropdown(inner, "Shape:", STICKER_SHAPE_CHOICES)
            shape_dropdown.set(sticker.shape or "heart")
            shape_dropdown.pack(anchor="w", pady=(0, theme.SPACE_XS))

        row1 = ctk.CTkFrame(inner, fg_color="transparent")
        row1.pack(fill="x", pady=(0, theme.SPACE_XS))
        start_entry = ctk.CTkEntry(row1, width=55)
        start_entry.insert(0, f"{sticker.start_seconds:g}")
        ctk.CTkLabel(row1, text="Start (s):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        start_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        end_entry = ctk.CTkEntry(row1, width=55)
        end_entry.insert(0, f"{sticker.end_seconds:g}")
        ctk.CTkLabel(row1, text="End (s):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        end_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        animation_dropdown = LabeledDropdown(row1, "Animation:", STICKER_ANIMATION_CHOICES)
        animation_dropdown.set(sticker.animation)
        animation_dropdown.pack(side="left")

        row2 = ctk.CTkFrame(inner, fg_color="transparent")
        row2.pack(fill="x", pady=(0, theme.SPACE_XS))
        x_entry = ctk.CTkEntry(row2, width=50)
        x_entry.insert(0, f"{sticker.x_fraction:g}")
        ctk.CTkLabel(row2, text="X (0-1):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        x_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        y_entry = ctk.CTkEntry(row2, width=50)
        y_entry.insert(0, f"{sticker.y_fraction:g}")
        ctk.CTkLabel(row2, text="Y (0-1):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        y_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        size_entry = ctk.CTkEntry(row2, width=50)
        size_entry.insert(0, f"{sticker.size_fraction:g}")
        ctk.CTkLabel(row2, text="Size (0-1):", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        size_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        rotation_entry = ctk.CTkEntry(row2, width=50)
        rotation_entry.insert(0, f"{sticker.rotation_degrees:g}")
        ctk.CTkLabel(row2, text="Rotation°:", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        rotation_entry.pack(side="left", padx=(0, theme.SPACE_MD))

        opacity_entry = ctk.CTkEntry(row2, width=50)
        opacity_entry.insert(0, f"{sticker.opacity:g}")
        ctk.CTkLabel(row2, text="Opacity:", font=ctk.CTkFont(family=theme.FONT_FAMILY_BODY, size=theme.FONT_SIZE_CAPTION)).pack(side="left", padx=(0, theme.SPACE_XS))
        opacity_entry.pack(side="left")

        error_label = status_label(inner, "", kind="error")

        def on_change(_event=None) -> None:
            try:
                new_sticker = dataclasses.replace(
                    sticker,
                    shape=(shape_dropdown.get() if shape_dropdown is not None else None),
                    start_seconds=float(start_entry.get()), end_seconds=float(end_entry.get()),
                    animation=animation_dropdown.get(),
                    x_fraction=float(x_entry.get()), y_fraction=float(y_entry.get()),
                    size_fraction=float(size_entry.get()), rotation_degrees=float(rotation_entry.get()),
                    opacity=float(opacity_entry.get()),
                )
            except ValueError:
                return
            problems = new_sticker.validate()
            if problems:
                error_label.configure(text=f"⚠️ {problems[0]}")
                error_label.pack(anchor="w", pady=(theme.SPACE_XS, 0))
            else:
                error_label.pack_forget()
            self._stickers[index] = new_sticker
            self._emit()

        entries = [start_entry, end_entry, x_entry, y_entry, size_entry, rotation_entry, opacity_entry]
        for entry in entries:
            entry.bind("<FocusOut>", on_change)
            entry.bind("<Return>", on_change)
        animation_dropdown.dropdown.configure(command=lambda _v: on_change())
        if shape_dropdown is not None:
            shape_dropdown.dropdown.configure(command=lambda _v: on_change())

        ctk.CTkButton(
            inner, text="📋 Duplicate", width=100, height=24, command=lambda i=index: self._duplicate(i),
            fg_color=theme.BG_CARD, hover_color=theme.BG_CARD_HOVER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", pady=(theme.SPACE_XS, 0))
        ctk.CTkButton(
            inner, text="🗑 Remove", width=90, height=24, command=lambda i=index: self._remove(i),
            fg_color=theme.BG_CARD, hover_color=theme.DANGER, border_width=1, border_color=theme.BORDER_SUBTLE,
        ).pack(side="left", padx=(theme.SPACE_XS, 0), pady=(theme.SPACE_XS, 0))

    def _duplicate(self, index: int) -> None:
        self._stickers.insert(index + 1, self._stickers[index])
        self._render()
        self._emit()

    def _remove(self, index: int) -> None:
        del self._stickers[index]
        self._render()
        self._emit()

    def _emit(self) -> None:
        valid = [s for s in self._stickers if not s.validate()]
        self._on_stickers_changed(valid)
