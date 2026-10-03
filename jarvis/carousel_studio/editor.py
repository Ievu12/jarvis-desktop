"""CarouselDocument: every edit the studio can make to a carousel, with
project-wide undo/redo. The GUI never mutates a Project directly - it
calls these methods, so each change is one undo step and triggers one
autosave.

Undo stores whole-project snapshots (JSON dicts). A carousel is at most
20 slides of small dicts, so a snapshot is a few KB; 100 of them are
cheaper and far less bug-prone than per-operation inverse commands."""

from __future__ import annotations

import copy
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from jarvis.carousel_studio.themes import PALETTES
from jarvis.carousel_studio.model import (
    FORMATS,
    MAX_SLIDES,
    MIN_SLIDES,
    Element,
    Project,
    Slide,
    make_image,
    make_line,
    make_shape,
    make_text,
    starter_slide,
)

HISTORY_LIMIT = 100


class EditorError(Exception):
    """A requested edit is not allowed (e.g. fewer than 2 slides); the
    message is shown to the person as-is (Lithuanian)."""


class CarouselDocument:
    def __init__(self, project: Project, *, on_change: Callable[[], None] | None = None) -> None:
        self.project = project
        self._undo: list[dict[str, Any]] = []
        self._redo: list[dict[str, Any]] = []
        self._coalesce_key: str | None = None
        self._on_change = on_change

    # --- history -----------------------------------------------------------------------

    def _snapshot(self) -> dict[str, Any]:
        return self.project.to_dict()

    def _restore(self, snap: dict[str, Any]) -> None:
        restored = Project.from_dict(copy.deepcopy(snap))
        restored.created_at = self.project.created_at
        self.project = restored

    def checkpoint(self, coalesce: str | None = None) -> None:
        """Records the current state as an undo step. With `coalesce`,
        consecutive checkpoints with the same key (typing in one text
        box, dragging a slider) collapse into a single undo step."""
        if coalesce is not None and coalesce == self._coalesce_key:
            return
        self._coalesce_key = coalesce
        self._undo.append(self._snapshot())
        if len(self._undo) > HISTORY_LIMIT:
            self._undo.pop(0)
        self._redo.clear()

    def break_coalescing(self) -> None:
        self._coalesce_key = None

    @contextmanager
    def change(self, coalesce: str | None = None) -> Iterator[None]:
        self.checkpoint(coalesce)
        yield
        self.changed()

    def changed(self) -> None:
        if self._on_change:
            self._on_change()

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._snapshot())
        self._restore(self._undo.pop())
        self._coalesce_key = None
        self.changed()
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._snapshot())
        self._restore(self._redo.pop())
        self._coalesce_key = None
        self.changed()
        return True

    # --- slides --------------------------------------------------------------------------

    @property
    def slides(self) -> list[Slide]:
        return self.project.slides

    def slide(self, index: int) -> Slide:
        return self.project.slides[index]

    def add_slide(self, after: int | None = None, role: str = "content") -> int:
        if len(self.slides) >= MAX_SLIDES:
            raise EditorError(f"Karuselėje gali būti daugiausia {MAX_SLIDES} skaidrių.")
        index = len(self.slides) if after is None else after + 1
        with self.change():
            content_no = sum(1 for s in self.slides[:index] if s.role == "content") + 1
            self.slides.insert(index, starter_slide(role, self.project.size, self.project.theme.margin, content_no))
        return index

    def duplicate_slide(self, index: int) -> int:
        if len(self.slides) >= MAX_SLIDES:
            raise EditorError(f"Karuselėje gali būti daugiausia {MAX_SLIDES} skaidrių.")
        with self.change():
            self.slides.insert(index + 1, self.slides[index].clone())
        return index + 1

    def delete_slide(self, index: int) -> int:
        if len(self.slides) <= MIN_SLIDES:
            raise EditorError(f"Karuselėje turi būti bent {MIN_SLIDES} skaidrės.")
        with self.change():
            del self.slides[index]
        return min(index, len(self.slides) - 1)

    def move_slide(self, src: int, dst: int) -> int:
        dst = max(0, min(len(self.slides) - 1, dst))
        if src == dst:
            return src
        with self.change():
            slide = self.slides.pop(src)
            self.slides.insert(dst, slide)
        return dst

    def set_slide_count(self, count: int) -> None:
        count = max(MIN_SLIDES, min(MAX_SLIDES, count))
        with self.change():
            while len(self.slides) > count:
                # Keep the cover and the closing slide; drop from the middle.
                del self.slides[-2 if len(self.slides) > 2 else -1]
            while len(self.slides) < count:
                content_no = sum(1 for s in self.slides if s.role == "content") + 1
                self.slides.insert(len(self.slides) - 1, starter_slide("content", self.project.size, self.project.theme.margin, content_no))

    def set_background(self, index: int, **background: Any) -> None:
        with self.change(coalesce=f"bg:{self.slides[index].id}:{','.join(sorted(background))}"):
            self.slides[index].background = {**self.slides[index].background, **background}

    def apply_background_to_all(self, index: int) -> None:
        with self.change():
            bg = copy.deepcopy(self.slides[index].background)
            for slide in self.slides:
                slide.background = copy.deepcopy(bg)

    def set_slide_role(self, index: int, role: str) -> None:
        with self.change():
            self.slides[index].role = role

    # --- theme -----------------------------------------------------------------------------

    def set_palette(self, key: str) -> None:
        """Switches the whole carousel's palette; fonts, spacing, texts,
        photos and any hand-picked literal colors are kept."""
        if key not in PALETTES:
            raise EditorError("Tokios paletės nėra.")
        with self.change():
            self.project.theme.palette_key = key
            self.project.theme.colors = dict(PALETTES[key][1])

    def set_theme_color(self, role: str, hex_color: str) -> None:
        with self.change(coalesce=f"theme:{role}"):
            self.project.theme.colors[role] = hex_color
            self.project.theme.palette_key = "custom"

    # --- project ---------------------------------------------------------------------------

    def rename(self, name: str) -> None:
        name = name.strip() or "Be pavadinimo"
        if name == self.project.name:
            return
        with self.change(coalesce="rename"):
            self.project.name = name

    def set_format(self, fmt: str) -> None:
        """Changes the slide size, moving every element proportionally
        vertically so the layout keeps its shape (width is always 1080)."""
        if fmt not in FORMATS or fmt == self.project.format:
            return
        _, _, old_h = FORMATS[self.project.format]
        _, _, new_h = FORMATS[fmt]
        ratio = new_h / old_h
        with self.change():
            self.project.format = fmt
            for slide in self.slides:
                for el in slide.elements:
                    cx, cy = el.x + el.w / 2, (el.y + el.h / 2) * ratio
                    if el.h > new_h and el.kind in ("image", "shape"):
                        shrink = new_h / el.h
                        el.w, el.h = el.w * shrink, el.h * shrink
                    elif el.kind == "text" and ratio < 1:
                        el.h *= ratio
                    el.x, el.y = cx - el.w / 2, cy - el.h / 2

    # --- elements -------------------------------------------------------------------------

    def add_element(self, slide_index: int, element: Element) -> Element:
        with self.change():
            self.slides[slide_index].elements.append(element)
        return element

    def new_text(self, slide_index: int, style: str) -> Element:
        w, h = self.project.size
        m = self.project.theme.margin
        defaults = {"heading": ("Nauja antraštė", 220), "subheading": ("Nauja paantraštė", 110), "body": ("Naujas tekstas", 200), "caption": ("Prierašas", 60)}
        text, box_h = defaults.get(style, defaults["body"])
        return self.add_element(slide_index, make_text(style, text, x=m, y=(h - box_h) / 2, w=w - 2 * m, h=box_h))

    def new_shape(self, slide_index: int, shape: str) -> Element:
        w, h = self.project.size
        size = 360
        return self.add_element(slide_index, make_shape(shape, x=(w - size) / 2, y=(h - size) / 2, w=size, h=size))

    def new_line(self, slide_index: int) -> Element:
        w, h = self.project.size
        return self.add_element(slide_index, make_line(x=(w - 400) / 2, y=h / 2, w=400))

    def new_image(self, slide_index: int, path: str, image_size: tuple[int, int]) -> Element:
        w, h = self.project.size
        iw, ih = image_size
        box_w = w * 0.7
        box_h = box_w * ih / max(iw, 1)
        if box_h > h * 0.7:
            box_h = h * 0.7
            box_w = box_h * iw / max(ih, 1)
        return self.add_element(slide_index, make_image(path, x=(w - box_w) / 2, y=(h - box_h) / 2, w=box_w, h=box_h))

    def update_element(self, slide_index: int, element_id: str, *, coalesce: str | None = None, **changes: Any) -> None:
        """Geometry fields (x, y, w, h, rotation, opacity) are set
        directly; anything else goes into props."""
        element = self._get(slide_index, element_id)
        key = coalesce if coalesce is not None else f"{element_id}:{','.join(sorted(changes))}"
        with self.change(coalesce=key):
            for name, value in changes.items():
                if name in ("x", "y", "w", "h", "rotation", "opacity"):
                    setattr(element, name, float(value))
                else:
                    element.props[name] = value

    def delete_element(self, slide_index: int, element_id: str) -> None:
        element = self._get(slide_index, element_id)
        with self.change():
            self.slides[slide_index].elements.remove(element)

    def duplicate_element(self, slide_index: int, element_id: str, offset: float = 30) -> Element:
        element = self._get(slide_index, element_id)
        dup = element.clone()
        dup.x += offset
        dup.y += offset
        with self.change():
            elements = self.slides[slide_index].elements
            elements.insert(elements.index(element) + 1, dup)
        return dup

    def copy_element_to_slide(self, slide_index: int, element_id: str, target_index: int) -> Element:
        dup = self._get(slide_index, element_id).clone()
        with self.change():
            self.slides[target_index].elements.append(dup)
        return dup

    def copy_element_to_all(self, slide_index: int, element_id: str) -> int:
        source = self._get(slide_index, element_id)
        with self.change():
            for i, slide in enumerate(self.slides):
                if i != slide_index:
                    slide.elements.append(source.clone())
        return len(self.slides) - 1

    def reorder_element(self, slide_index: int, element_id: str, where: str) -> None:
        """where: 'forward' | 'backward' | 'front' | 'back'."""
        elements = self.slides[slide_index].elements
        element = self._get(slide_index, element_id)
        i = elements.index(element)
        target = {"forward": i + 1, "backward": i - 1, "front": len(elements) - 1, "back": 0}[where]
        target = max(0, min(len(elements) - 1, target))
        if target == i:
            return
        with self.change():
            elements.pop(i)
            elements.insert(target, element)

    def _get(self, slide_index: int, element_id: str) -> Element:
        element = self.slides[slide_index].find(element_id)
        if element is None:
            raise EditorError("Elementas nerastas.")
        return element
