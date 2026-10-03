"""The template library: categories, topics, design styles and the
templates they make, all read from JSON files.

  - templates/data/styles.json            the design styles
  - templates/data/topics/*.json          one file per category (its topics)
  - <CAROUSEL_STUDIO_LIBRARY_DIR>/templates/*.json
                                          the person's own additions, same format

A topic lists the layouts and styles it suits; every (topic, layout)
pair is one template in the library, shown in the topic's style for
that layout. Any template can still be previewed and used in any other
style. Adding templates therefore means adding JSON, never code: a new
file in either folder is picked up on the next load(). See
templates/data/README.md for the file format.

The library is read once and cached; reload() re-reads it."""

from __future__ import annotations

import json
import random
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from jarvis.carousel_studio.model import Project
from jarvis.carousel_studio.templates.layouts import LAYOUTS, Ctx, TopicContent
from jarvis.carousel_studio.templates.styles import DesignStyle

DATA_DIR = Path(__file__).resolve().parent / "data"
TEMPLATE_FORMAT = "portrait"  # Instagram 4:5, 1080 x 1350
MIN_ITEMS, MAX_ITEMS = 1, 12


def _user_dir_default() -> Path:
    from jarvis.config import CAROUSEL_STUDIO_LIBRARY_DIR

    return CAROUSEL_STUDIO_LIBRARY_DIR / "templates"


USER_TEMPLATE_DIR: Path | None = None
"""Overrides the person's template folder (tests); None = the default."""


def user_template_dir() -> Path:
    return USER_TEMPLATE_DIR if USER_TEMPLATE_DIR is not None else _user_dir_default()


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    icon: str = ""
    order: int = 100

    @property
    def title(self) -> str:
        return f"{self.icon} {self.label}".strip()


@dataclass(frozen=True)
class Topic:
    key: str
    category: str
    title: str
    content: TopicContent
    layouts: tuple[str, ...]
    styles: tuple[str, ...]
    tags: tuple[str, ...] = ()
    popularity: int = 50
    added: str = ""
    order: int = 0


@dataclass(frozen=True)
class Template:
    id: str
    topic: Topic
    layout: str
    style: str
    order: int

    @property
    def name(self) -> str:
        return self.topic.title

    @property
    def slide_count(self) -> int:
        return len(self.topic.content.items) + 2

    @property
    def layout_label(self) -> str:
        return LAYOUTS[self.layout].label


@dataclass
class Library:
    categories: dict[str, Category] = field(default_factory=dict)
    styles: dict[str, DesignStyle] = field(default_factory=dict)
    topics: dict[str, Topic] = field(default_factory=dict)
    templates: list[Template] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    """Files or entries that were skipped, with the reason (shown in the UI)."""

    def template(self, template_id: str) -> Template | None:
        return next((t for t in self.templates if t.id == template_id), None)

    def category_label(self, key: str) -> str:
        category = self.categories.get(key)
        return category.title if category else key

    def style_label(self, key: str) -> str:
        style = self.styles.get(key)
        return style.label if style else key


# --- loading -------------------------------------------------------------------------------

def _json_files(folder: Path) -> list[Path]:
    return sorted(folder.glob("*.json")) if folder.is_dir() else []


def _read(path: Path, problems: list[str]) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"{path.name}: nepavyko perskaityti ({exc})")
        return None
    if not isinstance(data, dict):
        problems.append(f"{path.name}: turi būti JSON objektas")
        return None
    return data


def _items(raw: Iterable) -> list[tuple[str, str]]:
    items = []
    for entry in raw:
        if isinstance(entry, dict):
            title, body = entry.get("title", ""), entry.get("body", "")
        else:
            title, body = (list(entry) + ["", ""])[:2]
        if str(title).strip():
            items.append((str(title).strip(), str(body).strip()))
    return items


def _topic(data: dict[str, Any], category: str, defaults: dict[str, Any], styles: dict[str, DesignStyle],
           order: int) -> Topic:
    merged = {**defaults, **data}
    key = str(merged.get("key", "")).strip()
    title = str(merged.get("title", "")).strip()
    if not key or not title:
        raise ValueError("temai reikia „key“ ir „title“")
    items = _items(merged.get("items", ()))[:MAX_ITEMS]
    if len(items) < MIN_ITEMS:
        raise ValueError(f"temai „{key}“ reikia bent {MIN_ITEMS} punkto („items“)")
    layouts = tuple(l for l in merged.get("layouts", ()) if l in LAYOUTS) or ("classic",)
    topic_styles = tuple(s for s in merged.get("styles", ()) if s in styles) or (next(iter(styles)),)
    labels = tuple(merged.get("labels") or ("Prieš", "Po"))[:2]
    content = TopicContent(
        kicker=str(merged.get("kicker", "")), title=str(merged.get("cover_title") or title),
        subtitle=str(merged.get("subtitle", "")), items=items,
        cta_title=str(merged.get("cta_title", "Išsaugok ir *pasidalink*")),
        cta_body=str(merged.get("cta_body", "Parašyk komentaruose, kas tau naudingiausia")),
        button=str(merged.get("button", "Išsaugok")), labels=labels if len(labels) == 2 else ("Prieš", "Po"),
        swipe=str(merged.get("swipe", "Braukite →")),
    )
    return Topic(
        key=key, category=category, title=title, content=content, layouts=layouts, styles=topic_styles,
        tags=tuple(str(t) for t in merged.get("tags", ())), popularity=int(merged.get("popularity", 50)),
        added=str(merged.get("added", "")), order=order,
    )


def load(extra_dirs: Iterable[Path] = ()) -> Library:
    """Reads every style and topic file (built-in, then the person's own,
    then `extra_dirs`). A later topic with the same key replaces an
    earlier one, so the person can also restyle a built-in topic."""
    library = Library()
    problems = library.problems
    folders = [DATA_DIR / "topics", user_template_dir(), *extra_dirs]

    style_files = [DATA_DIR / "styles.json"] + [f for folder in folders[1:] for f in _json_files(folder)]
    for path in style_files:
        data = _read(path, problems) if path.is_file() else None
        for raw in (data or {}).get("styles", ()):
            try:
                style = DesignStyle.from_dict(raw)
            except (KeyError, TypeError, ValueError) as exc:
                problems.append(f"{path.name}: stilius praleistas ({exc})")
                continue
            library.styles[style.key] = style
    if not library.styles:
        raise RuntimeError("Nerasta nė vieno dizaino stiliaus (styles.json).")

    order = 0
    for folder in folders:
        for path in _json_files(folder):
            data = _read(path, problems)
            if data is None or "topics" not in data:
                continue
            raw_category = data.get("category") or {}
            category_key = str(raw_category.get("key", "")).strip() or path.stem
            if category_key not in library.categories:
                library.categories[category_key] = Category(
                    key=category_key, label=str(raw_category.get("label") or category_key),
                    icon=str(raw_category.get("icon", "")), order=int(raw_category.get("order", 100)),
                )
            defaults = data.get("defaults") or {}
            for raw in data["topics"]:
                order += 1
                try:
                    topic = _topic(raw, category_key, defaults, library.styles, order)
                except (TypeError, ValueError) as exc:
                    problems.append(f"{path.name}: tema praleista ({exc})")
                    continue
                library.topics[topic.key] = topic

    for topic in library.topics.values():
        for i, layout in enumerate(topic.layouts):
            library.templates.append(Template(
                id=f"{topic.key}.{layout}", topic=topic, layout=layout,
                style=topic.styles[(topic.order + i) % len(topic.styles)], order=topic.order * 100 + i,
            ))
    return library


_cache: Library | None = None


def library() -> Library:
    global _cache
    if _cache is None:
        _cache = load()
    return _cache


def reload() -> Library:
    global _cache
    _cache = None
    return library()


# --- searching -----------------------------------------------------------------------------

SORTS = {"all": "Visi", "newest": "Naujausi", "popular": "Populiariausi", "favorites": "⭐ Mano mėgstamiausi"}


def fold(text: str) -> str:
    """Lowercase without Lithuanian accents, so „rutina“ finds „Rutina“
    and „siandien“ finds „Šiandien“."""
    return "".join(c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn")


def _haystack(lib: Library, template: Template) -> str:
    topic = template.topic
    parts = [topic.title, topic.content.title, topic.content.kicker, *topic.tags, lib.category_label(topic.category),
             lib.style_label(template.style), template.layout_label]
    return fold(" ".join(parts))


def search(lib: Library, *, query: str = "", category: str | None = None, style: str | None = None,
           sort: str = "all", favorites: Iterable[str] = (), uses: dict[str, int] | None = None) -> list[Template]:
    """Templates matching every given filter. `style` keeps templates
    shown in that style; `sort` is one of SORTS."""
    words = fold(query).split()
    favorites = set(favorites)
    uses = uses or {}
    found = []
    for template in lib.templates:
        if category and template.topic.category != category:
            continue
        if style and template.style != style:
            continue
        if sort == "favorites" and template.id not in favorites:
            continue
        if words:
            haystack = _haystack(lib, template)
            if not all(word in haystack for word in words):
                continue
        found.append(template)
    if sort == "newest":
        found.sort(key=lambda t: (t.topic.added, t.order), reverse=True)
    elif sort == "popular":
        found.sort(key=lambda t: (popularity(t, uses), -t.order), reverse=True)
    return found


def popularity(template: Template, uses: dict[str, int]) -> int:
    """Built-in popularity plus how often the person used it."""
    return template.topic.popularity + 15 * uses.get(template.id, 0)


@dataclass(frozen=True)
class Inspiration:
    template: Template
    style: str
    layout: str


def inspire(lib: Library, rng: random.Random | None = None, *, category: str | None = None) -> Inspiration:
    """„Įkvėpti mane 🎲“: a random topic, a random design style and a
    random layout the topic supports."""
    rng = rng or random.Random()
    topics = [t for t in lib.topics.values() if not category or t.category == category] or list(lib.topics.values())
    topic = rng.choice(topics)
    layout = rng.choice(topic.layouts)
    style = rng.choice(sorted(lib.styles))
    template = lib.template(f"{topic.key}.{layout}")
    return Inspiration(template=template, style=style, layout=layout)


# --- building a carousel -------------------------------------------------------------------

def build_project(template: Template, *, style: str | None = None, name: str | None = None,
                  lib: Library | None = None) -> Project:
    """A new, fully editable carousel (not yet saved) from `template`,
    in `style` (default: the template's own)."""
    lib = lib or library()
    design = lib.styles.get(style or template.style) or lib.styles[template.style]
    project = Project(name=(name or template.name).strip() or template.name, format=TEMPLATE_FORMAT,
                      theme=design.theme())
    width, height = project.size
    ctx = Ctx(content=template.topic.content, style=design, width=width, height=height, margin=project.theme.margin + 10)
    project.slides = LAYOUTS[template.layout].build(ctx)
    return project


def uses_photo(project: Project) -> bool:
    from jarvis.carousel_studio.templates.layouts import PHOTO

    return any(e.kind == "image" and e.props.get("path") == PHOTO for s in project.slides for e in s.elements)
