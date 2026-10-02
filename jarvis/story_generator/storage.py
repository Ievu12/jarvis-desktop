"""Project directory management for AI Storytelling Generator - mirrors
jarvis.reel_generator.storage's exact pattern (see that module's own
docstring for the full rationale). One directory per project under
jarvis.config.STORY_GENERATOR_PROJECTS_DIR, named by the project's id:

  <STORY_GENERATOR_PROJECTS_DIR>/<project_id>/
    scenes/    - per-scene visuals (text-card renders via
                 jarvis.reel_generator.scenes.render_all_scenes(),
                 called unmodified - see this package's own __init__.py
                 docstring).
    exports/   - final rendered MP4s (jarvis.reel_generator.export
                 .export_reel_video(), called unmodified).

jarvis.story_generator.db stores each project's metadata (id, original
idea text, StoryStructure, Storyboard) separately - this module only
manages the ON-DISK layout.

No sandbox/approval check on any function here - same "direct file I/O
from a GUI view" precedent as jarvis.config.STORY_GENERATOR_PROJECTS_DIR's
own docstring explains."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from jarvis.config import STORY_GENERATOR_PROJECTS_DIR

_SUBDIRS = ("scenes", "exports")


class StorageError(Exception):
    """Raised for a project-storage-layer failure - always with a
    human-readable message, per this module's own brief."""


@dataclass(frozen=True)
class StoryProject:
    """A project's on-disk location - jarvis.story_generator.db
    separately stores this project's metadata row; this dataclass is
    purely about paths, returned by create_project() and
    project_paths()."""

    project_id: str
    root_dir: Path

    @property
    def scenes_dir(self) -> Path:
        return self.root_dir / "scenes"

    @property
    def exports_dir(self) -> Path:
        return self.root_dir / "exports"


def create_project() -> StoryProject:
    """Creates a brand-new project directory (with its fixed subfolder
    structure) under STORY_GENERATOR_PROJECTS_DIR. Raises StorageError
    only if the directory itself can't be created (e.g. disk full/
    permission denied)."""
    project_id = uuid.uuid4().hex
    root_dir = STORY_GENERATOR_PROJECTS_DIR / project_id
    try:
        for subdir in _SUBDIRS:
            (root_dir / subdir).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise StorageError(f"Couldn't create a new Story project: {e}") from e
    return StoryProject(project_id=project_id, root_dir=root_dir)


def project_paths(project_id: str) -> StoryProject:
    """Reconstructs a StoryProject's paths for an already-created
    project (e.g. when reopening a saved project from
    jarvis.story_generator.db) - does not touch disk or validate
    existence."""
    return StoryProject(project_id=project_id, root_dir=STORY_GENERATOR_PROJECTS_DIR / project_id)


def delete_project(project_id: str) -> None:
    """Permanently deletes a project's entire directory - an explicit,
    person-initiated action only, never called automatically by any
    generation step. Does nothing (not an error) if the project
    directory doesn't exist."""
    import shutil

    shutil.rmtree(STORY_GENERATOR_PROJECTS_DIR / project_id, ignore_errors=True)


def list_project_ids() -> list[str]:
    """Every project id currently on disk - used to reconcile "projects
    the database knows about" against "project folders that actually
    still exist", not as the primary listing source
    (jarvis.story_generator.db.list_projects() is)."""
    if not STORY_GENERATOR_PROJECTS_DIR.is_dir():
        return []
    return sorted(p.name for p in STORY_GENERATOR_PROJECTS_DIR.iterdir() if p.is_dir())
