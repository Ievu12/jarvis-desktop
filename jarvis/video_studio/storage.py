"""Project directory management for AI Video Studio (module brief,
section 13: "Every video should become a project" with a fixed
subfolder structure). One directory per project under
jarvis.config.VIDEO_STUDIO_PROJECTS_DIR, named by the project's id:

  <VIDEO_STUDIO_PROJECTS_DIR>/<project_id>/
    original/    - the uploaded video, copied here verbatim, never
                    modified or deleted by anything in this package
                    (per the brief: "Never delete the original
                    automatically", "verify original video remains
                    untouched")
    clips/       - trimmed/exported highlight clips (a later stage)
    audio/       - extracted audio (jarvis.video_studio.transcribe)
    subtitles/   - generated .srt files (a later stage)
    cover/       - extracted cover-frame candidates + generated covers
                    (a later stage)
    exports/     - final rendered Reels (a later stage)

jarvis.video_studio.db stores each project's metadata (id, original
filename, created_at, VideoAnalysis fields) separately - this module
only manages the ON-DISK layout, never touches SQLite.

Every function here is synchronous, plain file I/O with no
sandbox/approval check - the same precedent
jarvis.instagram_ai_manager.db already established for GUI-driven
direct file access (see jarvis.config.VIDEO_STUDIO_PROJECTS_DIR's own
docstring for why this is architecturally consistent with the rest of
the app). Copying a multi-hundred-MB video file is itself
I/O-and-therefore-blocking-main-thread work, so callers (GUI panels)
run create_project() through jarvis.gui.worker
.run_generation_in_background(), exactly like every other
possibly-slow call in this package.
"""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from jarvis.config import VIDEO_STUDIO_PROJECTS_DIR

_SUBDIRS = ("original", "clips", "audio", "subtitles", "cover", "exports")

# Formats the module's brief explicitly lists ("Support common formats
# such as: MP4 MOV M4V WEBM") - checked by extension only (a cheap,
# fast pre-check before the real validation, which is
# jarvis.video_studio.ffmpeg_utils.probe_video() actually being able to
# read the file - an unsupported container with a spoofed extension
# still fails there with a clear FFmpegError, so this is a fast-path
# rejection for obviously-wrong files, not the sole gatekeeper).
SUPPORTED_EXTENSIONS = frozenset({".mp4", ".mov", ".m4v", ".webm"})


class StorageError(Exception):
    """Raised for a project-storage-layer failure (unsupported
    extension, disk full during copy, missing source file) - always
    with a human-readable message, per the module's brief."""


@dataclass(frozen=True)
class VideoProject:
    """A project's on-disk location - jarvis.video_studio.db separately
    stores this project's metadata row; this dataclass is purely about
    paths, returned by create_project() and project_paths()."""

    project_id: str
    root_dir: Path
    original_path: Path

    @property
    def clips_dir(self) -> Path:
        return self.root_dir / "clips"

    @property
    def audio_dir(self) -> Path:
        return self.root_dir / "audio"

    @property
    def subtitles_dir(self) -> Path:
        return self.root_dir / "subtitles"

    @property
    def cover_dir(self) -> Path:
        return self.root_dir / "cover"

    @property
    def exports_dir(self) -> Path:
        return self.root_dir / "exports"


def create_project(source_path: Path) -> VideoProject:
    """Copies `source_path` (the file the person picked via the
    upload dialog) into a brand-new project directory under
    VIDEO_STUDIO_PROJECTS_DIR - the ORIGINAL file the person picked is
    never modified, moved, or deleted; this always COPIES it, per the
    module's brief ("Never overwrite the original video", "verify
    original video remains untouched"). Raises StorageError if the
    extension isn't one of SUPPORTED_EXTENSIONS, the source doesn't
    exist, or the copy fails (e.g. disk full)."""
    if not source_path.is_file():
        raise StorageError(f"File not found: {source_path}")
    if source_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        supported = ", ".join(sorted(e.lstrip(".").upper() for e in SUPPORTED_EXTENSIONS))
        raise StorageError(
            f"Unsupported file type '{source_path.suffix}'. Supported formats: {supported}."
        )

    project_id = uuid.uuid4().hex
    root_dir = VIDEO_STUDIO_PROJECTS_DIR / project_id
    for subdir in _SUBDIRS:
        (root_dir / subdir).mkdir(parents=True, exist_ok=True)

    destination = root_dir / "original" / source_path.name
    try:
        shutil.copy2(source_path, destination)
    except OSError as e:
        # Clean up the partially-created project directory rather than
        # leaving a broken, DB-less project folder behind.
        shutil.rmtree(root_dir, ignore_errors=True)
        raise StorageError(f"Couldn't copy {source_path.name} into a new project: {e}") from e

    return VideoProject(project_id=project_id, root_dir=root_dir, original_path=destination)


def project_paths(project_id: str, original_filename: str) -> VideoProject:
    """Reconstructs a VideoProject's paths for an already-created
    project (e.g. when reopening a saved project from
    jarvis.video_studio.db) - does not touch disk or validate
    existence; callers needing to confirm the files are actually still
    there should check `.original_path.exists()` themselves."""
    root_dir = VIDEO_STUDIO_PROJECTS_DIR / project_id
    return VideoProject(
        project_id=project_id, root_dir=root_dir, original_path=root_dir / "original" / original_filename,
    )


def delete_project(project_id: str) -> None:
    """Permanently deletes a project's entire directory (original video
    included) - an explicit, person-initiated action only (the "delete
    a project" button a later UI stage adds), never called
    automatically by any analysis/generation step in this package. Does
    nothing (not an error) if the project directory doesn't exist,
    matching Path.rmtree's own idempotent-delete conventions elsewhere
    in this codebase."""
    root_dir = VIDEO_STUDIO_PROJECTS_DIR / project_id
    shutil.rmtree(root_dir, ignore_errors=True)


def list_project_ids() -> list[str]:
    """Every project id currently on disk (a directory name under
    VIDEO_STUDIO_PROJECTS_DIR) - used by jarvis.video_studio.db-backed
    UI code to reconcile "projects the database knows about" against
    "project folders that actually still exist", not as the primary
    listing source (jarvis.video_studio.db.list_projects() is)."""
    if not VIDEO_STUDIO_PROJECTS_DIR.is_dir():
        return []
    return sorted(p.name for p in VIDEO_STUDIO_PROJECTS_DIR.iterdir() if p.is_dir())
