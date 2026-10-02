"""Content Package (module brief, section 15): "After Reel creation
automatically prepare: Reel, Cover, Caption, CTA, Hashtags, Story
promotion. Show: CONTENT PACKAGE READY."

Reuses jarvis.design_studio.render.render_design() directly, unmodified,
for every visual in the package (cover/story/post/carousel) - per
module brief section 17 ("Use AI Design Studio for: Reel cover, Story
promotion, promotional graphics, text cards"), same reuse this
package's own jarvis.reel_generator.cover/.scenes already apply. Every
DesignBrief in a package is built directly from the SAME headline/
supporting_text/cta and the SAME style - "consistent visual identity"
(module brief's own phrase) means literally reusing one brief's worth
of text and one style across every format, not independently generating
each piece and hoping they end up looking related.

Caption/CTA/hashtags are NOT regenerated here - a Content Package reuses
whatever jarvis.reel_generator.caption.ReelCaptionPackage (Mode B) or
jarvis.reel_generator.instagram_handoff-grounded content (Mode A) the
Reel already has, exactly like jarvis.reel_generator.instagram_handoff
forwards Stage 2's own caption package rather than re-generating it -
this module's only new work is the ADDITIONAL visual formats (story
promotion, post, carousel, Story CTA card), not new text content."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from jarvis.design_studio.brief import DesignBrief
from jarvis.design_studio.render import RenderError, RenderResult, render_design
from jarvis.design_studio.styles import DesignStyle, resolve_style

# The module brief's own listed package pieces (section 15), each
# mapped to one of jarvis.design_studio.render.FORMAT_DIMENSIONS'
# already-defined formats - no new format is introduced here.
PACKAGE_PIECES = ("cover", "story_promotion", "post", "carousel", "story_cta")

_PIECE_TO_FORMAT = {
    "cover": "reel_cover", "story_promotion": "story", "post": "post",
    "carousel": "carousel", "story_cta": "story",
}

_PIECE_LABELS = {
    "cover": "Reel Cover", "story_promotion": "Story Promotion", "post": "Post",
    "carousel": "Carousel", "story_cta": "Story CTA",
}


@dataclass(frozen=True)
class PackagePiece:
    piece: str  # one of PACKAGE_PIECES
    render_result: RenderResult | None
    error: str | None
    """Set if THIS piece's render failed - other pieces may still have
    succeeded, matching jarvis.design_studio.variants
    .generate_variants()'s own "one failure doesn't abort the batch"
    convention (a partial package is more useful than none)."""

    @property
    def label(self) -> str:
        return _PIECE_LABELS.get(self.piece, self.piece)


@dataclass(frozen=True)
class ContentPackage:
    pieces: tuple[PackagePiece, ...]

    @property
    def all_succeeded(self) -> bool:
        return all(p.error is None for p in self.pieces)

    @property
    def any_succeeded(self) -> bool:
        return any(p.error is None for p in self.pieces)


def _piece_brief(*, headline: str, supporting_text: str, cta: str, style: str, piece: str) -> DesignBrief:
    """Builds one piece's DesignBrief directly from already-decided
    text (never a new LLM call per piece - see this module's own
    docstring for why: the Reel's own headline/CTA already exist by
    the time a Content Package is requested)."""
    fmt = _PIECE_TO_FORMAT[piece]
    piece_supporting_text = supporting_text
    piece_cta = cta
    if piece == "story_cta":
        # A dedicated "Story CTA" card is CTA-forward by design (module
        # brief lists it separately from the general "Story promotion"
        # piece) - drop the supporting text so the CTA is the visual's
        # own clear focus, matching how jarvis.reel_generator.cover
        # already keeps its own cta field empty for a title-forward
        # card (the same render_design() empty-CTA-skips-the-pill fix
        # applies symmetrically here: an empty supporting_text simply
        # isn't drawn, never leaves a stray empty element).
        piece_supporting_text = ""
    return DesignBrief(
        topic=headline, objective="promote", audience="", tone="",
        headline=headline, supporting_text=piece_supporting_text, cta=piece_cta,
        format=fmt, style=style,
    )


def generate_content_package(
    *, headline: str, supporting_text: str, cta: str, style: str, output_dir: Path,
    style_transform: Callable[[DesignStyle], DesignStyle] | None = None,
    pieces: tuple[str, ...] = PACKAGE_PIECES,
) -> ContentPackage:
    """Renders every piece in `pieces` (default: all of PACKAGE_PIECES)
    from the SAME headline/supporting_text/cta/style, into `output_dir`
    - one file per piece, named by piece. `style_transform`, if given
    (e.g. jarvis.design_studio.brand_kit.apply_brand_kit()), is applied
    to every piece's style, same hook jarvis.reel_generator.scenes/
    .cover already expose for Brand Kit consistency. One piece's
    RenderError does not stop the others - recorded on that piece's own
    `.error` field instead, matching this package's established
    per-item partial-failure convention. Never raises."""
    output_dir.mkdir(parents=True, exist_ok=True)

    result_pieces: list[PackagePiece] = []
    for piece in pieces:
        brief = _piece_brief(headline=headline, supporting_text=supporting_text, cta=cta, style=style, piece=piece)
        resolved_style = resolve_style(style)
        if style_transform is not None:
            resolved_style = style_transform(resolved_style)
        output_path = output_dir / f"{piece}.jpg"
        try:
            result = render_design(brief, resolved_style, output_path=output_path)
            result_pieces.append(PackagePiece(piece=piece, render_result=result, error=None))
        except RenderError as e:
            result_pieces.append(PackagePiece(piece=piece, render_result=None, error=str(e)))

    return ContentPackage(pieces=tuple(result_pieces))
