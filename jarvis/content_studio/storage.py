"""Project directory management for AI Content Studio. One directory
per project under jarvis.config.CONTENT_STUDIO_PROJECTS_DIR, named by
the project's id:

  <CONTENT_STUDIO_PROJECTS_DIR>/<project_id>/
    pdf/    - generated PDF files (jarvis.content_studio.pdf_export) -
              the ONLY binary asset this package genuinely owns; every
              other content type's real assets (Reel scenes/exports,
              Design Studio renders) live in THEIR OWN modules' own
              projects directories - see this package's own __init__.py
              docstring for why Content Studio never copies those files
              into its own tree.

jarvis.content_studio.db stores each project's metadata (id, topic,
content plan, linked reel_generator/design_studio project ids, workflow
status) separately - this module only manages the ON-DISK layout,
matching jarvis.reel_generator.storage/jarvis.design_studio.storage's
own exact split (see either module's docstring for the full
rationale)."""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from jarvis.config import CONTENT_STUDIO_PROJECTS_DIR

_SUBDIRS = ("pdf",)


class StorageError(Exception):
    """Raised for a project-storage-layer failure - always with a
    human-readable message, per this codebase's established "never
    silently fail" convention."""


@dataclass(frozen=True)
class ContentProject:
    """A project's on-disk location - jarvis.content_studio.db
    separately stores this project's metadata row; this dataclass is
    purely about paths, returned by create_project() and
    project_paths()."""

    project_id: str
    root_dir: Path

    @property
    def pdf_dir(self) -> Path:
        return self.root_dir / "pdf"


def create_project() -> ContentProject:
    """Creates a brand-new project directory (with its fixed subfolder
    structure) under CONTENT_STUDIO_PROJECTS_DIR. Raises StorageError
    only if the directory itself can't be created (e.g. disk full/
    permission denied)."""
    project_id = uuid.uuid4().hex
    root_dir = CONTENT_STUDIO_PROJECTS_DIR / project_id
    try:
        for subdir in _SUBDIRS:
            (root_dir / subdir).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise StorageError(f"Couldn't create a new Content Studio project: {e}") from e
    return ContentProject(project_id=project_id, root_dir=root_dir)


def project_paths(project_id: str) -> ContentProject:
    """Reconstructs a ContentProject's paths for an already-created
    project - does not touch disk or validate existence."""
    return ContentProject(project_id=project_id, root_dir=CONTENT_STUDIO_PROJECTS_DIR / project_id)


def delete_project(project_id: str) -> None:
    """Permanently deletes a project's entire directory - an explicit,
    person-initiated action only. Does nothing (not an error) if the
    project directory doesn't exist. Never deletes the linked
    reel_generator/design_studio project this Content Studio project
    may point at - those are separate projects with their own lifecycle,
    same "two-step, never auto-cascaded" reasoning as
    jarvis.reel_generator.db's own delete functions document."""
    shutil.rmtree(CONTENT_STUDIO_PROJECTS_DIR / project_id, ignore_errors=True)


def list_project_ids() -> list[str]:
    """Every project id currently on disk - used to reconcile "projects
    the database knows about" against "project folders that actually
    still exist", not as the primary listing source
    (jarvis.content_studio.db.list_projects() is)."""
    if not CONTENT_STUDIO_PROJECTS_DIR.is_dir():
        return []
    return sorted(p.name for p in CONTENT_STUDIO_PROJECTS_DIR.iterdir() if p.is_dir())
