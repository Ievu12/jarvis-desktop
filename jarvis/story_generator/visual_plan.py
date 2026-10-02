"""Re-export shim: jarvis.story_generator's own per-scene visual plan
types now live in jarvis.reel_generator.visual_plan (promoted there once
AI Reel Generator's own Smart Visual Director brief needed the exact
same shape - see that module's own docstring for the full history/
reasoning). Kept as a plain re-export here so every existing
`from jarvis.story_generator.visual_plan import ScenePlan` (etc.) import
in this package's own modules and tests keeps working unmodified - the
REAL dataclasses/constants are defined once, in
jarvis.reel_generator.visual_plan, never duplicated."""

from __future__ import annotations

from jarvis.reel_generator.visual_plan import (  # noqa: F401 - re-exported, not used directly in this shim
    DEFAULT_LIGHTING,
    DEFAULT_MOTION,
    DEFAULT_PACING,
    DEFAULT_TRANSITION,
    DEFAULT_VISUAL_SOURCE,
    DEFAULT_VISUAL_STYLE,
    DEFAULT_VISUAL_TYPE,
    LIGHTING_CHOICES,
    MOTION_CHOICES,
    PACING_CHOICES,
    STICKER_CHOICES,
    STICKER_NAME_CHOICES,
    TEXT_ANIMATION_CHOICES,
    TEXT_POSITION_CHOICES,
    TRANSITION_CHOICES,
    VISUAL_SOURCE_CHOICES,
    VISUAL_STYLE_CHOICES,
    VISUAL_TYPE_CHOICES,
    ScenePlan,
    TextCue,
    VisualPlan,
)
