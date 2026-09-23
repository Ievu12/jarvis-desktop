"""Morning briefing: 'Labas rytas' -> today's date/weekday -> an
Instagram content brief for the day, spoken aloud via the EXISTING voice
module (jarvis.voice.text_to_speech.speak - not modified by this file)
and generated via the EXISTING agent loop (jarvis.core.agent.Agent.step
- also not modified). This module adds no new tool, no new Instagram
API call, and no new TTS code path - it only sequences calls to modules
that already work, exactly the way jarvis.voice.voice_loop.run_voice_mode
already sequences listen_once()/Agent.step()/speak() for a normal voice
turn.

MORNING_HOUR / MORNING_MINUTE below are the routine's own documented
default time - kept as plain module-level constants so they're trivial
to change (see the comment above them). The ACTUAL scheduling is done by
Windows Task Scheduler (see jarvis.cli.morning_briefing's docstring) via
its own trigger time, which is independent of these constants and does
not require touching this file to change - these constants exist for
in-app text (e.g. help output, logging) to describe when the briefing is
*intended* to run, not to drive any timer in this process.

Never publishes, comments, or sends any message on Instagram - it only
asks the agent (through the same tool-approval/RiskLevel-gated path as
any other turn) to read recent performance data with the existing
read-only Instagram tools, and to propose content ideas as plain text.
Nothing here writes to the Instagram account.
"""

from __future__ import annotations

from typing import Any

from jarvis.content_manager import (
    build_content_brief_prompt,
    extract_topic_summaries,
    format_greeting,
    record_used_topics,
)
from jarvis.core.agent import Agent, StepInterrupted
from jarvis.session.store import save_history
from jarvis.voice.text_to_speech import speak

# The routine's documented default run time - see module docstring for
# why this does not itself schedule anything. Change these two numbers
# (24h clock, local time) and re-register the Windows Task Scheduler
# trigger (jarvis.cli.morning_briefing's docstring has the exact
# command) to actually change when the briefing runs.
MORNING_HOUR = 8
MORNING_MINUTE = 0

# A short, fixed request asking the agent to use its EXISTING read-only
# Instagram tools (analyze_instagram_insights / compare_instagram_history
# / get_instagram_account_insights - whichever it judges most relevant)
# to summarize recent performance in a form the content-brief prompt can
# quote. Deliberately does not name a specific tool - the agent already
# knows all of them from its system prompt and picks appropriately, the
# same way it does for any other free-text Instagram question.
_INSTAGRAM_CONTEXT_REQUEST = (
    "Trumpai (kelios eilutės) apibendrink naujausius mano Instagram paskyros "
    "rezultatus (reach, likes, comments, shares, saved, total_interactions - "
    "kas prieinama), naudodamas esamus Instagram įrankius. Jei Instagram "
    "nesukonfigūruotas arba duomenų nėra, aiškiai tai pasakyk viena eilute - "
    "nieko nespėliok ir nieko nepublikuok."
)


def _speak_step(text: str) -> None:
    """Speaks one line via the existing voice module and prints it, the
    same "print + speak" pairing jarvis.voice.voice_loop uses for every
    turn - so a person watching the terminal can follow along even
    without audio. Notices (e.g. "no Lithuanian voice configured") are
    printed but not treated as fatal - text_to_speech.speak() already
    falls back gracefully on its own; this function does not duplicate
    that logic, only surfaces its notice if present."""
    print(f"[morning] {text}")
    result = speak(text)
    if result.notice:
        print(f"[morning] {result.notice}")


def run_morning_routine(agent: Agent, history: list[dict[str, Any]]) -> str:
    """Runs the full morning briefing once: greeting -> (best-effort)
    Instagram performance context -> content brief -> spoken aloud.
    Returns the content brief text (mainly for tests/callers that want
    to inspect what was generated) but the primary output is what gets
    spoken and printed along the way, exactly like a normal agent turn's
    reply. Every Agent.step() call here goes through the identical
    tool-approval/RiskLevel path as any other turn - this function adds
    no bypass.
    """
    _speak_step(format_greeting())

    instagram_context: str | None = None
    try:
        instagram_context = agent.step(history, _INSTAGRAM_CONTEXT_REQUEST)
    except StepInterrupted:
        # A turn requiring approval was denied/cancelled mid-flight -
        # the briefing continues without performance context rather
        # than aborting the whole morning routine over it.
        instagram_context = None
    except Exception:
        # Any other failure (network, tool error) - same graceful
        # degradation as content_manager.build_content_brief_prompt()
        # already handles a None/missing context.
        instagram_context = None
    finally:
        save_history(history)

    brief_prompt = build_content_brief_prompt(instagram_context=instagram_context)

    try:
        content_brief = agent.step(history, brief_prompt)
    except StepInterrupted:
        message = "Turinio plano paruošimas buvo atšauktas."
        _speak_step(message)
        save_history(history)
        return message
    except Exception as e:
        message = f"Nepavyko paruošti turinio plano: {e}"
        _speak_step(message)
        save_history(history)
        return message

    save_history(history)
    _speak_step(content_brief)

    record_used_topics(extract_topic_summaries(content_brief))

    return content_brief
