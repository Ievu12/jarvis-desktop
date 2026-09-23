"""CLI REPL entry point."""

from __future__ import annotations

import subprocess
import sys

from jarvis.config import ANTHROPIC_API_KEY, JARVIS_ROOT
from jarvis.core.agent import Agent, StepInterrupted
from jarvis.core.approval import confirm_side_effect
from jarvis.core.commit_message import suggest_commit_message
from jarvis.core.help_view import format_help
from jarvis.core.history_view import DEFAULT_ENTRY_LIMIT, format_history
from jarvis.core.llm import LLMClient
from jarvis.core.precommit_view import format_precommit
from jarvis.core.project_commit import build_commit_plan
from jarvis.core.project_commit_view import format_commit_plan
from jarvis.core.project_notes import load_project_notes
from jarvis.core.project_plan import build_plan
from jarvis.core.project_plan_view import format_plan
from jarvis.core.project_precommit import build_precommit_check
from jarvis.core.project_precommit_view import format_precommit_check
from jarvis.core.project_review import build_review
from jarvis.core.project_review_view import format_review_result
from jarvis.core.project_scan import scan_project
from jarvis.core.project_scan_view import format_scan_result
from jarvis.core.project_work import build_work_item
from jarvis.core.project_work_view import format_work_item
from jarvis.core.review_view import format_review, get_staged_diff
from jarvis.core.secrets import (
    check_env_gitignored,
    ensure_jarvis_dir_gitignored,
    mask_secret,
    redact_secret,
)
from jarvis.core.command_console import handle_command
from jarvis.core.status_view import format_status
from jarvis.core.task_planner import TaskPlanner
from jarvis.core.task_runner import TaskRunner
from jarvis.core.task_runner_view import format_task_run
from jarvis.core.task_view import format_task
from jarvis.integrations.connectors.email import EmailConnector
from jarvis.integrations.connectors.gmail import GmailConnector
from jarvis.integrations.connectors.google_calendar import GoogleCalendarConnector
from jarvis.integrations.connectors.instagram import InstagramConnector
from jarvis.integrations.connectors.stripe import StripeConnector
from jarvis.integrations.manager import IntegrationsManager
from jarvis.integrations.manager_view import format_integration_statuses
from jarvis.integrations.registry import IntegrationRegistry
from jarvis.morning_routine import run_morning_routine
from jarvis.session.store import load_history, save_history
from jarvis.session.tasks import load_tasks
from jarvis.voice.voice_loop import run_voice_mode
from jarvis.tools.base import ToolRegistry
from jarvis.tools.fs import (
    AppendToFileTool,
    CreateDirectoryTool,
    DeleteFileTool,
    EditFileLinesTool,
    ListDirectoryTool,
    MoveFileTool,
    ReadFileTool,
    ReplaceInFileTool,
    SearchFilesTool,
    WriteFileTool,
)
from jarvis.tools.calendar_tools import (
    GetCalendarEventTool,
    ListTodaysCalendarEventsTool,
    ListUpcomingCalendarEventsTool,
)
from jarvis.tools.email_tools import (
    CheckEmailConnectionTool,
    GetEmailAccountInfoTool,
    ListRecentEmailsTool,
    ReadEmailTool,
)
from jarvis.tools.gmail_tools import (
    GetGmailMessageContentTool,
    GetGmailMessageTool,
    ListRecentGmailMessagesTool,
    ListUnreadGmailMessagesTool,
    SearchGmailMessagesTool,
)
from jarvis.tools.instagram_tools import (
    AnalyzeInstagramInsightsTool,
    CompareInstagramHistoryTool,
    GetInstagramAccountInsightsTool,
    GetInstagramDailyReportTool,
    GetInstagramMediaDetailsTool,
    GetInstagramMediaInsightsTool,
    GetInstagramProfileTool,
    ListInstagramCommentsTool,
    ListRecentInstagramMediaTool,
    ListRecentInstagramMessagesTool,
    RecordInstagramDailySnapshotTool,
)
from jarvis.tools.plan import CreatePlanTool
from jarvis.tools.shell import ShellTool, _git_commit
from jarvis.tools.stripe_tools import (
    GetChargeStatusTool,
    GetStripeBalanceTool,
    ListRecentChargesTool,
    ListRecentPaymentIntentsTool,
)
from jarvis.tools.tasks import ManageTasksTool
from jarvis.tools.workflow_state import WorkflowStateTool


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ReadFileTool())
    registry.register(WriteFileTool())
    registry.register(AppendToFileTool())
    registry.register(ListDirectoryTool())
    registry.register(DeleteFileTool())
    registry.register(CreateDirectoryTool())
    registry.register(MoveFileTool())
    registry.register(SearchFilesTool())
    registry.register(ReplaceInFileTool())
    registry.register(EditFileLinesTool())
    registry.register(CreatePlanTool())
    registry.register(ManageTasksTool())
    registry.register(ShellTool())
    registry.register(WorkflowStateTool())
    registry.register(CheckEmailConnectionTool())
    registry.register(GetEmailAccountInfoTool())
    registry.register(ListRecentEmailsTool())
    registry.register(ReadEmailTool())
    registry.register(GetStripeBalanceTool())
    registry.register(ListRecentChargesTool())
    registry.register(GetChargeStatusTool())
    registry.register(ListRecentPaymentIntentsTool())
    registry.register(ListRecentGmailMessagesTool())
    registry.register(ListUnreadGmailMessagesTool())
    registry.register(SearchGmailMessagesTool())
    registry.register(GetGmailMessageTool())
    registry.register(GetGmailMessageContentTool())
    registry.register(ListTodaysCalendarEventsTool())
    registry.register(ListUpcomingCalendarEventsTool())
    registry.register(GetCalendarEventTool())
    registry.register(GetInstagramProfileTool())
    registry.register(ListRecentInstagramMediaTool())
    registry.register(GetInstagramMediaDetailsTool())
    registry.register(GetInstagramMediaInsightsTool())
    registry.register(GetInstagramAccountInsightsTool())
    registry.register(AnalyzeInstagramInsightsTool())
    registry.register(GetInstagramDailyReportTool())
    registry.register(RecordInstagramDailySnapshotTool())
    registry.register(CompareInstagramHistoryTool())
    registry.register(ListInstagramCommentsTool())
    registry.register(ListRecentInstagramMessagesTool())
    return registry


def build_integration_registry() -> IntegrationRegistry:
    """Registers every known Connector (email, Stripe, Google Calendar,
    Gmail, Instagram) into an IntegrationRegistry, the same shape
    build_registry() uses for agent Tools. Used by 'jarvis integrations' /
    the REPL 'integrations' keyword via IntegrationsManager - CLI code
    never talks to a Connector directly, only through IntegrationsManager,
    which is built from this registry.
    """
    registry = IntegrationRegistry()
    registry.register(EmailConnector())
    registry.register(StripeConnector())
    registry.register(GoogleCalendarConnector())
    registry.register(GmailConnector())
    registry.register(InstagramConnector())
    return registry


def run_startup_checks() -> None:
    for warning in check_env_gitignored(JARVIS_ROOT):
        print(f"[warning] {warning}")

    notice = ensure_jarvis_dir_gitignored(JARVIS_ROOT)
    if notice:
        print(f"[notice] {notice}")


def _handle_history_command(command: str) -> None:
    parts = command.split()
    limit = DEFAULT_ENTRY_LIMIT
    if len(parts) > 1:
        try:
            limit = max(int(parts[1]), 1)
        except ValueError:
            print("[JARVIS] Usage: history [N]  (N must be a positive integer)\n")
            return
    print(format_history(limit=limit))
    print()


def _handle_tasks_command() -> None:
    tasks = load_tasks()
    if not tasks:
        print("No tasks tracked.\n")
        return
    for t in tasks:
        print(f"[{'x' if t.done else ' '}] #{t.id} {t.text}")
    print()


def _handle_review_command() -> None:
    print(format_review())
    print()


def _handle_precommit_command() -> None:
    print(format_precommit())
    print()


_COMMAND_CONSOLE_PREFIX = "komanda:"


def _handle_command_console_command(user_input: str) -> None:
    """Handles the 'komanda: <text>' REPL prefix: a natural-language
    (Lithuanian-friendly) command routed through the existing Task
    Execution chain (TaskPlanner -> TaskRunner -> approval ->
    DryRunExecutor) via jarvis.core.command_console.handle_command -
    entirely separate from the free-form LLM agent conversation the rest
    of the REPL uses. No API key is required for this path; no new
    capability is introduced - see command_console.py's own docstring.
    """
    text = user_input[len(_COMMAND_CONSOLE_PREFIX):].strip()
    if not text:
        print("Usage: komanda: <ką norite, kad JARVIS padarytų>\n")
        return
    print(handle_command(text))
    print()


def _handle_help_command() -> None:
    print(format_help())
    print()


def _handle_status_command() -> None:
    print(format_status())
    print()


def _handle_integrations_command() -> None:
    print(format_integration_statuses(IntegrationsManager(build_integration_registry()).list_statuses()))
    print()


def run_cli_integrations() -> None:
    """Non-interactive 'jarvis integrations' entry point: lists every
    registered integration's status (not_configured/connected/
    disconnected/error) via IntegrationsManager.list_statuses(), then
    returns - no REPL, no LLM call, no API key required. This CLI layer
    never touches a Connector directly; it only builds an
    IntegrationRegistry (build_integration_registry()) and asks
    IntegrationsManager for the status, exactly as the REPL 'integrations'
    keyword does. Never displays a credential value - every detail string
    IntegrationsManager returns is already masked.
    """
    manager = IntegrationsManager(build_integration_registry())
    print(format_integration_statuses(manager.list_statuses()))


def run_cli_scan() -> None:
    """Non-interactive 'jarvis scan' entry point: performs the
    deterministic, structured project scan (jarvis.core.project_scan) and
    prints it, then returns - no REPL, no LLM call, no API key required.
    Distinct from the 'scan' REPL keyword, which sends SCAN_PROMPT through
    the LLM-driven agent loop for a conversational survey; this is the
    non-interactive counterpart requested for scripting/CI-style use and
    as a structured data source a future 'plan' command can consume.
    """
    result = scan_project(JARVIS_ROOT)
    print(format_scan_result(result))


def run_cli_plan() -> None:
    """Non-interactive 'jarvis plan' entry point: runs the same
    deterministic scan_project() used by 'jarvis scan', then derives a
    structured ProjectPlan from it (jarvis.core.project_plan.build_plan)
    and prints it - no REPL, no LLM call, no API key required, and no
    project files are modified. Reuses scan's detection logic entirely;
    this function never inspects the filesystem or git on its own -
    build_plan() only reasons about the already-computed scan result.
    """
    scan_result = scan_project(JARVIS_ROOT)
    plan = build_plan(scan_result)
    print(format_plan(plan))


def run_cli_work() -> None:
    """Non-interactive 'jarvis work' entry point: runs the same
    deterministic scan_project() -> build_plan() pipeline used by 'jarvis
    plan', then derives the single next actionable step from the plan
    (jarvis.core.project_work.build_work_item) and prints it - no REPL, no
    LLM call, no API key required. jarvis work never writes files or runs
    git itself; it only identifies the next step and shows the exact safe
    command to run, or a suggested prompt to hand to the JARVIS REPL for
    anything requiring judgment. Reuses scan/plan's logic entirely - this
    function never inspects the filesystem or git on its own.
    """
    scan_result = scan_project(JARVIS_ROOT)
    plan = build_plan(scan_result)
    work_item = build_work_item(plan)
    print(format_work_item(work_item))


def run_cli_task(description: str) -> None:
    """Non-interactive 'jarvis task "<description>"' entry point: the
    Task Execution layer's analysis stage. Builds a full Task
    (jarvis.core.task) via TaskPlanner (jarvis.core.task_planner), which
    internally classifies the free-text request
    (jarvis.core.task_intent.parse_task_intent), runs the same
    deterministic scan_project() -> build_plan() pipeline used by 'jarvis
    plan'/'jarvis work', and derives a structured, unexecuted
    ExecutionPlan (jarvis.core.task_execution_plan.build_execution_plan) -
    then prints it - no REPL, no LLM call, no API key required.

    jarvis task NEVER performs any work itself: no file writes, no git
    mutations, no shell execution, no external-service calls. It only
    analyzes the request and describes what would need to happen - any
    step it proposes still requires a human to act through the normal,
    approval-gated JARVIS REPL. Requests mentioning shell execution or an
    external integration (Instagram, Facebook, Gmail, Stripe, etc.) are
    always reported as blocked, regardless of wording - this stage of
    JARVIS does not support them. A fresh TaskPlanner is created per
    invocation, so 'jarvis task' always reports Task #1 - task ids only
    stay sequential within a single, longer-lived TaskPlanner instance
    (e.g. a future REPL session), not across separate CLI invocations.
    """
    task = TaskPlanner().plan(description)
    print(format_task(task))


def run_cli_run(description: str) -> None:
    """'jarvis run "<description>"' entry point: the Task Execution
    layer's controlled execution stage. Builds a Task exactly like
    'jarvis task' (via TaskPlanner), then - only if the Task isn't
    already blocked - hands it to TaskRunner (jarvis.core.task_runner)
    to advance step by step. No API key required (no LLM call is made).

    Unlike every other CLI argument in this file, this command CAN pause
    for interactive stdin input: TaskRunner's default approval function
    is jarvis.core.approval.confirm_side_effect, the same y/N prompt
    every other real side effect in JARVIS already uses - one prompt per
    step that requires approval, before that step is ever attempted.
    There is no flag to skip this in this stage; a denial stops the run
    immediately (see TaskRunner's own docstring for the full state
    machine).

    'jarvis run' still performs NO real action of any kind: the only
    executor TaskRunner is given here is the default DryRunExecutor,
    which never writes a file, never runs git or a shell command, and
    never calls an external service - it only reports what it *would*
    have done. Requests mentioning shell execution or an external
    integration (Instagram, Facebook, Gmail, Stripe, etc.), or that
    can't be confidently classified, are reported as blocked - by
    TaskPlanner, before TaskRunner is even invoked - exactly as 'jarvis
    task' already reports them.
    """
    task = TaskPlanner().plan(description)
    if task.status == "blocked":
        # Nothing for the Runner to do - and nothing for it to ask
        # approval for. Show the same plan/explanation 'jarvis task'
        # would, so the user sees exactly why, without an extra prompt.
        print(format_task(task))
        return

    run = TaskRunner().run(task)
    print(format_task_run(run))


def run_cli_review() -> None:
    """Non-interactive 'jarvis review' entry point: runs the same
    deterministic scan_project() -> build_plan() pipeline used by 'jarvis
    plan'/'jarvis work', then derives a structured pre-commit review from
    them (jarvis.core.project_review.build_review) and prints it - no
    REPL, no LLM call, no API key required, and no project files or git
    history are modified. Distinct from the REPL 'review' keyword (git
    status + diff via jarvis.core.review_view.format_review), which is
    unchanged and still available; this is the structured, scan/plan-aware
    counterpart answering findings/concerns/checks/commit-readiness.
    """
    scan_result = scan_project(JARVIS_ROOT)
    plan = build_plan(scan_result)
    review = build_review(scan_result, plan)
    print(format_review_result(review))


def run_cli_precommit() -> None:
    """Non-interactive 'jarvis precommit' entry point: runs the same
    deterministic scan_project() -> build_plan() -> build_review()
    pipeline used by 'jarvis review', then derives the final,
    structured pre-commit check from it (jarvis.core.project_precommit
    .build_precommit_check) and prints it - no REPL, no LLM call, no API
    key required, and no project files or git history are modified; the
    test suite is not run. Distinct from the REPL 'precommit' keyword
    (jarvis.core.precommit_view.format_precommit), which additionally runs
    the real test suite via subprocess - that behavior is unchanged and
    still available. This CLI counterpart stays execution-free: it only
    reads the already-computed review plus a fresh, read-only listing of
    changed file names, and never runs 'git commit' or any git-mutating
    command itself.
    """
    scan_result = scan_project(JARVIS_ROOT)
    plan = build_plan(scan_result)
    review = build_review(scan_result, plan)
    check = build_precommit_check(review)
    print(format_precommit_check(check))


def _build_current_commit_plan():
    """Shared pipeline for both 'jarvis commit' and the REPL 'commit'
    command: scan_project() -> build_plan() -> build_review() ->
    build_precommit_check() -> build_commit_plan(). Read-only - no file
    writes, no git mutations, no LLM call."""
    scan_result = scan_project(JARVIS_ROOT)
    plan = build_plan(scan_result)
    review = build_review(scan_result, plan)
    check = build_precommit_check(review)
    return build_commit_plan(check)


def _try_suggest_commit_message(staged_files: list[str]) -> str | None:
    """Best-effort LLM commit-message suggestion for the stdin prompt.
    Returns None (never raises) if there's no API key configured, or if
    the suggestion call itself fails for any reason - the caller falls
    back to a plain, unprompted stdin question exactly as before this
    feature existed. This never requires the API key for 'jarvis commit'
    as a whole; it's an optional enhancement layered on top of a flow
    that works perfectly well without it.
    """
    if not ANTHROPIC_API_KEY:
        return None
    try:
        llm = LLMClient()
    except RuntimeError:
        return None
    diff = get_staged_diff()
    return suggest_commit_message(llm, staged_files, diff)


def _prompt_for_commit_message(staged_files: list[str]) -> str | None:
    """Ask the user for a commit message on stdin, offering an LLM
    suggestion first when available. Returns None (never a blank string)
    if the user enters nothing and no suggestion was offered, so the
    caller can abort without attempting a commit with an empty message.

    If a suggestion is shown, pressing Enter with no other input accepts
    it - the suggestion is advisory text, never applied automatically;
    the user still must either accept it (Enter) or type their own
    message. Without a suggestion, the prompt behaves exactly as before:
    a bare Enter aborts.
    """
    suggestion = _try_suggest_commit_message(staged_files)
    if suggestion:
        print(f"Suggested commit message: {suggestion}")
        prompt_text = "Commit message [Enter to accept suggestion]: "
    else:
        prompt_text = "Commit message: "

    try:
        message = input(prompt_text).strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None

    if message:
        return message
    return suggestion or None


def _run_commit_flow(message: str | None) -> None:
    """The single execution path for actually running 'git commit',
    shared by 'jarvis commit' (CLI) and the REPL 'commit' command.

    Safety properties, all enforced here rather than just documented:
      - Never runs 'git add' - only files already staged are committed.
      - Stops before any prompt if there's no git repo, the pre-commit
        check didn't pass, or nothing is staged.
      - Shows exactly what would be committed (staged files) and what
        would NOT be committed (unstaged files) before asking anything.
      - The actual 'git commit' argv is built by jarvis.tools.shell
        ._git_commit(), the same validated, allowlisted builder the
        approval-gated agent tool uses - no second, looser way to shape
        the command.
      - Requires an explicit 'y' via confirm_side_effect() (defaults to
        No on anything else, including a bare Enter) before running
        'git commit'. The commit message is shown in the confirmation
        prompt itself, not just typed blind.
      - A KeyboardInterrupt during the confirmation prompt propagates
        (via confirm_side_effect) rather than being treated as approval.
      - If no message is given via -m, the stdin prompt may offer an
        LLM-suggested message (jarvis.core.commit_message) as a starting
        point - purely advisory text the user can accept (Enter) or
        overwrite. Suggesting a message never commits anything by itself;
        the same y/N confirm_side_effect() prompt still follows regardless
        of whether the message came from a suggestion or was typed by
        hand. If no API key is configured or the suggestion call fails,
        this falls back silently to the original plain prompt.
    """
    plan = _build_current_commit_plan()
    print(format_commit_plan(plan))
    print()

    if not plan.can_commit:
        return

    if message is None:
        message = _prompt_for_commit_message(plan.staged_files)
    if message is None:
        print("[JARVIS] No commit message given - aborting.\n")
        return

    try:
        argv = _git_commit(["-m", message])
    except ValueError as e:
        print(f"[JARVIS] Refusing to commit: {e}\n")
        return

    description = (
        f"commit {len(plan.staged_files)} staged file(s) with message: {message!r}"
    )
    if not confirm_side_effect(description):
        print("[JARVIS] Commit cancelled.\n")
        return

    try:
        completed = subprocess.run(
            argv,
            shell=False,
            cwd=JARVIS_ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        print("[JARVIS] git commit timed out.\n")
        return
    except OSError as e:
        print(f"[JARVIS] Failed to run git commit: {e}\n")
        return

    if completed.returncode == 0:
        print(f"[JARVIS] Committed.\n{completed.stdout.strip()}\n")
    else:
        print(f"[JARVIS] git commit failed:\n{(completed.stderr or completed.stdout).strip()}\n")


def run_cli_commit() -> None:
    """Non-interactive-first 'jarvis commit' entry point: builds the same
    scan -> plan -> review -> precommit -> commit-plan pipeline as the
    other CLI arguments, shows exactly what would be committed, and - only
    if a pre-commit check passes and files are staged - asks for a commit
    message (via '-m' or, if omitted, an interactive stdin prompt) and a
    final y/N confirmation before running 'git commit'. No API key or REPL
    required. Supports 'jarvis commit -m "message"' to skip the message
    prompt; the y/N confirmation is never skipped.
    """
    message = None
    if len(sys.argv) > 3 and sys.argv[2] == "-m":
        message = sys.argv[3]
    _run_commit_flow(message)


def run_cli_voice() -> None:
    """'jarvis voice' entry point: sets up the same LLMClient/Agent/tool
    registry/session history the interactive REPL uses (see main()
    below), then hands control to jarvis.voice.voice_loop.run_voice_mode
    instead of the typed input() loop. Needs ANTHROPIC_API_KEY exactly
    like the normal REPL does (a real Agent.step() call is made per
    voice turn) - unlike the read-only 'scan'/'plan'/... CLI arguments,
    this is not a lighter-weight, API-key-free path. Ends (returns to
    the shell) when the person says a stop phrase or interrupts with
    Ctrl+C inside run_voice_mode - never exits the process itself, so
    normal shell semantics (echo $?, chaining, etc.) work as expected.
    """
    print(f"JARVIS v0.1 - sandboxed to: {JARVIS_ROOT}")
    print(f"API key: {mask_secret(ANTHROPIC_API_KEY)}")

    run_startup_checks()

    notes_result = load_project_notes(JARVIS_ROOT)
    if notes_result.notice:
        print(f"[notice] {notes_result.notice}")

    llm = LLMClient(project_notes=notes_result.content)
    registry = build_registry()
    agent = Agent(llm, registry)

    load_result = load_history()
    if load_result.warning:
        print(f"[warning] {load_result.warning}")
    history = load_result.history

    run_voice_mode(agent, history)


SCAN_PROMPT = (
    "Survey this project so you can understand what it is. List the top-level "
    "structure, and read whatever conventionally-meaningful files actually exist "
    "(e.g. README, package.json, pyproject.toml, requirements.txt, .gitignore) - "
    "don't invent or assume files that aren't there. Check whether it's a git "
    "repository and, if so, get a sense of recent history. Then propose a draft "
    "JARVIS.md: a short summary of what the project is, its language/framework/"
    "conventions, and anything notable (e.g. no tests found, a large legacy/ "
    "directory, etc). Show me the draft in your response - do not write it to "
    "JARVIS.md or any file yet. I'll review it and ask you to save it if I want to "
    "keep it."
)


def _run_agent_turn(
    agent: Agent,
    history: list[dict],
    user_input: str,
) -> None:
    """Shared path for both normal conversational turns and the 'scan'
    command's canned prompt, so both get identical interrupt/error
    handling and history persistence - no duplicated logic to drift."""
    try:
        reply = agent.step(history, user_input)
    except StepInterrupted:
        print("\n[JARVIS] Turn cancelled. No pending approval was auto-accepted.\n")
        save_history(history)
        return
    except Exception as e:
        print(f"[error] {redact_secret(str(e))}")
        return

    print(f"jarvis> {reply}\n")
    save_history(history)


def _ensure_utf8_stdio() -> None:
    """Some Windows terminals report a legacy codepage (e.g. cp1252) as
    stdout's encoding, which raises UnicodeEncodeError the first time a
    model response contains a character outside that codepage (emoji,
    smart quotes, etc.) - crashing an otherwise-fine session. Reconfigure
    to UTF-8 with substitution for anything still unencodable, so output
    is never fatal. Not all stream objects support reconfigure() (e.g.
    when stdout is replaced in tests), so this is best-effort.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main() -> None:
    _ensure_utf8_stdio()

    # 'jarvis scan' / 'jarvis plan' / 'jarvis work' / 'jarvis task "<...>"' /
    # 'jarvis review' / 'jarvis precommit' / 'jarvis integrations' (real CLI
    # arguments, not REPL keywords) run their deterministic, non-interactive
    # counterparts and exit immediately - no REPL, no API key needed, so all
    # seven work even before ANTHROPIC_API_KEY is configured. 'jarvis run
    # "<...>"' is the one exception among these eight: still no API key
    # needed, but it CAN pause for interactive y/N approval prompts (see
    # run_cli_run()). Any other/no argument falls through to the normal
    # interactive REPL, unchanged from before this was added.
    if len(sys.argv) > 1 and sys.argv[1] == "integrations":
        run_cli_integrations()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "scan":
        run_cli_scan()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "plan":
        run_cli_plan()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "work":
        run_cli_work()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "task":
        if len(sys.argv) <= 2 or not sys.argv[2].strip():
            print('Usage: jarvis task "<description of what you want done>"')
            return
        run_cli_task(sys.argv[2])
        return
    # 'jarvis run "<...>"' is the one CLI argument above that is NOT purely
    # read-only in the way scan/plan/work/task are: it can pause for
    # interactive y/N approval prompts (one per step) via TaskRunner. It
    # still performs no real action itself (DryRunExecutor only) and
    # requires no API key - see run_cli_run()'s own docstring.
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        if len(sys.argv) <= 2 or not sys.argv[2].strip():
            print('Usage: jarvis run "<description of what you want done>"')
            return
        run_cli_run(sys.argv[2])
        return
    if len(sys.argv) > 1 and sys.argv[1] == "review":
        run_cli_review()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "precommit":
        run_cli_precommit()
        return
    # 'jarvis commit' is the one CLI argument that is NOT read-only: it can
    # run 'git commit' on already-staged changes. It still needs no API
    # key/REPL, but unlike the five above it may prompt on stdin for a
    # commit message and always asks for an explicit y/N confirmation
    # before running git - see run_cli_commit()/_run_commit_flow().
    if len(sys.argv) > 1 and sys.argv[1] == "commit":
        run_cli_commit()
        return
    # 'jarvis voice' is the one CLI argument that starts a full
    # microphone-in/speaker-out conversational session instead of running
    # one deterministic pipeline and exiting - see run_cli_voice()'s own
    # docstring. It needs ANTHROPIC_API_KEY like the normal REPL does.
    if len(sys.argv) > 1 and sys.argv[1] == "voice":
        run_cli_voice()
        return

    print(f"JARVIS v0.1 - sandboxed to: {JARVIS_ROOT}")
    print(f"API key: {mask_secret(ANTHROPIC_API_KEY)}")
    print("Type 'help' to get started, or 'exit' to quit.\n")

    run_startup_checks()

    notes_result = load_project_notes(JARVIS_ROOT)
    if notes_result.notice:
        print(f"[notice] {notes_result.notice}")

    llm = LLMClient(project_notes=notes_result.content)
    registry = build_registry()
    agent = Agent(llm, registry)

    load_result = load_history()
    if load_result.warning:
        print(f"[warning] {load_result.warning}")
    history = load_result.history

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit"}:
            break
        if user_input.lower() == "help":
            _handle_help_command()
            continue
        if user_input.lower() == "status":
            _handle_status_command()
            continue
        if user_input.lower() == "integrations":
            _handle_integrations_command()
            continue
        if user_input.lower() == "history" or user_input.lower().startswith("history "):
            _handle_history_command(user_input)
            continue
        if user_input.lower() == "tasks":
            _handle_tasks_command()
            continue
        if user_input.lower() == "review":
            _handle_review_command()
            continue
        if user_input.lower() == "precommit":
            _handle_precommit_command()
            continue
        if user_input.lower() == "commit":
            _run_commit_flow(None)
            continue
        if user_input.lower() == "scan":
            _run_agent_turn(agent, history, SCAN_PROMPT)
            continue
        if user_input.lower() == "voice":
            # Hands control to the listen -> Agent.step() -> speak loop;
            # returns here (to the normal typed REPL) once the person
            # says a stop phrase or interrupts with Ctrl+C inside it -
            # see jarvis.voice.voice_loop.run_voice_mode's own docstring.
            run_voice_mode(agent, history)
            continue
        if user_input.lower() == "morning":
            # Manual trigger for the same briefing the "JARVIS Morning
            # Briefing" scheduled task runs unattended - lets the person
            # test/replay it on demand from the normal REPL. See
            # jarvis.morning_routine.run_morning_routine's own docstring.
            run_morning_routine(agent, history)
            continue
        if user_input.lower().startswith(_COMMAND_CONSOLE_PREFIX):
            _handle_command_console_command(user_input)
            continue

        _run_agent_turn(agent, history, user_input)


if __name__ == "__main__":
    main()
