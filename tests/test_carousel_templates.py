"""The carousel template library (jarvis.carousel_studio.templates):
categories, topics and design styles from JSON, every template building
an editable 4:5 carousel, layout variety, search/filters/sorting,
favorites, „Įkvėpti mane 🎲“, using a template, and adding templates
with only a JSON file."""

from __future__ import annotations

import json
import random
import zipfile

import pytest

from jarvis.carousel_studio import export, storage
from jarvis.carousel_studio.editor import CarouselDocument
from jarvis.carousel_studio.model import Project, starter_slides
from jarvis.carousel_studio.render import render_slide
from jarvis.carousel_studio.templates import catalog, usage, use_template
from jarvis.carousel_studio.templates.layouts import LAYOUTS, PHOTO
from jarvis.carousel_studio.themes import PALETTES


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CAROUSEL_STUDIO_DB_FILE", tmp_path / "carousel.db")
    monkeypatch.setattr(storage, "CAROUSEL_STUDIO_PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(catalog, "USER_TEMPLATE_DIR", tmp_path / "my_templates")


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    saved = catalog.USER_TEMPLATE_DIR
    catalog.USER_TEMPLATE_DIR = tmp_path_factory.mktemp("no_user_templates")
    try:
        return catalog.load()
    finally:
        catalog.USER_TEMPLATE_DIR = saved


REQUESTED = {
    "beauty": ["Produkto pristatymas", "Produkto privalumai", "Skincare rutina", "Ingredientų paaiškinimas", "Prieš / po",
               "TOP produktai", "Naujiena", "Produkto apžvalga", "Rekomendacijos", "Odos priežiūros patarimai", "K-beauty", "Makiažas"],
    "lifestyle": ["Mano diena", "Morning routine", "Evening routine", "Self-care", "Savaitės ritualas", "Gyvenimo būdas",
                  "Mėgstamiausi dalykai", "Bucket list", "Savaitės recap"],
    "yoga": ["Jogos pozos", "Jogos rutina", "5 minučių rutina", "Rytinė joga", "Vakarinė joga", "Kvėpavimo pratimai", "Meditacija",
             "Wellness patarimai", "Saviugda", "Motyvacija"],
    "education": ["5 dalykai, kuriuos turi žinoti", "3 klaidos", "7 patarimai", "Žingsnis po žingsnio", "DUK", "Mitas / faktas",
                  "Problema / sprendimas", "Checklist", "Mini gidai", "Paaiškinimai"],
    "business": ["Verslo patarimai", "Instagram patarimai", "Reels patarimai", "Turinio idėjos", "Pardavimo patarimai",
                 "Produkto marketingas", "CTA šablonai", "Klientų pritraukimas", "El. prekyba", "Asmeninis prekės ženklas"],
    "sales": ["Produkto pristatymas", "Akcija", "Nauja prekė", "Ribotas pasiūlymas", "Bestseleris", "Produkto palyginimas",
              "Produkto savybės", "Kodėl verta rinktis", "Dovanos idėja", "Rinkinio pristatymas"],
    "personal": ["Mano istorija", "Apie mane", "Mano kelionė", "Mano tikslai", "Pamokos, kurias išmokau", "Užkulisiai",
                 "Asmeninė nuomonė", "Q&A", "Storytelling"],
    "seasonal": ["Pavasaris", "Vasara", "Ruduo", "Žiema", "Kalėdos", "Naujieji metai", "Valentino diena", "Moters diena", "Velykos",
                 "Black Friday", "Gimtadienis"],
    "engagement": ["Klausimas auditorijai", "Apklausa", "Rinkis A arba B", "Ar žinojai?", "Komentarų skatinimas",
                   "Save/share karuselė", "Tag someone", "Quiz", "This or that"],
    "inspiration": ["Citatos", "Motyvacija", "Mindset", "Savęs priėmimas", "Tikslai", "Produktyvumas", "Pozityvumas"],
}
STYLE_LABELS = ["Minimal", "Luxury", "Soft", "Feminine", "Modern", "Editorial", "Clean", "Beige", "Nude", "Pastel", "Black & White",
                "Bold", "Colorful", "K-beauty", "Organic", "Wellness", "Elegant", "Vintage", "Dark luxury"]


# --- the library ----------------------------------------------------------------------------

def test_every_requested_category_topic_and_style_is_in_the_library(lib):
    assert lib.problems == []
    assert set(REQUESTED) <= set(lib.categories)
    for category, titles in REQUESTED.items():
        have = {t.title for t in lib.topics.values() if t.category == category}
        assert set(titles) <= have, (category, set(titles) - have)
    assert sorted(s.label for s in lib.styles.values()) == sorted(STYLE_LABELS)
    assert len(lib.templates) >= 250


def test_every_style_has_templates_and_every_topic_several_layouts(lib):
    for style in lib.styles:
        assert sum(t.style == style for t in lib.templates) >= 5, style
    for topic in lib.topics.values():
        mine = [t for t in lib.templates if t.topic is topic]
        assert len(mine) >= 3 and len({t.layout for t in mine}) == len(mine), topic.key
    assert len(LAYOUTS) >= 12


def test_every_template_builds_an_editable_4_5_carousel_without_layout_problems(lib):
    for template in lib.templates:
        project = catalog.build_project(template, lib=lib)
        assert project.format == "portrait" and project.size == (1080, 1350)
        assert len(project.slides) == template.slide_count
        assert project.slides[0].role == "cover" and project.slides[-1].role == "cta"
        assert all(s.role == "content" for s in project.slides[1:-1])
        assert {e.kind for s in project.slides for e in s.elements} <= {"text", "shape", "line", "image"}
        assert export.check_layout(project) == [], template.id
        Project.from_dict(project.to_dict())  # saves like any carousel


def test_first_middle_and_last_slides_differ_and_layouts_of_a_topic_differ(lib):
    def shape(slide):
        return tuple((e.kind, round(e.x), round(e.y), round(e.w)) for e in slide.elements)

    topic = lib.topics["beauty_skincare_routine"]
    signatures = set()
    for template in (t for t in lib.templates if t.topic is topic):
        project = catalog.build_project(template, style="minimal", lib=lib)
        first, middle, last = (shape(project.slides[i]) for i in (0, 1, -1))
        assert len({first, middle, last}) == 3
        signatures.add((first, middle, last))
    assert len(signatures) == 3


def test_style_becomes_the_theme_and_the_editor_palette_switch_still_works(lib):
    template = lib.template("beauty_product_intro.product")
    project = catalog.build_project(template, style="dark_luxury", lib=lib)
    assert project.theme.colors == lib.styles["dark_luxury"].colors
    assert project.theme.heading_font == lib.styles["dark_luxury"].heading_font
    texts = [e.props["text"] for s in project.slides for e in s.elements if e.kind == "text"]
    doc = CarouselDocument(project)
    doc.set_palette("pastel")
    assert project.theme.colors == PALETTES["pastel"][1]
    assert [e.props["text"] for s in project.slides for e in s.elements if e.kind == "text"] == texts
    doc.undo()
    assert doc.project.theme.colors == lib.styles["dark_luxury"].colors


# --- search, filters, sorting -------------------------------------------------------------------

def test_search_ignores_case_and_lithuanian_accents(lib):
    found = {t.topic.key for t in catalog.search(lib, query="RUTINA")}
    assert {"beauty_skincare_routine", "yoga_5min"} <= found
    assert {t.topic.key for t in catalog.search(lib, query="kaledos")} == {"season_christmas"}
    assert catalog.search(lib, query="zzzz nieko") == []


def test_category_style_and_combined_filters(lib):
    beauty = catalog.search(lib, category="beauty")
    assert beauty and all(t.topic.category == "beauty" for t in beauty)
    luxury = catalog.search(lib, style="luxury")
    assert luxury and all(t.style == "luxury" for t in luxury)
    both = catalog.search(lib, category="beauty", style="luxury", query="rutina")
    assert all(t.topic.category == "beauty" and t.style == "luxury" for t in both)


def test_favorites_popular_and_newest(lib, tmp_path):
    chosen = lib.templates[40]
    assert usage.toggle_favorite(chosen.id) is True
    assert [t.id for t in catalog.search(lib, sort="favorites", favorites=usage.favorites())] == [chosen.id]
    assert usage.toggle_favorite(chosen.id) is False
    assert usage.favorites() == set()

    rarely = min(lib.templates, key=lambda t: t.topic.popularity)
    for _ in range(5):
        usage.record_use(rarely.id)
    assert catalog.search(lib, sort="popular", uses=usage.uses())[0].id == rarely.id

    folder = tmp_path / "my_templates"
    folder.mkdir()
    (folder / "naujas.json").write_text(json.dumps({
        "category": {"key": "beauty"},
        "topics": [{"key": "my_new", "title": "Mano naujas", "added": "2030-01-01", "items": [["A", "a"], ["B", "b"]],
                    "layouts": ["classic"], "styles": ["soft"]}],
    }), encoding="utf-8")
    newest = catalog.search(catalog.load(), sort="newest")
    assert newest[0].topic.key == "my_new"


def test_inspire_combines_a_topic_style_and_layout(lib):
    seen = set()
    for seed in range(30):
        idea = catalog.inspire(lib, random.Random(seed))
        assert idea.layout in idea.template.topic.layouts and idea.style in lib.styles
        seen.add((idea.template.topic.key, idea.style, idea.layout))
    assert len(seen) > 20
    assert catalog.inspire(lib, random.Random(1), category="yoga").template.topic.category == "yoga"


# --- using a template -----------------------------------------------------------------------------

def test_use_template_saves_a_normal_project_with_its_placeholder_photo(lib, tmp_path):
    template = lib.template("sale_discount.product")
    project = use_template(template, style="bold", name="Mano akcija", lib=lib)
    loaded = storage.load_project(project.id)
    assert loaded.name == "Mano akcija" and loaded.format == "portrait" and len(loaded.slides) == template.slide_count
    assert (storage.assets_dir(project.id) / PHOTO).is_file()
    assert [s.name for s in storage.list_projects()] == ["Mano akcija"]
    assert usage.uses() == {template.id: 1}

    image = render_slide(loaded, loaded.slides[0], assets_dir=storage.assets_dir(project.id))
    assert image.size == (1080, 1350)
    zip_path = export.export_zip(loaded, tmp_path / "out.zip", assets_dir=storage.assets_dir(project.id))
    assert len(zipfile.ZipFile(zip_path).namelist()) == template.slide_count

    doc = CarouselDocument(loaded)  # every editor operation works on it
    heading = next(e for e in loaded.slides[0].elements if e.kind == "text")
    doc.update_element(0, heading.id, text="Kitas tekstas")
    doc.duplicate_slide(1)
    assert len(loaded.slides) == template.slide_count + 1


def test_old_blank_carousel_is_unchanged():
    project = storage.create_project("Tuščia", "portrait", 5)
    fresh = starter_slides(5, project.size, project.theme.margin)
    assert [s.role for s in project.slides] == [s.role for s in fresh]
    assert project.theme.palette_key == "minimal"


# --- adding templates without code ----------------------------------------------------------------

def test_new_category_topic_and_style_come_from_a_json_file(tmp_path):
    folder = tmp_path / "my_templates"
    folder.mkdir()
    (folder / "mano.json").write_text(json.dumps({
        "styles": [{"key": "mint", "label": "Mint", "colors": {"background": "#F0FFF8", "surface": "#FFFFFF", "text": "#123",
                                                                  "primary": "#1A9E75", "accent": "#F28FAD"}}],
        "category": {"key": "food", "label": "Maistas", "icon": "🍰", "order": 11},
        "topics": [{"key": "food_recipe", "title": "Receptas", "cover_title": "Mano *receptas*", "items": [["Ingredientai", "..."],
                    ["Gaminimas", "..."]], "layouts": ["steps", "card", "no_such_layout"], "styles": ["mint"]}],
    }), encoding="utf-8")
    (folder / "sugadintas.json").write_text("{ne json", encoding="utf-8")
    lib = catalog.load()
    assert lib.categories["food"].label == "Maistas"
    assert [t.id for t in lib.templates if t.topic.key == "food_recipe"] == ["food_recipe.steps", "food_recipe.card"]
    project = catalog.build_project(lib.template("food_recipe.steps"), lib=lib)
    assert project.theme.colors["primary"] == "#1A9E75" and len(project.slides) == 4
    assert any("sugadintas.json" in p for p in lib.problems)
