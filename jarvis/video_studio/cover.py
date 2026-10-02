"""Cover Generator (module brief, section 7): extracts candidate cover
frames from a video, lets the person pick one, generates short cover
text grounded in the video's actual transcript (an LLM call, same
isolated/tool-free/JSON-only pattern as jarvis.video_studio.highlights/
.reel - see those modules' docstrings), and renders that text onto the
chosen frame as a 1080x1920 image via FFmpeg's `drawtext` filter.

Text is rendered with a fixed, generous font size chosen so it always
fits comfortably within the frame's HORIZONTAL safe area (the module
brief's "Keep important text inside a safe area so it remains visible
when Instagram displays the Reel cover in different contexts") -
word-wrapped by THIS module (via _wrap_text()) rather than relying on
FFmpeg's drawtext, which has no auto-wrap of its own; a pre-wrapped
multi-line string is written to a temp text file and passed via
drawtext's `textfile` option (confirmed, by hand testing, to render
multi-line text correctly, unlike drawtext's own `text=` option, whose
literal "\\n" is NOT interpreted as a line break).

Templates (module brief: "Minimal, Bold, Beauty, Yoga, Lifestyle,
Educational, Product") are realized here as different font-size/
box-opacity/text-position presets over the SAME chosen frame - not
different frame-processing pipelines - since this stage has no image-
generation or filter-effect capability beyond text overlay; a
template's job is purely typographic styling.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.core.llm import LLMClient
from jarvis.video_studio.ffmpeg_utils import FFmpegError, extract_frame

_COVER_WIDTH = 1080
_COVER_HEIGHT = 1920

# Instagram's own documented "safe area" guidance keeps critical
# content away from the very top/bottom (where the UI overlays
# username/caption/icons) - a conservative inset, not a value taken
# from any Instagram API (none exposes this), applied here as the
# vertical band this module places cover text within.
_SAFE_AREA_TOP_FRACTION = 0.25
_SAFE_AREA_BOTTOM_FRACTION = 0.80
_SAFE_AREA_HORIZONTAL_MARGIN_FRACTION = 0.08

_FONT_BOLD = "C:/Windows/Fonts/arialbd.ttf"
_FONT_REGULAR = "C:/Windows/Fonts/arial.ttf"

_GENERATION_TIMEOUT_SECONDS = 60
_RENDER_TIMEOUT_SECONDS = 30
_MAX_GENERATION_TOKENS = 300


@dataclass(frozen=True)
class CoverTemplate:
    name: str
    font_path: str
    font_size: int
    text_color: str  # FFmpeg color spec, e.g. "white", "0xFFD700"
    box_color: str  # FFmpeg color spec with alpha, e.g. "black@0.45"
    position: str  # "top" | "center" | "bottom" - vertical anchor within the safe area


# The module brief's own template list - font/color/position choices
# are this module's own reasonable defaults per template NAME (no
# design-system source to pull exact values from), not a claim of
# matching any specific brand's visual identity.
COVER_TEMPLATES: dict[str, CoverTemplate] = {
    "minimal": CoverTemplate("minimal", _FONT_REGULAR, 64, "white", "black@0.0", "center"),
    "bold": CoverTemplate("bold", _FONT_BOLD, 84, "white", "black@0.5", "center"),
    "beauty": CoverTemplate("beauty", _FONT_REGULAR, 70, "white", "0xD46A9F@0.35", "bottom"),
    "yoga": CoverTemplate("yoga", _FONT_REGULAR, 66, "white", "0x4A6B5A@0.35", "bottom"),
    "lifestyle": CoverTemplate("lifestyle", _FONT_REGULAR, 68, "white", "black@0.3", "bottom"),
    "educational": CoverTemplate("educational", _FONT_BOLD, 72, "white", "0x1E3A5F@0.55", "top"),
    "product": CoverTemplate("product", _FONT_BOLD, 76, "white", "black@0.45", "center"),
}


class CoverError(Exception):
    """Raised for any cover generation/rendering failure - always with
    a human-readable message."""


def extract_cover_candidates(
    video_path: Path, *, duration_seconds: float, output_dir: Path, count: int = 5,
) -> list[Path]:
    """Extracts `count` evenly-spaced candidate frames across the
    video's duration (module brief: "Extract suitable frames from the
    video. Allow the user to select a frame.") - purely mechanical
    (even spacing), no "best frame" judgment; the person picks. Skips
    the very first/last 5% of the video (opening/closing frames are
    rarely good cover candidates - often black frames or a person
    mid-blink at the very start). Raises CoverError if extraction
    fails for every candidate timestamp."""
    if duration_seconds <= 0:
        raise CoverError("This video has no measurable duration to extract frames from.")
    if count < 1:
        raise CoverError("count must be at least 1.")

    output_dir.mkdir(parents=True, exist_ok=True)
    margin = duration_seconds * 0.05
    usable_span = max(0.0, duration_seconds - 2 * margin)
    timestamps = (
        [duration_seconds / 2]
        if count == 1
        else [margin + usable_span * i / (count - 1) for i in range(count)]
    )

    candidates: list[Path] = []
    errors: list[str] = []
    for i, ts in enumerate(timestamps):
        candidate_path = output_dir / f"candidate_{i}.jpg"
        try:
            extract_frame(video_path, timestamp_seconds=ts, output_path=candidate_path)
            candidates.append(candidate_path)
        except FFmpegError as e:
            errors.append(str(e))

    if not candidates:
        raise CoverError(f"Couldn't extract any cover frame candidates: {'; '.join(errors)}")
    return candidates


_SYSTEM_PROMPT = (
    "You write short, punchy text for an Instagram Reel COVER IMAGE (not a "
    "caption - this text is overlaid directly on a still frame, so it must be "
    "very short). Given the actual transcript of the video, write ONE cover "
    "title of 3 to 7 words that captures the video's main point or hook. "
    "Grounded strictly in the given transcript - never invent a claim it "
    "doesn't support. Write in the same language as the transcript."
)

_JSON_INSTRUCTION = (
    'Reply with ONLY a single valid JSON object with exactly one key, "title" '
    "(a string, 3-7 words, no ending punctuation, no quotation marks around "
    "it, no hashtags or emoji). The response must be parseable by a strict "
    "JSON parser as-is."
)


def _extract_json(text: str) -> Any | None:
    stripped = text.strip()
    fence_match = re.match(r"^```(?:json)?\s*\n(.*)\n```$", stripped, re.DOTALL)
    if fence_match:
        stripped = fence_match.group(1).strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def generate_cover_text(llm: LLMClient, transcript_text: str) -> str | None:
    """Generates a short (3-7 word) cover title grounded in the video's
    actual transcript - never a generic/topic-only guess. Returns None
    on any failure (empty transcript, LLM error, malformed response) -
    a caller should let the person type their own text in that case,
    per every other generator in this codebase's "never silently fail,
    never fabricate" convention."""
    if not transcript_text.strip():
        return None

    prompt = f"Video transcript:\n\n{transcript_text.strip()}\n\n{_JSON_INSTRUCTION}"
    try:
        response = llm.send(
            [{"role": "user", "content": prompt}], [],
            max_tokens=_MAX_GENERATION_TOKENS, system=_SYSTEM_PROMPT,
        )
    except Exception:
        return None

    text = "".join(block.text for block in response.content if block.type == "text").strip()
    parsed = _extract_json(text) if text else None
    if not isinstance(parsed, dict):
        return None
    title = parsed.get("title")
    if not isinstance(title, str) or not title.strip():
        return None
    return title.strip()


def _wrap_text(text: str, *, max_chars_per_line: int) -> str:
    """Simple greedy word-wrap - FFmpeg's drawtext has no auto-wrap of
    its own (confirmed by hand testing), so this module wraps before
    handing text to it. `max_chars_per_line` is a character-count
    approximation of the safe area's width for the chosen font size,
    not a pixel-exact measurement (this module doesn't load the font
    to measure glyph widths) - generous enough in practice for the
    short (3-7 word) titles generate_cover_text() produces."""
    words = text.split()
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        if len(candidate) > max_chars_per_line and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)


def render_cover(
    frame_path: Path, text: str, *, template_name: str, output_path: Path,
) -> Path:
    """Burns `text` (word-wrapped, safe-area-positioned) onto
    `frame_path` using `template_name`'s CoverTemplate, writing a
    1080x1920 JPEG to `output_path`. Raises CoverError on failure
    (unknown template, missing frame, ffmpeg failure)."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise CoverError("FFmpeg was not found on PATH.")
    if template_name not in COVER_TEMPLATES:
        raise CoverError(f"Unknown cover template '{template_name}'.")
    if not frame_path.is_file():
        raise CoverError(f"Frame not found: {frame_path}")
    if not text.strip():
        raise CoverError("Cover text cannot be empty.")

    template = COVER_TEMPLATES[template_name]
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # A rough chars-per-line budget scaled from the safe area's usable
    # width against the template's font size - see _wrap_text()'s own
    # docstring for why this is an approximation, not exact glyph
    # measurement.
    usable_width = _COVER_WIDTH * (1 - 2 * _SAFE_AREA_HORIZONTAL_MARGIN_FRACTION)
    max_chars_per_line = max(6, int(usable_width / (template.font_size * 0.55)))
    wrapped = _wrap_text(text, max_chars_per_line=max_chars_per_line)

    text_file = output_path.with_suffix(".cover_text.txt")
    text_file.write_text(wrapped, encoding="utf-8")

    if template.position == "top":
        y_expr = f"h*{_SAFE_AREA_TOP_FRACTION}"
    elif template.position == "bottom":
        y_expr = f"h*{_SAFE_AREA_BOTTOM_FRACTION}-text_h"
    else:
        y_expr = "(h-text_h)/2"

    # Same Windows-drive-letter-colon-breaks-FFmpeg's-filtergraph-parser
    # issue documented in jarvis.video_studio.transcribe's docstring -
    # the font path must be relative to `cwd` (below) with forward
    # slashes, not an absolute "C:/Windows/Fonts/..." path, or FFmpeg
    # misparses the drive letter's colon as a filter-option separator
    # (confirmed by hand testing, same failure mode as the whisper
    # filter's `model=` option).
    cwd = output_path.parent
    try:
        relative_font = os.path.relpath(template.font_path, cwd).replace("\\", "/")
    except ValueError:
        relative_font = None
    font_arg = relative_font if relative_font is not None else Path(template.font_path).name
    if relative_font is None:
        shutil.copy2(template.font_path, cwd / font_arg)

    drawtext = (
        f"drawtext=fontfile={font_arg}:textfile={text_file.name}:"
        f"fontsize={template.font_size}:fontcolor={template.text_color}:"
        f"x=(w-text_w)/2:y={y_expr}:line_spacing=16:"
        f"box=1:boxcolor={template.box_color}:boxborderw=30"
    )
    filter_chain = f"scale={_COVER_WIDTH}:{_COVER_HEIGHT}:force_original_aspect_ratio=increase,crop={_COVER_WIDTH}:{_COVER_HEIGHT},{drawtext}"
    try:
        result = subprocess.run(
            [
                ffmpeg, "-y", "-i", str(frame_path.resolve()), "-vf", filter_chain,
                "-frames:v", "1", "-update", "1", output_path.name,
            ],
            capture_output=True, text=True, timeout=_RENDER_TIMEOUT_SECONDS, check=False, cwd=str(cwd),
        )
    except subprocess.TimeoutExpired:
        raise CoverError("Rendering the cover timed out.") from None
    finally:
        text_file.unlink(missing_ok=True)

    if result.returncode != 0 or not output_path.is_file():
        stderr_tail = result.stderr.strip()[-400:]
        raise CoverError(f"Couldn't render the cover. ffmpeg said: {stderr_tail}")

    return output_path
