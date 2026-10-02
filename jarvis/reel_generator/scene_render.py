"""Visual generation per scene, enriched by a ScenePlan - shared by AI
Reel Generator's own "Smart Visual Director" brief and
jarvis.story_generator (that feature's own earlier visual-richness
brief). Renders a scene's base visual exactly like
jarvis.reel_generator.scenes.render_scene_visual() does (Design
Studio's render_design(), UNMODIFIED - see that function's own
docstring for why: no AI image/video generation exists anywhere in this
codebase), then composites the ScenePlan's own supporting-visual
markers, on-screen text cues, and sticker/emoji ON TOP of that base
render with Pillow.

Originally written for jarvis.story_generator, PROMOTED here (see
jarvis.reel_generator.visual_plan's own docstring for the full
promotion history) once AI Reel Generator's own Smart Visual Director
brief needed the exact same rendering. jarvis.story_generator's GUI
dashboard now imports render_all_story_scenes() FROM here.

This is a SEPARATE module from jarvis.reel_generator.scenes, not an
edit to it - jarvis.reel_generator.scenes.render_all_scenes() stays
completely untouched (AI Reel Generator's own ORIGINAL, pre-Smart-
Visual-Director rendering path keeps working exactly as before - the
module brief's own hard requirement) while this module reuses its EXACT
same base-render approach (a DesignBrief built directly from scene
data, rendered via jarvis.design_studio.render.render_design()) and
then does one more Pillow pass for any scene that has a ScenePlan.

A scene with no ScenePlan (an older project, a plan that failed to
validate, or the ORIGINAL "CREATE FROM MY IDEA" flow that never
generates one) renders through
jarvis.reel_generator.scenes.render_scene_visual() directly instead -
see render_story_scene_visual()'s own docstring - so this module only
ever ADDS detail, never blocks a plain render.

Every ScenePlan field with a visible effect (supporting visual labels,
text cue text/position/timing represented as a single composited frame
- see this module's own "timing" caveat below - sticker glyph, and now
multiple uploaded photos composited as a real collage/picture-in-
picture layout) is drawn into the actual output JPEG, not merely stored
as metadata - this module's whole purpose is turning a plan into real
pixels.

Timing caveat: a single scene visual is ONE STILL IMAGE (Reel
Generator's storage model - jarvis.reel_generator.export loops a still
for the scene's duration, one input per scene). A ScenePlan's text
cues/sticker each have their OWN start/end within the scene, but a
still image cannot show three different layers appearing/disappearing
at different moments within one image file. This module resolves that
by choosing ONE composite moment per element (a text cue's own
start_seconds; the sticker's own sticker_start_seconds) and drawing
every element that would be visible at ITS OWN moment - in practice
this means all text cues and the sticker end up composited into the
same single frame (the still image IS the scene for its whole
duration), which is the most complete single frame anyone will actually
see for a still-image scene. A TextCue's own `animation` value
(including AI Reel Generator's newer word_by_word/typewriter) is shown
as a LABEL in the storyboard UI and honored by
jarvis.reel_generator.export's own subtitle timing (real per-word
reveal happens there, in the burned-in captions, which DO have their
own timeline - see that module's own docstring) - it is NOT simulated
on this module's own static scene image, which has no timeline of its
own to animate within."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from jarvis.design_studio.brief import DesignBrief
from jarvis.design_studio.render import FORMAT_DIMENSIONS, RenderError, render_design
from jarvis.design_studio.styles import DesignStyle, resolve_style
from jarvis.reel_generator import image_generation
from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.scenes import SceneVisual
from jarvis.reel_generator.storyboard import Scene, strip_hashtags
from jarvis.reel_generator.visual_plan import ScenePlan

_SCENE_FORMAT = "reel_cover"  # 1080x1920 (9:16) - same as jarvis.reel_generator.scenes' own constant

_EMOJI_FONT_PATH = "C:/Windows/Fonts/seguiemj.ttf"
_STICKER_SIZE_FRACTION = 0.14  # of canvas width
_STICKER_MARGIN_FRACTION = 0.06

_SUPPORTING_VISUAL_FONT_SIZE_FRACTION = 0.024
_SUPPORTING_VISUAL_BADGE_PADDING_FRACTION = 0.012

_TEXT_CUE_FONT_SIZE_FRACTION = 0.040
_TEXT_CUE_MARGIN_FRACTION = 0.08

# jarvis.story_generator's own original visual_type -> a DesignStyle
# NAME used as this scene's rendering treatment - mapped honestly to
# the closest EXISTING Pillow gradient/typography preset, since no AI
# image/video generation exists to actually produce a photo/product
# shot/B-roll. A scene's own brief.style (the whole Reel/story's
# overall style) is preferred when this table has no entry for the
# scene's own visual_type - this table is the FALLBACK/override
# specifically keyed to visual_type, giving each visual type a visibly
# different look even within one Reel/story, rather than every scene
# sharing one flat background.
_STYLE_BY_VISUAL_TYPE: dict[str, str] = {
    "photo_style": "lifestyle",
    "product_shot": "professional",
    "lifestyle": "wellness",
    "b_roll_style": "modern",
    "illustration": "feminine",
}

# AI Reel Generator's own requirement 8 "Visual Style" selector -
# a WHOLE-REEL style choice, mapped to the closest existing
# jarvis.design_studio.styles.DESIGN_STYLES preset. Applied as the
# REEL-WIDE base style (jarvis.reel_generator.brief.ReelBrief.style is
# overridden by this for the whole render call - see
# _style_for_scene_plan()'s own precedence order below), with
# _STYLE_BY_VISUAL_TYPE still able to override it per-scene when that
# scene's own visual_type suggests a more specific treatment.
_STYLE_BY_VISUAL_STYLE: dict[str, str] = {
    "cinematic": "modern", "lifestyle": "lifestyle", "minimal": "minimal", "luxury": "luxury",
    "soft_feminine": "feminine", "ugc": "clean", "emotional": "soft", "modern": "modern",
    "bold": "bold", "educational": "educational",
}


def _style_for_scene_plan(brief: ReelBrief, plan: ScenePlan | None, *, visual_style: str = "") -> str:
    """Precedence: a scene's own visual_type-specific treatment (most
    specific) > the Reel-wide visual_style selector > the brief's own
    plain style field (least specific, jarvis.reel_generator's own
    original behavior when no visual plan exists at all)."""
    base_style = _STYLE_BY_VISUAL_STYLE.get(visual_style, brief.style) if visual_style else brief.style
    if plan is None:
        return base_style
    return _STYLE_BY_VISUAL_TYPE.get(plan.visual_type, base_style)


def _scene_design_brief(
    brief: ReelBrief, scene: Scene, plan: ScenePlan | None, *, visual_style: str = "", render_textless: bool = False,
) -> DesignBrief:
    """Same construction as jarvis.reel_generator.scenes
    ._scene_design_brief() (a DesignBrief built directly from already-
    decided text, no new LLM call) - only the STYLE varies with the
    plan/visual_style, via _style_for_scene_plan().

    ONE authoritative text layer rule (this feature's own "text appears
    only once" fix - see this module's own docstring's changelog note):
    when `plan` has its own text_cues, or `render_textless=True`, this
    base render's headline is left BLANK (render_design() skips drawing
    an empty headline entirely). For a `plan` with text_cues,
    _composite_text_cues() becomes the ONLY place this scene's
    on-screen text is drawn - the plan's own cues already carry the
    actual text, timing, position, and animation the Visual Story
    Director decided on, and drawing scene.on_screen_text as a SEPARATE
    fixed headline on top of that was confirmed, by hand-testing real
    exported frames, to often just restate the same phrase a second
    time (legible, not overlapping, but still a real duplication the
    module brief explicitly calls out). For `render_textless=True` (the
    textless overlay-text mode), NEITHER the headline NOR the plan's own
    text_cues are drawn (see render_story_scene_visual()'s own
    docstring - the caller skips _composite_text_cues() entirely in
    this mode) - jarvis.reel_generator.export becomes the ONE place
    this scene's text is drawn at all. A plan with NO cues and
    render_textless=False (or no plan at all - the ORIGINAL, pre-Smart-
    Visual-Director path) still uses scene.on_screen_text as the
    headline exactly as before, since there is no other text layer to
    carry it in that case.

    strip_hashtags() is applied to the headline here too (same real,
    reported bug fix as jarvis.reel_generator.scenes's own
    _scene_design_brief() - see that function's own docstring) - the
    plan's own text_cues are separately sanitized where they're
    validated/composited (jarvis.reel_generator.storyboard
    ._validate_visual_plan_text_cue()/this module's own
    _composite_text_cues())."""
    headline = strip_hashtags(scene.on_screen_text)
    if render_textless or (plan is not None and plan.text_cues):
        headline = ""
    return DesignBrief(
        topic=brief.topic, objective=brief.objective, audience=brief.audience, tone=brief.tone,
        headline=headline, supporting_text="", cta="",
        format=_SCENE_FORMAT, style=_style_for_scene_plan(brief, plan, visual_style=visual_style),
    )


def _emoji_font(size: int) -> ImageFont.FreeTypeFont | None:
    try:
        return ImageFont.truetype(_EMOJI_FONT_PATH, size)
    except Exception:
        # A machine without Segoe UI Emoji installed (not Windows, or a
        # stripped install) - skip the sticker rather than fail the
        # whole scene render, matching this codebase's "a decorative
        # element's failure doesn't sink the render" convention.
        return None


def _composite_sticker(canvas: Image.Image, plan: ScenePlan, *, width: int, height: int) -> None:
    glyph = plan.sticker_glyph
    if not glyph:
        return
    size = int(width * _STICKER_SIZE_FRACTION)
    font = _emoji_font(size)
    if font is None:
        return
    margin = int(width * _STICKER_MARGIN_FRACTION)
    draw = ImageDraw.Draw(canvas)
    try:
        draw.text((width - margin - size, margin), glyph, font=font, embedded_color=True)
    except Exception:
        pass  # a glyph this Pillow/font combination can't draw - skip, never fail the render


def _composite_supporting_visuals(canvas: Image.Image, plan: ScenePlan, *, width: int, height: int) -> None:
    """Draws each supporting-visual LABEL (e.g. "arrow", "before/after
    split") as a small rounded badge along the bottom of the canvas - a
    real, visible element in the output frame (not just stored
    metadata), while staying honest that this codebase has no actual
    arrow/icon/illustration asset library to draw a literal graphic
    instead (see this module's own docstring)."""
    if not plan.supporting_visuals:
        return
    font_size = int(width * _SUPPORTING_VISUAL_FONT_SIZE_FRACTION)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", font_size)
    except Exception:
        return
    draw = ImageDraw.Draw(canvas)
    padding = int(width * _SUPPORTING_VISUAL_BADGE_PADDING_FRACTION)
    margin = int(width * 0.06)
    y = height - margin - font_size - padding * 2
    x = margin
    for label in plan.supporting_visuals:
        text = f"# {label}"
        bbox = font.getbbox(text)
        text_width, text_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
        badge_width = text_width + padding * 2
        badge_height = text_height + padding * 2
        if x + badge_width > width - margin:
            break  # stay within the canvas rather than overflow off-screen
        draw.rounded_rectangle(
            (x, y, x + badge_width, y + badge_height), radius=badge_height / 2, fill=(0, 0, 0, 140),
        )
        draw.text((x + padding, y + padding), text, font=font, fill="white")
        x += badge_width + padding


def _composite_text_cues(canvas: Image.Image, plan: ScenePlan, *, width: int, height: int) -> None:
    """Draws each TextCue's own exact text at its own POSITION (top/
    center/bottom) - see this module's own docstring for why every cue
    is drawn into the same single still frame rather than separate
    per-cue frames. `animation` is intentionally not simulated here (a
    still image has no motion of its own to animate within) - the
    cue's own timing/animation values remain on the ScenePlan for the
    storyboard UI to show and for jarvis.reel_generator.export's own
    subtitle timeline to use for real word-by-word reveal."""
    if not plan.text_cues:
        return
    font_size = int(width * _TEXT_CUE_FONT_SIZE_FRACTION)
    try:
        font = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", font_size)
    except Exception:
        return
    draw = ImageDraw.Draw(canvas)
    margin = int(width * _TEXT_CUE_MARGIN_FRACTION)
    max_width = width - margin * 2

    positions_used: dict[str, int] = {"top": margin, "center": height // 2, "bottom": height - margin}
    for cue in plan.text_cues:
        # strip_hashtags() applied at the actual drawing point too (see
        # this function's own caller-side fix in _scene_design_brief()'s
        # docstring) - cue.text is already sanitized wherever a ScenePlan
        # is validated (jarvis.reel_generator.storyboard
        # ._validate_visual_plan_text_cue()), but this is the true final
        # choke point before pixels exist, so it's applied again here as
        # a genuine single-point guarantee rather than trusting every
        # possible ScenePlan construction path to have already done it.
        cue_text = strip_hashtags(cue.text)
        if not cue_text:
            continue
        lines = _wrap_text(cue_text, font, max_width=max_width)
        if not lines:
            continue
        anchor_y = positions_used.get(cue.position, height // 2)
        total_height = sum((font.getbbox(line)[3] - font.getbbox(line)[1]) for line in lines)
        top_y = anchor_y if cue.position == "top" else (
            anchor_y - total_height // 2 if cue.position == "center" else anchor_y - total_height
        )
        y = top_y
        for line in lines:
            bbox = font.getbbox(line)
            line_width, line_height = bbox[2] - bbox[0], bbox[3] - bbox[1]
            x = (width - line_width) // 2
            # A simple outline (4 offset copies + the main draw) keeps
            # text legible over any background gradient without needing
            # a real drop-shadow/blur (no extra Pillow filter beyond
            # what's already used elsewhere in this codebase).
            for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2)):
                draw.text((x + dx, y + dy), line, font=font, fill="black")
            draw.text((x, y), line, font=font, fill="white")
            y += line_height + int(height * 0.01)


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, *, max_width: int) -> list[str]:
    words = text.split()
    if not words:
        return []
    lines: list[str] = []
    current: list[str] = []
    for word in words:
        candidate = " ".join(current + [word])
        if font.getbbox(candidate)[2] > max_width and current:
            lines.append(" ".join(current))
            current = [word]
        else:
            current.append(word)
    if current:
        lines.append(" ".join(current))
    return lines


def _cover_crop(image: Image.Image, width: int, height: int) -> Image.Image:
    """Scales `image` to COVER a width x height frame (never letterbox)
    and crops the overflow, centered - the standard "fill the frame"
    treatment for a real photo/generated image used as a scene's full-
    bleed base layer, same convention as _composite_uploaded_photos()'s
    own per-cell scaling below, applied here to a single whole-frame
    image instead of a grid cell."""
    img_w, img_h = image.size
    scale = max(width / img_w, height / img_h)
    resized = image.resize((max(1, round(img_w * scale)), max(1, round(img_h * scale))))
    crop_x = (resized.width - width) // 2
    crop_y = (resized.height - height) // 2
    return resized.crop((crop_x, crop_y, crop_x + width, crop_y + height))


def _composite_uploaded_photos(canvas: Image.Image, photo_paths: list[Path], *, width: int, height: int) -> None:
    """AI Reel Generator's own requirement 3 ("multiple photos in one
    scene"): when the person has uploaded 2+ photos for a scene, lays
    them out as a real grid/collage over the base render - genuinely
    real uploaded pixels, never regenerated or altered (this codebase's
    established "preserve the actual uploaded appearance" rule - see
    jarvis.design_studio.render's own docstring for the same rule
    applied to a single uploaded image). A single uploaded photo is NOT
    handled here - that already goes through
    jarvis.reel_generator.scenes' own uploaded_image_path parameter as
    a full-bleed background, same as before this module existed; this
    function only activates for 2 or more photos, where a full-bleed
    single image no longer makes sense."""
    count = len(photo_paths)
    if count < 2:
        return
    margin = int(width * 0.04)
    gap = int(width * 0.02)
    # A simple, predictable grid (2 columns, as many rows as needed) -
    # not a designed collage layout (no layout-generation capability
    # exists), but genuinely each photo's own real pixels, tiled.
    columns = 2
    rows = (count + columns - 1) // columns
    cell_width = (width - margin * 2 - gap * (columns - 1)) // columns
    cell_height = (height - margin * 2 - gap * (rows - 1)) // rows

    for i, photo_path in enumerate(photo_paths):
        if not photo_path.is_file():
            continue
        row, col = divmod(i, columns)
        cell_x = margin + col * (cell_width + gap)
        cell_y = margin + row * (cell_height + gap)
        try:
            photo = Image.open(photo_path).convert("RGB")
        except Exception:
            continue
        # Scale to COVER the cell (crop overflow) rather than letterbox
        # - a collage cell with visible background gaps around a
        # letterboxed photo looks broken; cropping to fill is the
        # standard collage/grid convention.
        photo_w, photo_h = photo.size
        scale = max(cell_width / photo_w, cell_height / photo_h)
        resized = photo.resize((max(1, int(photo_w * scale)), max(1, int(photo_h * scale))))
        crop_x = (resized.width - cell_width) // 2
        crop_y = (resized.height - cell_height) // 2
        cropped = resized.crop((crop_x, crop_y, crop_x + cell_width, crop_y + cell_height))
        canvas.paste(cropped, (cell_x, cell_y))


_REAL_IMAGE_VISUAL_SOURCES = ("ai_generated", "b_roll", "product_photo", "animated_background")
# ScenePlan.visual_source values for which a real, photorealistic
# generated image is attempted (Visual Story Director brief's own
# option B) - "typography" is explicitly the ONE source meant to stay a
# plain text card (the brief's own option G: "ONLY when no meaningful
# visual is possible"), so it's deliberately excluded here.
#
# "uploaded_photo"/"video_clip" are NOT in this tuple because they are
# normally handled by the person's own real upload instead (see
# uploaded_photo_paths below) - but see
# _UPLOAD_VISUAL_SOURCES_WITH_AI_FALLBACK's own docstring just below for
# what happens when the LLM picks one of THOSE two sources and no upload
# actually exists for that scene (a real, reported bug: this used to
# silently render a blank text card with no error at all).

_UPLOAD_VISUAL_SOURCES_WITH_AI_FALLBACK = ("uploaded_photo", "video_clip")
# Real, reported bug fix: the Smart Visual Director's own LLM call
# chooses visual_source per scene (jarvis.reel_generator.storyboard
# .generate_visual_plan()'s own prompt - see that module's own
# docstring), and can reasonably choose "uploaded_photo"/"video_clip"
# for a scene the person never actually uploaded a photo/video for (a
# CTA scene describing a phone screen tap is exactly the kind of shot
# an LLM reads as "footage", for example) - GENERATE SCENE VISUALS then
# used to silently render a blank Pillow text card for that ONE scene,
# with every OTHER AI-generated scene looking correct, and no error
# anywhere to explain why. Since these two sources exist specifically
# so a real uploaded photo/video frame takes priority over a generated
# image (see render_story_scene_visual()'s own docstring - checked
# BEFORE this function is even called), there is no meaningful
# difference once nothing was actually uploaded: the scene still needs
# SOME real visual, so it falls back to the exact same photorealistic
# AI generation as _REAL_IMAGE_VISUAL_SOURCES, using the SAME
# main_visual_prompt the Smart Visual Director already wrote for it.


def _generate_real_scene_image(
    plan: ScenePlan | None, *, output_path: Path, has_real_upload: bool = False,
) -> tuple[Path | None, str | None]:
    """Attempts real photorealistic image generation for this scene via
    jarvis.reel_generator.image_generation (OpenAI's Images API).
    Returns (image_path, warning):
      - (path, None) on success.
      - (None, None) when AI generation was never ATTEMPTED at all - no
        plan/prompt, the visual_source doesn't call for a generated
        photo, or OPENAI_API_KEY isn't configured - this is a normal,
        silent "fall back to Pillow" case exactly as before this
        function returned a plain Path | None, not a warning-worthy
        situation (most scenes/projects legitimately have no AI
        generation configured at all).
      - (None, warning) when generation WAS attempted (eligible +
        configured) but the API call itself failed (network/timeout/
        malformed response/local save error) - real, reported bug fix
        ("REGENERATE doesn't always generate a new image"): this is the
        ONE case that must be surfaced as a warning on the returned
        SceneVisual (see SceneVisual.ai_generation_warning's own
        docstring) rather than silently looking identical to "no AI
        generation was ever attempted", so a person isn't left thinking
        their REGENERATE click produced a fresh AI photo when it
        actually fell back to the same plain text-card render. Never
        raises either way - a warning is still a successful,
        usable-fallback outcome, matching
        jarvis.reel_generator.image_generation.generate_scene_image()'s
        own None-on-failure contract; the caller still renders a real,
        complete scene image regardless of which case this returns.

    `has_real_upload` is True only when the caller already found a real
    uploaded photo for THIS scene (render_story_scene_visual() checks
    that first and never calls this function at all in that case - see
    its own docstring) - it exists here only so this function's own
    eligibility check below can tell "visual_source is uploaded_photo/
    video_clip AND a real upload exists" (never reached, upload wins)
    apart from "same visual_source but NO upload actually exists" (the
    fallback this function now covers - see
    _UPLOAD_VISUAL_SOURCES_WITH_AI_FALLBACK's own docstring)."""
    if plan is None or not plan.main_visual_prompt:
        return None, None
    eligible = plan.visual_source in _REAL_IMAGE_VISUAL_SOURCES or (
        plan.visual_source in _UPLOAD_VISUAL_SOURCES_WITH_AI_FALLBACK and not has_real_upload
    )
    if not eligible:
        return None, None
    if not image_generation.is_configured():
        return None, None

    # Real, reported bug fix ("AI image generation failed (network
    # error or no usable response)" - a generic message with no way to
    # tell WHY): generate_scene_image() now returns the REAL, specific
    # failure reason (HTTP status + the API's own error message, e.g.
    # "insufficient_quota" - see that function's own docstring for the
    # real, hand-verified live API call that found this exact gap)
    # instead of a bare None - surfaced here verbatim rather than
    # replaced with a generic placeholder message.
    generated, failure_reason = image_generation.generate_scene_image(
        plan.main_visual_prompt, visual_source=plan.visual_source,
    )
    if generated is None:
        return None, f"AI image generation failed ({failure_reason}) - showing the text-card fallback instead."

    image_output_path = output_path.with_suffix(".generated.png")
    try:
        image_generation.save_scene_image(generated, output_path=image_output_path)
    except OSError as e:
        return None, f"AI image was generated but couldn't be saved ({e}) - showing the text-card fallback instead."
    return image_output_path, None


def render_story_scene_visual(
    brief: ReelBrief, scene: Scene, plan: ScenePlan | None, *, output_path: Path,
    style_transform: Callable[[DesignStyle], DesignStyle] | None = None,
    visual_style: str = "", uploaded_photo_paths: list[Path] | None = None,
    render_textless: bool = False,
) -> SceneVisual:
    """Renders one scene's visual. If a user-uploaded photo/video frame
    exists for this scene (`uploaded_photo_paths`, a single photo - see
    below for 2+), or a real photorealistic image can be generated via
    jarvis.reel_generator.image_generation (Visual Story Director
    brief's own option B - only attempted for
    visual_source in _REAL_IMAGE_VISUAL_SOURCES, and only when
    OPENAI_API_KEY is configured), that REAL image becomes the base
    layer scaled/cropped to fill the 1080x1920 frame - never this
    codebase's own Pillow gradient - with the scene's on-screen text/
    stickers/supporting-visual badges composited ON TOP of it, exactly
    matching the module brief's own "text must be an overlay ON TOP OF
    a visual, never a plain card" requirement.

    Falls back to the SAME base-render approach as
    jarvis.reel_generator.scenes.render_scene_visual() (a DesignBrief
    built from scene data, rendered via render_design() - reused
    unmodified) only when no real image is available for this scene -
    2+ uploaded photos still composite as a real grid/collage
    (`uploaded_photo_paths`, requirement 3) OVER that Pillow base, and -
    only when `plan` is given - the ScenePlan's supporting visuals, text
    cues, and sticker are composited on top of whichever base layer was
    actually used. Never raises - a RenderError is captured on the
    returned SceneVisual's `.error` field instead, matching
    jarvis.reel_generator.scenes's own established convention.

    `render_textless=True` (default False - every existing call site is
    completely unaffected) produces a scene image with NO on-screen
    text baked into its pixels AT ALL - not the headline, not a
    ScenePlan's own text_cues (which, like the headline, restate
    on_screen_text and would otherwise still bake text in even with a
    blank headline). scene.on_screen_text remains exactly what it
    always was - real, unmodified scene DATA - it simply isn't drawn
    onto this image; jarvis.reel_generator.export is the one place it
    gets drawn instead, as a real, styled, export-time caption (see
    that module's own CaptionStyle). Supporting-visual badges and the
    sticker are NOT text and still composite normally in this mode -
    only text_cues/headline are suppressed. The returned SceneVisual's
    own has_baked_in_text is set to False in this mode (True, the
    default, in every other case) - see that field's own docstring for
    why this flag exists and how jarvis.reel_generator.export uses it."""
    width, height = FORMAT_DIMENSIONS[_SCENE_FORMAT]

    real_photo_path: Path | None = None
    ai_generation_warning: str | None = None
    if uploaded_photo_paths and len(uploaded_photo_paths) == 1 and uploaded_photo_paths[0].is_file():
        # A single uploaded photo becomes the real base layer directly
        # (requirement C: user-provided photo) - 2+ uploaded photos
        # still go through the Pillow-based collage path below instead,
        # since "the real base layer" doesn't make sense for a grid of
        # several equally-weighted photos.
        real_photo_path = uploaded_photo_paths[0]
    else:
        # `has_real_upload` covers the 2+ uploaded-photos case too (that
        # collage still uses the person's own real photos, composited
        # over the Pillow base below - see this function's own docstring
        # for why 2+ photos don't use this "real base layer" path
        # directly) - AI generation must never override a real upload
        # that already exists, only fill in for a scene where the LLM
        # picked "uploaded_photo"/"video_clip" but nothing was actually
        # uploaded (see _UPLOAD_VISUAL_SOURCES_WITH_AI_FALLBACK's own
        # docstring - the exact bug this covers).
        has_real_upload = bool(uploaded_photo_paths)
        real_photo_path, ai_generation_warning = _generate_real_scene_image(
            plan, output_path=output_path, has_real_upload=has_real_upload,
        )

    source: str
    if real_photo_path is not None:
        try:
            canvas = Image.open(real_photo_path).convert("RGB")
            canvas = _cover_crop(canvas, width, height)
        except Exception as e:
            return SceneVisual(scene_number=scene.number, source="text_card", image_path=None, error=str(e))
        source = "uploaded_image" if uploaded_photo_paths else "generated_image"
        try:
            canvas.save(output_path, quality=92)
        except Exception as e:
            return SceneVisual(scene_number=scene.number, source="text_card", image_path=None, error=str(e))
    else:
        design_brief = _scene_design_brief(brief, scene, plan, visual_style=visual_style, render_textless=render_textless)
        style = resolve_style(design_brief.style)
        if style_transform is not None:
            style = style_transform(style)
        try:
            render_design(design_brief, style, output_path=output_path)
        except RenderError as e:
            return SceneVisual(scene_number=scene.number, source="text_card", image_path=None, error=str(e))
        source = "text_card"

    # Only re-open/re-composite/re-save when there is genuine enrichment
    # work to do - a lone (single) uploaded photo already used as the
    # real base layer above is NOT re-opened again here for the
    # collage path (see this function's own docstring: 2+ photos only
    # for the grid/collage), so `uploaded_photo_paths and len(...) >= 2`
    # mirrors _composite_uploaded_photos()'s own activation threshold
    # exactly - without this exact match, a single-photo call would
    # still trigger a needless lossy JPEG re-encode (open -> re-save at
    # quality=92) that changes NOTHING visible but was hand-tested to
    # produce a byte-different (though visually identical) file versus
    # never re-opening it at all.
    has_multi_photo_collage = bool(uploaded_photo_paths) and len(uploaded_photo_paths) >= 2
    if plan is not None or has_multi_photo_collage:
        try:
            canvas = Image.open(output_path).convert("RGB")
            width, height = canvas.size
            if has_multi_photo_collage:
                assert uploaded_photo_paths is not None
                _composite_uploaded_photos(canvas, uploaded_photo_paths, width=width, height=height)
                source = "uploaded_image"
            if plan is not None:
                _composite_supporting_visuals(canvas, plan, width=width, height=height)
                if not render_textless:
                    # Textless mode's whole point is NO baked-in text at
                    # all - a ScenePlan's own text_cues restate
                    # scene.on_screen_text exactly like the headline
                    # does (see this function's own docstring), so they
                    # are skipped here too, not just the headline.
                    _composite_text_cues(canvas, plan, width=width, height=height)
                _composite_sticker(canvas, plan, width=width, height=height)
            canvas.save(output_path, quality=92)
        except Exception as e:
            # The base render already succeeded and was saved - a
            # failure ENRICHING it is reported but the plain base image
            # stays on disk (a scene with a plain background is more
            # useful than none at all), matching this module's own
            # "enrichment never blocks a valid render" principle stated
            # in its own docstring.
            return SceneVisual(
                scene_number=scene.number, source="text_card", image_path=output_path,
                error=f"Visual plan details couldn't be composited: {e}", has_baked_in_text=not render_textless,
                ai_generation_warning=ai_generation_warning,
            )

    return SceneVisual(
        scene_number=scene.number, source=source, image_path=output_path, error=None,  # type: ignore[arg-type]
        has_baked_in_text=not render_textless, ai_generation_warning=ai_generation_warning,
    )


def render_all_story_scenes(
    brief: ReelBrief, scenes: tuple[Scene, ...], plans: dict[int, ScenePlan], *, output_dir: Path,
    style_transform: Callable[[DesignStyle], DesignStyle] | None = None,
    visual_style: str = "", uploaded_photos_by_scene: dict[int, list[Path]] | None = None,
    render_textless: bool = False,
) -> list[SceneVisual]:
    """Renders every scene's visual into `output_dir`, one file per
    scene named by scene number - same file-naming convention as
    jarvis.reel_generator.scenes.render_all_scenes() (so a project's
    reopen logic keeps working unmodified). `plans` maps a scene's
    `.number` to its ScenePlan; scenes not in this dict get a plain base
    render (no enrichment) - same "missing plan degrades gracefully"
    reasoning as this module's own docstring.
    `uploaded_photos_by_scene`, if given, maps a scene's `.number` to a
    list of that scene's own uploaded photo paths (requirement 3 - 2+
    photos trigger a real grid/collage composite; 0-1 are ignored here,
    since a single photo already has its own established full-bleed
    path via jarvis.reel_generator.scenes). One scene's failure doesn't
    stop the others, mirroring
    jarvis.reel_generator.scenes.render_all_scenes()'s own documented
    reasoning.

    `render_textless`, if True, applies to EVERY scene in this call -
    see render_story_scene_visual()'s own docstring for exactly what it
    does. Defaulting to False keeps every existing call site's output
    byte-for-byte unaffected."""
    output_dir.mkdir(parents=True, exist_ok=True)
    uploaded_photos_by_scene = uploaded_photos_by_scene or {}
    visuals: list[SceneVisual] = []
    for scene in scenes:
        output_path = output_dir / f"scene_{scene.number:02d}.jpg"
        visuals.append(
            render_story_scene_visual(
                brief, scene, plans.get(scene.number), output_path=output_path, style_transform=style_transform,
                visual_style=visual_style, uploaded_photo_paths=uploaded_photos_by_scene.get(scene.number),
                render_textless=render_textless,
            )
        )
    return visuals
