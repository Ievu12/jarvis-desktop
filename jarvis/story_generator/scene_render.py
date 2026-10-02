"""Re-export shim: jarvis.story_generator's own enriched scene rendering
now lives in jarvis.reel_generator.scene_render (promoted there once AI
Reel Generator's own Smart Visual Director brief needed the exact same
rendering - see that module's own docstring for the full history/
reasoning). Kept as a plain re-export here so every existing
`from jarvis.story_generator.scene_render import render_all_story_scenes`
(etc.) import in this package's own modules and tests keeps working
unmodified - the REAL rendering code is defined once, in
jarvis.reel_generator.scene_render, never duplicated."""

from __future__ import annotations

from jarvis.reel_generator.scene_render import (  # noqa: F401 - re-exported, not used directly in this shim
    render_all_story_scenes,
    render_story_scene_visual,
)
