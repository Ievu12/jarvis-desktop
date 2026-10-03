"""Carousel projects on disk: one folder per project
(CAROUSEL_STUDIO_PROJECTS_DIR/<id>/ holding project.json, assets/ and
thumb.png) plus a small SQLite index (CAROUSEL_STUDIO_DB_FILE) so the
project library lists without parsing every project.json.

project.json is the source of truth; the index row is rewritten on
every save and rebuilt from the folders if it is ever missing."""

from __future__ import annotations

import json
import shutil
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from PIL import Image

from jarvis.config import CAROUSEL_STUDIO_DB_FILE, CAROUSEL_STUDIO_PROJECTS_DIR
from jarvis.carousel_studio.model import DEFAULT_FORMAT, Project, starter_slides
from jarvis.carousel_studio.themes import Theme

_SCHEMA = """
CREATE TABLE IF NOT EXISTS carousel_projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    format TEXT NOT NULL,
    slide_count INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""
THUMB_WIDTH = 216
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")


class StorageError(Exception):
    pass


@dataclass(frozen=True)
class ProjectSummary:
    id: str
    name: str
    format: str
    slide_count: int
    created_at: str
    updated_at: str

    @property
    def thumbnail_path(self) -> Path:
        return project_dir(self.id) / "thumb.png"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    CAROUSEL_STUDIO_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(CAROUSEL_STUDIO_DB_FILE))
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def project_dir(project_id: str) -> Path:
    if not project_id or "/" in project_id or "\\" in project_id or ".." in project_id:
        raise StorageError("Netinkamas projekto ID.")
    return CAROUSEL_STUDIO_PROJECTS_DIR / project_id


def assets_dir(project_id: str) -> Path:
    return project_dir(project_id) / "assets"


def create_project(name: str, fmt: str = DEFAULT_FORMAT, slide_count: int = 5, theme: Theme | None = None) -> Project:
    project = Project(name=name.strip() or "Nauja karuselė", format=fmt, theme=theme or Theme())
    project.slides = starter_slides(slide_count, project.size, project.theme.margin)
    project.created_at = _now()
    save_project(project)
    return project


def save_project(project: Project, *, thumbnail: Image.Image | None = None) -> None:
    folder = project_dir(project.id)
    folder.mkdir(parents=True, exist_ok=True)
    project.updated_at = _now()
    if not project.created_at:
        project.created_at = project.updated_at
    tmp = folder / "project.json.tmp"
    tmp.write_text(json.dumps(project.to_dict(), ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(folder / "project.json")
    if thumbnail is None:
        thumbnail = _render_thumbnail(project)
    if thumbnail is not None:
        thumbnail.save(folder / "thumb.png")
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO carousel_projects (id, name, format, slide_count, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (project.id, project.name, project.format, len(project.slides), project.created_at, project.updated_at),
        )


def _render_thumbnail(project: Project) -> Image.Image | None:
    if not project.slides:
        return None
    from jarvis.carousel_studio.render import render_slide

    return render_slide(project, project.slides[0], scale=THUMB_WIDTH / project.size[0], assets_dir=assets_dir(project.id))


def load_project(project_id: str) -> Project:
    path = project_dir(project_id) / "project.json"
    try:
        return Project.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except FileNotFoundError as exc:
        raise StorageError("Projektas nerastas.") from exc
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise StorageError(f"Projekto failas sugadintas: {exc}") from exc


def list_projects() -> list[ProjectSummary]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT id, name, format, slide_count, created_at, updated_at FROM carousel_projects ORDER BY updated_at DESC"
        ).fetchall()
    summaries = [ProjectSummary(*row) for row in rows if (project_dir(row[0]) / "project.json").is_file()]
    if not rows and CAROUSEL_STUDIO_PROJECTS_DIR.is_dir():
        # Index missing (e.g. deleted DB): rebuild it from the folders.
        for folder in CAROUSEL_STUDIO_PROJECTS_DIR.iterdir():
            if (folder / "project.json").is_file():
                try:
                    project = load_project(folder.name)
                except StorageError:
                    continue
                with _connect() as conn:
                    conn.execute(
                        "INSERT OR REPLACE INTO carousel_projects VALUES (?, ?, ?, ?, ?, ?)",
                        (project.id, project.name, project.format, len(project.slides), project.created_at or _now(), project.updated_at or _now()),
                    )
                summaries.append(ProjectSummary(project.id, project.name, project.format, len(project.slides), project.created_at, project.updated_at))
        summaries.sort(key=lambda s: s.updated_at, reverse=True)
    return summaries


def rename_project(project_id: str, name: str) -> Project:
    project = load_project(project_id)
    project.name = name.strip() or project.name
    save_project(project)
    return project


def duplicate_project(project_id: str) -> Project:
    source = load_project(project_id)
    copy_ = Project.from_dict(source.to_dict())
    copy_.id = uuid.uuid4().hex
    copy_.name = f"{source.name} (kopija)"
    copy_.created_at = ""
    src_assets = assets_dir(project_id)
    if src_assets.is_dir():
        shutil.copytree(src_assets, assets_dir(copy_.id))
    save_project(copy_)
    return copy_


def delete_project(project_id: str) -> None:
    folder = project_dir(project_id)
    if folder.is_dir():
        shutil.rmtree(folder)
    with _connect() as conn:
        conn.execute("DELETE FROM carousel_projects WHERE id = ?", (project_id,))


def import_asset(project_id: str, source: str | Path) -> tuple[str, tuple[int, int]]:
    """Copies an image into the project's assets/ and returns its
    project-relative path and pixel size. The original file is never
    moved or modified."""
    source = Path(source)
    if source.suffix.lower() not in IMAGE_EXTENSIONS:
        raise StorageError("Palaikomi formatai: PNG, JPG, WEBP, GIF, BMP.")
    try:
        with Image.open(source) as img:
            size = img.size
    except OSError as exc:
        raise StorageError(f"Nepavyko atidaryti paveikslėlio: {exc}") from exc
    folder = assets_dir(project_id)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{uuid.uuid4().hex[:8]}_{source.name}"
    shutil.copy2(source, target)
    return target.name, size
