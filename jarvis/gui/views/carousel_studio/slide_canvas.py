"""The large interactive slide editor: shows the slide rendered by
jarvis.carousel_studio.render (the same renderer exports use) and draws
the editing chrome on top as tk.Canvas items - selection box, resize
handles, rotation handle, safe margins and alignment guides.

Mouse: click selects the top-most element under the pointer, dragging
moves it (snapping to the slide center and the safe margins, with a
guide line shown), corner handles resize with the opposite corner held
in place (works for rotated elements too), the round handle above the
box rotates (snaps to 0/90/180/270; Shift = 15° steps). Double-click
asks the editor to focus that element's text."""

from __future__ import annotations

import math
import tkinter as tk
from pathlib import Path
from typing import Callable

from PIL import ImageTk

from jarvis.carousel_studio.editor import CarouselDocument
from jarvis.carousel_studio.model import Element
from jarvis.carousel_studio.render import layout_text, render_slide
from jarvis.gui import theme

HANDLE = 6  # half size of a resize handle, screen px
ROTATE_OFFSET = 34  # distance of the rotation handle above the box, screen px
SNAP_PX = 8  # snapping distance, screen px
MIN_SIZE = 20  # minimum element size, slide px
SELECT_COLOR = "#4C8DFF"
GUIDE_COLOR = "#FF3DAF"
MARGIN_COLOR = "#26C6DA"
OVERFLOW_COLOR = "#FF4D4F"


class SlideCanvas(tk.Canvas):
    def __init__(
        self,
        master,
        *,
        on_select: Callable[[str | None], None],
        on_edited: Callable[[bool], None],
        on_double_click: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__(master, background=theme.BG_PRIMARY, highlightthickness=0, cursor="arrow")
        self._on_select = on_select
        self._on_edited = on_edited  # (final) - final=False while dragging, True on release
        self._on_double_click = on_double_click
        self.doc: CarouselDocument | None = None
        self.slide_index = 0
        self.selected_id: str | None = None
        self.assets_dir: Path | None = None
        self.show_margins = True
        self._photo: ImageTk.PhotoImage | None = None
        self.zoom = 1.0
        self.origin = (0.0, 0.0)
        self._drag: dict | None = None
        self._guides: list[tuple[str, float]] = []
        self.bind("<Configure>", lambda _e: self.redraw())
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Double-Button-1>", self._double)
        self.bind("<Motion>", self._hover)

    # --- public -----------------------------------------------------------------------

    def set_slide(self, doc: CarouselDocument, slide_index: int, assets_dir: Path | None, selected_id: str | None) -> None:
        self.doc = doc
        self.slide_index = slide_index
        self.assets_dir = assets_dir
        self.selected_id = selected_id
        self.redraw()

    def selected(self) -> Element | None:
        if self.doc is None or self.selected_id is None:
            return None
        return self.doc.slide(self.slide_index).find(self.selected_id)

    # --- coordinates ----------------------------------------------------------------------

    def to_screen(self, x: float, y: float) -> tuple[float, float]:
        return self.origin[0] + x * self.zoom, self.origin[1] + y * self.zoom

    def to_slide(self, sx: float, sy: float) -> tuple[float, float]:
        return (sx - self.origin[0]) / self.zoom, (sy - self.origin[1]) / self.zoom

    @staticmethod
    def corners(el: Element) -> list[tuple[float, float]]:
        """Box corners in slide px: top-left, top-right, bottom-right, bottom-left."""
        cx, cy = el.center
        a = math.radians(el.rotation)
        cos, sin = math.cos(a), math.sin(a)
        out = []
        for lx, ly in ((-el.w / 2, -el.h / 2), (el.w / 2, -el.h / 2), (el.w / 2, el.h / 2), (-el.w / 2, el.h / 2)):
            out.append((cx + lx * cos - ly * sin, cy + lx * sin + ly * cos))
        return out

    @staticmethod
    def to_local(el: Element, x: float, y: float) -> tuple[float, float]:
        cx, cy = el.center
        a = math.radians(el.rotation)
        dx, dy = x - cx, y - cy
        return dx * math.cos(a) + dy * math.sin(a), -dx * math.sin(a) + dy * math.cos(a)

    def _rotate_handle(self, el: Element) -> tuple[float, float]:
        """Screen position of the rotation handle (above the top edge center)."""
        cx, cy = el.center
        a = math.radians(el.rotation)
        dist = el.h / 2 + ROTATE_OFFSET / self.zoom
        return self.to_screen(cx + dist * math.sin(a), cy - dist * math.cos(a))

    # --- drawing ---------------------------------------------------------------------------

    def redraw(self) -> None:
        self.delete("all")
        if self.doc is None or not self.doc.slides:
            return
        self.slide_index = max(0, min(self.slide_index, len(self.doc.slides) - 1))
        project = self.doc.project
        width, height = project.size
        # Before the widget is mapped (or when withdrawn) winfo_width is 1;
        # fall back to the requested size.
        cw = self.winfo_width() if self.winfo_width() > 1 else self.winfo_reqwidth()
        ch = self.winfo_height() if self.winfo_height() > 1 else self.winfo_reqheight()
        cw, ch = max(cw, 50), max(ch, 50)
        pad = 28
        self.zoom = max(0.05, min((cw - 2 * pad) / width, (ch - 2 * pad) / height))
        self.origin = ((cw - width * self.zoom) / 2, (ch - height * self.zoom) / 2)
        slide = self.doc.slide(self.slide_index)
        image = render_slide(project, slide, scale=self.zoom, assets_dir=self.assets_dir)
        self._photo = ImageTk.PhotoImage(image)
        self.create_image(round(self.origin[0]), round(self.origin[1]), image=self._photo, anchor="nw")
        x0, y0 = self.to_screen(0, 0)
        x1, y1 = self.to_screen(width, height)
        self.create_rectangle(x0, y0, x1, y1, outline=theme.BORDER_SUBTLE)
        if self.show_margins:
            m = project.theme.margin
            mx0, my0 = self.to_screen(m, m)
            mx1, my1 = self.to_screen(width - m, height - m)
            self.create_rectangle(mx0, my0, mx1, my1, outline=MARGIN_COLOR, dash=(4, 4))
        for el in slide.elements:
            if el.kind == "text" and layout_text(el, project.theme).overflow:
                self._box(el, OVERFLOW_COLOR, dash=(6, 3))
        for kind, value in self._guides:
            if kind == "v":
                gx, _ = self.to_screen(value, 0)
                self.create_line(gx, y0, gx, y1, fill=GUIDE_COLOR, dash=(3, 3))
            else:
                _, gy = self.to_screen(0, value)
                self.create_line(x0, gy, x1, gy, fill=GUIDE_COLOR, dash=(3, 3))
        el = self.selected()
        if el is not None:
            self._box(el, SELECT_COLOR)
            pts = [self.to_screen(*p) for p in self.corners(el)]
            top_mid = ((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2)
            rx, ry = self._rotate_handle(el)
            self.create_line(*top_mid, rx, ry, fill=SELECT_COLOR)
            self.create_oval(rx - 7, ry - 7, rx + 7, ry + 7, fill="white", outline=SELECT_COLOR, width=2)
            for px, py in pts:
                self.create_rectangle(px - HANDLE, py - HANDLE, px + HANDLE, py + HANDLE, fill="white", outline=SELECT_COLOR, width=2)

    def _box(self, el: Element, color: str, dash: tuple[int, ...] = ()) -> None:
        pts = [self.to_screen(*p) for p in self.corners(el)]
        flat = [c for p in pts + [pts[0]] for c in p]
        self.create_line(*flat, fill=color, width=2, dash=dash)

    # --- hit testing ------------------------------------------------------------------------

    def element_at(self, sx: float, sy: float) -> Element | None:
        if self.doc is None:
            return None
        x, y = self.to_slide(sx, sy)
        tol = 6 / self.zoom
        for el in reversed(self.doc.slide(self.slide_index).elements):
            if el.props.get("hidden") or el.props.get("locked"):
                continue
            lx, ly = self.to_local(el, x, y)
            if abs(lx) <= el.w / 2 + tol and abs(ly) <= el.h / 2 + tol:
                return el
        return None

    def _handle_at(self, sx: float, sy: float) -> tuple[str, int] | None:
        el = self.selected()
        if el is None:
            return None
        rx, ry = self._rotate_handle(el)
        if math.hypot(sx - rx, sy - ry) <= 10:
            return ("rotate", -1)
        for i, (px, py) in enumerate(self.to_screen(*p) for p in self.corners(el)):
            if abs(sx - px) <= HANDLE + 3 and abs(sy - py) <= HANDLE + 3:
                return ("resize", i)
        return None

    # --- mouse -----------------------------------------------------------------------------

    def _hover(self, event) -> None:
        handle = self._handle_at(event.x, event.y)
        if handle is not None:
            self.configure(cursor="exchange" if handle[0] == "rotate" else "sizing")
        elif self.element_at(event.x, event.y) is not None:
            self.configure(cursor="fleur")
        else:
            self.configure(cursor="arrow")

    def _press(self, event) -> None:
        self.focus_set()
        if self.doc is None:
            return
        handle = self._handle_at(event.x, event.y)
        el = self.selected() if handle else self.element_at(event.x, event.y)
        if el is None:
            self._drag = None
            if self.selected_id is not None:
                self.selected_id = None
                self._on_select(None)
                self.redraw()
            return
        if el.id != self.selected_id:
            self.selected_id = el.id
            self._on_select(el.id)
        mode, corner = handle if handle else ("move", -1)
        x, y = self.to_slide(event.x, event.y)
        self._drag = {
            "mode": mode, "corner": corner, "start": (x, y), "orig": (el.x, el.y, el.w, el.h, el.rotation),
            "moved": False,
        }
        self.redraw()

    def _motion(self, event) -> None:
        drag = self._drag
        el = self.selected()
        if drag is None or el is None or self.doc is None:
            return
        x, y = self.to_slide(event.x, event.y)
        if not drag["moved"]:
            if math.hypot(x - drag["start"][0], y - drag["start"][1]) * self.zoom < 3:
                return
            drag["moved"] = True
            self.doc.checkpoint()
        ox, oy, ow, oh, orot = drag["orig"]
        shift = isinstance(event.state, int) and bool(event.state & 0x0001)
        self._guides = []
        if drag["mode"] == "move":
            el.x = ox + x - drag["start"][0]
            el.y = oy + y - drag["start"][1]
            if not shift:
                self._snap(el)
        elif drag["mode"] == "rotate":
            cx, cy = el.center
            angle = math.degrees(math.atan2(x - cx, -(y - cy)))
            if shift:
                angle = round(angle / 15) * 15
            else:
                for target in (-180, -90, 0, 90, 180):
                    if abs(angle - target) < 4:
                        angle = target
            el.rotation = angle % 360
        else:
            self._resize(el, drag, x, y, ow, oh, keep_ratio=shift or el.kind == "image")
        self._on_edited(False)
        self.redraw()

    def _release(self, _event) -> None:
        drag, self._drag = self._drag, None
        self._guides = []
        if drag and drag["moved"] and self.doc is not None:
            self.doc.break_coalescing()
            self._on_edited(True)
        self.redraw()

    def _double(self, event) -> None:
        el = self.element_at(event.x, event.y)
        if el is not None and self._on_double_click:
            self._on_double_click(el.id)

    # --- geometry helpers ----------------------------------------------------------------

    def _resize(self, el: Element, drag: dict, x: float, y: float, ow: float, oh: float, *, keep_ratio: bool) -> None:
        corner = drag["corner"]
        sx = 1 if corner in (1, 2) else -1
        sy = 1 if corner in (2, 3) else -1
        ox, oy, _, _, rot = drag["orig"]
        a = math.radians(rot)
        cos, sin = math.cos(a), math.sin(a)
        ocx, ocy = ox + ow / 2, oy + oh / 2
        # The opposite corner stays where it is.
        alx, aly = -sx * ow / 2, -sy * oh / 2
        ax, ay = ocx + alx * cos - aly * sin, ocy + alx * sin + aly * cos
        dx, dy = x - ax, y - ay
        du, dv = dx * cos + dy * sin, -dx * sin + dy * cos
        new_w = max(MIN_SIZE, sx * du)
        new_h = oh if el.kind == "line" else max(MIN_SIZE, sy * dv)
        if keep_ratio and el.kind != "line" and ow > 0 and oh > 0:
            factor = max(new_w / ow, new_h / oh)
            new_w, new_h = max(MIN_SIZE, ow * factor), max(MIN_SIZE, oh * factor)
        lcx, lcy = sx * new_w / 2, sy * new_h / 2
        ncx, ncy = ax + lcx * cos - lcy * sin, ay + lcx * sin + lcy * cos
        el.w, el.h = new_w, new_h
        el.x, el.y = ncx - new_w / 2, ncy - new_h / 2

    def _snap(self, el: Element) -> None:
        if self.doc is None:
            return
        width, height = self.doc.project.size
        m = self.doc.project.theme.margin
        tol = SNAP_PX / self.zoom
        cx, cy = el.center
        # vertical guides: slide center, margins; horizontal likewise.
        for target in (width / 2,):
            if abs(cx - target) < tol:
                el.x = target - el.w / 2
                self._guides.append(("v", target))
        if el.rotation % 360 == 0:
            for target, edge in ((m, "left"), (width - m, "right")):
                value = el.x if edge == "left" else el.x + el.w
                if abs(value - target) < tol:
                    el.x = target if edge == "left" else target - el.w
                    self._guides.append(("v", target))
            for target, edge in ((m, "top"), (height - m, "bottom")):
                value = el.y if edge == "top" else el.y + el.h
                if abs(value - target) < tol:
                    el.y = target if edge == "top" else target - el.h
                    self._guides.append(("h", target))
        if abs(cy - height / 2) < tol:
            el.y = height / 2 - el.h / 2
            self._guides.append(("h", height / 2))
        # Align with other elements' centers on this slide.
        for other in self.doc.slide(self.slide_index).elements:
            if other is el:
                continue
            ocx, ocy = other.center
            if abs(el.center[0] - ocx) < tol and ("v", ocx) not in self._guides:
                el.x = ocx - el.w / 2
                self._guides.append(("v", ocx))
                break
