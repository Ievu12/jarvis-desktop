"""Multiple Variants (module brief, section 9): "After generation
create 3 design variations... VARIANT A Minimal, VARIANT B Elegant,
VARIANT C Bold." A variant is the SAME DesignBrief (same headline/
supporting text/CTA - the words never change between variants) rendered
with a DIFFERENT DesignStyle - never a re-generation of the text
content itself, matching the module's own example (three named STYLES
of one design, not three different designs).

Style selection for the 3 variants: if the brief's own chosen/forced
style is one of _VARIANT_ANCHOR_STYLES (module brief's literal example
set), that style is variant A and the other two are filled in from the
same anchor set; otherwise the brief's style is variant A and two
contrasting styles (one lighter/minimal-leaning, one bolder) are picked
to give a genuinely different-looking second and third option rather
than three near-identical results.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal

from jarvis.design_studio.brief import DesignBrief
from jarvis.design_studio.render import RenderError, RenderResult, render_design
from jarvis.design_studio.styles import DesignStyle, resolve_style

# The module brief's own literal example: "VARIANT A Minimal, VARIANT B
# Elegant, VARIANT C Bold." Used as the default 3-style set whenever
# the brief's own style doesn't obviously belong to a different, more
# specific trio (see _pick_variant_styles()).
_DEFAULT_VARIANT_STYLES = ("minimal", "elegant", "bold")

# A handful of alternate 3-style sets, chosen so a project whose own
# brief-selected style is thematically distinct (e.g. "yoga") still
# gets a coherent trio built AROUND that style rather than always
# falling back to the brief's own generic minimal/elegant/bold example
# - each set's first entry is always the brief's own style.
_THEMED_VARIANT_STYLE_SETS: dict[str, tuple[str, str, str]] = {
    "yoga": ("yoga", "wellness", "minimal"),
    "wellness": ("wellness", "yoga", "soft"),
    "beauty": ("beauty", "k_beauty", "soft"),
    "k_beauty": ("k_beauty", "beauty", "soft"),
    "feminine": ("feminine", "soft", "elegant"),
    "soft": ("soft", "feminine", "minimal"),
    "luxury": ("luxury", "elegant", "professional"),
    "elegant": ("elegant", "luxury", "minimal"),
    "professional": ("professional", "educational", "modern"),
    "educational": ("educational", "professional", "clean"),
    "lifestyle": ("lifestyle", "soft", "modern"),
    "bold": ("bold", "modern", "minimal"),
    "modern": ("modern", "bold", "clean"),
    "clean": ("clean", "minimal", "modern"),
    "minimal": ("minimal", "clean", "elegant"),
}


def _pick_variant_styles(brief_style: str) -> tuple[str, str, str]:
    return _THEMED_VARIANT_STYLE_SETS.get(brief_style, _DEFAULT_VARIANT_STYLES)


@dataclass(frozen=True)
class DesignVariant:
    label: Literal["A", "B", "C"]
    style: str
    render_result: RenderResult | None
    error: str | None
    """Set if THIS variant's render failed - other variants may still
    have succeeded; see generate_variants()'s own docstring for why a
    single variant's failure doesn't fail the whole batch."""


def generate_variants(
    brief: DesignBrief, *, output_dir: Path,
    uploaded_image_path: Path | None = None,
    image_role: Literal["logo", "product", "background"] | None = None,
    style_transform: Callable[[DesignStyle], DesignStyle] | None = None,
) -> list[DesignVariant]:
    """Renders `brief` three times, once per style in
    _pick_variant_styles(brief.style), into `output_dir` (module brief
    section 9: "After generation create 3 design variations... Display
    them side by side"). Each variant is rendered independently - one
    variant's RenderError does not stop the others from being attempted
    (a partial set of working variants is more useful than none), and
    is instead recorded on that DesignVariant's own `.error` field for
    the UI to show a per-variant failure state, matching this
    codebase's established "partial success is still useful, never let
    one failure hide the rest" convention (see e.g.
    jarvis.instagram_ai_manager.ai_services.generate_hooks()'s own
    per-category tolerance).

    `style_transform`, if given, is applied to EACH variant's resolved
    style before rendering (e.g.
    jarvis.design_studio.brand_kit.apply_brand_kit(), bound to the
    caller's own BrandKit instance) - this module never imports
    jarvis.design_studio.brand_kit itself (keeping this module's own
    dependency surface to styles/brief/render only), so a caller
    wanting Brand Kit colors applied to every variant passes
    `functools.partial(apply_brand_kit, brand_kit=...)` or an
    equivalent lambda here instead."""
    style_names = _pick_variant_styles(brief.style)
    labels: tuple[Literal["A", "B", "C"], ...] = ("A", "B", "C")
    output_dir.mkdir(parents=True, exist_ok=True)

    variants: list[DesignVariant] = []
    for label, style_name in zip(labels, style_names):
        style = resolve_style(style_name)
        if style_transform is not None:
            style = style_transform(style)
        output_path = output_dir / f"variant_{label.lower()}.jpg"
        try:
            result = render_design(
                brief, style, output_path=output_path,
                uploaded_image_path=uploaded_image_path, image_role=image_role,
            )
            variants.append(DesignVariant(label=label, style=style_name, render_result=result, error=None))
        except RenderError as e:
            variants.append(DesignVariant(label=label, style=style_name, render_result=None, error=str(e)))

    return variants
