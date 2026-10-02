"""Tests for jarvis.story_generator.scene_render: a thin re-export shim
over jarvis.reel_generator.scene_render (promoted there - see that
module's own docstring for the full history/reasoning). The real
rendering behavior is tested at its real home,
tests/test_reel_generator_scene_render.py; this file only confirms the
re-export itself is wired correctly (the exact objects, not copies)."""

from __future__ import annotations

from jarvis.reel_generator import scene_render as real_module
from jarvis.story_generator import scene_render as shim_module


def test_render_all_story_scenes_is_the_same_function_object():
    assert shim_module.render_all_story_scenes is real_module.render_all_story_scenes


def test_render_story_scene_visual_is_the_same_function_object():
    assert shim_module.render_story_scene_visual is real_module.render_story_scene_visual
