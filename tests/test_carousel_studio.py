"""Tests for jarvis.carousel_studio: document model round-trip, edit
operations with undo/redo, the renderer, project storage and exports."""

from __future__ import annotations

import json
import re
import zipfile

import pytest
from PIL import Image

from jarvis.carousel_studio import export, render, storage
from jarvis.carousel_studio.editor import CarouselDocument, EditorError
from jarvis.carousel_studio.model import FORMATS, MAX_SLIDES, MIN_SLIDES, Project, make_text, starter_slides
from jarvis.carousel_studio.themes import PALETTES, Theme, contrast_ratio, normalize_hex


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "CAROUSEL_STUDIO_DB_FILE", tmp_path / "carousel.db")
    monkeypatch.setattr(storage, "CAROUSEL_STUDIO_PROJECTS_DIR", tmp_path / "projects")


def _project(count: int = 5, fmt: str = "portrait") -> Project:
    project = Project(name="Testas", format=fmt)
    project.slides = starter_slides(count, project.size, project.theme.margin)
    return project


# --- model / theme ----------------------------------------------------------------------

def test_project_round_trips_through_json():
    project = _project()
    project.slides[1].elements[0].rotation = 15
    data = json.loads(json.dumps(project.to_dict()))
    again = Project.from_dict(data)
    assert again.to_dict() == project.to_dict()


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_formats_have_instagram_sizes(fmt):
    _, w, h = FORMATS[fmt]
    assert w == 1080 and h in (1080, 1350, 1920)


def test_starter_slides_clamped_and_shaped():
    assert len(starter_slides(1, (1080, 1350), 80)) == MIN_SLIDES
    assert len(starter_slides(99, (1080, 1350), 80)) == MAX_SLIDES
    slides = starter_slides(4, (1080, 1350), 80)
    assert [s.role for s in slides] == ["cover", "content", "content", "cta"]


def test_theme_resolves_roles_and_hex():
    theme = Theme.from_palette("luxury")
    assert theme.resolve("theme:primary") == PALETTES["luxury"][1]["primary"]
    assert theme.resolve("#abc") == "#AABBCC"
    assert theme.resolve(None) is None
    assert normalize_hex("zzz") is None


def test_palettes_keep_text_readable():
    for key, (_, colors) in PALETTES.items():
        assert contrast_ratio(colors["text"], colors["background"]) >= 4.5, key


# --- editor -----------------------------------------------------------------------------

def test_slide_operations_and_limits():
    doc = CarouselDocument(_project(2))
    with pytest.raises(EditorError):
        doc.delete_slide(0)
    index = doc.add_slide(0)
    assert index == 1 and len(doc.slides) == 3
    first_id = doc.slides[0].id
    dup = doc.duplicate_slide(0)
    assert doc.slides[dup].id != first_id
    assert [e.props for e in doc.slides[dup].elements] == [e.props for e in doc.slides[0].elements]
    moved = doc.move_slide(0, 3)
    assert moved == 3 and doc.slides[3].id == first_id
    doc.set_slide_count(MAX_SLIDES + 5)
    assert len(doc.slides) == MAX_SLIDES
    with pytest.raises(EditorError):
        doc.add_slide()


def test_undo_redo_restores_every_change():
    doc = CarouselDocument(_project(3))
    original = doc.project.to_dict()
    el = doc.new_text(0, "heading")
    doc.update_element(0, el.id, text="Sveiki")
    doc.update_element(0, el.id, text="Sveiki, Ieva")  # coalesced with the previous edit
    doc.set_palette("autumn")
    assert doc.undo()  # palette
    assert doc.project.theme.palette_key == "minimal"
    assert doc.undo()  # text edits (one step)
    assert doc.slides[0].find(el.id).props["text"] == "Nauja antraštė"
    assert doc.undo()  # add
    assert doc.project.to_dict() == original
    assert not doc.undo()
    doc.redo(); doc.redo(); doc.redo()
    assert doc.slides[0].find(el.id).props["text"] == "Sveiki, Ieva"
    assert doc.project.theme.palette_key == "autumn"


def test_element_layering_duplicate_delete():
    doc = CarouselDocument(_project(2))
    a = doc.new_shape(0, "rect")
    b = doc.new_shape(0, "ellipse")
    doc.reorder_element(0, b.id, "back")
    assert doc.slides[0].elements[0].id == b.id
    dup = doc.duplicate_element(0, a.id)
    assert dup.id != a.id and dup.x == a.x + 30
    doc.delete_element(0, a.id)
    assert doc.slides[0].find(a.id) is None
    doc.copy_element_to_all(0, b.id)
    assert any(e.props.get("shape") == "ellipse" for e in doc.slides[1].elements)


def test_palette_switch_keeps_texts_and_literal_colors():
    doc = CarouselDocument(_project(3))
    el = doc.slides[0].elements[1]
    doc.update_element(0, el.id, color="#123456", text="Mano tekstas")
    doc.set_palette("vivid")
    assert el.props["text"] == "Mano tekstas"
    assert doc.project.theme.resolve(el.props["color"]) == "#123456"
    assert doc.project.theme.resolve("theme:background") == PALETTES["vivid"][1]["background"]


def test_format_change_keeps_elements_inside():
    doc = CarouselDocument(_project(3, "story"))
    doc.set_format("square")
    assert doc.project.size == (1080, 1080)
    for slide in doc.slides:
        for el in slide.elements:
            assert -1 <= el.y and el.y + el.h <= 1081


# --- rendering --------------------------------------------------------------------------

def test_markup_highlights_words():
    paragraphs = render.parse_markup("Labas *gražus pasauli* ir *tu*")
    assert paragraphs == [[("Labas", False), ("gražus", True), ("pasauli", True), ("ir", False), ("tu", True)]]


def test_autofit_shrinks_long_text_to_fit():
    el = make_text("heading", "Labai ilgas tekstas " * 20, x=0, y=0, w=600, h=200)
    layout = render.layout_text(el, Theme())
    assert not layout.overflow and layout.size < 96
    el.props["autofit"] = False
    assert render.layout_text(el, Theme()).overflow


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_render_slide_exact_size_and_scaled(fmt):
    project = _project(3, fmt)
    full = render.render_slide(project, project.slides[0])
    assert full.size == project.size
    half = render.render_slide(project, project.slides[0], scale=0.5)
    assert half.size == (project.size[0] // 2, project.size[1] // 2)


def test_render_uses_theme_background_and_rotated_image(tmp_path):
    project = _project(2)
    project.theme = Theme.from_palette("modern")
    photo = tmp_path / "red.png"
    Image.new("RGB", (400, 300), (255, 0, 0)).save(photo)
    doc = CarouselDocument(project)
    doc.slides[1].elements.clear()
    el = doc.new_image(1, str(photo), (400, 300))
    doc.update_element(1, el.id, rotation=30, radius=40)
    image = render.render_slide(project, project.slides[1])
    assert image.getpixel((2, 2)) == (0x0F, 0x17, 0x2A)
    cx, cy = (round(v) for v in el.center)
    assert image.getpixel((cx, cy)) == (255, 0, 0)


def test_gradient_background():
    project = _project(2)
    project.slides[0].elements.clear()
    project.slides[0].background = {"type": "gradient", "color1": "#000000", "color2": "#FFFFFF"}
    image = render.render_slide(project, project.slides[0])
    assert image.getpixel((540, 2))[0] < 10 and image.getpixel((540, 1347))[0] > 245


# --- storage ----------------------------------------------------------------------------

def test_project_library_lifecycle(tmp_path):
    project = storage.create_project("Jogos nauda rytais", "square", 4)
    assert (storage.project_dir(project.id) / "thumb.png").is_file()
    loaded = storage.load_project(project.id)
    assert loaded.name == "Jogos nauda rytais" and len(loaded.slides) == 4 and loaded.format == "square"
    photo = tmp_path / "p.jpg"
    Image.new("RGB", (50, 40), (0, 255, 0)).save(photo)
    rel, size = storage.import_asset(project.id, photo)
    assert size == (50, 40) and (storage.assets_dir(project.id) / rel).is_file()
    copy = storage.duplicate_project(project.id)
    assert copy.id != project.id and copy.name.endswith("(kopija)")
    assert (storage.assets_dir(copy.id) / rel).is_file()
    storage.rename_project(project.id, "Rytinė joga")
    names = {p.name for p in storage.list_projects()}
    assert names == {"Rytinė joga", "Jogos nauda rytais (kopija)"}
    storage.delete_project(project.id)
    assert [p.id for p in storage.list_projects()] == [copy.id]


def test_rejects_non_images_and_bad_ids(tmp_path):
    project = storage.create_project("x")
    bad = tmp_path / "notes.txt"
    bad.write_text("hi")
    with pytest.raises(storage.StorageError):
        storage.import_asset(project.id, bad)
    with pytest.raises(storage.StorageError):
        storage.project_dir("../etc")


def test_index_rebuilt_when_db_missing():
    project = storage.create_project("Atkurta")
    storage.CAROUSEL_STUDIO_DB_FILE.unlink()
    assert [p.id for p in storage.list_projects()] == [project.id]


# --- export -----------------------------------------------------------------------------

def test_export_png_jpg_zip_pdf(tmp_path):
    project = _project(3)
    project.name = "5 klaidos: oda/veidas"
    pngs = export.export_images(project, tmp_path / "png")
    assert [p.name for p in pngs] == ["5 klaidos odaveidas_01.png", "5 klaidos odaveidas_02.png", "5 klaidos odaveidas_03.png"]
    assert Image.open(pngs[0]).size == (1080, 1350)
    again = export.export_images(project, tmp_path / "png", indices=[0])
    assert again[0].name == "5 klaidos odaveidas_01 (2).png"  # never overwrites
    jpgs = export.export_images(project, tmp_path / "jpg", fmt="jpg")
    assert Image.open(jpgs[0]).format == "JPEG"
    project.caption = "Aprašymas"
    archive = export.export_zip(project, tmp_path / "k.zip", fmt="jpg")
    with zipfile.ZipFile(archive) as z:
        assert z.namelist() == ["5 klaidos odaveidas_01.jpg", "5 klaidos odaveidas_02.jpg", "5 klaidos odaveidas_03.jpg", "aprasymas.txt"]
    pdf = export.export_pdf(project, tmp_path / "k.pdf")
    assert len(re.findall(rb"/Type\s*/Page(?!s)", pdf.read_bytes())) == 3


def test_layout_check_flags_overflow_and_offcanvas():
    project = _project(2)
    el = project.slides[0].elements[1]
    el.props["autofit"] = False
    el.props["text"] = "Labai ilgas tekstas " * 30
    project.slides[1].elements[0].x = 1000
    issues = export.check_layout(project)
    messages = {(i.slide_number, i.message) for i in issues}
    assert (1, "tekstas netelpa į savo laukelį") in messages
    assert (2, "elementas išeina už skaidrės krašto") in messages
    assert export.check_layout(_project(3)) == []
