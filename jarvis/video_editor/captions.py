"""Animated Lithuanian (and any whisper.cpp-supported language) word-
by-word captions - Stage 4 of jarvis.video_editor's own staged rollout
(see that package's docstring).

Real, hand-tested finding that improves on this package's own original
plan: ffmpeg's built-in `whisper` audio filter (the SAME one
jarvis.video_studio.transcribe.transcribe_video() already uses for
sentence-level subtitles) genuinely supports REAL word-level timestamps
via `format=json:max_len=1` - confirmed by a real, hand-run spike
against both English and Lithuanian speech before writing this module.
This is NOT a heuristic/approximation (the original plan's documented
fallback, "distribute word start times proportionally by character
count across each segment's window") - every WordTiming here carries
the model's own real, measured start/end time for that exact word,
exactly as accurate as the sentence-level SRT timestamps
transcribe_video() already produces and this codebase already trusts.

Captions are composited as an OVERLAY STAGE on top of the already-
assembled timeline (the `[outv]` label jarvis.video_editor
.multisource_export.build_filtergraph() already produces) - the
existing concat/crossfade/retime filtergraph logic in that module is
NEVER modified; this module only ever adds ffmpeg `drawtext` clauses
reading from that one already-correct video label and producing a new
label, which export_timeline() maps instead of the un-captioned one
when captions are requested. This is the first real use of ffmpeg's
time-varying `enable='between(t,a,b)'` filter expressions anywhere in
this codebase (jarvis.reel_generator.export's own caption system uses a
burned-in SRT via the `subtitles=` filter - a fundamentally different
mechanism with no word-level timing control; this module's own
word-by-word reveal genuinely needs `drawtext`'s own per-clause
`enable=` windowing instead, which `subtitles=` cannot express)."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from jarvis.video_studio.transcribe import LANGUAGE_AUTO, model_is_downloaded, model_path

_TRANSCRIBE_TIMEOUT_SECONDS = 900
# Same generous bound jarvis.video_studio.transcribe.transcribe_video()
# already uses for a full encode+transcribe pass - this module performs
# its own, separate whisper invocation (requesting word-level JSON
# instead of sentence-level SRT), so it needs the same timeout rather
# than inheriting one from that sibling function, which it never calls
# directly (see generate_word_timings()'s own docstring for why).

CaptionPosition = Literal["top", "center", "bottom"]
CAPTION_POSITION_CHOICES: tuple[CaptionPosition, ...] = ("top", "center", "bottom")

CaptionAnimation = Literal["word_by_word", "none"]
CAPTION_ANIMATION_CHOICES: tuple[CaptionAnimation, ...] = ("word_by_word", "none")
# "none" burns in the FULL transcript text for its own whole segment's
# duration (no per-word reveal) - the simpler, non-animated fallback
# requirement 3 also asks for ("multiple animation style presets"),
# built on the exact same WordTiming data (grouped back into segments)
# rather than a second, separate transcription pass.

DEFAULT_CAPTION_FONT_SIZE = 64
DEFAULT_CAPTION_COLOR = "white"
DEFAULT_CAPTION_HIGHLIGHT_COLOR = "yellow"

_DEFAULT_FONT_FILE = "C:/Windows/Fonts/arialbd.ttf"
# The same confirmed-working Windows font file path this codebase
# already relies on elsewhere for Pillow text rendering
# (jarvis.design_studio.styles._FONT_BOLD, jarvis.reel_generator
# .scene_render's own ImageFont.truetype() calls) - required here for a
# real, hand-hit reason: this machine's ffmpeg build links against
# fontconfig, but has no fontconfig CONFIGURATION file installed
# ("Fontconfig error: Cannot load default config file"), which crashes
# `drawtext` outright (a real access violation, confirmed while
# building this module) if `fontfile=` is omitted and drawtext falls
# back to asking fontconfig to resolve a font by family name. Passing
# an explicit `fontfile=` path bypasses fontconfig entirely - drawtext
# loads the TrueType file directly via libfreetype, which this ffmpeg
# build also has (--enable-libfreetype), with no fontconfig involved at
# all. This is the SAME root-cause class as every other
# "ffmpeg filtergraph parser chokes on something from this codebase's
# Windows-specific environment, fixed by an explicit, hand-tested
# workaround" entry documented elsewhere in this codebase (colon/
# backslash path escaping, cwd-relative paths, etc.).


class CaptionError(Exception):
    """Raised for a genuine transcription/filter-building failure
    (ffmpeg missing, model not downloaded, no speech detected) - always
    with a human-readable message. A LOCAL exception, never subclassing
    jarvis.video_studio.transcribe.TranscriptionError or
    jarvis.video_editor.multisource_export.MultiSourceExportError - same
    isolation rule as every other new exception type in this package
    (see jarvis.video_editor's own package docstring)."""


@dataclass(frozen=True)
class WordTiming:
    """One real, whisper-measured word and its own exact on-screen
    timing window - `start_seconds`/`end_seconds` are positions in the
    SOURCE file's own timeline (the file `generate_word_timings()` was
    given), not yet adjusted for where that clip lands in the assembled
    multi-source timeline - see build_caption_filter()'s own
    `time_offset_seconds` parameter for that adjustment, applied by the
    caller (the GUI/orchestration layer), never inside this dataclass
    itself, keeping it a plain, source-relative measurement."""

    start_seconds: float
    end_seconds: float
    text: str


_LINE_GAP_SECONDS = 0.6
# A pause of at least this long between two consecutive words' own
# measured end/start times is treated as a natural sentence/phrase
# boundary - the same kind of silence-based segmentation
# jarvis.video_studio.transcribe.transcribe_video()'s own whisper.cpp
# SRT output already produces (that function's segments are a REAL
# sibling precedent for "words grouped into natural lines"), but
# derived here directly from this module's own real word timings
# rather than requiring a second, separate SRT-format transcription
# call against the same audio.


@dataclass(frozen=True)
class CaptionLine:
    """One EDITABLE subtitle line - requirement: "galimybė redaguoti
    kiekvieną subtitrų eilutę ir jos rodymo laiką" (edit each subtitle
    line and its own display time). Distinct from WordTiming (which is
    always the RAW, measured-by-whisper truth, never hand-edited) -
    a CaptionLine starts as a real transcription result (via
    group_words_into_lines() below) but is then a plain, person-
    editable value: its own `text`/`start_seconds`/`end_seconds` can be
    freely changed in the GUI before export, same "person edits what
    the machine measured" relationship jarvis.video_editor.timeline
    .TimelineClip's own source_in_seconds/source_out_seconds has to a
    video's real duration."""

    text: str
    start_seconds: float
    end_seconds: float

    def validate(self) -> list[str]:
        """Never raises - matches every other dataclass's own
        established "describe problems, don't throw" convention in this
        package."""
        problems: list[str] = []
        if not self.text.strip():
            problems.append("Subtitle line has no text.")
        if self.end_seconds <= self.start_seconds:
            problems.append("Subtitle line end time must be after its start time.")
        return problems


def group_words_into_lines(words: list[WordTiming]) -> list[CaptionLine]:
    """Groups real, measured WordTimings into natural, editable lines -
    a new line starts whenever the gap between one word's own end time
    and the next word's own start time is at least _LINE_GAP_SECONDS
    (a real pause in speech), never an arbitrary fixed word count.
    Returns [] for an empty `words` list (never raises)."""
    if not words:
        return []
    lines: list[CaptionLine] = []
    current: list[WordTiming] = [words[0]]
    for word in words[1:]:
        gap = word.start_seconds - current[-1].end_seconds
        if gap >= _LINE_GAP_SECONDS:
            lines.append(CaptionLine(
                text=" ".join(w.text for w in current),
                start_seconds=current[0].start_seconds, end_seconds=current[-1].end_seconds,
            ))
            current = [word]
        else:
            current.append(word)
    lines.append(CaptionLine(
        text=" ".join(w.text for w in current),
        start_seconds=current[0].start_seconds, end_seconds=current[-1].end_seconds,
    ))
    return lines


@dataclass(frozen=True)
class CaptionStyle:
    """Font/color/size/position/animation - requirement 3's own
    "editable font/color/size/position" plus "multiple animation style
    presets", all represented as plain, serializable values (no Path/
    callable fields) so a Timeline-adjacent caption configuration can be
    saved/resumed the same way jarvis.video_editor.storage.save_project()
    already JSON-round-trips every other plain dataclass in this
    package."""

    font_size: int = DEFAULT_CAPTION_FONT_SIZE
    color: str = DEFAULT_CAPTION_COLOR
    highlight_color: str = DEFAULT_CAPTION_HIGHLIGHT_COLOR
    position: CaptionPosition = "bottom"
    animation: CaptionAnimation = "word_by_word"


def generate_word_timings(source_path: Path, *, language: str = LANGUAGE_AUTO) -> list[WordTiming]:
    """Transcribes `source_path`'s speech into real, word-level timed
    segments via ffmpeg's own built-in `whisper` audio filter, the SAME
    whisper.cpp engine/model jarvis.video_studio.transcribe
    .transcribe_video() already uses (same model file, same
    LANGUAGE_LITHUANIAN="lt"/LANGUAGE_AUTO support) - requesting
    `format=json:max_len=1` instead of that function's own
    `format=srt` to get one JSON object per WORD instead of per
    sentence. This is a separate, sibling ffmpeg invocation (not a call
    into transcribe_video() itself) because that function's own
    contract is fixed at SRT/segment granularity - duplicating its
    small amount of subprocess-invocation logic here (the same cwd-
    relative-model-path workaround, documented below) was judged
    simpler and more honest than adding an undocumented word-level mode
    to a function whose name/docstring/every existing caller assumes
    segment-level output.

    Raises CaptionError (never returns an empty list silently) if
    ffmpeg is missing, the model hasn't been downloaded yet, or no
    speech was detected - matching this module's own "a caption
    request with no real words to show is a real, reportable problem"
    stance, distinct from transcribe_video()'s own softer "return an
    error field" convention, since captions are an explicit, opt-in
    action a person just clicked, not a background analysis step."""
    import shutil

    from jarvis.video_studio.ffmpeg_utils import FFmpegError, probe_video

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise CaptionError("FFmpeg was not found on PATH. The Video Editor requires FFmpeg to be installed.")
    if not source_path.is_file():
        raise CaptionError(f"File not found: {source_path}")
    if not model_is_downloaded():
        raise CaptionError(
            "The transcription model hasn't been downloaded yet. Download it from the Video Studio tab first."
        )
    # Checked via a real ffprobe measurement (probe_video().has_audio)
    # rather than string-matching ffmpeg's own stderr for a "no stream"
    # phrase (the approach jarvis.video_studio.transcribe
    # .transcribe_video() uses) - a real, hand-hit fragility found while
    # building this module: that exact stderr phrasing is ffmpeg-
    # version-dependent and did NOT match on this machine's installed
    # ffmpeg 9.0.1 build, so a silent video's real failure mode is
    # checked up front here instead, before ever invoking the slower
    # whisper filter at all.
    try:
        if not probe_video(source_path).has_audio:
            raise CaptionError(f"{source_path.name} has no audio track to transcribe.")
    except FFmpegError as e:
        raise CaptionError(f"Couldn't read {source_path.name}: {e}") from e

    with tempfile.TemporaryDirectory(prefix="jarvis_captions_") as tmp_dir:
        cwd = Path(tmp_dir)
        dest_name = "words.json"
        dest_path = cwd / dest_name

        # The exact same cwd-relative-model-path + forward-slash
        # workaround jarvis.video_studio.transcribe.transcribe_video()'s
        # own docstring documents in detail (a Windows drive-letter
        # colon/backslash both break the whisper filter's own
        # filtergraph option parsing) - replicated here since this is a
        # separate ffmpeg invocation, not a call into that function.
        try:
            relative_model = os.path.relpath(model_path(), cwd)
        except ValueError:
            relative_model = model_path().name
            import shutil as _shutil

            _shutil.copy2(model_path(), cwd / relative_model)
        relative_model = relative_model.replace("\\", "/")

        whisper_filter = (
            f"whisper=model={relative_model}:language={language}:queue=3s"
            f":destination={dest_name}:format=json:max_len=1"
        )
        try:
            result = subprocess.run(
                [ffmpeg, "-y", "-i", str(source_path.resolve()), "-af", whisper_filter, "-f", "null", "-"],
                capture_output=True, text=True, timeout=_TRANSCRIBE_TIMEOUT_SECONDS, check=False, cwd=str(cwd),
            )
        except subprocess.TimeoutExpired:
            raise CaptionError(f"Transcription timed out for {source_path.name}.") from None

        if not dest_path.is_file():
            stderr_tail = result.stderr.strip()[-400:]
            if "does not contain any stream" in result.stderr:
                raise CaptionError(f"{source_path.name} has no audio track to transcribe.")
            raise CaptionError(f"Transcription failed for {source_path.name}. ffmpeg said: {stderr_tail}")

        try:
            raw_text = dest_path.read_text(encoding="utf-8")
        except OSError as e:
            raise CaptionError(f"Couldn't read the generated word timings: {e}") from e

    words = _parse_word_timing_json(raw_text)
    if not words:
        raise CaptionError(
            f"No speech was detected in {source_path.name}. The audio may be silent, "
            f"music-only, or in a language the model couldn't recognize."
        )
    return words


def _parse_word_timing_json(raw_text: str) -> list[WordTiming]:
    """Parses the whisper filter's own `format=json` output - one JSON
    OBJECT PER LINE (confirmed by hand-running the filter, NOT a single
    JSON array) - `{"start":<ms>,"end":<ms>,"text":"<word>"}` per line,
    start/end in MILLISECONDS. Tolerant of a blank/malformed line
    (skipped, never raises) since this is machine-generated output, same
    tolerant-parsing stance jarvis.video_studio.transcribe._parse_srt()
    already takes for its own machine-generated SRT."""
    words: list[WordTiming] = []
    for line in raw_text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            start_ms = float(obj["start"])
            end_ms = float(obj["end"])
            text = str(obj["text"]).strip()
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            continue
        if text:
            words.append(WordTiming(start_seconds=start_ms / 1000.0, end_seconds=end_ms / 1000.0, text=text))
    return words


_POSITION_Y_EXPR = {
    "top": "h*0.1",
    "center": "(h-text_h)/2",
    "bottom": "h*0.8",
}


def _escape_drawtext_text(text: str) -> str:
    """ffmpeg drawtext's own text= value needs its special characters
    (: ' \\) escaped - a real, hand-confirmed requirement (an
    unescaped colon/apostrophe in a word's own text breaks the
    surrounding filtergraph the same way a Windows path's drive-letter
    colon does elsewhere in this codebase, see this module's own
    docstring and jarvis.video_studio.transcribe's identical path-
    escaping concern for the same underlying ffmpeg filtergraph parser
    limitation)."""
    return text.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def build_caption_filter(
    words: list[WordTiming], style: CaptionStyle, *, time_offset_seconds: float = 0.0,
    video_label: str = "outv", output_label: str = "capv",
) -> str:
    """Builds the ffmpeg filter clause(s) compositing `words` as burned-
    in captions on top of `[{video_label}]` (the already-assembled
    multisource timeline's own final video label -
    jarvis.video_editor.multisource_export.build_filtergraph()'s own
    "outv" by default), producing `[{output_label}]` - the caller
    (export_timeline()'s own caption-aware extension) maps THIS label
    instead of the un-captioned one when captions are requested, never
    modifying the existing concat/crossfade filter stages that produced
    `[{video_label}]` in the first place.

    `time_offset_seconds` shifts every word's own timing forward - the
    same "shift by where this clip actually lands in the assembled
    timeline" adjustment jarvis.reel_generator.export's own cover-intro
    feature already established for its own subtitle timing (see that
    module's own `_write_srt()` `time_offset_seconds` parameter for the
    precedent) - needed because `words` are always timed against ONE
    source clip's own original timeline, not the assembled multi-clip
    timeline's position.

    `style.animation == "word_by_word"`: each word gets its own
    `drawtext=...:enable='between(t,<start>,<end>)'` clause, visible
    only during its own real, measured speaking window - genuinely
    different text content appears/disappears per window, not a single
    static string with a fade. `style.animation == "none"`: every word
    is shown for the full `[min(starts), max(ends)]` span of the WHOLE
    `words` list as one combined string (the simpler, non-animated
    preset) - still real, measured timing (not an arbitrary fixed
    duration), just without per-word reveal."""
    if not words:
        return f"[{video_label}]null[{output_label}]"

    y_expr = _POSITION_Y_EXPR.get(style.position, _POSITION_Y_EXPR["bottom"])
    font_color = style.highlight_color if style.animation == "word_by_word" else style.color

    font_file_arg = _escape_drawtext_text(_DEFAULT_FONT_FILE)

    clauses: list[str] = []
    if style.animation == "none":
        combined_text = _escape_drawtext_text(" ".join(w.text for w in words))
        start = words[0].start_seconds + time_offset_seconds
        end = words[-1].end_seconds + time_offset_seconds
        clauses.append(
            f"drawtext=fontfile='{font_file_arg}':text='{combined_text}':fontsize={style.font_size}:fontcolor={style.color}:"
            f"x=(w-text_w)/2:y={y_expr}:box=1:boxcolor=black@0.5:boxborderw=10:"
            f"enable='between(t,{start},{end})'"
        )
    else:
        for word in words:
            start = word.start_seconds + time_offset_seconds
            end = word.end_seconds + time_offset_seconds
            escaped = _escape_drawtext_text(word.text)
            clauses.append(
                f"drawtext=fontfile='{font_file_arg}':text='{escaped}':fontsize={style.font_size}:fontcolor={font_color}:"
                f"x=(w-text_w)/2:y={y_expr}:box=1:boxcolor=black@0.5:boxborderw=10:"
                f"enable='between(t,{start},{end})'"
            )

    chain = ",".join(clauses)
    return f"[{video_label}]{chain}[{output_label}]"


def build_caption_filter_from_lines(
    lines: list[CaptionLine], style: CaptionStyle, *, time_offset_seconds: float = 0.0,
    video_label: str = "outv", output_label: str = "capv",
) -> str:
    """Builds the same drawtext-overlay filter clause as
    build_caption_filter(), but from person-EDITED CaptionLines rather
    than raw WordTimings - used once the person has reviewed/edited the
    transcription in the GUI before export (see CaptionLine's own
    docstring). Each line is shown as one combined string for its own
    `start_seconds`-`end_seconds` window (word-by-word reveal is
    meaningless here: an edited line's text is no longer tied to any
    individual word's own real measured timing, since the person may
    have rewritten/merged/retimed it), mirroring build_caption_filter()'s
    own `style.animation == "none"` behavior for every line rather than
    inventing a second rendering convention.

    Raises CaptionError if any line fails its own validate() - never
    silently drops/clamps an invalid line the caller didn't ask for."""
    for line in lines:
        problems = line.validate()
        if problems:
            raise CaptionError("; ".join(problems))

    if not lines:
        return f"[{video_label}]null[{output_label}]"

    y_expr = _POSITION_Y_EXPR.get(style.position, _POSITION_Y_EXPR["bottom"])
    font_file_arg = _escape_drawtext_text(_DEFAULT_FONT_FILE)

    clauses: list[str] = []
    for line in lines:
        start = line.start_seconds + time_offset_seconds
        end = line.end_seconds + time_offset_seconds
        escaped = _escape_drawtext_text(line.text)
        clauses.append(
            f"drawtext=fontfile='{font_file_arg}':text='{escaped}':fontsize={style.font_size}:fontcolor={style.color}:"
            f"x=(w-text_w)/2:y={y_expr}:box=1:boxcolor=black@0.5:boxborderw=10:"
            f"enable='between(t,{start},{end})'"
        )

    chain = ",".join(clauses)
    return f"[{video_label}]{chain}[{output_label}]"
