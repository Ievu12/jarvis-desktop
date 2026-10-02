"""Agent-facing tool letting the conversational agent create a NEW,
real AI Reel Generator project (module brief's own "STEP 1 - AI
CONCEPT" stage only: a real jarvis.reel_generator.storage project +
brief + script, generated via the SAME real LLM calls the Reel
Generator dashboard itself uses) directly from a chat message like
"Sukurk Reel apie rytinę jogą", then signals the GUI to open AI Reel
Generator on that exact project (jarvis.tools.reel_navigation - see
that module's own docstring for the full mechanism).

Deliberately does NOT go any further than brief+script (module brief's
own explicit constraint before storyboard approval: "Do not
automatically generate expensive video assets before storyboard
approval. Only generate final media after explicit user approval.") -
this tool creates the DRAFT, nothing more. Every real generation step
after that (storyboard, Smart Visual Director, scene visuals, export)
stays a GUI button click the person makes themselves, once the Reel
Generator view is open in front of them - this deliberately does NOT
reverse jarvis.tools.video_studio_tools's own documented "video/Reel
generation stays GUI-driven, never agent-tool-driven" decision (see
that module's own docstring) for anything beyond this one, cheap,
reviewable-before-any-real-cost first step (a Reel brief/script is text
generation only - no image-generation API call, no ffmpeg encode, no
real production cost is incurred by this tool).

"Regeneruok N scena"/"Patvirtinu"/"Eksportuok" and any other mid-
pipeline chat command remain OUT OF SCOPE for this tool (and for this
whole module) - those actions mutate an ALREADY-OPEN project the person
is looking at in the GUI, which is exactly the kind of step that stays
a reviewable button click, matching the same reasoning above."""

from __future__ import annotations

import dataclasses

from jarvis.core.llm import LLMClient
from jarvis.reel_generator import db, storage
from jarvis.reel_generator.brief import generate_reel_brief
from jarvis.reel_generator.script import generate_reel_script
from jarvis.tools.base import Tool, ToolResult
from jarvis.tools.reel_navigation import request_navigation

_LITHUANIAN_DIACRITICS = set("ąčęėįšųūžĄČĘĖĮŠŲŪŽ")
# Same minimal, honest "not a real language detector" heuristic as
# jarvis.content_studio.orchestrator._detect_language() - kept as this
# module's own self-contained copy rather than importing that module's
# private helper, matching this codebase's established "each feature
# module's backend stays self-contained" convention (see that
# function's own docstring for the full reasoning, equally applicable
# here).


def _detect_language(text: str) -> str:
    return "lt" if any(ch in _LITHUANIAN_DIACRITICS for ch in text) else "en"


class CreateReelDraftTool(Tool):
    name = "create_reel_draft"
    description = (
        "Create a new AI Reel Generator project from a plain-language Reel idea "
        "(e.g. \"Sukurk Reel apie rytinę jogą\" / \"Create a Reel about morning "
        "yoga\") and open it in the JARVIS desktop app's AI Reel Generator view. "
        "This generates a REAL Reel brief and script via the same pipeline the "
        "Reel Generator dashboard itself uses (real LLM calls, real project "
        "saved to disk) - it does NOT generate the storyboard, any scene "
        "visuals, or export a video; those steps require the person's own "
        "review and approval in the GUI, and this tool must never be used to "
        "attempt them. Use this tool whenever the person asks you to create, "
        "start, or make a Reel about some topic - after calling it, tell them "
        "their Reel draft is ready and open in AI Reel Generator, where they can "
        "review the brief/script and continue the workflow themselves."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "idea": {
                "type": "string",
                "description": (
                    "The person's own plain-language Reel idea, in whatever language they used "
                    "(e.g. Lithuanian or English) - passed through as-is, not translated or reworded."
                ),
            },
        },
        "required": ["idea"],
    }

    def run(self, **kwargs) -> ToolResult:
        idea = kwargs.get("idea")
        if not isinstance(idea, str) or not idea.strip():
            return ToolResult(ok=False, output="No Reel idea was given - ask the person what the Reel should be about.")
        idea = idea.strip()

        try:
            llm = LLMClient()
        except RuntimeError as e:
            return ToolResult(ok=False, output=f"Couldn't create the Reel draft: {e}")

        try:
            project = storage.create_project()
        except storage.StorageError as e:
            return ToolResult(ok=False, output=f"Couldn't create the Reel draft: {e}")

        db.create_project_record(project.project_id, idea)

        language = _detect_language(idea)
        brief = generate_reel_brief(llm, idea, language=language)
        if brief is None:
            return ToolResult(
                ok=False,
                output="JARVIS couldn't create a Reel brief for that idea - ask the person to rephrase it, or open AI Reel Generator and try again there.",
            )
        db.save_brief(project.project_id, dataclasses.asdict(brief))

        script = generate_reel_script(llm, brief)
        if script is None:
            # The brief itself is real and saved - a person can still
            # open the project and regenerate the script from the GUI,
            # so this is reported as a partial result, not a total
            # failure (same "string on failure is for the WHOLE call
            # only" nuance jarvis.content_studio.orchestrator's own
            # create_reel_project() documents for its own identical
            # brief-then-script sequence).
            request_navigation(project.project_id)
            return ToolResult(
                ok=True,
                output=(
                    f"Created a Reel brief for '{brief.topic}', but the script didn't fit the "
                    f"{brief.duration_seconds}-second duration - opening AI Reel Generator so the "
                    "person can regenerate the script themselves."
                ),
            )
        db.save_script(project.project_id, dataclasses.asdict(script))

        request_navigation(project.project_id)
        return ToolResult(
            ok=True,
            output=(
                f"Created a real Reel draft: topic '{brief.topic}', {brief.duration_seconds}-second "
                f"{brief.style} style, with a full hook/value/cta script - opened in AI Reel "
                "Generator for the person to review and approve."
            ),
        )
