"""Personal Brand Kit (module brief, section 6): "MY BRAND" - a single,
global (not per-project) set of preferences the person configures once
and JARVIS applies automatically to every new design: brand name, logo,
primary/secondary/accent color, preferred fonts, preferred visual
style, Instagram username. "When creating a new design: Use my brand
automatically... JARVIS automatically applies my saved brand style."

A single row (not a table keyed by id) - there is exactly one Brand
Kit per JARVIS installation, unlike jarvis.design_studio.db's
`projects` table, which has many rows. Uses the same SQLite file
(jarvis.config.DESIGN_STUDIO_DB_FILE) as jarvis.design_studio.db via a
separate `brand_kit` table, rather than a second database file - one
person's design data belongs together.

apply_brand_kit() is the one function jarvis.design_studio.dashboard
(a later addition to that module) calls to fold a configured Brand
Kit's colors/fonts into whatever DesignStyle a design would otherwise
use - it never fabricates a brand preference the person hasn't set
(module brief: "Do not make irreversible changes based on AI
assumptions"), and every field defaults to None/empty (never a made-up
placeholder brand name or color) until the person explicitly saves one.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Iterator

from jarvis.config import DESIGN_STUDIO_DB_FILE
from jarvis.design_studio.styles import DesignStyle

_SCHEMA = """
CREATE TABLE IF NOT EXISTS brand_kit (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    brand_name TEXT,
    logo_path TEXT,
    primary_color TEXT,
    secondary_color TEXT,
    accent_color TEXT,
    preferred_fonts TEXT,
    preferred_style TEXT,
    instagram_username TEXT,
    updated_at TEXT
);
"""
# `id INTEGER PRIMARY KEY CHECK (id = 1)` enforces exactly one row -
# save_brand_kit() always upserts id=1, so there is never a second
# Brand Kit row to accidentally read the wrong one from.


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    DESIGN_STUDIO_DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DESIGN_STUDIO_DB_FILE))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


@dataclass(frozen=True)
class BrandKit:
    brand_name: str | None
    logo_path: str | None
    primary_color: str | None  # hex, e.g. "#FF3B30"
    secondary_color: str | None
    accent_color: str | None
    preferred_fonts: list[str]
    """A list rather than a single font path, matching the module
    brief's plural "Preferred fonts" - in practice this codebase only
    has two confirmed-working fonts (arial.ttf/arialbd.ttf, same
    constraint jarvis.design_studio.styles.DESIGN_STYLES documents),
    so this field is stored for forward compatibility (a later stage
    adding more fonts) but apply_brand_kit() below does not currently
    use it to override a style's font choice."""
    preferred_style: str | None  # one of jarvis.design_studio.styles.DESIGN_STYLES' keys, or None
    instagram_username: str | None

    @property
    def is_configured(self) -> bool:
        """True if the person has set ANYTHING - used to decide
        whether apply_brand_kit() has anything to do at all, and by
        the UI to show "no brand kit yet" vs. a filled-in form."""
        return any(
            (
                self.brand_name, self.logo_path, self.primary_color, self.secondary_color,
                self.accent_color, self.preferred_fonts, self.preferred_style, self.instagram_username,
            )
        )


_EMPTY_BRAND_KIT = BrandKit(
    brand_name=None, logo_path=None, primary_color=None, secondary_color=None, accent_color=None,
    preferred_fonts=[], preferred_style=None, instagram_username=None,
)


def get_brand_kit() -> BrandKit:
    """Returns the configured Brand Kit, or an all-None/empty one if
    the person has never saved one - never raises, never fabricates a
    default brand name/color (module brief section 15: "Do not make
    irreversible changes based on AI assumptions")."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT brand_name, logo_path, primary_color, secondary_color, accent_color, "
            "preferred_fonts, preferred_style, instagram_username FROM brand_kit WHERE id = 1"
        ).fetchone()
    if row is None:
        return _EMPTY_BRAND_KIT
    return BrandKit(
        brand_name=row[0], logo_path=row[1], primary_color=row[2], secondary_color=row[3],
        accent_color=row[4], preferred_fonts=json.loads(row[5]) if row[5] else [],
        preferred_style=row[6], instagram_username=row[7],
    )


def save_brand_kit(brand_kit: BrandKit) -> None:
    """Upserts the single Brand Kit row (module brief section 15's
    "Allow the user to change or reset preferences" - this is a full
    replace, not a merge, so a caller wanting to clear one field passes
    the rest through unchanged via dataclasses.replace() on the result
    of get_brand_kit(), and clears a field by passing None/[] for it -
    never a silent partial update the caller didn't ask for)."""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO brand_kit (id, brand_name, logo_path, primary_color, secondary_color, "
            "accent_color, preferred_fonts, preferred_style, instagram_username, updated_at) "
            "VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(id) DO UPDATE SET "
            "brand_name = excluded.brand_name, logo_path = excluded.logo_path, "
            "primary_color = excluded.primary_color, secondary_color = excluded.secondary_color, "
            "accent_color = excluded.accent_color, preferred_fonts = excluded.preferred_fonts, "
            "preferred_style = excluded.preferred_style, instagram_username = excluded.instagram_username, "
            "updated_at = excluded.updated_at",
            (
                brand_kit.brand_name, brand_kit.logo_path, brand_kit.primary_color,
                brand_kit.secondary_color, brand_kit.accent_color,
                json.dumps(brand_kit.preferred_fonts, ensure_ascii=False) if brand_kit.preferred_fonts else None,
                brand_kit.preferred_style, brand_kit.instagram_username,
            ),
        )


def reset_brand_kit() -> None:
    """Clears every Brand Kit field back to unset (module brief
    section 15's explicit "Allow the user to change or reset
    preferences" requirement) - does NOT delete any uploaded logo/
    image file from disk (jarvis.design_studio.storage's own brand
    asset directory is untouched; a person re-uploading later reuses
    the same upload flow), only the DATABASE row's field values."""
    save_brand_kit(_EMPTY_BRAND_KIT)


def apply_brand_kit(style: DesignStyle, brand_kit: BrandKit) -> DesignStyle:
    """Returns a new DesignStyle with `brand_kit`'s configured colors
    substituted in wherever they're set (module brief: "When creating a
    new design: Use my brand automatically") - a color the person
    hasn't configured leaves the base style's own value untouched
    (never a fabricated substitute). primary_color overrides the
    style's background gradient (both ends, for a solid-brand-color
    feel) and CTA background; secondary_color overrides the headline
    color; accent_color overrides the style's own accent_color AND the
    CTA pill's background (never the page background - a CTA pill
    sharing the page's exact background color would be invisible; a
    real bug found by hand-testing this function's own output, fixed
    by using accent_color instead of primary_color for the CTA pill's
    fill, falling back to secondary_color, then the base style's own
    unmodified cta_background, in that order, so the pill always
    contrasts against the primary-colored page). If
    brand_kit.is_configured is False, returns `style` completely
    unchanged (no-op) - this function is always safe to call even when
    no Brand Kit has been saved."""
    if not brand_kit.is_configured:
        return style

    updates: dict[str, str] = {}
    if brand_kit.primary_color:
        updates["background_color_1"] = brand_kit.primary_color
        updates["background_color_2"] = brand_kit.primary_color
    if brand_kit.secondary_color:
        updates["headline_color"] = brand_kit.secondary_color
    if brand_kit.accent_color:
        updates["accent_color"] = brand_kit.accent_color

    cta_background = brand_kit.accent_color or brand_kit.secondary_color
    if cta_background:
        updates["cta_background"] = cta_background
        # A brand color is arbitrary (chosen by the person, not this
        # module), so the base style's own cta_color (often a fixed
        # light/dark value tuned for that style's own, different pill
        # color) can no longer be assumed readable against it - pick
        # black or white by simple relative-luminance contrast instead
        # of leaving a potentially unreadable color in place (a real
        # legibility bug found by hand-testing this function's own
        # output with a bright accent color).
        updates["cta_color"] = _readable_text_color(cta_background)

    if not updates:
        return style
    return replace(style, **updates)


def _readable_text_color(background_hex: str) -> str:
    """Returns "#000000" or "#FFFFFF", whichever contrasts better
    against `background_hex`, via the standard relative-luminance
    formula (ITU-R BT.601 perceptual weighting) - not a claim of full
    WCAG contrast-ratio compliance, just a simple, dependency-free
    "is this background light or dark" heuristic sufficient for a
    CTA pill's text."""
    hex_color = background_hex.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    return "#000000" if luminance > 0.6 else "#FFFFFF"
