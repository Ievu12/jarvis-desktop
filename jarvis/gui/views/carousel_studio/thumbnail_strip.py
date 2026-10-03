"""Slide thumbnail strip: every slide in order, rendered by the same
renderer as the editor. Click opens a slide; drag a thumbnail left or
right and drop it to reorder (a vertical marker shows where it lands).
Thumbnails are cached by slide content, so only changed slides are
re-rendered."""

from __future__ import annotations

import json
import tkinter as tk
from pathlib import Path
from typing import Callable

import customtkinter as ctk
from PIL import Image, ImageTk

from jarvis.carousel_studio.editor import CarouselDocument
from jarvis.carousel_studio.render import render_slide
from jarvis.gui import theme

THUMB_HEIGHT = 118
GAP = 14
PAD = 10
SELECT_COLOR = "#4C8DFF"


class ThumbnailStrip(tk.Frame):
    def __init__(self, master, *, on_open: Callable[[int], None], on_move: Callable[[int, int], None]) -> None:
        super().__init__(master, background=theme.BG_CARD)
        self._on_open = on_open
        self._on_move = on_move
        self.canvas = tk.Canvas(self, height=THUMB_HEIGHT + 2 * PAD + 18, background=theme.BG_CARD, highlightthickness=0)
        self.scroll = ctk.CTkScrollbar(self, orientation="horizontal", command=self.canvas.xview, height=12)
        self.canvas.configure(xscrollcommand=self.scroll.set)
        self.canvas.pack(fill="x", side="top")
        self.scroll.pack(fill="x", side="bottom")
        self.doc: CarouselDocument | None = None
        self.assets_dir: Path | None = None
        self.current = 0
        self._cache: dict[str, tuple[str, ImageTk.PhotoImage]] = {}  # slide id -> (content key, photo)
        self._slots: list[tuple[float, float]] = []  # x ranges of each thumbnail
        self._drag: dict | None = None
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Shift-MouseWheel>", lambda e: self.canvas.xview_scroll(-1 if e.delta > 0 else 1, "units"))

    def set_doc(self, doc: CarouselDocument, assets_dir: Path | None, current: int) -> None:
        self.doc, self.assets_dir, self.current = doc, assets_dir, current
        self.redraw()

    def _photo_for(self, index: int) -> ImageTk.PhotoImage:
        assert self.doc is not None
        project = self.doc.project
        slide = project.slides[index]
        key = json.dumps([slide.to_dict(), project.theme.to_dict(), project.format], sort_keys=True)
        cached = self._cache.get(slide.id)
        if cached and cached[0] == key:
            return cached[1]
        scale = THUMB_HEIGHT / project.size[1]
        # Rendered at 2x and downscaled: tiny text stays crisp and evenly spaced.
        big = render_slide(project, slide, scale=scale * 2, assets_dir=self.assets_dir)
        photo = ImageTk.PhotoImage(big.resize((max(1, big.width // 2), max(1, big.height // 2)), Image.Resampling.LANCZOS))
        self._cache[slide.id] = (key, photo)
        return photo

    def redraw(self) -> None:
        self.canvas.delete("all")
        self._slots = []
        if self.doc is None:
            return
        x = PAD
        alive = set()
        for i, slide in enumerate(self.doc.slides):
            alive.add(slide.id)
            photo = self._photo_for(i)
            w = photo.width()
            self.canvas.create_image(x, PAD, image=photo, anchor="nw")
            outline = SELECT_COLOR if i == self.current else theme.BORDER_SUBTLE
            self.canvas.create_rectangle(x - 2, PAD - 2, x + w + 1, PAD + THUMB_HEIGHT + 1, outline=outline, width=3 if i == self.current else 1)
            self.canvas.create_text(x + w / 2, PAD + THUMB_HEIGHT + 10, text=str(i + 1), fill=theme.TEXT_SECONDARY, font=("Segoe UI", 9))
            self._slots.append((x, x + w))
            x += w + GAP
        for stale in set(self._cache) - alive:
            del self._cache[stale]
        self.canvas.configure(scrollregion=(0, 0, x + PAD, THUMB_HEIGHT + 2 * PAD + 18))

    def _index_at(self, cx: float) -> int | None:
        for i, (a, b) in enumerate(self._slots):
            if a - GAP / 2 <= cx <= b + GAP / 2:
                return i
        return None

    def _drop_index(self, cx: float) -> int:
        for i, (a, b) in enumerate(self._slots):
            if cx < (a + b) / 2:
                return i
        return len(self._slots)

    def _press(self, event) -> None:
        cx = self.canvas.canvasx(event.x)
        index = self._index_at(cx)
        self._drag = {"index": index, "x": cx, "moved": False} if index is not None else None

    def _motion(self, event) -> None:
        if not self._drag:
            return
        cx = self.canvas.canvasx(event.x)
        if abs(cx - self._drag["x"]) > 8:
            self._drag["moved"] = True
        if self._drag["moved"]:
            self.canvas.delete("marker")
            drop = self._drop_index(cx)
            mx = self._slots[drop][0] - GAP / 2 if drop < len(self._slots) else self._slots[-1][1] + GAP / 2
            self.canvas.create_line(mx, PAD - 4, mx, PAD + THUMB_HEIGHT + 4, fill=SELECT_COLOR, width=4, tags="marker")

    def _release(self, event) -> None:
        drag, self._drag = self._drag, None
        self.canvas.delete("marker")
        if not drag:
            return
        if not drag["moved"]:
            self._on_open(drag["index"])
            return
        drop = self._drop_index(self.canvas.canvasx(event.x))
        src = drag["index"]
        dst = drop - 1 if drop > src else drop
        if dst != src:
            self._on_move(src, dst)
