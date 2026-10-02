"""Final render/export (module brief, sections 13-14): renders an
approved Reel's scene visuals into an actual MP4 - each scene's still
image is turned into a timed video segment (ffmpeg can loop a still
image for a fixed duration), concatenated in scene order, burned with
subtitles built directly from each scene's own on_screen_text (module
brief section 8: "If AI video generation is unavailable, create the
Reel using AI-generated images... text and transitions" - this module
is exactly that fallback path, since no AI video generation exists in
this codebase - see this package's own __init__.py docstring), and
encoded H.264/AAC at 1080x1920 (module brief section 14's exact spec).

Closely follows jarvis.video_studio.export.export_reel()'s exact
filtergraph shape (trim/concat + crop/scale + subtitle burn-in +
libx264/aac encode - see that module's own docstring) rather than
importing it verbatim, since that function is typed specifically
against jarvis.video_studio.reel.ReelEditPlan/PlannedClip (trims of an
EXISTING video's timeline), not a sequence of still images that don't
exist as clips yet. The "loop a still image for N seconds" step this
module adds (`-loop 1 -t <duration> -i <image>`, one input per scene)
has no equivalent in that module at all, since it never needed one -
Video Studio's clips are always already-timed video, not images.

Silent output (no audio stream) UNLESS `voiceover_path` is given (Stage
B: jarvis.reel_generator.voiceover.generate_voiceover() - see
export_reel_video()'s own docstring for the exact mux/loudnorm/pad-or-
trim behavior). Module brief section 10's music suggestion is still
text-only, never an actual audio track added here - a person adds their
own music in Instagram/CapCut afterward, same as before this stage.
`-c:a aac` and an audio map are omitted entirely when no voiceover is
given, rather than faked with silence, since a silent MP4 the person
can still add their own audio/music to is more honest than pretending
an audio pipeline exists when none was actually used.

ALWAYS writes to a NEW file under the project's exports_dir
(jarvis.reel_generator.storage.ReelProject.exports_dir) - never
modifies/deletes a scene image, matching this codebase's established
"never overwrite the original" convention (module brief section 14:
"Never overwrite the original assets.")."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from jarvis.reel_generator.scenes import SceneVisual
from jarvis.reel_generator.storyboard import Scene, strip_hashtags

_EXPORT_TIMEOUT_SECONDS = 600
_EXPORT_WIDTH = 1080
_EXPORT_HEIGHT = 1920

# Caption styling (Stage C / module brief section 6: "style/font/
# position/size/animation") - a fixed, small vocabulary rather than
# free-form CSS-like input, matching this codebase's established
# "vocabulary tables + DEFAULT_* constant" convention (e.g.
# jarvis.reel_generator.visual_plan's own CAMERA_SHOT_CHOICES etc.).
# Applied via ffmpeg's own libass "subtitles=...:force_style=..."
# option - the SAME underlying subtitle renderer burn_in_captions has
# always used, just with real style parameters instead of libass's own
# unstyled defaults.
CAPTION_FONT_CHOICES = ("Arial", "Impact", "Georgia", "Verdana", "Comic Sans MS")
DEFAULT_CAPTION_FONT = "Arial"
# Real, reported bug fix ("Lithuanian subtitle letters are garbled in
# the exported video"): the actual corruption was libass misreading the
# UTF-8 .srt file under the wrong codepage - fixed below via
# charenc=UTF-8 on the subtitles= filter itself (see that call site's
# own comment for the full root-cause explanation). It was never a
# missing-glyph/font problem: Arial/Verdana/Georgia (the Windows/Office-
# standard TrueType fonts CAPTION_FONT_CHOICES already uses) all have
# complete Latin Extended-A coverage, which is what every Lithuanian
# diacritic (ą/č/ę/ė/į/š/ų/ū/ž) belongs to - once libass reads the
# correct bytes, these already-chosen fonts render them correctly, with
# no font-vocabulary change needed. DejaVu Sans/Noto Sans (both with
# complete, guaranteed Latin Extended-A coverage by design) remain a
# reasonable font CHOICE for a caller/future UI to explicitly pick from
# CAPTION_FONT_CHOICES if ever added - but adding them speculatively
# here, when they were never the actual cause of this bug, would be
# exactly the unnecessary rewrite the module brief's own "minimal
# pataisymas" requirement warns against.

CAPTION_POSITION_CHOICES = ("bottom", "middle", "top")
DEFAULT_CAPTION_POSITION = "bottom"
# libass numpad-style \an alignment codes: 2=bottom-center,
# 5=middle-center, 8=top-center.
_CAPTION_POSITION_ALIGNMENT = {"bottom": 2, "middle": 5, "top": 8}

CAPTION_SIZE_CHOICES = ("small", "medium", "large")
DEFAULT_CAPTION_SIZE = "medium"
_CAPTION_SIZE_FONT_SIZE = {"small": 48, "medium": 64, "large": 84}

CAPTION_ANIMATION_CHOICES = ("none", "fade")
DEFAULT_CAPTION_ANIMATION = "none"
_CAPTION_FADE_DURATION_MS = 300  # a quick, subtle fade-in/out - not a slow, distracting effect


@dataclass(frozen=True)
class CaptionStyle:
    """Burned-in caption styling (module brief section 6) - passed to
    export_reel_video()'s own `caption_style` parameter, only meaningful
    when `burn_in_captions=True` (this codebase's own default renders
    on-screen text directly onto each scene's image instead - see
    export_reel_video()'s own docstring for why burn_in_captions
    defaults to False - so CaptionStyle has no effect at all unless a
    caller explicitly opts into the ffmpeg-subtitle path)."""

    font: str = DEFAULT_CAPTION_FONT
    position: str = DEFAULT_CAPTION_POSITION
    size: str = DEFAULT_CAPTION_SIZE
    animation: str = DEFAULT_CAPTION_ANIMATION

    def force_style(self) -> str:
        """Builds the libass force_style string ffmpeg's own
        `subtitles=...:force_style=...` option expects - a single
        semicolon-joined `Key=Value` string, never user-supplied raw
        text (every value here comes from this dataclass's own fixed
        vocabulary/constants above, so there is nothing to escape).

        FontSize alone is NOT resolution-independent in libass - without
        an explicit PlayResX/PlayResY, libass assumes its own internal
        default script resolution (384x288) and scales FontSize relative
        to THAT, not the actual 1080x1920 output frame - hand-tested by
        extracting and visually inspecting a real exported frame: an
        "84pt" caption filled nearly the entire vertical height of the
        screen, wildly larger than intended, exactly this bug. Passing
        PlayResX/PlayResY matching _EXPORT_WIDTH/_EXPORT_HEIGHT is the
        documented libass fix - it makes FontSize (and Outline/Shadow
        widths) scale correctly against the REAL output resolution."""
        font_size = _CAPTION_SIZE_FONT_SIZE.get(self.size, _CAPTION_SIZE_FONT_SIZE[DEFAULT_CAPTION_SIZE])
        alignment = _CAPTION_POSITION_ALIGNMENT.get(self.position, _CAPTION_POSITION_ALIGNMENT[DEFAULT_CAPTION_POSITION])
        font_name = self.font if self.font in CAPTION_FONT_CHOICES else DEFAULT_CAPTION_FONT
        return (
            f"FontName={font_name},FontSize={font_size},Alignment={alignment},"
            "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=0,"
            f"PlayResX={_EXPORT_WIDTH},PlayResY={_EXPORT_HEIGHT}"
        )


class ExportError(Exception):
    """Raised for any export failure (ffmpeg missing, no scenes, a
    scene missing its rendered image, subprocess failure/timeout) -
    always with a human-readable message, per this codebase's
    established "never silently fail" convention."""


@dataclass(frozen=True)
class ReelExportResult:
    output_path: Path
    duration_seconds: float
    width: int
    height: int
    file_size_bytes: int
    scene_fallback_warnings: tuple[str, ...] = ()
    """NATURAL MOTION / HYBRID modes only - one human-readable warning
    per scene whose real video clip was requested but rejected at
    splice time (couldn't be probed, or came back shorter than its own
    scene's duration budget - see export_reel_video()'s own
    `clip_by_scene` docstring) and fell back to that scene's still image
    instead. Empty tuple (the default) for every Static-mode export, and
    for a Natural Motion/Hybrid export where every requested clip was
    genuinely usable - never populated just because clip_by_scene was
    passed at all."""


def _require_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if path is None:
        raise ExportError(
            "FFmpeg was not found on PATH. AI Reel Generator requires FFmpeg to be installed."
        )
    return path


def _srt_timestamp(seconds: float) -> str:
    total_ms = int(round(seconds * 1000))
    hours, rem = divmod(total_ms, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, ms = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def _write_srt(
    scenes: tuple[Scene, ...], destination: Path, *, animation: str = DEFAULT_CAPTION_ANIMATION,
    time_offset_seconds: float = 0.0,
) -> None:
    """Writes an SRT with each scene's OWN on_screen_text (never the
    full voice_text verbatim - module brief section 9's "don't simply
    place the entire script on screen" applies to burned-in captions
    exactly as it does to the on-canvas text) shown for that scene's
    own duration, timed against the ASSEMBLED (concatenated) export
    timeline - same "timed against where it lands in the export, not
    its original position" approach as
    jarvis.video_studio.export._write_srt().

    `animation="fade"` wraps each line's own text in a libass override
    tag (`{\\fad(<ms>,<ms>)}`) - SRT's own format has no native
    animation support, but ffmpeg's libass subtitle renderer (the same
    renderer `subtitles=` already uses) honors ASS/SSA override tags
    embedded directly in an otherwise-plain SRT's text lines, so this
    needs no format change, no separate .ass file, and no extra ffmpeg
    option - "none" (the default) writes byte-for-byte the same SRT as
    before this parameter existed.

    `time_offset_seconds` (real, reported bug fix - "Intro Cover" mode:
    when a cover image is inserted as the video's own first segment, see
    export_reel_video()'s own docstring for the full mechanism, every
    scene's own subtitle timing must be pushed back by that segment's
    duration, or captions would appear during the cover instead of their
    own scene. 0.0 (the default) keeps every existing call site's output
    byte-for-byte unchanged.

    strip_hashtags() is applied to each scene's own on_screen_text here
    too (real, reported bug fix - "hashtags appear in the final Reel
    video", same fix as jarvis.reel_generator.scenes'/.scene_render's
    own _scene_design_brief()) - burned-in subtitles are the OTHER real
    choke point where scene text becomes part of the exported video's
    own pixels, alongside the baked-in scene image itself."""
    lines = []
    cursor = time_offset_seconds
    for i, scene in enumerate(scenes, start=1):
        start = cursor
        end = cursor + scene.duration_seconds
        text = strip_hashtags(scene.on_screen_text)
        if animation == "fade":
            text = f"{{\\fad({_CAPTION_FADE_DURATION_MS},{_CAPTION_FADE_DURATION_MS})}}{text}"
        lines.append(str(i))
        lines.append(f"{_srt_timestamp(start)} --> {_srt_timestamp(end)}")
        lines.append(text)
        lines.append("")
        cursor = end
    destination.write_text("\n".join(lines), encoding="utf-8")


_MOTION_CHOICES = ("zoom_in", "zoom_out", "pan_left", "pan_right", "static")
_MOTION_FPS = 30
# The fixed framerate every scene in a motion-enabled export uses -
# both the zoompan branch (explicit fps=) and the plain-scale branch
# (explicit fps= added only when motion is in use - see
# export_reel_video()'s own comment on `plain_scale_suffix`) - keeping
# every concatenated segment's framerate identical is what fixes the
# corrupted-duration bug that motivated adding this constant at all.


def _zoompan_filter_for_motion(motion: str, *, duration_seconds: float, fps: int = _MOTION_FPS) -> str | None:
    """Returns an ffmpeg zoompan filter expression implementing `motion`
    as a real, visible Ken Burns pan/zoom effect on a still image, or
    None for "static"/an unrecognized value (no effect - the caller
    falls back to a plain scale, identical to this function never having
    been called). Hand-tested directly against real ffmpeg (zoompan
    filter, standard since ffmpeg 2.x) before being wired in here - see
    this module's own CHANGELOG-style docstring note on
    export_reel_video()'s `motion_by_scene` parameter for the call-site
    contract this exists to serve.

    Every source image is pre-scaled to twice the export resolution
    (scale=2160:-1, i.e. 2x _EXPORT_WIDTH) before zoompan - enough
    headroom for zoompan's own per-frame crop to stay smooth/non-jittery
    without the extreme (and, hand-tested, extremely slow - a naive
    scale=8000:-1 recipe seen in some online examples took long enough
    per scene to make an 8-scene export impractical) upscale factor a
    higher-resolution target might suggest."""
    frames = max(1, int(round(duration_seconds * fps)))
    upscale_width = _EXPORT_WIDTH * 2
    if motion == "zoom_in":
        zoom_expr = "min(zoom+0.0015,1.3)"
        x_expr, y_expr = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif motion == "zoom_out":
        zoom_expr = "if(eq(on,0),1.3,max(zoom-0.0015,1.0))"
        x_expr, y_expr = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif motion == "pan_left":
        zoom_expr = "1.15"
        x_expr, y_expr = "max(0,iw*0.15-on*2)", "ih/2-(ih/zoom/2)"
    elif motion == "pan_right":
        zoom_expr = "1.15"
        x_expr, y_expr = "min(iw*0.15,on*2)", "ih/2-(ih/zoom/2)"
    else:
        return None
    return (
        f"scale={upscale_width}:-1,zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':"
        f"d={frames}:s={_EXPORT_WIDTH}x{_EXPORT_HEIGHT}:fps={fps}"
    )


def _clip_input_filter(*, duration_seconds: float, fps: int = _MOTION_FPS) -> str:
    """NATURAL MOTION / HYBRID mode's own filter chain for a REAL video
    clip input (jarvis.reel_generator.motion_engine's output, spliced in
    via `clip_by_scene` - see export_reel_video()'s own docstring),
    sibling to _zoompan_filter_for_motion() above but for an already-
    timed video input rather than a looped still image (so no "-loop 1"
    on that input, and no zoompan - the clip already has its own real
    motion).

    ffmpeg's `concat` filter does NOT implicitly resample - every
    spliced segment must already match resolution, SAR, and fps before
    reaching concat, or the result is commonly corrupted/desynced
    output (the single most-cited ffmpeg concat gotcha). This filter
    chain normalizes all three:
      - `scale=...:force_original_aspect_ratio=increase,crop=...` -
        the EXACT same cover-crop-to-fill treatment
        jarvis.reel_generator.scene_render already applies to every
        still image, so a mixed still-then-clip sequence never visibly
        "jumps" in framing/zoom between a still scene and a clip scene.
      - `setsar=1` - matches every still-image branch's own SAR (1,
        since they come from Pillow-authored JPEGs with no anamorphic
        pixel aspect).
      - `fps=<fps>` - matches whichever fps the rest of THIS export's
        segments are using (see export_reel_video()'s own `any_motion`
        calculation, extended by this feature to also become True when
        `clip_by_scene` is non-empty, so every segment in a mixed
        export shares one consistent fps).
      - `trim=duration=<scene.duration_seconds>` - clamps a clip that
        came back LONGER than requested (Runway's own duration
        parameter is a hint, not an exact contract) to the scene's own
        exact duration budget - same clamping role `-t <duration>`
        plays for a looped still image. A clip SHORTER than requested
        is caught separately, BEFORE this filter is ever built - see
        export_reel_video()'s own too-short-clip guard.
      - `setpts=PTS-STARTPTS` - resets the segment's own timestamp base
        to start at 0 after `trim` - mandatory (a well-documented ffmpeg
        gotcha): omitting this after a `trim` silently leaves the
        segment's original, non-zero PTS offset in place, which breaks
        concat's own timing assumptions for every segment after it."""
    return (
        f"scale={_EXPORT_WIDTH}:{_EXPORT_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={_EXPORT_WIDTH}:{_EXPORT_HEIGHT},setsar=1,fps={fps},"
        f"trim=duration={duration_seconds},setpts=PTS-STARTPTS"
    )


def export_reel_video(
    scenes: tuple[Scene, ...], visuals: list[SceneVisual], *, output_path: Path,
    burn_in_captions: bool | None = None, motion_by_scene: dict[int, str] | None = None,
    voiceover_path: Path | None = None, caption_style: CaptionStyle | None = None,
    cover_intro_path: Path | None = None, cover_intro_duration_seconds: float = 1.5,
    clip_by_scene: dict[int, Path] | None = None,
) -> ReelExportResult:
    """Renders `scenes` (in order, each already having a successfully
    rendered SceneVisual in `visuals`) into a single 1080x1920 MP4 at
    `output_path`. Raises ExportError if `scenes` is empty, any scene
    lacks a successfully rendered visual (a caller should filter/regen
    failed scenes before calling this - export does not silently skip a
    missing scene, since a Reel with a gap in it is a real problem, not
    a partial success to tolerate), ffmpeg is missing, or the subprocess
    itself fails/times out.

    `burn_in_captions` now DEFAULTS TO None (Stage D: textless scene
    visuals) - AUTO-DERIVED from `visuals` themselves rather than a
    fixed True/False: True if ANY scene's own SceneVisual
    .has_baked_in_text is False (see that field's own docstring - a
    genuinely textless scene image, jarvis.reel_generator.scene_render
    /.scenes's own render_textless=True mode), False otherwise (every
    scene's image already has scene.on_screen_text drawn directly into
    its pixels - this codebase's ORIGINAL, still-default rendering
    behavior). This auto-derivation exists because burning captions on
    top of an ALREADY-text-bearing image was confirmed by hand-testing
    (extracting and visually inspecting real frames from a real export)
    to produce two overlapping, illegible copies of the identical
    phrase in every frame - the exact bug this parameter's own default
    was first introduced to prevent; deriving it from the real,
    per-scene has_baked_in_text flag (rather than trusting a caller to
    remember to flip one global bool correctly) makes that mistake
    structurally impossible instead of merely documented against.

    Passing an EXPLICIT True or False here still overrides the
    auto-derivation entirely (unchanged behavior from before this
    parameter's default changed) - only omitting it (or passing None
    explicitly) triggers auto-derivation. No pre-existing call site in
    this codebase ever passed this parameter explicitly, so every one
    of them keeps producing EXACTLY the same output as before (their
    scenes all have has_baked_in_text=True, since none of them use the
    new render_textless=True path yet - auto-derivation resolves to
    False for them, identical to the previous hardcoded default).

    `motion_by_scene`, if given, maps a scene's `.number` to one of
    _MOTION_CHOICES ("zoom_in"/"zoom_out"/"pan_left"/"pan_right"/
    "static") - a real ffmpeg zoompan Ken Burns pan/zoom effect applied
    to that scene's still image instead of a plain static scale (see
    jarvis.story_generator's own visual-richness brief, requirement 5).
    Defaulting to None means every scene renders with a PLAIN scale
    filter, byte-for-byte the SAME ffmpeg command shape this function
    has always produced - AI Reel Generator's own call site never passes
    this parameter, so its output is completely unaffected by this
    parameter's existence. A scene missing from the dict, or mapped to
    "static"/an unrecognized value, also gets the plain scale filter.

    `voiceover_path`, if given (a real, existing WAV file - see
    jarvis.reel_generator.voiceover.generate_voiceover(), Stage B),
    muxes that audio into the export as its own AAC audio track,
    loudness-normalized via ffmpeg's own `loudnorm` filter (EBU R128,
    the standard "make the actual playback volume consistent and not
    over/under loud" normalization, applied here since the raw Azure
    Neural TTS output has no such guarantee on its own) and padded with
    trailing silence or trimmed to the video's own concatenated
    duration (via `apad`+`atrim` - a voiceover is rarely EXACTLY as long
    as the rendered video, since it comes from a separate, independent
    TTS call rather than from the same per-scene duration numbers), so
    the output's video length is never changed by adding audio.
    Omitting this parameter (the default, None) produces the EXACT same
    silent, video-only export this function has always produced - every
    pre-existing call site is completely unaffected.

    `caption_style` (Stage C, CaptionStyle above), if given, controls
    the font/position/size/animation of the burned-in ffmpeg subtitle -
    it has NO effect at all unless captions end up burned in (either
    via auto-derivation finding a textless scene, or an explicit
    `burn_in_captions=True` - see that parameter's own docstring).
    Omitting it (the default, None) when captions ARE burned in falls
    back to CaptionStyle()'s own defaults.

    `cover_intro_path` (real, reported bug fix: "the selected Reel cover
    never appears in the exported video at all" - see
    jarvis.reel_generator.cover's own "Intro Cover"/"Both" integration
    modes), if given, inserts that REAL image file as the video's own
    first segment, shown for `cover_intro_duration_seconds` (default
    1.5s, the module brief's own "1-2 seconds" requirement) before the
    first real scene - using the exact same "-loop 1 -t <duration> -i
    <image>" + scale/setsar input shape every scene already uses (see
    the per-scene loop below), so it is rendered with IDENTICAL 1080x1920
    scaling/encoding, never a second, different code path. The cover
    segment carries NO subtitle text and NO voiceover audio of its own -
    every scene's own subtitle timing (`_write_srt`'s own
    `time_offset_seconds`) and the voiceover mux's own timeline are both
    shifted forward by exactly this duration, so burned-in captions/
    narration still line up with their own real scenes, never
    the cover. Omitting this (the default, None) produces the EXACT same
    export as before this parameter existed - every pre-existing call
    site is completely unaffected.

    `clip_by_scene` (NATURAL MOTION / HYBRID modes), if given, maps a
    scene's `.number` to a REAL, already-generated video clip file
    (jarvis.reel_generator.motion_engine's own output) to splice in for
    that scene INSTEAD of its still image - see _clip_input_filter()'s
    own docstring for the exact normalization chain (scale/crop/setsar/
    fps/trim/setpts) that keeps a mixed still+clip export concat-safe.
    **Precedence rule**: a scene present in `clip_by_scene` ALWAYS uses
    its real clip and any `motion_by_scene` entry for that SAME scene
    number is ignored - a real clip already has its own real motion
    baked in, and applying a fake Ken-Burns zoompan on top of it would
    be visually wrong (a zoom effect on already-moving footage). A
    clip's own real, measured duration (via jarvis.video_studio
    .ffmpeg_utils.probe_video(), never Runway's self-reported metadata -
    matching jarvis.reel_generator.video_generation's own "measure the
    real file" convention) is checked against `scene.duration_seconds`
    BEFORE it is spliced in: a clip meaningfully SHORTER than the
    scene's own duration budget cannot be trimmed up to fit (only
    trimmed down), so that scene silently falls back to its still image
    instead, and a human-readable warning is appended to this result's
    own `scene_fallback_warnings` - never a hard export failure just
    because one clip came back short, and never an awkward hard cut
    mid-segment either. Omitting this parameter (the default, None)
    produces the EXACT same export as before this parameter existed -
    every pre-existing call site (including every Static-mode project)
    is completely unaffected."""
    ffmpeg = _require_ffmpeg()
    if not scenes:
        raise ExportError("This Reel has no scenes to export.")

    visuals_by_number = {v.scene_number: v for v in visuals}
    for scene in scenes:
        visual = visuals_by_number.get(scene.number)
        if visual is None or visual.image_path is None:
            raise ExportError(
                f"Scene {scene.number} has no rendered visual yet - regenerate it before exporting."
            )
        if not visual.image_path.is_file():
            raise ExportError(f"Scene {scene.number}'s image file is missing: {visual.image_path}")

    if burn_in_captions is None:
        # Auto-derivation (Stage D) - see this function's own docstring
        # for the full reasoning. Only scenes actually present in
        # `visuals` are consulted (a scene failing the "has a rendered
        # visual" check above already raised, so by this point every
        # scene in `scenes` has a matching entry).
        burn_in_captions = any(not v.has_baked_in_text for v in visuals)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    cwd = output_path.parent

    # One "-loop 1 -t <duration> -i <image>" input per scene (ffmpeg
    # loops a still image into a timed video segment - a motion-enabled
    # scene instead omits "-t" and relies on the filter graph's own
    # trim= to cap its length, see the per-scene loop below), then
    # concat (video-only - no audio stream exists, see this module's own
    # docstring for why). Every image path is made cwd-relative with
    # forward slashes before being placed in the command - the SAME
    # Windows drive-letter-colon-breaks-the-filtergraph-parser
    # workaround jarvis.video_studio.export/.transcribe/.cover already
    # document and rely on (a drive letter's colon is otherwise
    # misparsed as a filtergraph option separator, even escaped).
    motion_by_scene = motion_by_scene or {}
    clip_by_scene = clip_by_scene or {}
    scene_fallback_warnings: list[str] = []

    # A clip that's too short to fill its own scene's duration budget is
    # rejected HERE, before any input/filter construction, so the rest
    # of this function only ever sees a clip_by_scene entry it will
    # actually use - a rejected scene simply behaves as if it were never
    # in clip_by_scene at all (falls through to its motion_by_scene/
    # plain-still handling below), with a specific warning recorded.
    # Real, measured duration (probe_video()) - never Runway's own
    # self-reported metadata - matching jarvis.reel_generator
    # .video_generation's own "measure the real downloaded file"
    # convention.
    verified_clip_by_scene: dict[int, Path] = {}
    if clip_by_scene:
        from jarvis.video_studio.ffmpeg_utils import FFmpegError, probe_video

        for scene in scenes:
            clip_path = clip_by_scene.get(scene.number)
            if clip_path is None:
                continue
            try:
                clip_probe = probe_video(clip_path)
            except FFmpegError as e:
                scene_fallback_warnings.append(
                    f"Scene {scene.number}'s clip couldn't be read ({e}) - used its still image instead."
                )
                continue
            if clip_probe.duration_seconds < scene.duration_seconds - 0.1:
                scene_fallback_warnings.append(
                    f"Scene {scene.number}'s clip ({clip_probe.duration_seconds:.1f}s) is shorter than its "
                    f"scene duration ({scene.duration_seconds:.1f}s) - used its still image instead."
                )
                continue
            verified_clip_by_scene[scene.number] = clip_path
    clip_by_scene = verified_clip_by_scene

    # zoompan's own output is a fixed-fps stream (see
    # _zoompan_filter_for_motion()'s own `fps` parameter); the plain
    # "-loop 1" scale branch below has NO explicit fps and inherits
    # ffmpeg's own default input framerate for a looped still image
    # (25fps) - concatenating a 25fps segment with a 30fps zoompan
    # segment produces a badly corrupted duration (hand-tested: an
    # 8-scene, 24-second story concatenated to over 7 minutes of
    # output). Forcing the SAME explicit fps on every plain-scale
    # segment fixes this - now ALSO triggered by a non-empty
    # clip_by_scene (a real video clip's own _clip_input_filter() always
    # sets an explicit fps too - see that function's own docstring - so
    # every segment in a mixed export must share one consistent fps) -
    # but ONLY when motion_by_scene/clip_by_scene actually have entries,
    # so a call with neither (every pre-existing AI Reel Generator call
    # site) produces the EXACT same ffmpeg command shape as before
    # either parameter existed - see this function's own docstring.
    any_motion = clip_by_scene or any(
        _zoompan_filter_for_motion(m, duration_seconds=1.0) is not None for m in motion_by_scene.values()
    )
    plain_scale_suffix = f",fps={_MOTION_FPS}" if any_motion else ""

    input_args: list[str] = []
    trim_parts: list[str] = []
    concat_inputs: list[str] = []
    for i, scene in enumerate(scenes):
        visual = visuals_by_number[scene.number]
        assert visual.image_path is not None

        clip_path = clip_by_scene.get(scene.number)
        if clip_path is not None:
            # NATURAL MOTION / HYBRID: a real, already-verified-long-
            # enough video clip takes precedence over BOTH the still
            # image and any motion_by_scene entry for this same scene
            # number (see this function's own docstring's precedence
            # rule) - a plain "-i <clip>" input (no "-loop 1": it's
            # already a timed video, unlike a still image).
            try:
                relative_clip_path = clip_path.resolve().relative_to(cwd.resolve())
                clip_arg = str(relative_clip_path).replace("\\", "/")
            except ValueError:
                clip_arg = str(clip_path.resolve()).replace("\\", "/")
            input_args += ["-i", clip_arg]
            trim_parts.append(
                f"[{i}:v]{_clip_input_filter(duration_seconds=scene.duration_seconds)}[v{i}];"
            )
            concat_inputs.append(f"[v{i}]")
            continue

        try:
            relative_image_path = visual.image_path.resolve().relative_to(cwd.resolve())
            image_arg = str(relative_image_path).replace("\\", "/")
        except ValueError:
            # The image lives outside the output directory's own tree
            # (e.g. a scene image from a different project) - fall back
            # to an absolute path with forward slashes; still avoids
            # backslashes, which is the more commonly-cited half of the
            # Windows-path pitfall, though the drive-letter colon issue
            # is the one actually hand-tested/confirmed elsewhere in
            # this codebase (see this module's own docstring) - a scene
            # image is always under the SAME project's scenes_dir in
            # every normal call path, so this branch is a defensive
            # fallback, not the expected case.
            image_arg = str(visual.image_path.resolve()).replace("\\", "/")

        motion = motion_by_scene.get(scene.number)
        zoompan = _zoompan_filter_for_motion(motion, duration_seconds=scene.duration_seconds) if motion else None
        if zoompan is not None:
            # No "-t <duration>" on this input - zoompan's own `d=`
            # (frame count) defines its output length, and pairing it
            # with an ALSO-time-limited looped input was hand-tested to
            # badly corrupt the segment's real duration (zoompan+scale
            # in one filter chain, fed a "-t"-limited infinite-loop
            # input, produced 75x the intended length - see this
            # function's own docstring/changelog for the full
            # investigation). A trailing `trim=duration=` clamps the
            # filter's own output to the scene's exact intended length
            # as a hard safety net regardless of frame-count rounding.
            input_args += ["-loop", "1", "-i", image_arg]
            trim_parts.append(f"[{i}:v]{zoompan},setsar=1,trim=duration={scene.duration_seconds}[v{i}];")
        else:
            input_args += ["-loop", "1", "-t", str(scene.duration_seconds), "-i", image_arg]
            trim_parts.append(f"[{i}:v]scale={_EXPORT_WIDTH}:{_EXPORT_HEIGHT}{plain_scale_suffix},setsar=1[v{i}];")
        concat_inputs.append(f"[v{i}]")

    # Intro Cover ("both"/"intro" integration mode - see this function's
    # own docstring) - added as ONE MORE input, physically AFTER every
    # scene input (never inserted at index 0), specifically so its own
    # input index never shifts any scene's own [i:v] references above,
    # or the voiceover mux's own `audio_input_index = len(scenes)`
    # convention just below - `[vcover]` is instead placed at the FRONT
    # of `concat_inputs` (ffmpeg's concat filter orders its OUTPUT by
    # the order labels are listed here, completely independent of each
    # label's own physical input index), so the cover still plays FIRST
    # in the assembled video despite being the LAST "-i" argument on the
    # command line.
    num_concat_segments = len(scenes)
    if cover_intro_path is not None:
        cover_input_index = len(scenes)
        try:
            relative_cover_path = cover_intro_path.resolve().relative_to(cwd.resolve())
            cover_image_arg = str(relative_cover_path).replace("\\", "/")
        except ValueError:
            cover_image_arg = str(cover_intro_path.resolve()).replace("\\", "/")
        input_args += ["-loop", "1", "-t", str(cover_intro_duration_seconds), "-i", cover_image_arg]
        trim_parts.append(
            f"[{cover_input_index}:v]scale={_EXPORT_WIDTH}:{_EXPORT_HEIGHT}{plain_scale_suffix},setsar=1[vcover];"
        )
        concat_inputs.insert(0, "[vcover]")
        num_concat_segments += 1

    concat_filter = "".join(trim_parts) + "".join(concat_inputs) + f"concat=n={num_concat_segments}:v=1:a=0[catv]"

    video_label = "catv"
    filter_stages = [concat_filter]

    cover_offset_seconds = cover_intro_duration_seconds if cover_intro_path is not None else 0.0

    srt_path: Path | None = None
    if burn_in_captions:
        style = caption_style if caption_style is not None else CaptionStyle()
        srt_path = output_path.with_suffix(".captions.srt")
        _write_srt(scenes, srt_path, animation=style.animation, time_offset_seconds=cover_offset_seconds)
        # force_style is single-quoted so its own commas are never
        # mistaken for filtergraph argument separators (ffmpeg's
        # documented escaping for a subtitles= suboption value
        # containing commas/colons) - the style string itself is built
        # entirely from CaptionStyle's own fixed vocabulary/constants
        # (see force_style()'s own docstring), never free-form/user-
        # supplied text, so there is nothing else to escape.
        #
        # charenc=UTF-8 (real, reported bug fix - "Lithuanian subtitle
        # letters are garbled in the exported video"): _write_srt() above
        # always writes the .srt as UTF-8 (destination.write_text(...,
        # encoding="utf-8")), but ffmpeg's own `subtitles=` filter (which
        # goes through libass to actually read that file back) does NOT
        # assume UTF-8 on its own - without an explicit charenc, libass
        # falls back to iconv's locale/codepage auto-detection, which on
        # this Windows machine is a Windows-125x codepage, not UTF-8. A
        # Lithuanian diacritic (ą/č/ę/ė/į/š/ų/ū/ž) is valid UTF-8 but gets
        # misinterpreted byte-by-byte under that codepage, producing
        # exactly the "wrong characters instead of the correct text"
        # symptom that was reported - plain ASCII text never showed this,
        # which is why it went unnoticed until Lithuanian captions were
        # actually exercised. charenc=UTF-8 tells libass explicitly what
        # encoding the file already is in, matching what was actually
        # written - no change to _write_srt() itself was needed.
        filter_stages.append(
            f"[{video_label}]subtitles={srt_path.name}:charenc=UTF-8:force_style='{style.force_style()}'[outv]"
        )
        video_label = "outv"

    # Voiceover mux (Stage B): the audio input is appended AFTER every
    # per-scene image input (and after the cover intro input, if one
    # exists - see cover_intro_path's own docstring), so its own ffmpeg
    # input index is exactly len(scenes) plus 1 more if a cover intro is
    # present - the same "next free index" convention the per-scene loop
    # above already establishes for [0:v]..[N-1:v]. loudnorm is a
    # one-pass (not the more "correct" but much slower two-pass)
    # application - good enough for a short-form Reel's own narration,
    # matching this module's own "no new heavyweight dependency, plain
    # ffmpeg filters only" convention. apad appends silence so a
    # voiceover SHORTER than the video never leaves the tail silent-cut-
    # off-sounding; atrim=0:<duration> then clamps it to the video's
    # own exact total length either way (a voiceover LONGER than the
    # video is simply cut off at the video's end, rather than extending
    # the export - module brief's own "video length is the master
    # timeline" assumption, matching how burned-in captions are already
    # timed against the assembled export, not the narration).
    # `adelay` shifts the ENTIRE narration forward by the cover's own
    # duration when a cover intro exists, so the voiceover starts
    # exactly when the first real scene does, never overlapping the
    # (silent) cover segment - `atrim`'s own total_duration below already
    # includes that same offset, so the clamp still lands on the video's
    # real total length.
    audio_label: str | None = None
    if voiceover_path is not None:
        total_duration = cover_offset_seconds + sum(scene.duration_seconds for scene in scenes)
        audio_input_index = len(scenes) + (1 if cover_intro_path is not None else 0)
        input_args += ["-i", str(voiceover_path)]
        delay_ms = int(round(cover_offset_seconds * 1000))
        adelay_stage = f",adelay={delay_ms}:all=1" if delay_ms > 0 else ""
        filter_stages.append(
            f"[{audio_input_index}:a]loudnorm{adelay_stage},apad,atrim=0:{total_duration}[outa]"
        )
        audio_label = "outa"

    full_filter = ";".join(filter_stages)

    cmd = [ffmpeg, "-y", *input_args, "-filter_complex", full_filter, "-map", f"[{video_label}]"]
    if audio_label is not None:
        cmd += ["-map", f"[{audio_label}]", "-c:v", "libx264", "-c:a", "aac"]
    else:
        cmd += ["-c:v", "libx264"]
    cmd += ["-movflags", "+faststart", output_path.name]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=_EXPORT_TIMEOUT_SECONDS,
            check=False, cwd=str(cwd),
        )
    except subprocess.TimeoutExpired:
        raise ExportError(f"Export timed out for {output_path.name}.") from None
    finally:
        if srt_path is not None:
            srt_path.unlink(missing_ok=True)

    if result.returncode != 0 or not output_path.is_file():
        stderr_tail = result.stderr.strip()[-400:]
        raise ExportError(f"Export failed. ffmpeg said: {stderr_tail}")

    from jarvis.video_studio.ffmpeg_utils import probe_video

    probe = probe_video(output_path)
    return ReelExportResult(
        output_path=output_path, duration_seconds=probe.duration_seconds,
        width=probe.width or _EXPORT_WIDTH, height=probe.height or _EXPORT_HEIGHT,
        file_size_bytes=probe.file_size_bytes,
        scene_fallback_warnings=tuple(scene_fallback_warnings),
    )
