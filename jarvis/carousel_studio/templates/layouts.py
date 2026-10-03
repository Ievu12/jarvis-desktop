"""Slide layouts for the template library. A layout turns a topic's
texts (TopicContent) and a DesignStyle into ordinary carousel slides:
the first slide (viršelis), one middle slide per item, and the last
slide (kvietimas veikti). Every slide is made only of the editor's own
elements (text, shape, line, image), so a template, once used, is
edited like any other carousel.

Each layout gives the first, middle and last slides their own
arrangement, and different layouts arrange them differently, so two
templates of one topic never look the same.

Adding a layout: subclass Layout, give it a key/label/description,
override cover()/middle()/final() and decorate it with @register. A
topic file then lists the new key in its "layouts"."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from jarvis.carousel_studio.model import Element, Slide, make_image, make_line, make_shape, make_text
from jarvis.carousel_studio.templates.styles import DesignStyle

PHOTO = "sablono_nuotrauka.png"
"""Placeholder photo every template photo slot points at; it is written
into the project's assets/ on use and replaced with "Pakeisti nuotrauką…"."""


@dataclass
class TopicContent:
    kicker: str
    title: str
    subtitle: str
    items: list[tuple[str, str]]
    cta_title: str
    cta_body: str
    button: str = "Išsaugok"
    labels: tuple[str, str] = ("Prieš", "Po")
    swipe: str = "Braukite →"


@dataclass
class Ctx:
    """What a layout needs while building one carousel."""

    content: TopicContent
    style: DesignStyle
    width: int
    height: int
    margin: int = 90
    total: int = field(init=False)

    def __post_init__(self) -> None:
        self.total = len(self.content.items) + 2

    @property
    def inner(self) -> float:
        return self.width - 2 * self.margin

    def y(self, fraction: float) -> float:
        return round(self.height * fraction, 1)

    # --- element helpers ---------------------------------------------------------------

    def text(self, style: str, text: str, x: float, y: float, w: float, h: float, *, align: str = "left",
             size: float | None = None, color: str | None = None, uppercase: bool | None = None,
             heading: bool | None = None, bold: bool | None = None, spacing: float | None = None) -> Element:
        element = make_text(style, text, x=x, y=y, w=w, h=h, align=align)
        props = element.props
        if style == "heading":
            props["size"] = round((size or props["size"]) * self.style.heading_scale)
            props["uppercase"] = self.style.heading_uppercase if uppercase is None else uppercase
        elif size is not None:
            props["size"] = round(size)
        if style != "heading" and uppercase is not None:
            props["uppercase"] = uppercase
        if color:
            props["color"] = color
            if color != "theme:text":
                # On a colored block the accent could vanish; keep *words* readable.
                props["highlight"] = color
        if heading is not None:
            props["font_role"] = "heading" if heading else "body"
        if bold is not None:
            props["bold"] = bold
        if spacing is not None:
            props["line_spacing"] = spacing
        return element

    def shape(self, shape: str, x: float, y: float, w: float, h: float, *, fill: str | None = "theme:primary",
              radius: float | None = None, stroke: str | None = None, stroke_width: float = 0,
              opacity: float = 1.0, rotation: float = 0.0) -> Element:
        if shape == "rounded" and radius is None:
            radius = self.style.radius
        if shape == "rounded" and not radius:
            shape = "rect"
        element = make_shape(shape, x=x, y=y, w=w, h=h, fill=fill)
        element.props["radius"] = radius or 0
        element.props["stroke"] = stroke
        element.props["stroke_width"] = stroke_width
        element.opacity = opacity
        element.rotation = rotation
        return element

    def line(self, x: float, y: float, w: float, *, color: str = "theme:primary", width: int = 6) -> Element:
        return make_line(x=x, y=y, w=w, color=color, width=width)

    def photo(self, x: float, y: float, w: float, h: float, *, radius: float | None = None,
              rotation: float = 0.0) -> Element:
        element = make_image(PHOTO, x=x, y=y, w=w, h=h)
        element.props["radius"] = self.style.radius if radius is None else radius
        element.rotation = rotation
        return element

    def pill(self, text: str, x: float, y: float, w: float, h: float = 88, *, fill: str = "theme:primary",
             color: str = "theme:background", size: float = 34) -> list[Element]:
        radius = h / 2 if self.style.radius else 0
        return [
            self.shape("rounded" if radius else "rect", x, y, w, h, fill=fill, radius=radius),
            self.text("caption", text, x + 16, y + (h - size * 1.3) / 2, w - 32, size * 1.3, align="center",
                      size=size, color=color, bold=True, uppercase=True),
        ]

    def page(self, index: int) -> Element:
        return self.text("caption", f"{index}/{self.total}", self.width - self.margin - 160, self.height - self.margin + 10,
                         160, 40, align="right", size=26)

    def swipe(self) -> Element:
        return self.text("caption", self.content.swipe, self.margin, self.height - self.margin - 10, self.inner, 44,
                         align="right", size=28)


# --- decorations (behind everything, one look per style) ---------------------------------

def decoration(ctx: Ctx, role: str) -> list[Element]:
    w, h, m = ctx.width, ctx.height, ctx.margin
    decor = ctx.style.decor
    big = role in ("cover", "cta")
    if decor == "frame":
        return [ctx.shape("rect", 36, 36, w - 72, h - 72, fill=None, stroke="theme:primary", stroke_width=3)]
    if decor == "double_frame":
        return [
            ctx.shape("rect", 30, 30, w - 60, h - 60, fill=None, stroke="theme:primary", stroke_width=3),
            ctx.shape("rect", 44, 44, w - 88, h - 88, fill=None, stroke="theme:primary", stroke_width=1.5),
        ]
    if decor == "dots":
        size = 34 if big else 22
        return [ctx.shape("ellipse", w - m - i * (size + 18) - size, m * 0.55, size, size,
                          fill="theme:accent" if i == 0 else "theme:primary", opacity=0.85 - i * 0.2) for i in range(3)]
    if decor == "blob":
        d = w * (0.95 if big else 0.7)
        return [
            ctx.shape("ellipse", w - d * 0.62, -d * 0.38, d, d, fill="theme:surface", opacity=0.9),
            ctx.shape("ellipse", -d * 0.35, h - d * 0.42, d * 0.62, d * 0.62, fill="theme:primary", opacity=0.12),
        ]
    if decor == "corner":
        return [ctx.shape("rect", 0, 0, 34 if big else 20, h * (0.42 if big else 0.22), fill="theme:accent")]
    if decor == "lines":
        return [ctx.line(m, m * 0.6, ctx.inner, color="theme:text", width=2),
                ctx.line(m, h - m * 0.6, ctx.inner, color="theme:text", width=2)]
    if decor == "arch":
        aw = w * 0.56
        return [ctx.shape("rounded", w - aw - m * 0.4, h * (0.08 if big else 0.55), aw, h * 0.62, fill="theme:surface",
                          radius=aw / 2, opacity=0.95)]
    if decor == "grid":
        return [ctx.line(m, h * f, ctx.inner, color="theme:surface", width=2) for f in (0.25, 0.5, 0.75)]
    if decor == "sparkle":
        out = []
        for i, (fx, fy, s) in enumerate(((0.86, 0.07, 46), (0.79, 0.12, 24), (0.08, 0.9, 30))):
            if not big and i == 2:
                continue
            out.append(ctx.shape("rect", w * fx, h * fy, s, s, fill="theme:accent", rotation=45, opacity=0.8))
        return out
    return []


def _slide(ctx: Ctx, role: str, elements: list[Element], background: dict | None = None) -> Slide:
    if background is None:
        if ctx.style.background == "gradient":
            background = {"type": "gradient", "color1": "theme:background", "color2": "theme:surface"}
        else:
            background = {"type": "color", "color": "theme:background"}
    return Slide(elements=decoration(ctx, role) + elements, background=background, role=role)


# --- layouts -----------------------------------------------------------------------------

class Layout:
    key = ""
    label = ""
    description = ""

    def build(self, ctx: Ctx) -> list[Slide]:
        slides = [_slide(ctx, "cover", self.cover(ctx))]
        for index, item in enumerate(ctx.content.items, start=1):
            slides.append(_slide(ctx, "content", self.middle(ctx, index, item)))
        slides.append(_slide(ctx, "cta", self.final(ctx)))
        return slides

    def cover(self, ctx: Ctx) -> list[Element]:
        raise NotImplementedError

    def middle(self, ctx: Ctx, index: int, item: tuple[str, str]) -> list[Element]:
        raise NotImplementedError

    def final(self, ctx: Ctx) -> list[Element]:
        """Centered call to action with a button - shared by most layouts."""
        m, c = ctx.margin, ctx.content
        return [
            ctx.text("heading", c.cta_title, m, ctx.y(0.27), ctx.inner, ctx.y(0.24), align="center", size=86),
            ctx.text("body", c.cta_body, m + 40, ctx.y(0.53), ctx.inner - 80, ctx.y(0.15), align="center"),
            *ctx.pill(c.button, ctx.width / 2 - 200, ctx.y(0.74), 400),
        ]


LAYOUTS: dict[str, Layout] = {}


def register(cls: type[Layout]) -> type[Layout]:
    LAYOUTS[cls.key] = cls()
    return cls


@register
class Classic(Layout):
    key, label, description = "classic", "Klasikinis", "Linija ir didelė antraštė, numeruoti patarimai."

    def cover(self, ctx):
        m, c = ctx.margin, ctx.content
        return [
            ctx.text("caption", c.kicker, m, ctx.y(0.24), ctx.inner, 44, size=30, color="theme:primary", bold=True, uppercase=True),
            ctx.line(m, ctx.y(0.24) + 60, 140, width=8),
            ctx.text("heading", c.title, m, ctx.y(0.24) + 100, ctx.inner, ctx.y(0.28), size=100),
            ctx.text("subheading", c.subtitle, m, ctx.y(0.24) + 130 + ctx.y(0.28), ctx.inner, 130, size=46),
            ctx.swipe(),
        ]

    def middle(self, ctx, index, item):
        m = ctx.margin
        return [
            ctx.text("subheading", f"{index:02d}", m, m + 40, ctx.inner, 80, size=60),
            ctx.text("heading", item[0], m, m + 150, ctx.inner, ctx.y(0.24), size=80),
            ctx.text("body", item[1], m, m + 190 + ctx.y(0.24), ctx.inner, ctx.y(0.36)),
            ctx.page(index + 1),
        ]


@register
class PhotoCover(Layout):
    key, label, description = "photo_cover", "Nuotrauka viršelyje", "Didelė nuotrauka pirmoje skaidrėje, nuotrauka ir tekstas viduje."

    def cover(self, ctx):
        m, c, w = ctx.margin, ctx.content, ctx.width
        return [
            ctx.photo(0, 0, w, ctx.y(0.6), radius=0),
            ctx.shape("rect", 0, ctx.y(0.6), w, ctx.y(0.4), fill="theme:background"),
            ctx.text("caption", c.kicker, m, ctx.y(0.63), ctx.inner, 44, size=28, color="theme:primary", bold=True, uppercase=True),
            ctx.text("heading", c.title, m, ctx.y(0.63) + 54, ctx.inner, ctx.y(0.19), size=84),
            ctx.text("body", c.subtitle, m, ctx.y(0.87), ctx.inner - 180, 70, size=32),
            ctx.text("caption", "→", w - m - 80, ctx.y(0.87), 80, 70, align="right", size=56, color="theme:primary", bold=True),
        ]

    def middle(self, ctx, index, item):
        m = ctx.margin
        side = ctx.inner * 0.46
        return [
            ctx.photo(ctx.width - m - side, m, side, side * 1.2),
            ctx.text("heading", f"{index}", m, m, ctx.inner - side - 30, side * 0.5, size=150, color="theme:primary"),
            ctx.text("heading", item[0], m, m + side * 1.2 + 50, ctx.inner, ctx.y(0.17), size=70),
            ctx.text("body", item[1], m, m + side * 1.2 + 80 + ctx.y(0.17), ctx.inner, ctx.y(0.26)),
        ]

    def final(self, ctx):
        m, c = ctx.margin, ctx.content
        d = ctx.width * 0.38
        return [
            ctx.photo((ctx.width - d) / 2, ctx.y(0.1), d, d, radius=d / 2),
            ctx.text("heading", c.cta_title, m, ctx.y(0.1) + d + 50, ctx.inner, ctx.y(0.18), align="center", size=78),
            ctx.text("body", c.cta_body, m + 30, ctx.y(0.1) + d + 70 + ctx.y(0.18), ctx.inner - 60, ctx.y(0.12), align="center"),
            *ctx.pill(c.button, ctx.width / 2 - 190, ctx.y(0.85), 380, 84),
        ]


@register
class BigNumber(Layout):
    key, label, description = "big_number", "Dideli skaičiai", "Milžiniškas skaičius kiekvienoje skaidrėje, santrauka pabaigoje."

    def cover(self, ctx):
        m, c = ctx.margin, ctx.content
        return [
            ctx.text("heading", str(len(c.items)), m, ctx.y(0.1), ctx.inner, ctx.y(0.34), size=420, color="theme:primary", uppercase=False),
            ctx.text("heading", c.title, m, ctx.y(0.47), ctx.inner, ctx.y(0.26), size=92),
            ctx.text("body", c.subtitle, m, ctx.y(0.75), ctx.inner, 110),
            ctx.swipe(),
        ]

    def middle(self, ctx, index, item):
        m = ctx.margin
        return [
            ctx.text("heading", f"{index}", m - 10, m - 20, ctx.inner * 0.5, ctx.y(0.3), size=340, color="theme:primary", uppercase=False),
            ctx.line(m, ctx.y(0.4), 200, color="theme:accent", width=10),
            ctx.text("heading", item[0], m, ctx.y(0.44), ctx.inner, ctx.y(0.18), size=76),
            ctx.text("body", item[1], m, ctx.y(0.64), ctx.inner, ctx.y(0.24)),
        ]

    def final(self, ctx):
        m, c = ctx.margin, ctx.content
        rows = c.items[:7]
        row_h = min(90, ctx.y(0.42) / max(1, len(rows)))
        out = [ctx.text("heading", c.cta_title, m, m + 20, ctx.inner, ctx.y(0.2), size=80)]
        top = m + 50 + ctx.y(0.2)
        for i, (title, _body) in enumerate(rows, start=1):
            out.append(ctx.text("subheading", f"{i}.", m, top + (i - 1) * row_h, 70, row_h, size=40))
            out.append(ctx.text("body", title, m + 80, top + (i - 1) * row_h, ctx.inner - 80, row_h, size=36))
        out.extend(ctx.pill(c.button, m, ctx.height - m - 100, 380, 84))
        return out


@register
class Cards(Layout):
    key, label, description = "card", "Kortelės", "Tekstas baltose kortelėse su eigos taškeliais."

    def _card(self, ctx, top, height):
        return ctx.shape("rounded", ctx.margin - 20, top, ctx.inner + 40, height, fill="theme:surface")

    def _dots(self, ctx, current):
        size, gap = 16, 14
        count = ctx.total
        x0 = (ctx.width - (count * size + (count - 1) * gap)) / 2
        return [ctx.shape("ellipse", x0 + i * (size + gap), ctx.height - ctx.margin + 6, size, size,
                          fill="theme:primary", opacity=1.0 if i == current else 0.25) for i in range(count)]

    def cover(self, ctx):
        m, c = ctx.margin, ctx.content
        return [
            self._card(ctx, ctx.y(0.2), ctx.y(0.56)),
            *ctx.pill(c.kicker, m + 30, ctx.y(0.2) + 50, 320, 66, size=26),
            ctx.text("heading", c.title, m + 30, ctx.y(0.2) + 150, ctx.inner - 60, ctx.y(0.26), size=90),
            ctx.text("body", c.subtitle, m + 30, ctx.y(0.2) + 180 + ctx.y(0.26), ctx.inner - 60, ctx.y(0.12)),
            *self._dots(ctx, 0),
        ]

    def middle(self, ctx, index, item):
        m = ctx.margin
        return [
            self._card(ctx, ctx.y(0.12), ctx.y(0.72)),
            ctx.shape("ellipse", m + 30, ctx.y(0.12) + 50, 110, 110, fill="theme:primary"),
            ctx.text("subheading", str(index), m + 30, ctx.y(0.12) + 72, 110, 70, align="center", size=54, color="theme:background"),
            ctx.text("heading", item[0], m + 30, ctx.y(0.12) + 200, ctx.inner - 60, ctx.y(0.2), size=72),
            ctx.text("body", item[1], m + 30, ctx.y(0.12) + 230 + ctx.y(0.2), ctx.inner - 60, ctx.y(0.3)),
            *self._dots(ctx, index),
        ]

    def final(self, ctx):
        return [self._card(ctx, ctx.y(0.18), ctx.y(0.66))] + super().final(ctx) + self._dots(ctx, ctx.total - 1)


@register
class Split(Layout):
    key, label, description = "split", "Spalvų blokai", "Ryškus spalvos blokas ir šviesi teksto dalis."

    def cover(self, ctx):
        m, c, w = ctx.margin, ctx.content, ctx.width
        return [
            ctx.shape("rect", 0, 0, w, ctx.y(0.56), fill="theme:primary"),
            ctx.text("caption", c.kicker, m, m, ctx.inner, 44, size=30, color="theme:background", bold=True, uppercase=True),
            ctx.text("heading", c.title, m, ctx.y(0.2), ctx.inner, ctx.y(0.32), size=100, color="theme:background"),
            ctx.text("subheading", c.subtitle, m, ctx.y(0.62), ctx.inner, ctx.y(0.16), size=48),
            ctx.swipe(),
        ]

    def middle(self, ctx, index, item):
        m = ctx.margin
        bar = 230
        return [
            ctx.shape("rect", 0, 0, bar, ctx.height, fill="theme:primary"),
            ctx.text("heading", f"{index:02d}", 10, m, bar - 20, 150, align="center", size=110, color="theme:background", uppercase=False),
            ctx.text("heading", item[0], bar + 60, m + 20, ctx.width - bar - 60 - m, ctx.y(0.3), size=74),
            ctx.text("body", item[1], bar + 60, m + 60 + ctx.y(0.3), ctx.width - bar - 60 - m, ctx.y(0.42)),
        ]

    def final(self, ctx):
        m, c, w = ctx.margin, ctx.content, ctx.width
        return [
            ctx.text("heading", c.cta_title, m, m + 30, ctx.inner, ctx.y(0.3), size=96),
            ctx.shape("rect", 0, ctx.y(0.52), w, ctx.y(0.48), fill="theme:primary"),
            ctx.text("body", c.cta_body, m, ctx.y(0.58), ctx.inner, ctx.y(0.18), color="theme:background"),
            *ctx.pill(c.button, m, ctx.y(0.82), 380, 84, fill="theme:background", color="theme:primary"),
        ]


@register
class Editorial(Layout):
    key, label, description = "editorial", "Žurnalo", "Žurnalo stilius: plonos linijos, didelis šriftas, puslapių numeriai."

    def cover(self, ctx):
        m, c = ctx.margin, ctx.content
        return [
            ctx.text("caption", c.kicker, m, m, ctx.inner * 0.6, 40, size=26, uppercase=True, bold=True),
            ctx.text("caption", "Nr. 01", ctx.width - m - 200, m, 200, 40, align="right", size=26),
            ctx.line(m, m + 56, ctx.inner, color="theme:text", width=2),
            ctx.text("heading", c.title, m, ctx.y(0.3), ctx.inner, ctx.y(0.4), size=118, heading=True),
            ctx.line(m, ctx.y(0.74), ctx.inner, color="theme:text", width=2),
            ctx.text("body", c.subtitle, m, ctx.y(0.76), ctx.inner * 0.75, ctx.y(0.12), size=34),
        ]

    def middle(self, ctx, index, item):
        m = ctx.margin
        return [
            ctx.text("caption", ctx.content.kicker, m, m, ctx.inner * 0.7, 40, size=24, uppercase=True),
            ctx.line(m, m + 50, ctx.inner, color="theme:text", width=2),
            ctx.text("heading", item[0], m, m + 110, ctx.inner, ctx.y(0.3), size=92),
            ctx.line(m, m + 140 + ctx.y(0.3), 120, color="theme:accent", width=6),
            ctx.text("body", item[1], m, m + 180 + ctx.y(0.3), ctx.inner * 0.86, ctx.y(0.34), size=38),
            ctx.page(index + 1),
        ]

    def final(self, ctx):
        m, c = ctx.margin, ctx.content
        return [
            ctx.line(m, ctx.y(0.2), ctx.inner, color="theme:text", width=2),
            ctx.text("heading", c.cta_title, m, ctx.y(0.24), ctx.inner, ctx.y(0.3), size=100),
            ctx.text("body", c.cta_body, m, ctx.y(0.58), ctx.inner * 0.8, ctx.y(0.14)),
            ctx.line(m, ctx.y(0.76), ctx.inner, color="theme:text", width=2),
            ctx.text("caption", c.button, m, ctx.y(0.79), ctx.inner, 50, size=30, bold=True, uppercase=True, color="theme:accent"),
        ]


@register
class Checklist(Layout):
    key, label, description = "checklist", "Kontrolinis sąrašas", "Varnelės ir sąrašas, kurį norisi išsaugoti."

    def _box(self, ctx, x, y, size=70, filled=True):
        out = [ctx.shape("rounded", x, y, size, size, fill="theme:primary" if filled else None, radius=min(16, ctx.style.radius),
                         stroke="theme:primary", stroke_width=4)]
        if filled:  # a dot, not a ✓ glyph: not every font on Windows has one
            dot = size * 0.36
            out.append(ctx.shape("ellipse", x + (size - dot) / 2, y + (size - dot) / 2, dot, dot, fill="theme:background"))
        return out

    def cover(self, ctx):
        m, c = ctx.margin, ctx.content
        out = [
            ctx.text("caption", c.kicker, m, m + 20, ctx.inner, 44, size=30, color="theme:primary", bold=True, uppercase=True),
            ctx.text("heading", c.title, m, m + 80, ctx.inner, ctx.y(0.3), size=96),
        ]
        top = m + 120 + ctx.y(0.3)
        for i, (title, _b) in enumerate(c.items[:3]):
            out += self._box(ctx, m, top + i * 100, 64, filled=i == 0)
            out.append(ctx.text("body", title, m + 90, top + i * 100 + 6, ctx.inner - 90, 64, size=34))
        out.append(ctx.swipe())
        return out

    def middle(self, ctx, index, item):
        m = ctx.margin
        return [
            *self._box(ctx, m, m + 30, 96),
            ctx.text("caption", f"{index} / {len(ctx.content.items)}", m + 120, m + 58, 300, 44, size=30, color="theme:primary", bold=True),
            ctx.text("heading", item[0], m, m + 180, ctx.inner, ctx.y(0.24), size=78),
            ctx.shape("rounded", m - 20, m + 230 + ctx.y(0.24), ctx.inner + 40, ctx.y(0.34), fill="theme:surface"),
            ctx.text("body", item[1], m + 20, m + 270 + ctx.y(0.24), ctx.inner - 40, ctx.y(0.34) - 80),
        ]

    def final(self, ctx):
        m, c = ctx.margin, ctx.content
        rows = c.items[:7]
        row_h = min(96, ctx.y(0.5) / max(1, len(rows)))
        out = [ctx.text("heading", c.cta_title, m, m, ctx.inner, ctx.y(0.17), size=76)]
        top = m + 40 + ctx.y(0.17)
        for i, (title, _b) in enumerate(rows):
            out += self._box(ctx, m, top + i * row_h, row_h * 0.62)
            out.append(ctx.text("body", title, m + row_h * 0.62 + 24, top + i * row_h, ctx.inner - row_h, row_h * 0.7, size=34))
        out.extend(ctx.pill(c.button, m, ctx.height - m - 96, 380, 84))
        return out


@register
class Quote(Layout):
    key, label, description = "quote", "Citata", "Didelės kabutės ir centruotas tekstas."

    def cover(self, ctx):
        m, c = ctx.margin, ctx.content
        return [
            ctx.text("heading", "“", m, ctx.y(0.08), ctx.inner, ctx.y(0.22), align="center", size=320, color="theme:primary", uppercase=False),
            ctx.text("heading", c.title, m, ctx.y(0.32), ctx.inner, ctx.y(0.32), align="center", size=94),
            ctx.line(ctx.width / 2 - 60, ctx.y(0.68), 120, color="theme:accent", width=6),
            ctx.text("body", c.subtitle, m, ctx.y(0.71), ctx.inner, ctx.y(0.12), align="center"),
            ctx.swipe(),
        ]

    def middle(self, ctx, index, item):
        m = ctx.margin
        return [
            ctx.text("heading", "“", m, ctx.y(0.06), 200, ctx.y(0.18), size=260, color="theme:primary", uppercase=False),
            ctx.text("heading", item[0], m, ctx.y(0.26), ctx.inner, ctx.y(0.32), size=82),
            ctx.line(m, ctx.y(0.62), 100, color="theme:accent", width=6),
            ctx.text("body", item[1], m, ctx.y(0.65), ctx.inner, ctx.y(0.22)),
            ctx.page(index + 1),
        ]


@register
class TwoPanel(Layout):
    key, label, description = "two_panel", "Du langeliai", "Dvi dalys: prieš/po, mitas/faktas, problema/sprendimas, A/B."

    def cover(self, ctx):
        m, c, w = ctx.margin, ctx.content, ctx.width
        half = (ctx.inner - 30) / 2
        return [
            ctx.text("heading", c.title, m, m + 20, ctx.inner, ctx.y(0.28), size=92, align="center"),
            ctx.shape("rounded", m, ctx.y(0.4), half, ctx.y(0.4), fill="theme:surface"),
            ctx.shape("rounded", m + half + 30, ctx.y(0.4), half, ctx.y(0.4), fill="theme:primary"),
            ctx.text("subheading", c.labels[0], m + 20, ctx.y(0.56), half - 40, 90, align="center", size=56),
            ctx.text("subheading", c.labels[1], m + half + 50, ctx.y(0.56), half - 40, 90, align="center", size=56, color="theme:background"),
            ctx.text("body", c.subtitle, m, ctx.y(0.83), ctx.inner, 90, align="center", size=32),
        ]

    def middle(self, ctx, index, item):
        m, w, c = ctx.margin, ctx.width, ctx.content
        top_h = ctx.y(0.42)
        return [
            ctx.shape("rounded", m - 20, m, ctx.inner + 40, top_h, fill="theme:surface"),
            *ctx.pill(c.labels[0], m + 10, m + 30, 260, 64, size=26, fill="theme:text", color="theme:surface"),
            ctx.text("heading", item[0], m + 10, m + 120, ctx.inner - 20, top_h - 150, size=64),
            ctx.shape("rounded", m - 20, m + top_h + 30, ctx.inner + 40, ctx.height - 2 * m - top_h - 30, fill="theme:primary"),
            *ctx.pill(c.labels[1], m + 10, m + top_h + 60, 260, 64, size=26, fill="theme:background", color="theme:primary"),
            ctx.text("body", item[1], m + 10, m + top_h + 150, ctx.inner - 20, ctx.height - 2 * m - top_h - 200, color="theme:background"),
        ]


@register
class QandA(Layout):
    key, label, description = "qa", "Klausimai ir atsakymai", "Klausimo ir atsakymo burbulai."

    def cover(self, ctx):
        m, c = ctx.margin, ctx.content
        d = ctx.width * 0.34
        return [
            ctx.shape("ellipse", (ctx.width - d) / 2, ctx.y(0.1), d, d, fill="theme:primary"),
            ctx.text("heading", "?", (ctx.width - d) / 2, ctx.y(0.1) + d * 0.12, d, d * 0.76, align="center", size=260, color="theme:background", uppercase=False),
            ctx.text("heading", c.title, m, ctx.y(0.1) + d + 50, ctx.inner, ctx.y(0.26), align="center", size=90),
            ctx.text("body", c.subtitle, m, ctx.y(0.1) + d + 80 + ctx.y(0.26), ctx.inner, ctx.y(0.12), align="center"),
        ]

    def middle(self, ctx, index, item):
        m = ctx.margin
        q_h = ctx.y(0.3)
        return [
            ctx.text("caption", f"KLAUSIMAS {index}", m, m, ctx.inner, 44, size=28, bold=True, color="theme:primary"),
            ctx.shape("rounded", m - 10, m + 60, ctx.inner * 0.9, q_h, fill="theme:primary", radius=max(30, ctx.style.radius)),
            ctx.text("heading", item[0], m + 30, m + 100, ctx.inner * 0.9 - 80, q_h - 80, size=64, color="theme:background"),
            ctx.shape("rounded", m + ctx.inner * 0.1 + 10, m + 100 + q_h, ctx.inner * 0.9, ctx.y(0.4), fill="theme:surface",
                      radius=max(30, ctx.style.radius)),
            ctx.text("body", item[1], m + ctx.inner * 0.1 + 50, m + 140 + q_h, ctx.inner * 0.9 - 80, ctx.y(0.4) - 80),
        ]


@register
class Steps(Layout):
    key, label, description = "steps", "Žingsniai", "Žingsnio ženkliukas ir eigos juosta."

    def cover(self, ctx):
        m, c = ctx.margin, ctx.content
        return [
            *ctx.pill(f"{len(c.items)} žingsniai", m, ctx.y(0.22), 330, 72, size=28),
            ctx.text("heading", c.title, m, ctx.y(0.22) + 110, ctx.inner, ctx.y(0.3), size=100),
            ctx.text("body", c.subtitle, m, ctx.y(0.22) + 140 + ctx.y(0.3), ctx.inner, ctx.y(0.12)),
            ctx.swipe(),
        ]

    def middle(self, ctx, index, item):
        m, n = ctx.margin, len(ctx.content.items)
        return [
            *ctx.pill(f"Žingsnis {index}", m, m + 20, 300, 70, size=28),
            ctx.text("heading", item[0], m, m + 150, ctx.inner, ctx.y(0.26), size=82),
            ctx.text("body", item[1], m, m + 190 + ctx.y(0.26), ctx.inner, ctx.y(0.34)),
            ctx.shape("rounded", m, ctx.height - m - 14, ctx.inner, 14, fill="theme:surface", radius=7),
            ctx.shape("rounded", m, ctx.height - m - 14, ctx.inner * index / n, 14, fill="theme:primary", radius=7),
        ]


@register
class Product(Layout):
    key, label, description = "product", "Produktas", "Produkto nuotrauka centre, ženkliukas ir savybės."

    def cover(self, ctx):
        m, c, w = ctx.margin, ctx.content, ctx.width
        pw = w * 0.66
        badge = 190
        return [
            ctx.shape("rounded", (w - pw) / 2 - 24, ctx.y(0.07) - 24, pw + 48, pw + 48, fill="theme:surface"),
            ctx.photo((w - pw) / 2, ctx.y(0.07), pw, pw),
            ctx.shape("ellipse", (w + pw) / 2 - badge * 0.6, ctx.y(0.07) - badge * 0.3, badge, badge, fill="theme:primary", rotation=-12),
            ctx.text("caption", c.kicker, (w + pw) / 2 - badge * 0.6 + 14, ctx.y(0.07) - badge * 0.3 + badge * 0.36, badge - 28,
                     badge * 0.3, align="center", size=30, color="theme:background", bold=True, uppercase=True),
            ctx.text("heading", c.title, m, ctx.y(0.07) + pw + 60, ctx.inner, ctx.y(0.15), align="center", size=80),
            ctx.text("body", c.subtitle, m, ctx.y(0.07) + pw + 80 + ctx.y(0.15), ctx.inner, ctx.y(0.08), align="center", size=34),
        ]

    def middle(self, ctx, index, item):
        m, w, h = ctx.margin, ctx.width, ctx.height
        pw = w * 0.5
        return [
            ctx.photo(0, 0, pw, h, radius=0),
            ctx.text("subheading", f"0{index}" if index < 10 else str(index), pw + 50, m + 20, w - pw - 50 - m, 80, size=56),
            ctx.line(pw + 50, m + 120, 90, color="theme:accent", width=6),
            ctx.text("heading", item[0], pw + 50, m + 160, w - pw - 50 - m, ctx.y(0.3), size=60),
            ctx.text("body", item[1], pw + 50, m + 200 + ctx.y(0.3), w - pw - 50 - m, ctx.y(0.4), size=34),
        ]

    def final(self, ctx):
        m, c = ctx.margin, ctx.content
        return [
            ctx.shape("rounded", m - 20, ctx.y(0.16), ctx.inner + 40, ctx.y(0.5), fill="theme:primary"),
            ctx.text("heading", c.cta_title, m + 20, ctx.y(0.16) + 60, ctx.inner - 40, ctx.y(0.24), align="center", size=86,
                     color="theme:background"),
            ctx.text("body", c.cta_body, m + 40, ctx.y(0.16) + 90 + ctx.y(0.24), ctx.inner - 80, ctx.y(0.14), align="center",
                     color="theme:background"),
            *ctx.pill(c.button, ctx.width / 2 - 210, ctx.y(0.74), 420, 92),
        ]


@register
class Poll(Layout):
    key, label, description = "poll", "Apklausa", "Atsakymų variantai A, B, C - auditorija renkasi komentaruose."

    def cover(self, ctx):
        m, c = ctx.margin, ctx.content
        out = [ctx.text("heading", c.title, m, m + 40, ctx.inner, ctx.y(0.32), size=96, align="center")]
        top = ctx.y(0.48)
        for i, (title, _b) in enumerate(c.items[:4]):
            out.append(ctx.shape("rounded", m, top + i * 120, ctx.inner, 96, fill="theme:surface", radius=48 if ctx.style.radius else 0))
            out.append(ctx.text("subheading", f"{'ABCD'[i]}", m + 20, top + i * 120 + 18, 80, 60, align="center", size=46))
            out.append(ctx.text("body", title, m + 110, top + i * 120 + 22, ctx.inner - 140, 56, size=34))
        return out

    def middle(self, ctx, index, item):
        m = ctx.margin
        letter = "ABCDEFGHIJ"[(index - 1) % 10]
        d = 220
        return [
            ctx.shape("ellipse", m, m + 20, d, d, fill="theme:primary"),
            ctx.text("heading", letter, m, m + 20 + d * 0.15, d, d * 0.7, align="center", size=150, color="theme:background", uppercase=False),
            ctx.text("heading", item[0], m, m + d + 80, ctx.inner, ctx.y(0.24), size=80),
            ctx.text("body", item[1], m, m + d + 110 + ctx.y(0.24), ctx.inner, ctx.y(0.28)),
            ctx.page(index + 1),
        ]


@register
class Centered(Layout):
    key, label, description = "centered", "Centruotas", "Daug erdvės, viskas centre, minimalus."

    def cover(self, ctx):
        m, c = ctx.margin, ctx.content
        return [
            ctx.text("caption", c.kicker, m, ctx.y(0.3), ctx.inner, 44, align="center", size=28, uppercase=True, color="theme:primary", bold=True),
            ctx.text("heading", c.title, m + 30, ctx.y(0.3) + 70, ctx.inner - 60, ctx.y(0.28), align="center", size=96),
            ctx.shape("ellipse", ctx.width / 2 - 9, ctx.y(0.3) + 110 + ctx.y(0.28), 18, 18, fill="theme:accent"),
            ctx.text("body", c.subtitle, m + 60, ctx.y(0.3) + 160 + ctx.y(0.28), ctx.inner - 120, ctx.y(0.12), align="center"),
        ]

    def middle(self, ctx, index, item):
        m = ctx.margin
        return [
            ctx.text("caption", f"— {index} —", m, ctx.y(0.2), ctx.inner, 44, align="center", size=30, color="theme:primary", bold=True),
            ctx.text("heading", item[0], m + 30, ctx.y(0.2) + 80, ctx.inner - 60, ctx.y(0.26), align="center", size=80),
            ctx.text("body", item[1], m + 60, ctx.y(0.2) + 120 + ctx.y(0.26), ctx.inner - 120, ctx.y(0.3), align="center"),
        ]


@register
class Collage(Layout):
    key, label, description = "collage", "Koliažas", "Kelios pasuktos nuotraukos ir rankraštinis jausmas."

    def cover(self, ctx):
        m, c, w = ctx.margin, ctx.content, ctx.width
        pw = w * 0.42
        return [
            ctx.photo(m - 10, ctx.y(0.06), pw, pw * 1.25, rotation=-6),
            ctx.photo(w - m - pw + 10, ctx.y(0.12), pw, pw * 1.25, rotation=5),
            ctx.shape("rounded", m - 20, ctx.y(0.56), ctx.inner + 40, ctx.y(0.34), fill="theme:surface"),
            ctx.text("caption", c.kicker, m + 10, ctx.y(0.56) + 30, ctx.inner - 20, 40, size=26, uppercase=True, bold=True, color="theme:primary"),
            ctx.text("heading", c.title, m + 10, ctx.y(0.56) + 80, ctx.inner - 20, ctx.y(0.2), size=82),
            ctx.text("body", c.subtitle, m + 10, ctx.y(0.56) + 90 + ctx.y(0.2), ctx.inner - 20, 70, size=30),
        ]

    def middle(self, ctx, index, item):
        m, w = ctx.margin, ctx.width
        pw = ctx.inner * 0.9
        return [
            ctx.photo((w - pw) / 2, m, pw, ctx.y(0.4), rotation=-2 if index % 2 else 2),
            ctx.text("subheading", f"#{index}", m, m + ctx.y(0.4) + 50, ctx.inner, 70, size=54),
            ctx.text("heading", item[0], m, m + ctx.y(0.4) + 130, ctx.inner, ctx.y(0.15), size=66),
            ctx.text("body", item[1], m, m + ctx.y(0.4) + 150 + ctx.y(0.15), ctx.inner, ctx.y(0.2)),
        ]


LayoutBuilder = Callable[[Ctx], list[Slide]]


def layout_labels() -> dict[str, str]:
    return {key: layout.label for key, layout in LAYOUTS.items()}
