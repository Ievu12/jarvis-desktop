"""Carousel export: each slide as PNG/JPG at exact Instagram size, the
whole carousel as a ZIP of numbered files in posting order, or a PDF
for review. check_layout() lists anything that would look cut off so
the UI can warn before exporting."""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from jarvis.carousel_studio.model import Project
from jarvis.carousel_studio.render import element_bounds, layout_text, render_slide

JPG_QUALITY = 95


class ExportError(Exception):
    pass


@dataclass(frozen=True)
class LayoutIssue:
    slide_number: int  # 1-based
    element_id: str
    message: str


def safe_filename(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip().strip(".")
    return cleaned[:60] or "karusele"


def slide_filename(project: Project, index: int, fmt: str) -> str:
    width = 2 if len(project.slides) < 100 else 3
    return f"{safe_filename(project.name)}_{index + 1:0{width}d}.{'jpg' if fmt == 'jpg' else 'png'}"


def check_layout(project: Project) -> list[LayoutIssue]:
    """Text that does not fit its box, and elements reaching outside the
    slide. Elements deliberately bleeding off the edge (e.g. a big
    decorative circle) are reported too; the person decides."""
    issues: list[LayoutIssue] = []
    width, height = project.size
    for number, slide in enumerate(project.slides, start=1):
        for element in slide.elements:
            if element.props.get("hidden"):
                continue
            if element.kind == "text" and str(element.props.get("text", "")).strip():
                if layout_text(element, project.theme).overflow:
                    issues.append(LayoutIssue(number, element.id, "tekstas netelpa į savo laukelį"))
            left, top, right, bottom = element_bounds(element)
            tolerance = 1.0
            if element.kind in ("text", "image") and (left < -tolerance or top < -tolerance or right > width + tolerance or bottom > height + tolerance):
                issues.append(LayoutIssue(number, element.id, "elementas išeina už skaidrės krašto"))
    return issues


def _render(project: Project, index: int, assets_dir: Path | None):
    return render_slide(project, project.slides[index], scale=1.0, assets_dir=assets_dir)


def _encode(image, fmt: str) -> bytes:
    buffer = io.BytesIO()
    if fmt == "jpg":
        image.save(buffer, "JPEG", quality=JPG_QUALITY, subsampling=0, optimize=True)
    else:
        image.save(buffer, "PNG", optimize=True)
    return buffer.getvalue()


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    n = 2
    while (candidate := path.with_name(f"{stem} ({n}){suffix}")).exists():
        n += 1
    return candidate


def export_images(project: Project, out_dir: Path, *, fmt: str = "png", assets_dir: Path | None = None, indices: list[int] | None = None) -> list[Path]:
    """Writes one file per slide; never overwrites an existing file."""
    if fmt not in ("png", "jpg"):
        raise ExportError("Formatas turi būti PNG arba JPG.")
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for index in indices if indices is not None else range(len(project.slides)):
        target = _unique(out_dir / slide_filename(project, index, fmt))
        target.write_bytes(_encode(_render(project, index, assets_dir), fmt))
        written.append(target)
    return written


def export_zip(project: Project, zip_path: Path, *, fmt: str = "png", assets_dir: Path | None = None) -> Path:
    if fmt not in ("png", "jpg"):
        raise ExportError("Formatas turi būti PNG arba JPG.")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_STORED) as archive:
        for index in range(len(project.slides)):
            archive.writestr(slide_filename(project, index, fmt), _encode(_render(project, index, assets_dir), fmt))
        if project.caption.strip():
            archive.writestr("aprasymas.txt", project.caption)
    return zip_path


def export_pdf(project: Project, pdf_path: Path, *, assets_dir: Path | None = None) -> Path:
    pages = [_render(project, i, assets_dir) for i in range(len(project.slides))]
    if not pages:
        raise ExportError("Karuselėje nėra skaidrių.")
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(pdf_path, "PDF", resolution=150.0, save_all=True, append_images=pages[1:], title=project.name)
    return pdf_path
