"""AI Design Studio: a modular JARVIS desktop feature for creating
Instagram-ready visual designs (Stories, posts, Reel covers, carousels)
from a plain-language description - a structured AI-generated design
brief, then a typography/gradient/photo composition rendered locally
with Pillow.

No image-generation AI is used anywhere in this package (there is none
available in this codebase - confirmed by architecture inspection
before this module was written: no DALL-E/Stable Diffusion/etc.
dependency, and jarvis.core.llm.LLMClient is text-only, no vision/image
generation). Every visual here is composed from: a solid color or
linear gradient background (from a design style's own color palette),
AI-GENERATED TEXT (headline/supporting text/CTA - via the exact
isolated, tool-free, JSON-only LLM call pattern
jarvis.instagram_ai_manager.ai_services already established) rendered
with Pillow's ImageDraw/ImageFont, and optionally a user-uploaded image
(logo, brand photo, product photo) composited in - never a "generate a
novel photo of X" step, which this codebase has no capability for.

Submodules (mirroring jarvis.video_studio's package split):
  - storage.py: project directory management (jarvis.config
    .DESIGN_STUDIO_PROJECTS_DIR) - one directory per design project.
  - db.py: SQLite metadata (jarvis.config.DESIGN_STUDIO_DB_FILE) - one
    project row per design, holding its brief/variants/selection as
    JSON, same idempotent-migration pattern as jarvis.video_studio.db.
  - brief.py: generate_design_brief() - an isolated LLM call that turns
    a plain-language request into a structured DesignBrief (topic,
    objective, audience, tone, headline, supporting text, CTA) - see
    that module's own docstring for the exact JSON contract.
  - styles.py: the design style system (module brief, section 5) - a
    fixed table of named DesignStyle presets (colors, fonts, spacing)
    mirroring jarvis.video_studio.cover.COVER_TEMPLATES' shape.
  - render.py: renders a DesignBrief + DesignStyle into an actual
    1080x1920/1080x1350/1080x1080 image via Pillow - the only place in
    this package (or the whole codebase) doing ImageDraw/ImageFont
    compositing; see that module's own docstring for the exact layout
    algorithm and safe-area handling.

UI code (jarvis.gui.views.design_studio) is entirely separate, exactly
like jarvis.video_studio - this package has no customtkinter import
anywhere.
"""
