"""Background music mixing - Stage 4's own music feature (requirement
6: "upload music/audio, trim music, adjust volume, add fade"). A real
MP3/WAV file is trimmed to a chosen window, volume-adjusted, given a
fade-in/fade-out, and mixed with the timeline's own existing audio
track (voiceover/original clip audio, if any) via ffmpeg's own `amix`
filter - never REPLACING the existing audio, always MIXING with it, so
a clip's own original sound (or an uploaded voiceover, a future stage)
and the chosen music both remain audible, exactly as a real multi-track
audio editor would behave.

This module builds ONE extra ffmpeg input (`-i <music_file>`) plus a
filter CLAUSE string that jarvis.video_editor.multisource_export
.export_timeline()'s own `audio_mix_filter` parameter threads straight
through, without that module ever importing from or knowing about this
one's own internals - same decoupled-by-a-plain-tuple-contract
isolation jarvis.video_editor.captions/export_timeline() already
establish for captions (see that integration's own docstring in
export_timeline() for the identical pattern applied to video instead of
audio).

Beat-sync (automatically aligning cuts/transitions to the music's own
rhythm) is explicitly NOT part of this stage - the user's own request
named it as optional, later work; this module only trims/levels/fades a
real music track and mixes it in, nothing more."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SUPPORTED_MUSIC_EXTENSIONS = frozenset({".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg"})
# A broader set than jarvis.video_editor.media_import's own video/photo
# extensions (a SEPARATE, local constant - same "no shared mutable
# constants across this package's own submodules" isolation convention
# every other extension set in this package already follows) - common
# audio container/codec formats a person would realistically have a
# music track saved as.

_DEFAULT_FADE_SECONDS = 1.0


class AudioMixingError(Exception):
    """Raised for a genuine music-mixing configuration failure (missing
    file, unsupported extension, an in/out range that doesn't make
    sense) - always with a human-readable message. A LOCAL exception,
    never subclassing any sibling module's own exception type (same
    isolation rule as every other new exception in this package)."""


@dataclass(frozen=True)
class MusicTrack:
    """One imported music file plus the person's own trim/volume/fade
    choices for it - a plain, JSON-serializable value (no callable
    fields) so it round-trips through jarvis.video_editor.storage
    .save_project() the same way every other Timeline-adjacent
    configuration dataclass in this package already does."""

    source_path: Path
    """The music file's own real, on-disk path - copied into the
    project's own media directory by the caller (the GUI/orchestration
    layer, via jarvis.video_editor.storage.copy_media_into_project(),
    the SAME function media_import.py's own video/photo import already
    uses) before being wrapped in this dataclass, matching this
    package's "never read directly from wherever the person's file
    picker happened to point" convention for every other imported
    asset."""

    trim_start_seconds: float = 0.0
    trim_end_seconds: float | None = None
    """None means "use the music track's own full real duration from
    trim_start_seconds onward" - resolved to a real number by
    build_music_mix_filter() once it knows the ASSEMBLED timeline's own
    total duration (the music is always clamped to fit the Reel's own
    length, never extending it - same "the video's own length is the
    master timeline" convention jarvis.reel_generator.export's own
    voiceover-mux feature already established for an analogous audio-
    track-vs-video-length relationship)."""

    volume: float = 1.0
    """A linear gain multiplier - 1.0 is unchanged, 0.5 is half volume,
    2.0 is double. Validated to a sane [0.0, 4.0] range by
    validate_music_track() below (a value outside that range is almost
    certainly a mistake, not a deliberate creative choice, so it's
    flagged rather than silently clipped or accepted)."""

    fade_in_seconds: float = _DEFAULT_FADE_SECONDS
    fade_out_seconds: float = _DEFAULT_FADE_SECONDS


def validate_music_track(track: MusicTrack) -> list[str]:
    """Returns a list of human-readable problems, or an empty list if
    well-formed - never raises, same contract as
    jarvis.video_editor.timeline.Timeline.validate()."""
    problems: list[str] = []
    if not track.source_path.is_file():
        problems.append(f"Music file not found: {track.source_path}")
    if track.source_path.suffix.lower() not in SUPPORTED_MUSIC_EXTENSIONS:
        supported = ", ".join(sorted(e.lstrip(".").upper() for e in SUPPORTED_MUSIC_EXTENSIONS))
        problems.append(f"Unsupported music file type '{track.source_path.suffix}'. Supported formats: {supported}.")
    if track.trim_end_seconds is not None and track.trim_end_seconds <= track.trim_start_seconds:
        problems.append("Music trim end must be after trim start.")
    if not (0.0 <= track.volume <= 4.0):
        problems.append(f"Music volume {track.volume}x is outside the supported 0x-4x range.")
    if track.fade_in_seconds < 0.0 or track.fade_out_seconds < 0.0:
        problems.append("Fade durations cannot be negative.")
    return problems


def build_music_mix_filter(
    track: MusicTrack, *, timeline_duration_seconds: float, cwd: Path,
    timeline_audio_input_count: int, timeline_audio_label: str = "outa",
) -> tuple[list[str], str, str]:
    """Builds the (extra_input_args, filter_clause, output_label) tuple
    jarvis.video_editor.multisource_export.export_timeline()'s own
    `audio_mix_filter` parameter expects (see that function's own
    docstring for the exact contract).

    `timeline_audio_input_count` is the number of DISTINCT `-i` inputs
    the timeline's own build_filtergraph() call already added (so this
    music input lands at index `timeline_audio_input_count`, never
    colliding with or renumbering any existing scene/clip input - the
    same "append as the LAST physical input" convention
    jarvis.reel_generator.export's own cover-intro feature already
    established for an analogous "add one more real media input without
    disturbing existing index math" problem).

    The music track is trimmed to `[trim_start, trim_end or full
    duration]`, volume-scaled, faded in/out via `afade`, then clamped
    (via `atrim`) to `timeline_duration_seconds` - NEVER longer than the
    Reel's own assembled video, matching
    jarvis.reel_generator.export's own "the video's own length is the
    master timeline" convention for its voiceover mux. Mixed with the
    timeline's own existing audio (`timeline_audio_label`) via `amix`
    (duration=first, so the mix's own length is governed by whichever
    of the two inputs is actually longer AFTER both have already been
    clamped to `timeline_duration_seconds` - i.e. exactly that
    duration, never shorter/longer due to amix's own default
    behavior)."""
    problems = validate_music_track(track)
    if problems:
        raise AudioMixingError("; ".join(problems))

    try:
        relative_path = track.source_path.resolve().relative_to(cwd.resolve())
        path_arg = str(relative_path).replace("\\", "/")
    except ValueError:
        path_arg = str(track.source_path.resolve()).replace("\\", "/")

    input_args = ["-i", path_arg]
    music_input_index = timeline_audio_input_count

    trim_end = track.trim_end_seconds if track.trim_end_seconds is not None else track.trim_start_seconds + timeline_duration_seconds
    fade_out_start = max(0.0, (trim_end - track.trim_start_seconds) - track.fade_out_seconds)

    music_chain = (
        f"[{music_input_index}:a]atrim=start={track.trim_start_seconds}:end={trim_end},asetpts=PTS-STARTPTS,"
        f"volume={track.volume},"
        f"afade=t=in:st=0:d={track.fade_in_seconds},"
        f"afade=t=out:st={fade_out_start}:d={track.fade_out_seconds},"
        f"atrim=duration={timeline_duration_seconds}[music]"
    )
    mix_chain = f"[{timeline_audio_label}][music]amix=inputs=2:duration=first:dropout_transition=0[mixedaudio]"

    filter_clause = f"{music_chain};{mix_chain}"
    return input_args, filter_clause, "mixedaudio"
