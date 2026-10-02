"""Project directory management for AI Reel Generator (module brief,
section 21: "Use the existing JARVIS project/storage architecture.").
One directory per project under jarvis.config.REEL_GENERATOR_PROJECTS_DIR,
named by the project's id:

  <REEL_GENERATOR_PROJECTS_DIR>/<project_id>/
    scenes/    - per-scene visuals: text-card renders (jarvis.design_studio
                 .render, Stage 2) and/or copies of user-uploaded scene
                 images/clips - never the original upload itself if it
                 came from AI Video Studio (that stays owned by
                 jarvis.video_studio.storage; Stage 3's Mode A only
                 references it, never copies/moves it a second time).
    cover/     - generated Reel cover (jarvis.design_studio.render,
                 Stage 2).
    exports/   - final rendered MP4s (Stage 2+).
    voiceover/ - generated narration audio (jarvis.reel_generator
                 .voiceover, Stage B) - one WAV per project, overwritten
                 on regeneration (not a history of past attempts, same
                 "current, not accumulated" convention as cover/).

jarvis.reel_generator.db stores each project's metadata (id, original
idea text, ReelBrief, ReelScript, storyboard, handoff record)
separately - this module only manages the ON-DISK layout, matching
jarvis.video_studio.storage/jarvis.design_studio.storage's own exact
split (see either module's docstring for the full rationale).

No sandbox/approval check on any function here - same "direct file I/O
from a GUI view" precedent as jarvis.config.REEL_GENERATOR_PROJECTS_DIR's
own docstring explains.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from jarvis.config import REEL_GENERATOR_PROJECTS_DIR

_SUBDIRS = ("scenes", "cover", "exports", "voiceover")


class StorageError(Exception):
    """Raised for a project-storage-layer failure - always with a
    human-readable message, per this module's own brief."""


@dataclass(frozen=True)
class ReelProject:
    """A project's on-disk location - jarvis.reel_generator.db
    separately stores this project's metadata row; this dataclass is
    purely about paths, returned by create_project() and
    project_paths()."""

    project_id: str
    root_dir: Path

    @property
    def scenes_dir(self) -> Path:
        return self.root_dir / "scenes"

    @property
    def cover_dir(self) -> Path:
        return self.root_dir / "cover"

    @property
    def exports_dir(self) -> Path:
        return self.root_dir / "exports"

    @property
    def voiceover_dir(self) -> Path:
        return self.root_dir / "voiceover"


def create_project() -> ReelProject:
    """Creates a brand-new project directory (with its fixed subfolder
    structure) under REEL_GENERATOR_PROJECTS_DIR. Unlike
    jarvis.video_studio.storage.create_project(), this takes no source
    file - a Reel Generator project starts from a plain-language idea
    (Stage 1), not an upload; Stage 3's Mode A adds its own upload
    handling separately once footage is actually involved. Raises
    StorageError only if the directory itself can't be created (e.g.
    disk full/permission denied)."""
    project_id = uuid.uuid4().hex
    root_dir = REEL_GENERATOR_PROJECTS_DIR / project_id
    try:
        for subdir in _SUBDIRS:
            (root_dir / subdir).mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise StorageError(f"Couldn't create a new Reel project: {e}") from e
    return ReelProject(project_id=project_id, root_dir=root_dir)


def project_paths(project_id: str) -> ReelProject:
    """Reconstructs a ReelProject's paths for an already-created
    project (e.g. when reopening a saved project from
    jarvis.reel_generator.db) - does not touch disk or validate
    existence."""
    return ReelProject(project_id=project_id, root_dir=REEL_GENERATOR_PROJECTS_DIR / project_id)


def delete_project(project_id: str) -> None:
    """Permanently deletes a project's entire directory - an explicit,
    person-initiated action only, never called automatically by any
    generation step. Does nothing (not an error) if the project
    directory doesn't exist."""
    import shutil

    shutil.rmtree(REEL_GENERATOR_PROJECTS_DIR / project_id, ignore_errors=True)


def list_project_ids() -> list[str]:
    """Every project id currently on disk - used to reconcile "projects
    the database knows about" against "project folders that actually
    still exist", not as the primary listing source
    (jarvis.reel_generator.db.list_projects() is)."""
    if not REEL_GENERATOR_PROJECTS_DIR.is_dir():
        return []
    return sorted(p.name for p in REEL_GENERATOR_PROJECTS_DIR.iterdir() if p.is_dir())
