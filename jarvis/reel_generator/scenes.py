"""Visual generation per scene (module brief, section 6): chooses a
visual source for each Storyboard Scene and produces an actual image
file for it.

Architecture inspection (this package's own __init__.py docstring)
confirmed there is no AI image or AI video generation anywhere in this
codebase - so exactly two visual sources are genuinely available for
Mode B (create-from-idea, this stage):

  1. A user-uploaded image for that specific scene (module brief's
     source #2) - copied in verbatim, never regenerated or altered,
     same "preserve the actual appearance" rule
     jarvis.design_studio.render.render_design() already documents and
     enforces for its own uploaded_image_path parameter.
  2. A PIL-rendered typographic "text card" - the scene's own
     on_screen_text laid out over a gradient background, reusing
     jarvis.design_studio.render.render_design() UNMODIFIED (module
     brief section 17: "Use AI Design Studio for... text cards"). A
     DesignBrief is constructed directly from the Scene's own data
     (never through jarvis.design_studio.brief.generate_design_brief(),
     which generates one brief per whole design REQUEST, not one per
     Reel scene) - DesignBrief is a plain frozen dataclass, trivially
     constructible without another LLM call.

Source #3 (existing JARVIS media library) and #4/#5 (AI-generated
image/video) either don't exist yet or don't exist at all in THIS
module - not offered here. Mode A (create-from-my-footage, a later
stage) is the path for using an existing uploaded VIDEO's own frames;
this module only ever produces STILL images, one per scene, later
turned into a timed video segment by jarvis.reel_generator.export
(ffmpeg can loop a still image into a timed clip).

Update (NATURAL MOTION / HYBRID modes): real AI image-to-video
generation now exists elsewhere in this codebase -
jarvis.reel_generator.video_generation (the real Runway API call) and
jarvis.reel_generator.motion_engine (settings/orchestration) - entirely
separate modules from this one, which still only ever produces STILL
images exactly as before. This module's own SceneVisual is deliberately
left untouched by that feature (see SceneMotionClip's own docstring
just below for the isolation reasoning); SceneMotionClip lives in this
file only because it is SceneVisual's direct sibling/correlate by
scene_number, not because this module does any video generation
itself."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from jarvis.design_studio.brief import DesignBrief
from jarvis.design_studio.render import RenderError, render_design
from jarvis.design_studio.styles import DesignStyle, resolve_style
from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.storyboard import Scene, strip_hashtags

# The module brief's own "reel_cover" format is 1080x1920 (9:16) - the
# exact frame a Reel's own scenes need too, so every scene text-card is
# rendered at that same format rather than introducing a new one.
_SCENE_FORMAT = "reel_cover"


@dataclass(frozen=True)
class SceneVisual:
    scene_number: int
    source: Literal["text_card", "uploaded_image"]
    image_path: Path | None
    error: str | None
    """Set if THIS scene's render/copy failed - other scenes may still
    have succeeded, matching jarvis.design_studio.variants
    .generate_variants()'s own "one failure doesn't abort the batch"
    convention (a partial storyboard is more useful than none)."""

    ai_generation_warning: str | None = None
    """Real, reported bug fix ("REGENERATE doesn't always generate a new
    image"): set (never on this original scenes.py's own plain path,
    which has no AI generation at all - only jarvis.reel_generator
    .scene_render.render_story_scene_visual() ever sets this) when real
    AI image generation was genuinely ATTEMPTED for this scene (its
    ScenePlan's own visual_source called for one, and OPENAI_API_KEY was
    configured) but the API call itself failed (network/timeout/
    malformed response) - the scene still gets a real, usable image
    (the same Pillow text-card fallback as before this field existed,
    `.error` stays None), but this field distinguishes "the AI image you
    expected didn't actually get made" from a genuine success, so 10/10
    quality-score display is never mistaken for confirmation that a real
    photo exists. None (the default) covers every other case: no AI
    generation was ever attempted at all (no plan, wrong visual_source,
    no API key configured), OR it succeeded."""

    has_baked_in_text: bool = True
    """True (the default) means this scene's own IMAGE FILE already has
    scene.on_screen_text drawn directly into its pixels (this module's
    own render_scene_visual()/jarvis.reel_generator.scene_render
    .render_story_scene_visual()'s ORIGINAL behavior, unconditionally,
    for every render before the textless mode existed) - a caller must
    NEVER also burn that same text in again as an export-time subtitle,
    or it duplicates (this codebase's own previously-fixed "text
    rendered twice" bug - see jarvis.reel_generator.export's own
    docstring). False means this scene's image is genuinely TEXTLESS
    (the new overlay-text mode, jarvis.reel_generator.scene_render's own
    render_textless=True path) - on_screen_text lives ONLY as scene
    data, never baked into pixels, and jarvis.reel_generator.export is
    the ONE place it gets drawn, as a real styled caption/subtitle.
    Defaulting to True (rather than requiring every existing call site
    to specify it) is what keeps every project rendered before this
    field existed, and every call site that doesn't know about this
    field yet, working exactly as before - see this dataclass's own
    module docstring for the full backward-compatibility reasoning."""


@dataclass(frozen=True)
class SceneMotionClip:
    """NATURAL MOTION / HYBRID mode's own per-scene real AI-generated
    video clip - jarvis.reel_generator.motion_engine's own return type,
    correlated to a SceneVisual only by `scene_number`, deliberately
    NEVER merged into SceneVisual itself (see this module's own
    architecture note above, updated for this feature: SceneVisual's
    `source` Literal stays exactly ("text_card", "uploaded_image") -
    every existing consumer of SceneVisual pattern-matches on exactly
    those two values, and widening it to include a video source would
    risk an unhandled branch somewhere in code that has no reason to
    know clips exist at all). A scene with motion requested/attempted
    always has BOTH its own SceneVisual (the still image, rendered
    first as in every existing mode - it's both the base frame Runway
    animates FROM, via jarvis.reel_generator.video_generation
    .generate_scene_video(), and the Static/Hybrid-static fallback) AND,
    only if generation was attempted, a SceneMotionClip - the two are
    never the same object, so any code that only knows about
    `list[SceneVisual]` (every existing Static-mode code path) stays
    completely oblivious to this dataclass's existence."""

    scene_number: int
    video_path: Path | None
    """The real, saved clip file on disk, or None if generation was
    never attempted/configured or failed - `error` (below) explains
    which."""

    source_image_path: Path | None
    """The still image this clip was generated FROM (that scene's own
    SceneVisual.image_path at generation time) - needed for the
    compare-original-vs-clip preview UI, and to re-derive a still
    fallback if the clip is later discarded. None only if the scene had
    no still image to animate from in the first place (see
    jarvis.reel_generator.motion_engine.generate_motion_for_scene()'s
    own docstring for when this happens)."""

    error: str | None
    """Set if THIS scene's clip generation was attempted but failed (not
    configured, provider error, network error, timed out) - a real,
    specific reason string, never a generic placeholder, matching
    jarvis.reel_generator.image_generation's own established convention.
    None means either generation succeeded (`video_path` is set) or was
    never attempted for this scene at all (Static mode, or a Hybrid
    scene left as static)."""

    provider_task_id: str | None
    """Runway's own task id for this generation attempt, kept for
    support/debugging purposes only - never shown as primary UI text,
    never required to be present (None for a pre-generation clip, a
    failure that occurred before a task id was ever issued, or any
    future provider that doesn't use task ids)."""

    duration_seconds: float
    """The REQUESTED clip duration (from jarvis.reel_generator
    .motion_engine.MotionSettings.clip_duration_seconds at generation
    time) - NOT necessarily the real, measured duration of the actual
    downloaded file at `video_path` (see jarvis.reel_generator.export's
    own too-short-clip guard, which re-probes the real file at splice
    time rather than trusting this requested value)."""


SceneMotionClips = list[SceneMotionClip]
"""Type alias for signature readability wherever a whole batch of
per-scene motion clips is passed around (jarvis.reel_generator
.motion_engine.generate_motion_for_scenes()'s own return type, the GUI's
own motion_clips_data serialization, etc.) - purely a naming
convenience, carries no behavior of its own."""


def _scene_design_brief(brief: ReelBrief, scene: Scene, *, render_textless: bool = False) -> DesignBrief:
    """Builds a DesignBrief directly from a Scene's own already-approved
    text - bypasses jarvis.design_studio.brief.generate_design_brief()
    entirely (no new LLM call per scene; the words are already decided
    by the approved script/storyboard, this only lays them out).

    `render_textless=True` (Stage D: textless scene visuals, see
    SceneVisual.has_baked_in_text's own docstring) leaves the headline
    BLANK - scene.on_screen_text stays exactly what it always was as
    scene DATA, it simply isn't drawn into this image's pixels;
    jarvis.reel_generator.export becomes the one place it's drawn, as a
    real export-time caption. Defaults to False - every existing call
    site keeps drawing the headline exactly as before.

    strip_hashtags() is applied to the headline right here (real,
    reported bug fix - "hashtags appear in the final Reel video") as
    the last defensive layer before this text becomes actual pixels -
    jarvis.reel_generator.storyboard's own scene-generation prompt/
    validation now also refuses a hashtag at the SOURCE (see that
    module's own strip_hashtags() docstring), but this catches every
    OTHER path too, including a person manually typing one into the
    EDIT SCENE dialog, without needing a separate fix at every text-
    input call site."""
    headline = "" if render_textless else strip_hashtags(scene.on_screen_text)
    return DesignBrief(
        topic=brief.topic, objective=brief.objective, audience=brief.audience, tone=brief.tone,
        headline=headline, supporting_text="", cta="",
        format=_SCENE_FORMAT, style=brief.style,
    )


def render_scene_visual(
    brief: ReelBrief, scene: Scene, *, output_path: Path,
    style_transform: Callable[[DesignStyle], DesignStyle] | None = None,
    uploaded_image_path: Path | None = None, render_textless: bool = False,
) -> SceneVisual:
    """Produces the visual for one Scene. If `uploaded_image_path` is
    given, copies it in verbatim (source="uploaded_image") rather than
    rendering a text card - module brief's "preserve the actual
    appearance" rule applies here exactly as it does in
    jarvis.design_studio.render.render_design(), which is what actually
    performs the copy-in (via its own uploaded_image_path/image_role
    params, role="background" so it fills the frame behind the scene's
    on_screen_text rather than being a small logo). Otherwise renders a
    plain text-card (source="text_card"). Never raises - a RenderError
    is captured on the returned SceneVisual's `.error` field instead,
    matching this package's established per-item partial-failure
    convention.

    `render_textless`, if True, renders NO on-screen text into this
    scene's image at all (see _scene_design_brief()'s own docstring) -
    the returned SceneVisual's has_baked_in_text is set to False to
    match (True, the default, otherwise) - see that field's own
    docstring for why jarvis.reel_generator.export relies on it. An
    uploaded image is unaffected either way (it never had baked-in text
    to begin with - "uploaded_image" sources are the person's own real
    photo, composited as a background with NO text drawn over it by
    this module at all, textless or not) other than also correctly
    reporting has_baked_in_text=False so export still knows to add a
    caption for it."""
    design_brief = _scene_design_brief(brief, scene, render_textless=render_textless)
    style = resolve_style(design_brief.style)
    if style_transform is not None:
        style = style_transform(style)

    try:
        if uploaded_image_path is not None:
            result = render_design(
                design_brief, style, output_path=output_path,
                uploaded_image_path=uploaded_image_path, image_role="background",
            )
            source: Literal["text_card", "uploaded_image"] = "uploaded_image"
        else:
            result = render_design(design_brief, style, output_path=output_path)
            source = "text_card"
    except RenderError as e:
        return SceneVisual(scene_number=scene.number, source="text_card", image_path=None, error=str(e))

    return SceneVisual(
        scene_number=scene.number, source=source, image_path=result.output_path, error=None,
        has_baked_in_text=not render_textless,
    )


def render_all_scenes(
    brief: ReelBrief, scenes: tuple[Scene, ...], *, output_dir: Path,
    style_transform: Callable[[DesignStyle], DesignStyle] | None = None,
    uploaded_images_by_scene: dict[int, Path] | None = None, render_textless: bool = False,
) -> list[SceneVisual]:
    """Renders every scene's visual into `output_dir`
    (jarvis.reel_generator.storage.ReelProject.scenes_dir), one file per
    scene named by scene number. `uploaded_images_by_scene` maps a
    scene's `.number` to a user-uploaded image path for that specific
    scene, if the person supplied one - scenes not in this dict get a
    plain text-card render. One scene's failure doesn't stop the
    others, mirroring jarvis.design_studio.variants.generate_variants()'s
    own documented reasoning.

    `render_textless`, if True, applies to every scene - see
    render_scene_visual()'s own docstring. Defaults to False, keeping
    every existing call site's output byte-for-byte unaffected."""
    output_dir.mkdir(parents=True, exist_ok=True)
    uploaded_images_by_scene = uploaded_images_by_scene or {}

    visuals: list[SceneVisual] = []
    for scene in scenes:
        output_path = output_dir / f"scene_{scene.number:02d}.jpg"
        visuals.append(
            render_scene_visual(
                brief, scene, output_path=output_path, style_transform=style_transform,
                uploaded_image_path=uploaded_images_by_scene.get(scene.number), render_textless=render_textless,
            )
        )
    return visuals
