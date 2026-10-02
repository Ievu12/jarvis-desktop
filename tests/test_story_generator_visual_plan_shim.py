"""Tests for jarvis.story_generator.visual_plan: a thin re-export shim
over jarvis.reel_generator.visual_plan (promoted there - see that
module's own docstring for the full history/reasoning). Confirms the
re-export is wired correctly (the exact objects, not copies)."""

from __future__ import annotations

from jarvis.reel_generator import visual_plan as real_module
from jarvis.story_generator import visual_plan as shim_module


def test_scene_plan_is_the_same_class_object():
    assert shim_module.ScenePlan is real_module.ScenePlan


def test_text_cue_is_the_same_class_object():
    assert shim_module.TextCue is real_module.TextCue


def test_visual_plan_is_the_same_class_object():
    assert shim_module.VisualPlan is real_module.VisualPlan


def test_sticker_choices_is_the_same_object():
    assert shim_module.STICKER_CHOICES is real_module.STICKER_CHOICES
