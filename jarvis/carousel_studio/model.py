"""The carousel document: Project -> Slides -> Elements, as plain
dataclasses that round-trip through JSON (project.json).

Coordinates are in output pixels (the slide is always 1080 wide), so
what is stored is exactly what is exported. Each element has a box
(x, y, w, h), a clockwise rotation in degrees around the box center and
an opacity; everything kind-specific lives in `props` so new element
kinds never need a schema migration."""

from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from typing import Any

from jarvis.carousel_studio.themes import Theme

SCHEMA_VERSION = 1

# key -> (label, width, height)
FORMATS: dict[str, tuple[str, int, int]] = {
    "portrait": ("Instagram 4:5 (1080 × 1350)", 1080, 1350),
    "square": ("Kvadratas 1:1 (1080 × 1080)", 1080, 1080),
    "story": ("Vertikalus 9:16 (1080 × 1920)", 1080, 1920),
}
DEFAULT_FORMAT = "portrait"
MIN_SLIDES = 2
MAX_SLIDES = 20

ELEMENT_KINDS = ("text", "image", "shape", "line")
SLIDE_ROLES = {"cover": "Viršelis", "content": "Turinys", "summary": "Apibendrinimas", "cta": "Kvietimas veikti"}

# Text "styles" (heading / subheading / body) drive default size, font
# and color; the person can still override any of them per element.
TEXT_STYLES: dict[str, dict[str, Any]] = {
    "heading": {"size": 96, "bold": True, "color": "theme:text", "font_role": "heading", "line_spacing": 1.08},
    "subheading": {"size": 52, "bold": True, "color": "theme:primary", "font_role": "heading", "line_spacing": 1.15},
    "body": {"size": 40, "bold": False, "color": "theme:text", "font_role": "body", "line_spacing": 1.3},
    "caption": {"size": 28, "bold": False, "color": "theme:text", "font_role": "body", "line_spacing": 1.25},
}


def new_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass
class Element:
    kind: str
    x: float
    y: float
    w: float
    h: float
    rotation: float = 0.0
    opacity: float = 1.0
    props: dict[str, Any] = field(default_factory=dict)
    id: str = field(default_factory=new_id)

    @property
    def center(self) -> tuple[float, float]:
        return self.x + self.w / 2, self.y + self.h / 2

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "x": self.x, "y": self.y, "w": self.w, "h": self.h,
            "rotation": self.rotation, "opacity": self.opacity, "props": copy.deepcopy(self.props),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Element":
        return cls(
            kind=data["kind"], x=float(data["x"]), y=float(data["y"]), w=float(data["w"]), h=float(data["h"]),
            rotation=float(data.get("rotation", 0.0)), opacity=float(data.get("opacity", 1.0)),
            props=copy.deepcopy(data.get("props") or {}), id=data.get("id") or new_id(),
        )

    def clone(self) -> "Element":
        dup = Element.from_dict(self.to_dict())
        dup.id = new_id()
        return dup


@dataclass
class Slide:
    elements: list[Element] = field(default_factory=list)
    background: dict[str, Any] = field(default_factory=lambda: {"type": "color", "color": "theme:background"})
    role: str = "content"
    id: str = field(default_factory=new_id)

    def find(self, element_id: str) -> Element | None:
        return next((e for e in self.elements if e.id == element_id), None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "role": self.role, "background": copy.deepcopy(self.background),
            "elements": [e.to_dict() for e in self.elements],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Slide":
        return cls(
            elements=[Element.from_dict(e) for e in data.get("elements", [])],
            background=copy.deepcopy(data.get("background") or {"type": "color", "color": "theme:background"}),
            role=data.get("role", "content"), id=data.get("id") or new_id(),
        )

    def clone(self) -> "Slide":
        dup = Slide.from_dict(self.to_dict())
        dup.id = new_id()
        for element in dup.elements:
            element.id = new_id()
        return dup


@dataclass
class Project:
    name: str
    format: str = DEFAULT_FORMAT
    theme: Theme = field(default_factory=Theme)
    slides: list[Slide] = field(default_factory=list)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = ""
    updated_at: str = ""
    caption: str = ""  # Instagram post description (AI stage fills it)

    @property
    def size(self) -> tuple[int, int]:
        _, w, h = FORMATS.get(self.format, FORMATS[DEFAULT_FORMAT])
        return w, h

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION, "id": self.id, "name": self.name, "format": self.format,
            "theme": self.theme.to_dict(), "slides": [s.to_dict() for s in self.slides],
            "created_at": self.created_at, "updated_at": self.updated_at, "caption": self.caption,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Project":
        fmt = data.get("format", DEFAULT_FORMAT)
        return cls(
            name=data.get("name") or "Be pavadinimo",
            format=fmt if fmt in FORMATS else DEFAULT_FORMAT,
            theme=Theme.from_dict(data.get("theme")),
            slides=[Slide.from_dict(s) for s in data.get("slides", [])],
            id=data.get("id") or uuid.uuid4().hex,
            created_at=data.get("created_at", ""), updated_at=data.get("updated_at", ""),
            caption=data.get("caption", ""),
        )


# --- element factories -------------------------------------------------------------------

def make_text(style: str, text: str, *, x: float, y: float, w: float, h: float, align: str = "left") -> Element:
    preset = TEXT_STYLES[style]
    return Element(
        kind="text", x=x, y=y, w=w, h=h,
        props={
            "text": text, "style": style, "size": preset["size"], "bold": preset["bold"],
            "color": preset["color"], "font": None, "font_role": preset["font_role"],
            "align": align, "line_spacing": preset["line_spacing"], "highlight": "theme:accent",
            "uppercase": False, "autofit": True,
        },
    )


def make_shape(shape: str, *, x: float, y: float, w: float, h: float, fill: str | None = "theme:primary") -> Element:
    return Element(
        kind="shape", x=x, y=y, w=w, h=h,
        props={"shape": shape, "fill": fill, "stroke": None, "stroke_width": 0, "radius": 40 if shape == "rounded" else 0},
    )


def make_line(*, x: float, y: float, w: float, color: str = "theme:primary", width: int = 6) -> Element:
    return Element(kind="line", x=x, y=y, w=w, h=max(width, 1), props={"color": color, "width": width})


def make_image(path: str, *, x: float, y: float, w: float, h: float) -> Element:
    return Element(
        kind="image", x=x, y=y, w=w, h=h,
        props={"path": path, "fit": "cover", "zoom": 1.0, "offset_x": 0.0, "offset_y": 0.0, "radius": 0},
    )


def starter_slide(role: str, size: tuple[int, int], margin: int, index: int = 1) -> Slide:
    """A sensible editable first draft for a slide of `role` (point 7's
    auto layout grows from this in stage 3)."""
    w, h = size
    inner = w - 2 * margin
    slide = Slide(role=role)
    if role == "cover":
        slide.elements = [
            make_line(x=margin, y=h * 0.30, w=140, width=8),
            make_text("heading", "Jūsų *antraštė* čia", x=margin, y=h * 0.30 + 40, w=inner, h=h * 0.26),
            make_text("subheading", "Trumpa paantraštė", x=margin, y=h * 0.30 + 60 + h * 0.26, w=inner, h=120),
            make_text("caption", "Braukite →", x=margin, y=h - margin - 50, w=inner, h=50, align="right"),
        ]
    elif role == "cta":
        slide.elements = [
            make_text("heading", "Išsaugok ir *pasidalink*", x=margin, y=h * 0.30, w=inner, h=h * 0.25, align="center"),
            make_text("body", "Parašyk komentaruose, kuris patarimas tau naudingiausias", x=margin, y=h * 0.58, w=inner, h=h * 0.18, align="center"),
        ]
    else:
        slide.elements = [
            make_text("subheading", f"{index:02d}", x=margin, y=margin + 40, w=inner, h=80),
            make_text("heading", "Patarimo pavadinimas", x=margin, y=margin + 140, w=inner, h=h * 0.22),
            make_text("body", "Čia parašykite trumpą, aiškų paaiškinimą. Paryškinkite *svarbius žodžius* žvaigždutėmis.", x=margin, y=margin + 160 + h * 0.22, w=inner, h=h * 0.35),
        ]
    return slide


def starter_slides(count: int, size: tuple[int, int], margin: int) -> list[Slide]:
    count = max(MIN_SLIDES, min(MAX_SLIDES, count))
    slides = [starter_slide("cover", size, margin)]
    for i in range(1, count - 1):
        slides.append(starter_slide("content", size, margin, i))
    slides.append(starter_slide("cta", size, margin))
    return slides
