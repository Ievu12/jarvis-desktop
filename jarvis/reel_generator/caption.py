"""Caption generation (module brief, section 12): "Automatically
generate: Instagram caption, CTA, relevant hashtags. Caption must be
based on the actual Reel."

Calls jarvis.instagram_ai_manager.ai_services.generate_caption()/
.generate_hashtags() directly, unmodified - per this project's
established "Do not duplicate existing AI infrastructure" rule (the
exact same reuse jarvis.video_studio.instagram_handoff already applies
- see that module's own docstring for the precedent). This module adds
NO new LLM-calling logic of its own for caption/hashtag text; it only
builds the content_description input those functions need from the
Reel's own approved script (grounding the caption in what the Reel
ACTUALLY says, never a generic topic guess) and returns their results
for display.

This is Stage 2's caption step: GENERATE and DISPLAY only, with a
[Copy Caption] action (module brief section 12). Saving this content
into Instagram AI Manager's own database (so it appears in that
module's Content Studio UI) is Stage 4's job
(jarvis.reel_generator.instagram_handoff, not built yet) - kept
separate so a person can preview/regenerate a caption for a Reel that
isn't ready to hand off yet without polluting Instagram AI Manager's
own content history."""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.core.llm import LLMClient
from jarvis.instagram_ai_manager import ai_services
from jarvis.reel_generator.brief import ReelBrief
from jarvis.reel_generator.script import ReelScript


def _content_description(brief: ReelBrief, script: ReelScript) -> str:
    """Grounds the caption/hashtag generation in what this specific
    Reel actually says - the approved script's full text - never a
    generic topic-only guess, mirroring
    jarvis.video_studio.instagram_handoff._build_content_description()'s
    own reasoning for the same choice."""
    return f"{script.full_text} {brief.cta}".strip()


@dataclass(frozen=True)
class ReelCaptionPackage:
    caption: dict[str, str] | None  # {short_caption, medium_caption, long_caption} or None on failure
    hashtags: dict[str, list[str]] | None  # {niche, medium_competition, broader, branded} or None on failure

    @property
    def insufficient_data(self) -> bool:
        return self.caption is None and self.hashtags is None


def generate_reel_caption_package(
    llm: LLMClient, brief: ReelBrief, script: ReelScript, *, tone: str = "friendly",
) -> ReelCaptionPackage:
    """Generates caption variants + hashtags for an already-approved
    Reel script. Never raises - a generator's own None-on-failure
    result is simply carried through on the returned package (partial
    success still useful, matching ai_services' own "never raise,
    return None on failure" contract and
    jarvis.video_studio.instagram_handoff.HandoffResult's own partial-
    success reasoning)."""
    content_description = _content_description(brief, script)
    caption = ai_services.generate_caption(
        llm, topic=brief.topic, content_description=content_description, hook=script.segments[0].text,
        tone=tone, target_audience=brief.audience, cta=brief.cta,
    )
    hashtags = ai_services.generate_hashtags(llm, brief.topic)
    return ReelCaptionPackage(caption=caption, hashtags=hashtags)
