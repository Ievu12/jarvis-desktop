"""Animated stickers/GIF/emoji overlays - distinct from
jarvis.video_editor.text_overlay (free TEXT) and .captions (transcribed
speech TEXT): a sticker is an IMAGE (built-in shape, an uploaded PNG
with transparency, or an uploaded animated GIF) composited via ffmpeg's
own `overlay` filter, with its own time-varying position/rotation/
opacity/scale animation.

Built-in collection (requirement: "didelė animuotų lipdukų... kolekcija"
- a large collection): a genuinely SMALL, honestly-scoped starter set
(heart/star/arrow/sparkle/flower/cloud/glow - 7 shapes) each rendered as
a real, transparent PNG via Pillow's own ImageDraw (the same library
jarvis.design_studio.render/.reel_generator.scene_render already use
for drawn graphics) - never a fake/placeholder image, every shape is
genuinely drawable and visible. This is deliberately NOT "a large
collection" in the literal sense the request asked for (that would mean
either a real third-party asset pack with its own licensing to clear,
or weeks of hand-drawn art - neither attempted here) - combined with
real PNG/GIF upload support (the actual path to "a large collection" in
practice: the person's own asset library), this gives genuine,
immediately-usable functionality without overpromising scope that was
never built.

Unlike jarvis.video_editor.text_overlay/.captions (drawtext-only, no
extra ffmpeg input needed), a sticker's own image file becomes a SECOND
ffmpeg input composited via `overlay` - structurally closer to
jarvis.video_editor.audio_mixing's own (extra_input_args, filter_clause,
output_label) tuple contract than to build_text_overlay_filter()'s
single-string-with-assumed-video_label contract, since overlay must
read from BOTH the base video label AND this sticker's own new input
index."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

StickerShape = Literal[
    # Širdelės ir meilė (hearts & love)
    "heart", "heart_small", "double_heart", "heart_outline",
    # Gėlės ir gamta (flowers & nature)
    "flower", "leaf", "butterfly", "sun",
    # Ruduo ir žiema (autumn & winter)
    "autumn_leaf", "snowflake", "pumpkin", "mitten",
    # Joga ir meditacija (yoga & meditation)
    "lotus", "om_circle", "zen_stone",
    # Kosmetika ir grožis (cosmetics & beauty)
    "lipstick", "mirror", "diamond",
    # Kava ir gyvenimo būdas (coffee & lifestyle)
    "coffee_cup", "croissant", "book",
    # Blizgučiai ir šviesos efektai (sparkles & light)
    "sparkle", "glow", "star_small", "ray_burst",
    # Rodyklės, rėmeliai ir dekoracijos (arrows, frames & decor)
    "arrow", "arrow_curved", "frame_circle", "frame_square",
    # Šventės ir gimtadieniai (celebrations & birthdays)
    "balloon", "gift", "party_hat", "confetti",
    # Original general-purpose shapes
    "star", "cloud",
]
STICKER_SHAPE_CHOICES: tuple[StickerShape, ...] = (
    "heart", "heart_small", "double_heart", "heart_outline",
    "flower", "leaf", "butterfly", "sun",
    "autumn_leaf", "snowflake", "pumpkin", "mitten",
    "lotus", "om_circle", "zen_stone",
    "lipstick", "mirror", "diamond",
    "coffee_cup", "croissant", "book",
    "sparkle", "glow", "star_small", "ray_burst",
    "arrow", "arrow_curved", "frame_circle", "frame_square",
    "balloon", "gift", "party_hat", "confetti",
    "star", "cloud",
)

StickerCategory = Literal[
    "hearts_love", "flowers_nature", "autumn_winter", "yoga_meditation",
    "cosmetics_beauty", "coffee_lifestyle", "sparkles_light", "arrows_frames", "celebrations",
]
STICKER_CATEGORY_CHOICES: tuple[StickerCategory, ...] = (
    "hearts_love", "flowers_nature", "autumn_winter", "yoga_meditation",
    "cosmetics_beauty", "coffee_lifestyle", "sparkles_light", "arrows_frames", "celebrations",
)
STICKER_CATEGORY_LABELS: dict[StickerCategory, str] = {
    "hearts_love": "💗 Širdelės ir meilė", "flowers_nature": "🌸 Gėlės ir gamta",
    "autumn_winter": "🍂 Ruduo ir žiema", "yoga_meditation": "🧘 Joga ir meditacija",
    "cosmetics_beauty": "💄 Kosmetika ir grožis", "coffee_lifestyle": "☕ Kava ir gyvenimo būdas",
    "sparkles_light": "✨ Blizgučiai ir šviesos efektai", "arrows_frames": "➡️ Rodyklės, rėmeliai ir dekoracijos",
    "celebrations": "🎉 Šventės ir gimtadieniai",
}
STICKER_CATEGORY_SHAPES: dict[StickerCategory, tuple[StickerShape, ...]] = {
    "hearts_love": ("heart", "heart_small", "double_heart", "heart_outline"),
    "flowers_nature": ("flower", "leaf", "butterfly", "sun"),
    "autumn_winter": ("autumn_leaf", "snowflake", "pumpkin", "mitten"),
    "yoga_meditation": ("lotus", "om_circle", "zen_stone"),
    "cosmetics_beauty": ("lipstick", "mirror", "diamond"),
    "coffee_lifestyle": ("coffee_cup", "croissant", "book"),
    "sparkles_light": ("sparkle", "glow", "star_small", "ray_burst"),
    "arrows_frames": ("arrow", "arrow_curved", "frame_circle", "frame_square"),
    "celebrations": ("balloon", "gift", "party_hat", "confetti"),
}
# Requirement 1 ("Sukurk patogią integruotą lipdukų biblioteką su
# kategorijomis") - 9 real categories, each with 3-4 real, hand-drawn
# (Pillow ImageDraw) shapes - "star"/"cloud" (this module's own
# original Stage 1 shapes) stay available but outside the new category
# grouping (they predate it and don't fit a single new category
# cleanly), never removed since existing projects may already reference
# them by name.

StickerAnimation = Literal[
    "none", "pop_in", "fade_in_out", "float", "spin", "blink", "bounce",
]
STICKER_ANIMATION_CHOICES: tuple[StickerAnimation, ...] = (
    "none", "pop_in", "fade_in_out", "float", "spin", "blink", "bounce",
)

_DEFAULT_STICKER_SIZE_PX = 200
# The built-in shapes are rendered at this fixed source resolution, then
# scaled to whatever on-screen size_fraction the person picks at
# composite time - keeps _render_builtin_sticker()'s own drawing code
# resolution-independent of the final video canvas.

_PASTEL_PALETTE: dict[StickerShape, tuple[int, int, int]] = {
    "heart": (255, 133, 161), "star": (255, 214, 102), "arrow": (124, 157, 255),
    "sparkle": (255, 240, 158), "flower": (200, 150, 255), "cloud": (200, 220, 255),
    "glow": (255, 200, 120),
    "heart_small": (255, 150, 175), "double_heart": (255, 120, 150), "heart_outline": (255, 105, 135),
    "leaf": (140, 200, 120), "butterfly": (180, 160, 255), "sun": (255, 210, 90),
    "autumn_leaf": (220, 130, 60), "snowflake": (200, 230, 255), "pumpkin": (255, 150, 60), "mitten": (230, 90, 90),
    "lotus": (255, 190, 210), "om_circle": (180, 140, 220), "zen_stone": (170, 170, 160),
    "lipstick": (220, 60, 100), "mirror": (210, 210, 230), "diamond": (170, 220, 255),
    "coffee_cup": (150, 100, 70), "croissant": (230, 180, 110), "book": (150, 130, 200),
    "star_small": (255, 220, 120), "ray_burst": (255, 230, 150),
    "arrow_curved": (124, 157, 255), "frame_circle": (220, 210, 200), "frame_square": (220, 210, 200),
    "balloon": (255, 120, 150), "gift": (200, 90, 130), "party_hat": (130, 200, 230), "confetti": (255, 190, 100),
}
# Soft, muted tones (requirement: "estetiniai, minimalistiniai, švelnių
# spalvų... elementai, tinkantys grožio, jogos, gyvenimo būdo" - gentle-
# colored, minimalist elements for beauty/yoga/lifestyle content) -
# every shape's own default color, overridable per-instance via
# StickerInstance.tint below.


class StickerError(Exception):
    """Raised for an invalid StickerInstance or an unreadable custom
    sticker file - a LOCAL exception, never subclassing a sibling
    module's own error type, matching this package's established
    per-module isolation convention."""


@dataclass(frozen=True)
class StickerInstance:
    """One placed sticker - either a built-in `shape` or a custom
    `custom_path` (mutually exclusive: exactly one must be set,
    enforced by validate() below, never silently preferring one over
    the other). Position/size are fractions of the canvas (0.0-1.0,
    same convention jarvis.video_editor.text_overlay.TextOverlay's own
    x_fraction/y_fraction already uses) so a sticker's own placement is
    resolution-independent."""

    start_seconds: float
    end_seconds: float
    shape: StickerShape | None = None
    custom_path: Path | None = None
    x_fraction: float = 0.5
    y_fraction: float = 0.5
    size_fraction: float = 0.15
    rotation_degrees: float = 0.0
    opacity: float = 1.0
    animation: StickerAnimation = "pop_in"
    tint: tuple[int, int, int] | None = None

    def validate(self) -> list[str]:
        """Never raises - matches every other dataclass's own
        established "describe problems, don't throw" convention."""
        problems: list[str] = []
        if self.shape is None and self.custom_path is None:
            problems.append("Sticker needs either a built-in shape or a custom image file.")
        if self.shape is not None and self.custom_path is not None:
            problems.append("Sticker cannot use both a built-in shape and a custom image file at once.")
        if self.custom_path is not None and not self.custom_path.is_file():
            problems.append(f"Custom sticker file not found: {self.custom_path}")
        if self.end_seconds <= self.start_seconds:
            problems.append("Sticker end time must be after its start time.")
        if not (0.0 <= self.x_fraction <= 1.0):
            problems.append(f"Sticker x position {self.x_fraction} must be between 0.0 and 1.0.")
        if not (0.0 <= self.y_fraction <= 1.0):
            problems.append(f"Sticker y position {self.y_fraction} must be between 0.0 and 1.0.")
        if not (0.01 <= self.size_fraction <= 1.0):
            problems.append(f"Sticker size {self.size_fraction} must be between 0.01 and 1.0.")
        if not (0.0 <= self.opacity <= 1.0):
            problems.append(f"Sticker opacity {self.opacity} must be between 0.0 and 1.0.")
        if self.animation not in STICKER_ANIMATION_CHOICES:
            problems.append(f"Unknown sticker animation: {self.animation!r}.")
        return problems


def render_builtin_sticker(shape: StickerShape, *, output_path: Path, tint: tuple[int, int, int] | None = None) -> None:
    """Writes builtin_sticker_image(shape, tint=tint) to `output_path`
    as a PNG - the file ffmpeg's `overlay` input reads at export."""
    image = builtin_sticker_image(shape, tint=tint)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    image.save(output_path, "PNG")


def builtin_sticker_image(shape: StickerShape, *, tint: tuple[int, int, int] | None = None):
    """Draws `shape` as a real, transparent RGBA image via Pillow's own
    ImageDraw - the exact same drawing library jarvis.design_studio
    .render/.reel_generator.scene_render already use elsewhere in this
    codebase for drawn (non-photographic) graphics, applied here to a
    small built-in shape set rather than invented as a new drawing
    mechanism. Raises StickerError for an unknown shape."""
    from PIL import Image, ImageDraw

    if shape not in STICKER_SHAPE_CHOICES:
        raise StickerError(f"Unknown built-in sticker shape: {shape!r}")

    size = _DEFAULT_STICKER_SIZE_PX
    color = tint or _PASTEL_PALETTE[shape]
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    rgba = (*color, 255)

    if shape == "heart":
        # Two overlapping circles (the lobes) + a triangle (the point) -
        # the standard, simple vector-heart construction.
        r = size // 4
        draw.ellipse([size // 2 - r * 2, size // 4 - r, size // 2, size // 4 + r], fill=rgba)
        draw.ellipse([size // 2, size // 4 - r, size // 2 + r * 2, size // 4 + r], fill=rgba)
        draw.polygon([(size // 2 - r * 2, size // 4), (size // 2 + r * 2, size // 4), (size // 2, size - size // 8)], fill=rgba)
    elif shape == "star":
        draw.polygon(_star_points(size // 2, size // 2, size * 0.45, size * 0.2, 5), fill=rgba)
    elif shape == "arrow":
        draw.polygon([
            (size * 0.1, size * 0.45), (size * 0.6, size * 0.45), (size * 0.6, size * 0.25),
            (size * 0.9, size * 0.5), (size * 0.6, size * 0.75), (size * 0.6, size * 0.55), (size * 0.1, size * 0.55),
        ], fill=rgba)
    elif shape == "sparkle":
        draw.polygon(_star_points(size // 2, size // 2, size * 0.45, size * 0.1, 4), fill=rgba)
    elif shape == "flower":
        petal_r = size * 0.18
        center_r = size * 0.5
        import math

        for i in range(6):
            angle = math.pi * 2 * i / 6
            cx = size / 2 + math.cos(angle) * center_r * 0.3
            cy = size / 2 + math.sin(angle) * center_r * 0.3
            draw.ellipse([cx - petal_r, cy - petal_r, cx + petal_r, cy + petal_r], fill=rgba)
        draw.ellipse([size / 2 - size * 0.12, size / 2 - size * 0.12, size / 2 + size * 0.12, size / 2 + size * 0.12], fill=(255, 235, 150, 255))
    elif shape == "cloud":
        for cx, cy, r in ((0.35, 0.55, 0.18), (0.5, 0.42, 0.22), (0.65, 0.55, 0.18), (0.5, 0.6, 0.25)):
            draw.ellipse([(cx - r) * size, (cy - r) * size, (cx + r) * size, (cy + r) * size], fill=rgba)
    elif shape == "glow":
        for i, alpha in enumerate((60, 100, 160, 255)):
            r = size * 0.5 * (1 - i * 0.2)
            draw.ellipse([size / 2 - r, size / 2 - r, size / 2 + r, size / 2 + r], fill=(*color, alpha))
    elif shape == "heart_small":
        r = size // 6
        cx, cy = size // 2, size // 2
        draw.ellipse([cx - r * 2, cy - r, cx, cy + r], fill=rgba)
        draw.ellipse([cx, cy - r, cx + r * 2, cy + r], fill=rgba)
        draw.polygon([(cx - r * 2, cy), (cx + r * 2, cy), (cx, cy + r * 3)], fill=rgba)
    elif shape == "double_heart":
        r = size // 6
        for offset, alpha in ((-size * 0.12, 180), (size * 0.08, 255)):
            cx, cy = size / 2 + offset, size / 2
            draw.ellipse([cx - r * 2, cy - r, cx, cy + r], fill=(*color, alpha))
            draw.ellipse([cx, cy - r, cx + r * 2, cy + r], fill=(*color, alpha))
            draw.polygon([(cx - r * 2, cy), (cx + r * 2, cy), (cx, cy + r * 2.6)], fill=(*color, alpha))
    elif shape == "heart_outline":
        r = size // 4
        width = max(4, size // 20)
        mask = Image.new("L", (size, size), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse([size // 2 - r * 2, size // 4 - r, size // 2, size // 4 + r], fill=255)
        mask_draw.ellipse([size // 2, size // 4 - r, size // 2 + r * 2, size // 4 + r], fill=255)
        mask_draw.polygon([(size // 2 - r * 2, size // 4), (size // 2 + r * 2, size // 4), (size // 2, size - size // 8)], fill=255)
        inner = Image.new("L", (size, size), 0)
        inner_draw = ImageDraw.Draw(inner)
        shrink = width
        inner_draw.ellipse([size // 2 - r * 2 + shrink, size // 4 - r + shrink, size // 2 - shrink, size // 4 + r], fill=255)
        inner_draw.ellipse([size // 2 + shrink, size // 4 - r + shrink, size // 2 + r * 2 - shrink, size // 4 + r], fill=255)
        inner_draw.polygon([(size // 2 - r * 2 + shrink, size // 4), (size // 2 + r * 2 - shrink, size // 4), (size // 2, size - size // 8 - shrink)], fill=255)
        from PIL import ImageChops

        ring = ImageChops.subtract(mask, inner)
        colored = Image.new("RGBA", (size, size), rgba)
        image.paste(colored, (0, 0), ring)
    elif shape == "leaf":
        draw.ellipse([size * 0.25, size * 0.15, size * 0.75, size * 0.85], fill=rgba)
        draw.line([(size * 0.5, size * 0.15), (size * 0.5, size * 0.85)], fill=(255, 255, 255, 180), width=max(2, size // 50))
    elif shape == "butterfly":
        for sign in (-1, 1):
            cx = size / 2 + sign * size * 0.2
            draw.ellipse([cx - size * 0.18, size * 0.2, cx + size * 0.18, size * 0.5], fill=rgba)
            draw.ellipse([cx - size * 0.13, size * 0.5, cx + size * 0.13, size * 0.75], fill=(*color, 200))
        draw.line([(size * 0.5, size * 0.15), (size * 0.5, size * 0.8)], fill=(80, 60, 60, 255), width=max(3, size // 35))
    elif shape == "sun":
        import math

        r_core = size * 0.22
        draw.ellipse([size / 2 - r_core, size / 2 - r_core, size / 2 + r_core, size / 2 + r_core], fill=rgba)
        for i in range(8):
            angle = math.pi * 2 * i / 8
            x1 = size / 2 + math.cos(angle) * r_core * 1.3
            y1 = size / 2 + math.sin(angle) * r_core * 1.3
            x2 = size / 2 + math.cos(angle) * r_core * 2.0
            y2 = size / 2 + math.sin(angle) * r_core * 2.0
            draw.line([(x1, y1), (x2, y2)], fill=rgba, width=max(3, size // 25))
    elif shape == "autumn_leaf":
        draw.polygon([
            (size * 0.5, size * 0.1), (size * 0.75, size * 0.35), (size * 0.65, size * 0.4),
            (size * 0.85, size * 0.6), (size * 0.55, size * 0.55), (size * 0.5, size * 0.9),
            (size * 0.45, size * 0.55), (size * 0.15, size * 0.6), (size * 0.35, size * 0.4),
            (size * 0.25, size * 0.35),
        ], fill=rgba)
    elif shape == "snowflake":
        import math

        cx, cy = size / 2, size / 2
        arm_len = size * 0.38
        for i in range(6):
            angle = math.pi * 2 * i / 6
            x2 = cx + math.cos(angle) * arm_len
            y2 = cy + math.sin(angle) * arm_len
            draw.line([(cx, cy), (x2, y2)], fill=rgba, width=max(3, size // 30))
            branch_x = cx + math.cos(angle) * arm_len * 0.6
            branch_y = cy + math.sin(angle) * arm_len * 0.6
            for branch_angle_offset in (-0.5, 0.5):
                bx = branch_x + math.cos(angle + branch_angle_offset) * size * 0.1
                by = branch_y + math.sin(angle + branch_angle_offset) * size * 0.1
                draw.line([(branch_x, branch_y), (bx, by)], fill=rgba, width=max(2, size // 50))
    elif shape == "pumpkin":
        draw.ellipse([size * 0.15, size * 0.3, size * 0.85, size * 0.85], fill=rgba)
        for x_frac in (0.33, 0.5, 0.67):
            draw.line([(size * x_frac, size * 0.3), (size * x_frac, size * 0.85)], fill=(0, 0, 0, 40), width=max(2, size // 60))
        draw.rectangle([size * 0.46, size * 0.12, size * 0.54, size * 0.3], fill=(120, 90, 50, 255))
    elif shape == "mitten":
        draw.rounded_rectangle([size * 0.25, size * 0.35, size * 0.75, size * 0.85], radius=size * 0.15, fill=rgba)
        draw.ellipse([size * 0.6, size * 0.3, size * 0.85, size * 0.55], fill=rgba)
        draw.rectangle([size * 0.3, size * 0.78, size * 0.7, size * 0.9], fill=(255, 255, 255, 220))
    elif shape == "lotus":
        import math

        # Each petal is a simple diamond (4-point polygon) with its own
        # near tip anchored at the shared base point and its own far tip
        # pointing outward along a fanned angle - built directly as
        # polygon coordinates (no image rotation/compositing, which an
        # earlier attempt got wrong and produced a single disconnected
        # blob instead of a recognizable flower, caught by hand-
        # inspecting the rendered PNG before shipping). This is the same
        # "compute real coordinates, draw a polygon" technique
        # _star_points()/the "autumn_leaf"/"diamond" shapes already use
        # elsewhere in this function, applied to a fan of petals instead
        # of one single shape.
        base_x, base_y = size / 2, size * 0.8
        petal_count = 7
        petal_len = size * 0.48
        petal_half_width = size * 0.085
        for i in range(petal_count):
            angle_deg = -90 + (i - (petal_count - 1) / 2) * (150 / (petal_count - 1))
            angle = math.radians(angle_deg)
            dir_x, dir_y = math.cos(angle), math.sin(angle)
            perp_x, perp_y = -dir_y, dir_x
            tip_x = base_x + dir_x * petal_len
            tip_y = base_y + dir_y * petal_len
            mid_x = base_x + dir_x * petal_len * 0.55
            mid_y = base_y + dir_y * petal_len * 0.55
            alpha = 255 if i == petal_count // 2 else 200
            draw.polygon([
                (base_x, base_y),
                (mid_x + perp_x * petal_half_width, mid_y + perp_y * petal_half_width),
                (tip_x, tip_y),
                (mid_x - perp_x * petal_half_width, mid_y - perp_y * petal_half_width),
            ], fill=(*color, alpha))
        draw.ellipse([base_x - size * 0.1, base_y - size * 0.1, base_x + size * 0.1, base_y + size * 0.1], fill=(255, 230, 150, 255))
    elif shape == "om_circle":
        width = max(4, size // 20)
        draw.ellipse([size * 0.1, size * 0.1, size * 0.9, size * 0.9], outline=rgba, width=width)
        draw.ellipse([size * 0.42, size * 0.42, size * 0.58, size * 0.58], fill=rgba)
    elif shape == "zen_stone":
        for cy_frac, w_frac, h_frac in ((0.75, 0.5, 0.18), (0.55, 0.38, 0.16), (0.37, 0.26, 0.14)):
            draw.ellipse([
                size / 2 - size * w_frac / 2, size * cy_frac - size * h_frac / 2,
                size / 2 + size * w_frac / 2, size * cy_frac + size * h_frac / 2,
            ], fill=rgba)
    elif shape == "lipstick":
        draw.rectangle([size * 0.4, size * 0.45, size * 0.6, size * 0.85], fill=(60, 60, 65, 255))
        draw.polygon([(size * 0.4, size * 0.45), (size * 0.6, size * 0.45), (size * 0.5, size * 0.15)], fill=rgba)
    elif shape == "mirror":
        draw.ellipse([size * 0.25, size * 0.1, size * 0.75, size * 0.65], outline=rgba, width=max(5, size // 18))
        draw.rectangle([size * 0.47, size * 0.6, size * 0.53, size * 0.85], fill=rgba)
        draw.line([(size * 0.35, size * 0.85), (size * 0.65, size * 0.85)], fill=rgba, width=max(4, size // 25))
    elif shape == "diamond":
        draw.polygon([
            (size * 0.5, size * 0.15), (size * 0.8, size * 0.4), (size * 0.5, size * 0.85), (size * 0.2, size * 0.4),
        ], fill=rgba)
        draw.line([(size * 0.2, size * 0.4), (size * 0.8, size * 0.4)], fill=(255, 255, 255, 150), width=max(2, size // 60))
    elif shape == "coffee_cup":
        draw.rounded_rectangle([size * 0.25, size * 0.35, size * 0.65, size * 0.8], radius=size * 0.05, fill=rgba)
        draw.arc([size * 0.6, size * 0.4, size * 0.85, size * 0.65], start=300, end=120, fill=rgba, width=max(4, size // 25))
        for i in range(3):
            x = size * (0.35 + i * 0.1)
            draw.line([(x, size * 0.3), (x, size * 0.15)], fill=(200, 200, 200, 180), width=max(2, size // 60))
    elif shape == "croissant":
        import math

        for i in range(5):
            angle = math.pi * 0.2 * i
            x = size * 0.3 + i * size * 0.1
            y = size * 0.5 + math.sin(angle) * size * 0.1
            draw.ellipse([x - size * 0.12, y - size * 0.08, x + size * 0.12, y + size * 0.08], fill=rgba)
    elif shape == "book":
        draw.polygon([(size * 0.2, size * 0.2), (size * 0.5, size * 0.3), (size * 0.5, size * 0.85), (size * 0.2, size * 0.75)], fill=rgba)
        draw.polygon([(size * 0.8, size * 0.2), (size * 0.5, size * 0.3), (size * 0.5, size * 0.85), (size * 0.8, size * 0.75)], fill=(*color, 200))
    elif shape == "star_small":
        draw.polygon(_star_points(size // 2, size // 2, size * 0.3, size * 0.13, 5), fill=rgba)
    elif shape == "ray_burst":
        import math

        for i in range(12):
            angle = math.pi * 2 * i / 12
            x2 = size / 2 + math.cos(angle) * size * 0.45
            y2 = size / 2 + math.sin(angle) * size * 0.45
            draw.line([(size / 2, size / 2), (x2, y2)], fill=rgba, width=max(2, size // 40))
    elif shape == "arrow_curved":
        draw.arc([size * 0.15, size * 0.15, size * 0.85, size * 0.85], start=200, end=340, fill=rgba, width=max(5, size // 18))
        draw.polygon([
            (size * 0.78, size * 0.25), (size * 0.95, size * 0.3), (size * 0.85, size * 0.45),
        ], fill=rgba)
    elif shape == "frame_circle":
        draw.ellipse([size * 0.08, size * 0.08, size * 0.92, size * 0.92], outline=rgba, width=max(5, size // 18))
    elif shape == "frame_square":
        draw.rounded_rectangle([size * 0.08, size * 0.08, size * 0.92, size * 0.92], radius=size * 0.08, outline=rgba, width=max(5, size // 18))
    elif shape == "balloon":
        draw.ellipse([size * 0.25, size * 0.1, size * 0.75, size * 0.6], fill=rgba)
        draw.polygon([(size * 0.46, size * 0.6), (size * 0.54, size * 0.6), (size * 0.5, size * 0.68)], fill=rgba)
        draw.line([(size * 0.5, size * 0.68), (size * 0.5, size * 0.95)], fill=(120, 120, 120, 200), width=max(2, size // 60))
    elif shape == "gift":
        draw.rectangle([size * 0.2, size * 0.4, size * 0.8, size * 0.85], fill=rgba)
        draw.rectangle([size * 0.45, size * 0.4, size * 0.55, size * 0.85], fill=(255, 255, 255, 220))
        draw.rectangle([size * 0.2, size * 0.32, size * 0.8, size * 0.44], fill=(*color, 230))
        draw.ellipse([size * 0.35, size * 0.15, size * 0.5, size * 0.33], outline=rgba, width=max(3, size // 35))
        draw.ellipse([size * 0.5, size * 0.15, size * 0.65, size * 0.33], outline=rgba, width=max(3, size // 35))
    elif shape == "party_hat":
        draw.polygon([(size * 0.5, size * 0.1), (size * 0.75, size * 0.8), (size * 0.25, size * 0.8)], fill=rgba)
        draw.ellipse([size * 0.44, size * 0.05, size * 0.56, size * 0.17], fill=(255, 230, 150, 255))
    elif shape == "confetti":
        import random

        rng = random.Random(42)  # deterministic - a real sticker PNG must render identically every time
        for _ in range(14):
            x = rng.uniform(size * 0.1, size * 0.9)
            y = rng.uniform(size * 0.1, size * 0.9)
            w = rng.uniform(size * 0.04, size * 0.08)
            draw.rectangle([x, y, x + w, y + w], fill=(*color, 230))

    return image


def _star_points(cx: float, cy: float, outer_r: float, inner_r: float, points: int) -> list[tuple[float, float]]:
    import math

    result = []
    for i in range(points * 2):
        angle = math.pi * i / points - math.pi / 2
        r = outer_r if i % 2 == 0 else inner_r
        result.append((cx + math.cos(angle) * r, cy + math.sin(angle) * r))
    return result


def _animation_overlay_expressions(
    sticker: StickerInstance, *, canvas_width: int, canvas_height: int, sticker_width: int, sticker_height: int,
) -> tuple[str, str]:
    """Returns (x_expr, y_expr) - ffmpeg overlay's own time-varying
    position expressions implementing `sticker.animation`. `t` is
    ffmpeg's own current-timestamp variable (seconds, absolute - not
    relative to the sticker's own start) - every expression below
    anchors to `sticker.start_seconds` explicitly for this reason.

    The sticker is centered on (x_fraction, y_fraction) using overlay's
    own `overlay_w`/`overlay_h` (the REAL composited size) rather than
    the pre-rotation size: `rotate` expands the image to fit the turned
    corners, and centering on the unrotated size shifted every rotated
    sticker down/right of where it was placed. Unrotated stickers are
    unaffected (overlay_w == sticker_width)."""
    base_x = f"{sticker.x_fraction * canvas_width}-overlay_w/2"
    base_y = f"{sticker.y_fraction * canvas_height}-overlay_h/2"
    start = sticker.start_seconds

    if sticker.animation == "pop_in":
        # Scale-like effect approximated via a quick vertical settle
        # (true scale-over-time needs a second, per-frame-scaled input,
        # not just overlay's own x/y - this approximates "pop" via a
        # fast ease-in slide from slightly below, which reads as a pop
        # at normal playback speed, a real, intentional simplification).
        return (f"{base_x}", f"if(lt(t,{start}+0.15),{base_y}+20*(1-(t-{start})/0.15),{base_y})")
    if sticker.animation == "float":
        return (f"{base_x}", f"{base_y}+8*sin((t-{start})*2)")
    if sticker.animation == "bounce":
        return (f"{base_x}", f"{base_y}-abs(15*sin((t-{start})*4))")
    # "none"/"fade_in_out"/"spin"/"blink" use a fixed position - fade/
    # blink are opacity-only (see _alpha_expression() below), spin is
    # rotation-only (see build_sticker_filter()'s own rotate stage).
    return (f"{base_x}", f"{base_y}")


_TIME_DRIVEN_ANIMATIONS = ("spin", "blink", "fade_in_out")


def sticker_fade_seconds(sticker: StickerInstance) -> float:
    """The "fade_in_out" ramp length - shared with
    jarvis.video_editor.preview_compositor so the live preview fades
    over exactly the same window the export does."""
    return max(0.05, min(0.4, (sticker.end_seconds - sticker.start_seconds) / 2))


def is_animated_gif(path: Path | None) -> bool:
    return path is not None and path.suffix.lower() == ".gif"


def _alpha_clause(sticker: StickerInstance, *, label_in: str, label_out: str) -> str:
    """Builds the opacity-handling stage of the filter chain -
    `colorchannelmixer`'s own `aa=` option does NOT support time-
    varying expressions (`t`/`if`/`lt` are rejected outright - a real,
    hand-hit bug found while testing this exact code: ffmpeg's
    AVOptions eval context for colorchannelmixer has no frame-time
    variable, unlike drawtext's `enable=`), so each animation that
    needs real time-varying alpha uses whichever ffmpeg mechanism
    actually supports it: `fade=...:alpha=1` (a real, built-in
    time-windowed alpha fade, confirmed working) for "fade_in_out", and
    `geq=a=...` (confirmed to support the uppercase `T` time variable,
    unlike colorchannelmixer) for "blink"'s own periodic on/off pattern.
    A constant opacity (every other animation) stays on the cheaper,
    simpler `colorchannelmixer=aa=<constant>` stage."""
    base = sticker.opacity

    if sticker.animation == "fade_in_out":
        start, end = sticker.start_seconds, sticker.end_seconds
        fade = sticker_fade_seconds(sticker)
        # The fades scale whatever alpha the sticker already has, so the
        # constant opacity is applied first - without it a faded sticker
        # ignored its own opacity setting entirely.
        opacity_stage = f"colorchannelmixer=aa={base}," if base != 1.0 else ""
        return (
            f"[{label_in}]{opacity_stage}fade=t=in:st={start}:d={fade}:alpha=1,"
            f"fade=t=out:st={end - fade}:d={fade}:alpha=1[{label_out}]"
        )
    if sticker.animation == "blink":
        # Scales the sticker's OWN per-pixel alpha - a constant `a=`
        # here used to make every transparent pixel opaque, so a
        # blinking heart showed up as a solid square.
        start = sticker.start_seconds
        return (
            f"[{label_in}]geq=r='r(X,Y)':g='g(X,Y)':b='b(X,Y)':"
            f"a='alpha(X,Y)*if(lt(mod(T-{start},0.6),0.3),{base},{base * 0.2})'[{label_out}]"
        )
    return f"[{label_in}]colorchannelmixer=aa={base}[{label_out}]"


def build_sticker_filter(
    sticker: StickerInstance, *, canvas_width: int, canvas_height: int, cwd: Path,
    video_label: str = "outv", output_label: str = "stickv", input_index: int,
) -> tuple[list[str], str]:
    """Builds (extra_input_args, filter_clause) for ONE sticker -
    `input_index` is the ffmpeg input index this sticker's own image
    will occupy (the caller's responsibility to choose, same convention
    jarvis.video_editor.audio_mixing.build_music_mix_filter()'s own
    `timeline_audio_input_count` parameter already establishes for a
    music track's input index). Raises StickerError if `sticker` fails
    its own validate(), or if a built-in shape's own PNG can't be
    rendered to a temp file."""
    problems = sticker.validate()
    if problems:
        raise StickerError("; ".join(problems))

    if sticker.custom_path is not None:
        image_path = sticker.custom_path
    else:
        image_path = cwd / f"_sticker_{sticker.shape}_{input_index}.png"
        render_builtin_sticker(sticker.shape, output_path=image_path, tint=sticker.tint)

    sticker_px = int(sticker.size_fraction * canvas_width)
    x_expr, y_expr = _animation_overlay_expressions(
        sticker, canvas_width=canvas_width, canvas_height=canvas_height,
        sticker_width=sticker_px, sticker_height=sticker_px,
    )

    scaled_label = f"stk{input_index}scaled"
    alpha_label = f"stk{input_index}"
    alpha_clause = _alpha_clause(sticker, label_in=scaled_label, label_out=alpha_label)

    rotate_clause = ""
    rotated_label = alpha_label
    if sticker.rotation_degrees != 0.0 or sticker.animation == "spin":
        import math

        if sticker.animation == "spin":
            angle_expr = f"(t-{sticker.start_seconds})*2*PI"
        else:
            angle_expr = f"{math.radians(sticker.rotation_degrees)}"
        rotated_label = f"stk{input_index}rot"
        rotate_clause = (
            f";[{alpha_label}]rotate={angle_expr}:c=none:ow=rotw(iw):oh=roth(ih)[{rotated_label}]"
        )

    # An animated GIF loops for as long as the sticker is visible and
    # starts playing at the sticker's own start time. Before, the GIF
    # stream started at t=0 of the whole video and played once, so a
    # GIF placed at 5s had usually already finished (frozen on its last
    # frame) by the time it appeared. -ignore_loop 0 loops it forever;
    # -t bounds that infinite input to the sticker's own duration so
    # ffmpeg still terminates; setpts shifts its first frame to `start`.
    gif_prefix = ""
    extra_input_args = ["-i", str(image_path)]
    if sticker.animation in _TIME_DRIVEN_ANIMATIONS:
        # A plain `-i image.png` is ONE frame at t=0 that overlay keeps
        # repeating, so `rotate`'s t, `geq`'s T and `fade` all saw t=0
        # forever: "spin" froze at one angle, "blink" never blinked and
        # "fade_in_out" stayed at its t=0 alpha. -loop 1 makes the image
        # a real stream whose timestamps follow the timeline's own.
        extra_input_args = ["-loop", "1", "-framerate", "30", "-t", f"{sticker.end_seconds}", "-i", str(image_path)]
    if is_animated_gif(sticker.custom_path):
        duration = sticker.end_seconds - sticker.start_seconds
        extra_input_args = ["-ignore_loop", "0", "-t", f"{duration}", "-i", str(image_path)]
        gif_prefix = f"setpts=PTS-STARTPTS+{sticker.start_seconds}/TB,"

    filter_clause = (
        f"[{input_index}:v]{gif_prefix}scale={sticker_px}:{sticker_px},format=rgba[{scaled_label}];"
        f"{alpha_clause}"
        f"{rotate_clause};"
        f"[{video_label}][{rotated_label}]overlay=x='{x_expr}':y='{y_expr}':"
        f"enable='between(t,{sticker.start_seconds},{sticker.end_seconds})'[{output_label}]"
    )
    return extra_input_args, filter_clause
