"""GUI tests for „📚 Šablonų biblioteka“ in the carousel studio: opening
it from the project library, filters, search, sorting, favorites,
preview in another style, „Įkvėpti mane 🎲“ and „Naudoti šį šabloną“
opening the new carousel in the editor."""

from __future__ import annotations

import random

import customtkinter as ctk
import pytest

from jarvis.carousel_studio import storage
from jarvis.carousel_studio.templates import catalog
from jarvis.gui.views.carousel_studio.dashboard import CarouselStudioView
from jarvis.gui.views.carousel_studio.template_library import CARD_W, PAGE_SIZE, PREVIEW_W


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CAROUSEL_STUDIO_DB_FILE", tmp_path / "carousel.db")
    monkeypatch.setattr(storage, "CAROUSEL_STUDIO_PROJECTS_DIR", tmp_path / "projects")
    monkeypatch.setattr(catalog, "USER_TEMPLATE_DIR", tmp_path / "my_templates")
    monkeypatch.setattr(catalog, "_cache", None)


@pytest.fixture(scope="module")
def root():
    r = ctk.CTk()
    r.withdraw()
    yield r
    r.destroy()


@pytest.fixture(autouse=True)
def _destroy_children(root):
    yield
    for child in list(root.winfo_children()):
        child.destroy()


@pytest.fixture
def screen(root):
    view = CarouselStudioView(root)
    view.pack(fill="both", expand=True)
    view.show_templates()
    root.update_idletasks()
    return view, view.templates


def test_library_opens_from_the_project_list_and_goes_back(screen):
    view, s = screen
    assert s.winfo_manager() and not view.library.winfo_manager()
    assert len(s.results) == len(s.lib.templates) >= 250
    assert len(s._cards) == PAGE_SIZE  # one page, "Rodyti daugiau" for the rest
    s._show_more()
    assert len(s._cards) == 2 * PAGE_SIZE
    s.finish_thumbnails()
    image = s._images[-1].cget("light_image")
    assert image.size == (CARD_W, round(CARD_W * 1350 / 1080))  # 4:5
    view.show_library()
    assert view.library.winfo_manager() and not s.winfo_manager()


def test_category_style_search_and_combined_filters(screen):
    _view, s = screen
    s.set_filters(category="yoga")
    assert s.results and all(t.topic.category == "yoga" for t in s.results)
    assert s.count_label.cget("text") == f"Rasta: {len(s.results)}"
    s.set_filters(style="wellness")
    assert s.results and all(t.topic.category == "yoga" and t.style == "wellness" for t in s.results)
    s.set_filters(category="", style="", query="kvepavimas")
    assert {t.topic.key for t in s.results} == {"yoga_breathing"}
    s.set_filters(query="niekas tokio nera")
    assert s.results == [] and "Nieko nerasta" in s.grid_frame.winfo_children()[0].cget("text")


def test_favorites_and_sorting(screen):
    _view, s = screen
    first = s.results[0]
    s.toggle_favorite(first)
    assert s._cards[first.id].star_button.cget("text") == "★"
    s.set_filters(sort="favorites")
    assert [t.id for t in s.results] == [first.id]
    s.toggle_favorite(first)  # unstarring in the favorites view removes it
    assert s.results == []
    s.set_filters(sort="popular")
    popularity = [t.topic.popularity for t in s.results]
    assert popularity == sorted(popularity, reverse=True)
    s.set_filters(sort="newest")
    assert len(s.results) == len(s.lib.templates)


def test_preview_shows_topic_style_slides_and_can_switch_style(screen):
    _view, s = screen
    template = s.lib.template("edu_3_mistakes.two_panel")
    s.select(template)
    info = s.preview_info.cget("text")
    assert "3 klaidos" in s.preview_title.cget("text")
    assert f"Skaidrių: {template.slide_count}" in info and "4:5" in info and s.lib.style_label(template.style) in info
    assert len(s.strip.winfo_children()) == template.slide_count
    s.show_slide(2)
    big = s._images[-1].cget("light_image")
    assert big.size[0] == PREVIEW_W and abs(big.size[1] - 1350 * PREVIEW_W / 1080) <= 1  # 4:5
    before = s.slide_image(template, s.preview_style, 0, 80).tobytes()
    s.set_preview_style("dark_luxury")
    assert "Dark luxury" in s.preview_info.cget("text")
    assert s.slide_image(template, "dark_luxury", 0, 80).tobytes() != before


def test_inspire_me_picks_a_random_combination(screen):
    _view, s = screen
    s._rng = random.Random(3)
    idea = s.inspire()
    assert s.selected is idea.template and s.preview_style == idea.style
    assert s.preview_title.cget("text").startswith("🎲")


def test_use_this_template_opens_it_in_the_editor(screen, root):
    view, s = screen
    s.set_filters(category="sales")
    template = s.results[0]
    s.select(template, style="luxury")
    s.name_entry.insert(0, "Rudens akcija")
    project = s.use_selected()
    root.update_idletasks()
    assert view.editor.winfo_manager() and not s.winfo_manager()
    doc = view.editor.doc
    assert doc.project.id == project.id and doc.project.name == "Rudens akcija"
    assert doc.project.format == "portrait" and len(doc.project.slides) == template.slide_count
    assert doc.project.theme.colors == s.lib.styles["luxury"].colors
    view.show_library()
    assert [p.name for p in storage.list_projects()] == ["Rudens akcija"]


def test_blank_carousel_creation_still_works(root):
    view = CarouselStudioView(root)
    view.pack(fill="both", expand=True)
    view.library.name_entry.insert(0, "Tuščia")
    view.library._create()
    assert view.editor.doc.project.name == "Tuščia" and len(view.editor.doc.project.slides) == 5
