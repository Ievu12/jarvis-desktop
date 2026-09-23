"""Non-interactive entry point for an automated (e.g. Windows Task
Scheduler) daily Instagram Insights run: 'python -m
jarvis.cli.daily_instagram_report'.

Runs exactly three READ_ONLY InstagramConnector actions, in order, and
prints each result to stdout:
    1. record_daily_snapshot - reads today's real Instagram Insights and
       saves them to the local history file (jarvis.integrations
       .instagram_history) - the ONLY write this script performs, and it
       is to a local JARVIS file, never to the Instagram account itself.
    2. daily_report           - today's reach change, per-post totals for
       today's posts, and the best-performing post published today.
    3. compare_history         - compares PREVIOUSLY RECORDED snapshots
       only (today vs yesterday, today vs 7 days ago, last 7 recorded
       days vs the 7 before that); never a live API call, never guesses
       a day that was never recorded.

Deliberately NOT the LLM/REPL path (jarvis.cli.main.main): this script
makes no Anthropic API call, needs no ANTHROPIC_API_KEY, prompts for no
input, and calls only these three fixed, already-implemented actions -
nothing here can publish, comment, message, or otherwise write to the
Instagram account, and nothing here touches Gmail, Google Calendar, or
jarvis.integrations.oauth. It exists so a scheduled task (Windows Task
Scheduler or similar) can run a completely deterministic, unattended
daily report without an LLM in the loop.

Exit code is 0 only if Instagram is configured and all three actions
succeeded (ExternalActionResult.ok is True for each) - non-zero
otherwise, so a scheduled run's failure is visible to whatever is
watching the task's history/last run result, without needing to parse
stdout. The Instagram OAuth access token is never read directly here
(InstagramConnector handles that internally) and never appears in any
printed output - only ExternalActionResult.output, which - like every
other Instagram action - is a plain data summary in Lithuanian and,
per InstagramConnector's own redaction, never carries the token.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone

from jarvis.integrations.base import ExternalActionResult
from jarvis.integrations.connectors.instagram import InstagramConnector


def _ensure_utf8_stdio() -> None:
    """Same best-effort UTF-8 reconfiguration as jarvis.cli.main's helper
    of the same name - needed here too since this script prints
    Lithuanian text (with non-ASCII characters) and a scheduled task's
    console/log encoding can't be assumed to be UTF-8 by default on
    Windows."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def _print_result(heading: str, result: ExternalActionResult) -> None:
    print(f"=== {heading} ===")
    print(result.output)
    print()


def run() -> int:
    """Runs the three actions in order and prints each result. Returns a
    process exit code (0 = success, 1 = failure) rather than calling
    sys.exit() itself, so it stays testable without a real process exit.
    """
    _ensure_utf8_stdio()

    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print(f"JARVIS - Instagram kasdienė ataskaita ({started_at} UTC)")
    print()

    connector = InstagramConnector()
    if not connector.is_configured():
        print(
            "Instagram nesukonfigūruotas - nėra išsaugotų OAuth tokenų. "
            "Prijunk Instagram Business/Creator paskyrą, kad ši ataskaita veiktų."
        )
        return 1

    ok = True

    snapshot_result = connector.execute("record_daily_snapshot")
    _print_result("Šiandienos snapshotas išsaugotas", snapshot_result)
    ok = ok and snapshot_result.ok

    report_result = connector.execute("daily_report")
    _print_result("Kasdienė ataskaita", report_result)
    ok = ok and report_result.ok

    compare_result = connector.execute("compare_history")
    _print_result("Palyginimas su ankstesniais duomenimis", compare_result)
    ok = ok and compare_result.ok

    return 0 if ok else 1


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
