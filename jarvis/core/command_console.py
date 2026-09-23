"""The natural-language command console: a single entry point that hands
a free-text user command to the already-existing Task Execution chain -
TaskPlanner -> Task -> TaskRunner -> approval -> (dry-run) executor - and
returns the formatted result.

This module adds NO new reasoning, NO new safety rule, and NO new
capability of its own. It is purely an orchestration/presentation layer
on top of jarvis.core.task_planner.TaskPlanner and jarvis.core
.task_runner.TaskRunner, exactly mirroring what jarvis.cli.main
.run_cli_run() already does for the 'jarvis run "<description>"' CLI
argument - this module exists so the same chain can also be reached from
inside the interactive REPL (via the 'komanda:' prefix - see
jarvis.cli.main), without duplicating that wiring in two places.

Unchanged by this module (imported and used exactly as-is):
  - jarvis.core.task_planner.TaskPlanner - classification + planning.
  - jarvis.core.task_runner.TaskRunner - the approval-gated state
    machine, and its default DryRunExecutor (no real action of any
    kind).
  - jarvis.core.approval.confirm_side_effect - the same y/N prompt every
    other real side effect in JARVIS already uses (TaskRunner's default
    approve_fn already wires this in - handle_command only forwards a
    caller-supplied approve_fn through, never replaces it with anything
    looser).
"""

from __future__ import annotations

from collections.abc import Callable

from jarvis.core.task import BLOCKED
from jarvis.core.task_execution_plan import ExecutionStep
from jarvis.core.task_planner import TaskPlanner
from jarvis.core.task_runner import TaskRunner
from jarvis.core.task_runner_view import format_task_run
from jarvis.core.task_view import format_task

ApproveFn = Callable[[ExecutionStep], bool]


def handle_command(text: str, approve_fn: ApproveFn | None = None) -> str:
    """Plan and (unless blocked) run a free-text command through the
    existing TaskPlanner -> TaskRunner chain, returning formatted output
    ready to print.

    `approve_fn` is forwarded to TaskRunner as-is; when None, TaskRunner
    uses its own default (jarvis.core.approval.confirm_side_effect - a
    real interactive y/N prompt). Passing a custom approve_fn is only
    meant for tests/non-interactive callers - it grants no new
    capability, since a step is only ever attempted if approve_fn
    returns True, exactly as TaskRunner already enforces on its own.

    A fresh TaskPlanner and TaskRunner are created per call, mirroring
    run_cli_run()'s behavior - task ids are only sequential within one
    call's TaskPlanner instance.
    """
    task = TaskPlanner().plan(text)

    if task.status == BLOCKED:
        # Nothing for the Runner to do, and nothing to ask approval for -
        # same short-circuit run_cli_run() already applies for 'jarvis
        # run'. Show the same plan/explanation 'komanda:'/'jarvis task'
        # would, without ever invoking TaskRunner.
        return format_task(task)

    run = TaskRunner(approve_fn=approve_fn).run(task)
    return format_task_run(run)
