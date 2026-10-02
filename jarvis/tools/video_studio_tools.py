"""Agent-facing tool wrapper around jarvis.video_studio.db: lets the
conversational agent (and therefore the morning briefing - see
jarvis.morning_routine's own docstring for that flow) answer "do I have
any unfinished video projects?" and similar questions, per the module
brief's section 18 ("JARVIS Morning Workflow" - "Good morning. You have
one unfinished video project. Would you like me to finish the Reel?").

READ-ONLY, same as jarvis.tools.instagram_tools' entire tool set (see
that module's own docstring for the reasoning) - this tool only lists
jarvis.video_studio.db project records (id, filename, status, created
date), never opens a file, never runs ffmpeg/whisper/an LLM generation
call, and never triggers any AI Video Studio action. "Finishing a Reel"
or "creating today's Reel" (the brief's other example command) is NOT
something this tool does or that the agent can trigger on its own -
those are multi-step, real-FFmpeg/LLM work with their own approval-
adjacent UI (Create Reel / Export Reel buttons in
jarvis.gui.views.video_studio) that only makes sense driven from the
GUI, where a person can review each step (transcript, highlight
candidates, the edit plan, the exported file) before committing to it;
routing that whole pipeline through a single agent tool call would
skip every one of those review points. This tool's only job is
answering the STATUS question so the agent can raise it in
conversation and point the person to the AI Video Studio view - it
never does the work itself.
"""

from __future__ import annotations

from jarvis.tools.base import Tool, ToolResult
from jarvis.video_studio import db

# Statuses jarvis.video_studio.db.ProjectRecord.status reaches once
# fully exported - anything else (uploaded/analyzed/transcribed/
# reel_created) is "unfinished" for this tool's purposes, matching the
# module brief's own "unfinished video project" phrasing.
_FINISHED_STATUSES = frozenset({"exported"})


class ListVideoStudioProjectsTool(Tool):
    name = "list_video_studio_projects"
    description = (
        "List AI Video Studio projects (recent first) with their filename, "
        "status (uploaded/analyzed/transcribed/reel_created/exported), and "
        "when created - so you can tell the person whether they have any "
        "unfinished video projects and mention it in conversation. "
        "Read-only, no approval required. This tool does NOT create, edit, "
        "transcribe, or export any video - it only reports project status. "
        "If asked to 'finish the Reel' or 'create today's Reel', tell the "
        "person to open the AI Video Studio view in the JARVIS desktop app "
        "to do it themselves (with each step - transcript, highlights, edit "
        "plan, export - reviewable there), rather than attempting it "
        "through this tool."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "unfinished_only": {
                "type": "boolean",
                "description": "If true, only list projects that haven't been exported yet (default true).",
            },
            "limit": {
                "type": "integer",
                "description": "Max projects to list (default 10, max 50).",
            },
        },
    }

    def run(self, *, unfinished_only: bool = True, limit: int = 10) -> ToolResult:
        limit = max(1, min(limit, 50))
        try:
            records = db.list_projects(limit=limit if not unfinished_only else limit * 4)
        except Exception as e:
            return ToolResult(ok=False, output=f"Couldn't read AI Video Studio projects: {e}")

        if unfinished_only:
            records = [r for r in records if r.status not in _FINISHED_STATUSES][:limit]
        else:
            records = records[:limit]

        if not records:
            message = (
                "No unfinished AI Video Studio projects."
                if unfinished_only
                else "No AI Video Studio projects yet."
            )
            return ToolResult(ok=True, output=message)

        lines = [
            f"- {r.original_filename} (status: {r.status}, created {r.created_at[:10]})"
            for r in records
        ]
        return ToolResult(ok=True, output="\n".join(lines))
