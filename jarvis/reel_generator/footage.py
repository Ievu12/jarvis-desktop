"""Mode A: Create Reel From My Footage (module brief, sections 18-19):
"If the user already has video footage: Allow 'Create Reel from my
video.' Send the footage to AI Video Studio. AI Video Studio should:
analyze footage, detect highlights, create clips, add subtitles,
create the Reel."

This module is an ORCHESTRATOR, not a reimplementation - it calls
jarvis.video_studio's own already-existing, already-tested pipeline
functions directly and in the same order its own dashboard already
does (storage.create_project() -> transcribe.transcribe_video() ->
analysis.analyze_video() -> highlights.find_highlights() ->
reel.generate_reel_edit() -> export.export_reel()), per this project's
established "Do not duplicate existing AI infrastructure" rule (the
same reuse jarvis.reel_generator.caption/.instagram_handoff already
apply to jarvis.instagram_ai_manager - see either's own docstring for
the identical rationale applied to a different module).

The uploaded video file itself is owned by AI Video Studio's own
storage (jarvis.video_studio.storage.VideoProject,
VIDEO_STUDIO_PROJECTS_DIR) and metadata by its own db
(jarvis.video_studio.db, VIDEO_STUDIO_DB_FILE) - NOT duplicated into
jarvis.reel_generator's own storage/db. A Reel Generator project
created via Mode A instead stores just the linked Video Studio
project's id (see jarvis.reel_generator.db's own
video_studio_project_id column) - "the Reel" for a Mode-A project IS
that Video Studio project's own transcript/highlights/edit-plan/export,
not a second copy of it. This mirrors jarvis.video_studio
.instagram_handoff's own "send data INTO the other module's real
storage, don't build a parallel copy" precedent, applied in the
opposite direction here (Reel Generator reading FROM Video Studio's
storage, rather than Video Studio writing INTO Instagram AI Manager's).
"""

from __future__ import annotations

import dataclasses
import time
from dataclasses import dataclass
from pathlib import Path

from jarvis.core.llm import LLMClient
from jarvis.video_studio import analysis as video_analysis
from jarvis.video_studio import db as video_db
from jarvis.video_studio import highlights as video_highlights
from jarvis.video_studio import reel as video_reel
from jarvis.video_studio import storage as video_storage
from jarvis.video_studio import transcribe as video_transcribe
from jarvis.video_studio.export import EXPORT_FORMATS, ExportError, ExportResult, export_reel
from jarvis.video_studio.storage import StorageError, VideoProject


@dataclass(frozen=True)
class FootageReelResult:
    video_project_id: str
    analysis: video_analysis.VideoAnalysis
    transcription: video_transcribe.TranscriptionResult
    highlights: video_highlights.HighlightResult
    plan: video_reel.ReelEditPlan
    insufficient_data: bool
    message: str | None
    """Set when the pipeline couldn't produce a usable edit plan (no
    speech/transcript, no highlight candidates found, none fit the
    target duration) - the earlier stages' own results are still
    returned so a caller can show WHERE the pipeline stopped, rather
    than only a single generic failure message."""


def create_reel_from_footage(
    llm: LLMClient, source_path: Path, *,
    target_duration_seconds: int, style: str, pacing: str, language: str = video_transcribe.LANGUAGE_AUTO,
) -> FootageReelResult | str:
    """Runs the full Mode-A pipeline against an uploaded video file:
    creates a NEW Video Studio project (source_path is copied in,
    never modified - jarvis.video_studio.storage.create_project()'s own
    guarantee), transcribes it, analyzes it (scene changes/silence),
    detects highlight candidates from the transcript, and plans a Reel
    edit. Returns a plain error string only for a failure BEFORE a
    Video Studio project could even be created (bad file/unsupported
    format) - every failure from that point on (no speech, no
    highlights, plan doesn't fit) comes back as a FootageReelResult with
    insufficient_data=True/message set, so the caller can always show
    what DID succeed (e.g. "transcribed fine, but no highlight
    candidates were found") rather than losing that context to a bare
    string. Never raises."""
    try:
        video_project = video_storage.create_project(source_path)
    except StorageError as e:
        return str(e)

    video_db.create_project_record(video_project.project_id, source_path.name)

    transcription = video_transcribe.transcribe_video(video_project.original_path, language=language)
    video_db.save_transcript(video_project.project_id, _transcription_to_dict(transcription))

    analysis = video_analysis.analyze_video(video_project.original_path)
    video_db.save_analysis(video_project.project_id, _analysis_to_dict(analysis))

    if transcription.error or not transcription.segments:
        empty_highlights = video_highlights.HighlightResult(
            candidates=[], insufficient_data=True,
            message=transcription.error or "No speech was found in this video to build a Reel from.",
        )
        empty_plan = video_reel._empty_plan(  # noqa: SLF001 - same empty-plan shape generate_reel_edit() itself returns for "no candidates"; reused directly rather than duplicating its construction
            target_duration_seconds, style, pacing, "Transcription found no speech to work with.",
        )
        return FootageReelResult(
            video_project_id=video_project.project_id, analysis=analysis, transcription=transcription,
            highlights=empty_highlights, plan=empty_plan, insufficient_data=True,
            message=transcription.error or "No speech was found in this video to build a Reel from.",
        )

    highlight_result = video_highlights.find_highlights(llm, analysis, transcription)
    video_db.save_highlights(video_project.project_id, _highlights_to_dict(highlight_result))

    if highlight_result.insufficient_data or not highlight_result.candidates:
        empty_plan = video_reel._empty_plan(  # noqa: SLF001 - see above
            target_duration_seconds, style, pacing,
            highlight_result.message or "No highlight candidates were found in this video.",
        )
        return FootageReelResult(
            video_project_id=video_project.project_id, analysis=analysis, transcription=transcription,
            highlights=highlight_result, plan=empty_plan, insufficient_data=True,
            message=highlight_result.message or "No highlight candidates were found in this video.",
        )

    source_has_silence_gaps = len(analysis.silence_gaps) > 0
    source_is_vertical = (analysis.height or 0) > (analysis.width or 0)
    plan = video_reel.generate_reel_edit(
        llm, highlight_result.candidates, target_duration_seconds=target_duration_seconds,
        style=style, pacing=pacing, source_has_silence_gaps=source_has_silence_gaps,
        source_is_vertical=source_is_vertical,
    )
    video_db.save_reel_plan(video_project.project_id, _plan_to_dict(plan))

    return FootageReelResult(
        video_project_id=video_project.project_id, analysis=analysis, transcription=transcription,
        highlights=highlight_result, plan=plan, insufficient_data=plan.insufficient_data, message=plan.message,
    )


def export_footage_reel(
    video_project_id: str, original_filename: str, plan: video_reel.ReelEditPlan, *,
    export_format: str = "instagram_reel",
) -> ExportResult | str:
    """Renders `plan` via jarvis.video_studio.export.export_reel() -
    the exact same export step Video Studio's own dashboard already
    uses - into a NEW file under that Video Studio project's own
    exports_dir. Returns a plain error string on failure (never
    raises)."""
    if export_format not in EXPORT_FORMATS:
        return f"Unknown export format '{export_format}'."
    video_project: VideoProject = video_storage.project_paths(video_project_id, original_filename)
    if not video_project.original_path.is_file():
        return f"Source video not found: {video_project.original_path}"

    output_path = video_project.exports_dir / f"reel_{export_format}.mp4"
    if output_path.exists():
        output_path = video_project.exports_dir / f"reel_{export_format}_{int(time.time())}.mp4"
    try:
        result = export_reel(video_project.original_path, plan, export_format=export_format, output_path=output_path)
    except ExportError as e:
        return str(e)

    video_db.save_export_record(video_project_id, result)
    return result


def _transcription_to_dict(t: video_transcribe.TranscriptionResult) -> dict:
    return dataclasses.asdict(t)


def _analysis_to_dict(a: video_analysis.VideoAnalysis) -> dict:
    return dataclasses.asdict(a)


def _highlights_to_dict(h: video_highlights.HighlightResult) -> dict:
    return dataclasses.asdict(h)


def _plan_to_dict(p: video_reel.ReelEditPlan) -> dict:
    return dataclasses.asdict(p)
