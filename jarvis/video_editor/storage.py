"""Project directory management for the Professional Video Editor - one
directory per project under jarvis.config.VIDEO_EDITOR_PROJECTS_DIR,
named by the project's id:

  <VIDEO_EDITOR_PROJECTS_DIR>/<project_id>/
    media/     - every imported video clip and photo, copied here
                 verbatim (originals are NEVER modified/deleted by this
                 module - same "never touch the original" convention
                 jarvis.video_studio.storage already established for
                 its own uploaded video)
    exports/   - final rendered MP4s

A SEPARATE tree from jarvis.video_studio's own VIDEO_STUDIO_PROJECTS_DIR
- see jarvis.config.VIDEO_EDITOR_PROJECTS_DIR's own docstring for why.
jarvis.video_editor.db stores each project's metadata (Timeline,
MediaItem list, export settings) separately - this module only manages
the ON-DISK layout, never touches SQLite, exactly mirroring
jarvis.video_studio.storage's own division of responsibility.

Every function here is synchronous, plain file I/O with no sandbox/
approval check - same "direct file I/O from a GUI view" precedent as
jarvis.video_studio.storage/jarvis.instagram_ai_manager.db (see
jarvis.config.VIDEO_EDITOR_PROJECTS_DIR's own docstring). Copying a
multi-hundred-MB video file is itself blocking I/O, so callers run this
through jarvis.gui.worker.run_generation_in_background(), same as every
other possibly-slow call in this codebase."""

from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from jarvis.config import VIDEO_EDITOR_PROJECTS_DIR

_SUBDIRS = ("media", "exports")


class VideoEditorStorageError(Exception):
    """Raised for a project-storage-layer failure (missing source file,
    disk full during copy) - always with a human-readable message. A
    LOCAL exception, never subclassing jarvis.video_studio.storage
    .StorageError - see jarvis.video_editor's own package docstring's
    isolation rules for why the two modules' exception hierarchies stay
    separate (a `try/except StorageError` somewhere in existing
    video_studio code must never accidentally catch a new-module error,
    or vice versa)."""


@dataclass(frozen=True)
class VideoEditorProject:
    """A project's on-disk location - jarvis.video_editor.db separately
    stores this project's metadata row; this dataclass is purely about
    paths, mirroring jarvis.video_studio.storage.VideoProject's own
    shape exactly (for anyone already familiar with that sibling
    module), but for an entirely separate directory tree."""

    project_id: str
    root_dir: Path

    @property
    def media_dir(self) -> Path:
        return self.root_dir / "media"

    @property
    def exports_dir(self) -> Path:
        return self.root_dir / "exports"


def create_project() -> VideoEditorProject:
    """Creates a brand-new, empty project directory (media/exports
    subdirs) under VIDEO_EDITOR_PROJECTS_DIR - unlike jarvis.video_studio
    .storage.create_project(), this does NOT take a source file: a Video
    Editor project can start empty and have media imported into it one
    piece at a time (possibly many files, possibly added later), rather
    than being created FROM one upload the way a single-source Video
    Studio project is."""
    project_id = uuid.uuid4().hex
    root_dir = VIDEO_EDITOR_PROJECTS_DIR / project_id
    for subdir in _SUBDIRS:
        (root_dir / subdir).mkdir(parents=True, exist_ok=True)
    return VideoEditorProject(project_id=project_id, root_dir=root_dir)


def project_paths(project_id: str) -> VideoEditorProject:
    """Reconstructs a VideoEditorProject's paths for an already-created
    project (e.g. when reopening a saved project from
    jarvis.video_editor.db) - does not touch disk or validate existence,
    same contract as jarvis.video_studio.storage.project_paths()."""
    return VideoEditorProject(project_id=project_id, root_dir=VIDEO_EDITOR_PROJECTS_DIR / project_id)


def copy_media_into_project(project: VideoEditorProject, source_path: Path) -> Path:
    """Copies `source_path` (a file the person picked via the upload
    dialog) into this project's own media/ directory, verbatim - the
    original file the person picked is never modified, moved, or
    deleted. Returns the new, project-local path. Raises
    VideoEditorStorageError if the source doesn't exist or the copy
    fails (e.g. disk full). Extension/format validation is
    jarvis.video_editor.media_import's own job (this function is a
    pure, format-agnostic file copier), matching the division of
    responsibility jarvis.video_studio.storage.create_project()/
    jarvis.video_studio.ffmpeg_utils.probe_video() already establish
    (a fast extension pre-check happens at the IMPORT layer; storage
    itself just moves bytes)."""
    if not source_path.is_file():
        raise VideoEditorStorageError(f"File not found: {source_path}")

    # A uuid-prefixed destination filename avoids a collision if two
    # imported files happen to share the same original name (e.g. two
    # different phones both producing "IMG_0001.jpg") - never silently
    # overwriting an earlier import.
    destination = project.media_dir / f"{uuid.uuid4().hex}_{source_path.name}"
    try:
        shutil.copy2(source_path, destination)
    except OSError as e:
        raise VideoEditorStorageError(f"Couldn't copy {source_path.name} into the project: {e}") from e
    return destination


def delete_project(project_id: str) -> None:
    """Permanently deletes a project's entire directory - an explicit,
    person-initiated action only. Does nothing (not an error) if the
    project directory doesn't exist, matching
    jarvis.video_studio.storage.delete_project()'s own idempotent-delete
    convention."""
    root_dir = VIDEO_EDITOR_PROJECTS_DIR / project_id
    shutil.rmtree(root_dir, ignore_errors=True)


def list_project_ids() -> list[str]:
    """Every project id currently on disk - used to reconcile "projects
    the database knows about" against "project folders that actually
    still exist", matching jarvis.video_studio.storage
    .list_project_ids()'s own contract exactly."""
    if not VIDEO_EDITOR_PROJECTS_DIR.is_dir():
        return []
    return sorted(p.name for p in VIDEO_EDITOR_PROJECTS_DIR.iterdir() if p.is_dir())


def save_project(project_id: str, timeline, media_items: dict) -> None:
    """The save/resume feature's own single entry point (requirement 8:
    "ability to save a project and resume editing later") - serializes
    `timeline` (a jarvis.video_editor.timeline.Timeline) and
    `media_items` (a dict[str, jarvis.video_editor.media_import
    .MediaItem]) to plain JSON-compatible dicts and persists them via
    jarvis.video_editor.db.save_timeline()/save_media_items(). This
    function is the ONE place Path objects (MediaItem.stored_path) get
    converted to plain strings for storage - callers never need to know
    the serialization details themselves.

    A thin import here (not at module level) avoids a import cycle risk:
    jarvis.video_editor.timeline/.media_import don't import
    jarvis.video_editor.storage, but keeping the dependency direction
    explicit and lazy here costs nothing and documents the intent."""
    import dataclasses

    from jarvis.video_editor import db

    timeline_data = dataclasses.asdict(timeline)
    media_items_data = [
        {**dataclasses.asdict(item), "stored_path": str(item.stored_path)}
        for item in media_items.values()
    ]
    db.save_timeline(project_id, timeline_data)
    db.save_media_items(project_id, media_items_data)


def load_project(project_id: str):
    """The inverse of save_project() - returns (Timeline, dict[str,
    MediaItem]) reconstructed from this project's own persisted
    metadata, or (None, {}) if the project has never been saved (a
    brand-new project with no timeline/media yet - never an error, same
    "nothing saved yet is a normal state" convention
    jarvis.video_studio.db's own NULL-column reads already establish)."""
    from pathlib import Path as _Path

    from jarvis.video_editor import db
    from jarvis.video_editor.media_import import MediaItem
    from jarvis.video_editor.effects import EffectSpec
    from jarvis.video_editor.timeline import Timeline, TimelineClip, TimelineStill, TransitionSpec

    record = db.get_project(project_id)
    if record is None:
        return None, {}

    timeline = None
    if record.timeline_data is not None:
        items = []
        for item_data in record.timeline_data.get("items", []):
            transition_data = item_data.get("transition_out") or {}
            transition = _dataclass_from_dict(TransitionSpec, transition_data)
            # `effect` was saved (dataclasses.asdict() includes it) but
            # never read back - a real bug: every motion/fade/color
            # effect silently disappeared when a project was reopened.
            effect = _dataclass_from_dict(EffectSpec, item_data.get("effect") or {})
            if "source_in_seconds" in item_data:
                items.append(TimelineClip(
                    clip_id=item_data["clip_id"], media_item_id=item_data["media_item_id"],
                    source_in_seconds=item_data["source_in_seconds"], source_out_seconds=item_data["source_out_seconds"],
                    speed_factor=item_data.get("speed_factor", 1.0), transition_out=transition, effect=effect,
                    volume=item_data.get("volume", 1.0),
                    audio_fade_in_seconds=item_data.get("audio_fade_in_seconds", 0.0),
                    audio_fade_out_seconds=item_data.get("audio_fade_out_seconds", 0.0),
                ))
            else:
                items.append(TimelineStill(
                    clip_id=item_data["clip_id"], media_item_id=item_data["media_item_id"],
                    display_duration_seconds=item_data["display_duration_seconds"], transition_out=transition,
                    effect=effect,
                ))
        timeline = Timeline(items=tuple(items), aspect_ratio=record.timeline_data.get("aspect_ratio", "9:16"))

    media_items: dict[str, MediaItem] = {}
    for item_data in record.media_items_data or []:
        media_items[item_data["media_item_id"]] = MediaItem(
            media_item_id=item_data["media_item_id"], original_filename=item_data["original_filename"],
            stored_path=_Path(item_data["stored_path"]), kind=item_data["kind"],
            duration_seconds=item_data.get("duration_seconds"), width=item_data.get("width"),
            height=item_data.get("height"), fps=item_data.get("fps"),
        )

    return timeline, media_items


def _dataclass_from_dict(cls, data: dict, **overrides):
    """Builds `cls` from a saved dict, ignoring keys `cls` no longer
    has and letting fields missing from older saves fall back to their
    own defaults - so adding a field to TextOverlay/StickerInstance/
    EffectSpec later never breaks reopening an older project."""
    import dataclasses

    field_names = {f.name for f in dataclasses.fields(cls)}
    kwargs = {k: v for k, v in data.items() if k in field_names}
    kwargs.update(overrides)
    return cls(**kwargs)


@dataclass(frozen=True)
class ProjectOverlays:
    """Everything a project shows on top of its timeline - the state
    the dashboard holds outside TimelinePanel. Saved as one JSON object
    in jarvis.video_editor.db's overlays_data column."""

    text_overlays: tuple = ()
    stickers: tuple = ()
    caption_style: object | None = None
    caption_lines: tuple | None = None
    music_track: object | None = None
    reels: object | None = None
    """jarvis.video_editor.reels.ReelsLayers (Reels mode)."""


def save_overlays(project_id: str, overlays: ProjectOverlays) -> None:
    """Persists `overlays` (Path fields converted to plain strings here,
    the same single-conversion-point rule save_project() follows)."""
    import dataclasses

    from jarvis.video_editor import db
    from jarvis.video_editor import reels as reels_module

    def sticker_dict(sticker) -> dict:
        data = dataclasses.asdict(sticker)
        data["custom_path"] = str(sticker.custom_path) if sticker.custom_path is not None else None
        data["tint"] = list(sticker.tint) if sticker.tint is not None else None
        return data

    music_data = None
    if overlays.music_track is not None:
        music_data = {**dataclasses.asdict(overlays.music_track), "source_path": str(overlays.music_track.source_path)}

    db.save_overlays(project_id, {
        "text_overlays": [dataclasses.asdict(o) for o in overlays.text_overlays],
        "stickers": [sticker_dict(s) for s in overlays.stickers],
        "caption_style": dataclasses.asdict(overlays.caption_style) if overlays.caption_style is not None else None,
        "caption_lines": (
            [dataclasses.asdict(line) for line in overlays.caption_lines]
            if overlays.caption_lines is not None else None
        ),
        "music_track": music_data,
        "reels": reels_module.to_dict(overlays.reels) if overlays.reels is not None else None,
    })


def load_overlays(project_id: str) -> ProjectOverlays:
    """The inverse of save_overlays() - an empty ProjectOverlays for a
    project that never saved any (every project created before the
    overlays_data column existed)."""
    from pathlib import Path as _Path

    from jarvis.video_editor import db
    from jarvis.video_editor import reels as reels_module
    from jarvis.video_editor.audio_mixing import MusicTrack
    from jarvis.video_editor.captions import CaptionLine, CaptionStyle
    from jarvis.video_editor.stickers import StickerInstance
    from jarvis.video_editor.text_overlay import TextOverlay

    record = db.get_project(project_id)
    if record is None or not record.overlays_data:
        return ProjectOverlays()
    data = record.overlays_data

    stickers = []
    for sticker_data in data.get("stickers") or []:
        custom_path = sticker_data.get("custom_path")
        tint = sticker_data.get("tint")
        stickers.append(_dataclass_from_dict(
            StickerInstance, sticker_data,
            custom_path=_Path(custom_path) if custom_path else None,
            tint=tuple(tint) if tint else None,
        ))

    caption_lines = data.get("caption_lines")
    music_data = data.get("music_track")
    return ProjectOverlays(
        text_overlays=tuple(_dataclass_from_dict(TextOverlay, o) for o in data.get("text_overlays") or []),
        stickers=tuple(stickers),
        caption_style=_dataclass_from_dict(CaptionStyle, data["caption_style"]) if data.get("caption_style") else None,
        caption_lines=(
            tuple(_dataclass_from_dict(CaptionLine, line) for line in caption_lines)
            if caption_lines is not None else None
        ),
        music_track=(
            _dataclass_from_dict(MusicTrack, music_data, source_path=_Path(music_data["source_path"]))
            if music_data else None
        ),
        reels=reels_module.from_dict(data.get("reels")),
    )
