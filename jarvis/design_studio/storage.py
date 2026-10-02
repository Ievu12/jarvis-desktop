"""Project directory management for AI Design Studio (module brief,
section 17: "Design Projects" with a fixed subfolder structure). One
directory per project under jarvis.config.DESIGN_STUDIO_PROJECTS_DIR,
named by the project's id:

  <DESIGN_STUDIO_PROJECTS_DIR>/<project_id>/
    variants/    - the 3 generated design variants (a later stage;
                    Stage 1 renders directly into this directory too,
                    as the project's one-and-only current design)
    uploads/     - any image the person uploaded FOR THIS project
                    (a product photo, a one-off image) - distinct from
                    the Brand Kit's own shared assets (module brief
                    section 6), which live under
                    jarvis.config.DESIGN_STUDIO_BRAND_KIT_DIR instead,
                    since a Brand Kit asset is reused across every
                    project, not scoped to one
    exports/     - the final exported PNG/JPG file(s)

Unlike jarvis.video_studio.storage.create_project() (which copies an
uploaded SOURCE FILE, since a video project always starts from one), a
design project starts from a TEXT PROMPT - there is no source file to
copy at creation time. create_project() here therefore takes no
argument beyond an optional original prompt string (stored by
jarvis.design_studio.db, not this module) and just creates the empty
directory structure; save_uploaded_asset() is a separate call for when
a person later attaches an image to an existing project.

jarvis.design_studio.db stores each project's metadata (id, original
prompt, generated brief, format, style, status) separately - this
module only manages the ON-DISK layout, never touches SQLite. Same
"direct file I/O from a GUI view, no agent-tool sandbox check"
precedent as jarvis.video_studio.storage - see
jarvis.config.DESIGN_STUDIO_PROJECTS_DIR's own docstring for the full
reasoning.
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from jarvis.config import DESIGN_STUDIO_PROJECTS_DIR

_SUBDIRS = ("variants", "uploads", "exports")

# Formats supported for an uploaded logo/brand/product/personal image
# (module brief, section 6) - image formats only, unrelated to
# jarvis.video_studio.storage.SUPPORTED_EXTENSIONS (video formats).
SUPPORTED_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".webp"})


class StorageError(Exception):
    """Raised for a project-storage-layer failure (unsupported image
    extension, disk full during copy, missing source file) - always
    with a human-readable message, per the module's brief."""


@dataclass(frozen=True)
class DesignProject:
    """A project's on-disk location - jarvis.design_studio.db
    separately stores this project's metadata row; this dataclass is
    purely about paths, returned by create_project() and
    project_paths()."""

    project_id: str
    root_dir: Path

    @property
    def variants_dir(self) -> Path:
        return self.root_dir / "variants"

    @property
    def uploads_dir(self) -> Path:
        return self.root_dir / "uploads"

    @property
    def exports_dir(self) -> Path:
        return self.root_dir / "exports"


def create_project() -> DesignProject:
    """Creates a brand-new, empty design project directory (variants/
    uploads/exports subdirs) under DESIGN_STUDIO_PROJECTS_DIR. Unlike
    jarvis.video_studio.storage.create_project(), there is no source
    file to copy - a design project starts from a text prompt (stored
    by jarvis.design_studio.db, not here). Raises StorageError only if
    directory creation itself fails (e.g. disk full/permissions)."""
    project_id = uuid.uuid4().hex
    root_dir = DESIGN_STUDIO_PROJECTS_DIR / project_id
    try:
        for subdir in _SUBDIRS:
            (root_dir / subdir).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        shutil.rmtree(root_dir, ignore_errors=True)
        raise StorageError(f"Couldn't create a new design project: {e}") from e
    return DesignProject(project_id=project_id, root_dir=root_dir)


def save_uploaded_asset(project: DesignProject, source_path: Path) -> Path:
    """Copies `source_path` (an image the person picked via the upload
    dialog) into `project`'s uploads/ directory - the ORIGINAL file is
    never modified, moved, or deleted; this always COPIES it, same
    "preserve the actual product appearance" rule
    jarvis.design_studio.render's own docstring states for how an
    uploaded image is later composited. Raises StorageError if the
    extension isn't one of SUPPORTED_IMAGE_EXTENSIONS, the source
    doesn't exist, or the copy fails. Returns the new file's path
    inside the project."""
    if not source_path.is_file():
        raise StorageError(f"File not found: {source_path}")
    if source_path.suffix.lower() not in SUPPORTED_IMAGE_EXTENSIONS:
        supported = ", ".join(sorted(e.lstrip(".").upper() for e in SUPPORTED_IMAGE_EXTENSIONS))
        raise StorageError(
            f"Unsupported file type '{source_path.suffix}'. Supported formats: {supported}."
        )

    destination = project.uploads_dir / source_path.name
    try:
        shutil.copy2(source_path, destination)
    except OSError as e:
        raise StorageError(f"Couldn't copy {source_path.name} into the project: {e}") from e
    return destination


def project_paths(project_id: str) -> DesignProject:
    """Reconstructs a DesignProject's paths for an already-created
    project (e.g. when reopening a saved project from
    jarvis.design_studio.db) - does not touch disk or validate
    existence."""
    return DesignProject(project_id=project_id, root_dir=DESIGN_STUDIO_PROJECTS_DIR / project_id)


def delete_project(project_id: str) -> None:
    """Permanently deletes a project's entire directory - an explicit,
    person-initiated action only, never called automatically by any
    generation step in this package. Does nothing (not an error) if
    the project directory doesn't exist, matching
    jarvis.video_studio.storage.delete_project()'s own idempotent-
    delete convention."""
    root_dir = DESIGN_STUDIO_PROJECTS_DIR / project_id
    shutil.rmtree(root_dir, ignore_errors=True)


def list_project_ids() -> list[str]:
    """Every project id currently on disk - used to reconcile "projects
    the database knows about" against "project folders that actually
    still exist", not as the primary listing source
    (jarvis.design_studio.db.list_projects() is)."""
    if not DESIGN_STUDIO_PROJECTS_DIR.is_dir():
        return []
    return sorted(p.name for p in DESIGN_STUDIO_PROJECTS_DIR.iterdir() if p.is_dir())
