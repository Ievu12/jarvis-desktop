# JARVIS v0.1

A sandboxed automation agent, built on the Claude API. JARVIS operates only inside
its own project directory by default, requires explicit approval for anything
outside that boundary or with side effects, and never runs shell commands (that
capability is disabled in v0.1).

## Setup

Requires Python 3.11+.

```
py -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
```

### API key

JARVIS reads `ANTHROPIC_API_KEY` from the environment only. It is never read from
a file, never hardcoded, and never written to disk, logs, or the terminal in full.

Set it before running JARVIS, using whichever fits your workflow:

```powershell
# Current session only
$env:ANTHROPIC_API_KEY = "your-key-here"

# Persistent (Windows user environment variable; open a new terminal after)
setx ANTHROPIC_API_KEY "your-key-here"
```

The CLI startup banner shows a masked confirmation (e.g. `****...a1b2`) that the
key loaded — never the full value.

### Email (optional)

To enable the four read-only email tools (see
[External integrations](#external-integrations)), set these before
running JARVIS — otherwise the tools just report "not configured" and
nothing else changes:

```powershell
$env:EMAIL_IMAP_HOST = "imap.example.com"
$env:EMAIL_ADDRESS = "you@example.com"
$env:EMAIL_PASSWORD = "an-app-specific-password"
```

Like `ANTHROPIC_API_KEY`, these are read only from the environment — never
written to disk, logged, or requested by JARVIS through a prompt.

### Stripe (optional)

To enable the four read-only Stripe tools (see
[External integrations](#external-integrations)), set this before running
JARVIS — otherwise the tools just report "not configured" and nothing
else changes:

```powershell
$env:STRIPE_API_KEY = "sk_test_..."   # or a restricted, read-only-scoped key
```

Use a Stripe **test mode** key (`sk_test_...`) for development — every
request automatically goes to Stripe's test environment, never touching
real money; Stripe itself routes based on the key's prefix, so no JARVIS
setting or code path needs to change between test and live. A
**restricted key** scoped to read-only access is recommended over a full
secret key for the same reason a scoped email app-password is preferred
over a full account password: JARVIS's own code never issues a write
request, but a narrower key limits the blast radius of any future mistake
to "can only read," not just "shouldn't write."

Like `ANTHROPIC_API_KEY`, this is read only from the environment — never
written to disk, logged, or requested by JARVIS through a prompt. Never
paste a Stripe key into a chat message with JARVIS; it wouldn't be used
even if you did, since every Stripe tool reads the key from the
environment itself, not from anything you type.

## Running JARVIS

```
jarvis
```

(or `python -m jarvis.cli.main` if you didn't install the package)

This starts an interactive REPL:

```
JARVIS v0.1 - sandboxed to: C:\Users\you\Desktop\JARVIS
API key: ****...a1b2
Type 'help' to get started, or 'exit' to quit.

you> help
```

Type `help` for a full orientation: what JARVIS can do, every CLI command,
a few example requests to try, and pointers to the planning workflow and
`JARVIS.md`. This is the fastest way to get oriented in a new session
rather than reading the rest of this README.

Type `status` any time for a one-glance view of where you are in the
typical workflow — **scan** (understand the project) → **plan** (JARVIS
lays out steps for multi-step work) → do the work (approval-gated as it
goes) → **review/precommit** (check everything before committing) →
**commit**. It combines whether `JARVIS.md` exists, the current plan's
progress, and a one-line uncommitted-changes summary into a single view,
with a suggested next step — e.g. "type `scan`" if there's no project
context yet, "N step(s) remaining" if a plan is in progress, or "type
`review` or `precommit`" once a plan is done and there are uncommitted
changes. Purely read-only, reusing the same checks `scan`/`tasks`/`review`
already make — nothing new is written or tracked.

Type `exit` or `quit` to leave, or press Ctrl+C at any point — mid-response,
mid-approval-prompt, or at the input line — to cancel cleanly without corrupting
session state or accidentally approving a pending action.

Type `history` (or `history N` for a specific count, default 20) to see a
readable summary of recent approval/denial decisions — a formatted view over
`.jarvis/audit.log`. This log is cumulative for the whole project, not scoped
to the current process, and the output says so.

Type `tasks` to see JARVIS's current working task list — a persistent,
local to-do list (`.jarvis/tasks.json`) the model uses to track its own
progress on a multi-step request, so a plan survives across turns (and
across resuming the session later) instead of being re-derived from
scratch each time.

For requests with three or more distinguishable steps, JARVIS is
instructed to call `create_plan` once at the start — a single atomic
action that lays out the whole plan before any work begins, distinct from
the individual `manage_tasks` add/complete calls used to execute and track
progress against it afterward. This is a strong system-prompt nudge, not a
hard guarantee: for small, single-step requests JARVIS skips it and just
does the work directly. Check `tasks` any time to see the live plan.

Type `review` to see a combined summary of everything currently uncommitted
in the project — `git status --short` plus the actual diff content, in one
view. This is a human-facing convenience (no approval needed, purely
read-only) for reviewing the cumulative effect of a multi-step task before
deciding to commit it, rather than having to remember to run `git diff`
yourself or approve each change blind to how it fits with the others.

Type `precommit` for the same diff summary as `review`, followed by a real
run of the project's test suite and a clear PASSED/FAILED summary — the
single checkpoint that answers both "what changed" and "does it still
work" before you commit. Reuses the exact same `pytest` invocation the
model's `run_command` tool would use (`sys.executable -m pytest`), so
there's no second, divergent way tests get run. Like `review`, it's a
human-facing convenience with no approval prompt; note that a project's
own test suite can have its own side effects (temp files, a local
database, etc.) if its tests do that — the same trust boundary already
implicit in letting the model run `pytest` at all, not a new one.

## Project notes (`JARVIS.md`)

If a `JARVIS.md` file exists at the project root, JARVIS loads it
automatically at startup and gives the model that content as background
context (what the project is, conventions, standing preferences) for the
whole session — a visible `[notice]` confirms when this happens. This is
long-term project memory, complementing `manage_tasks`' short-term
session-plan memory: `JARVIS.md` survives across every future session,
the same way a `CLAUDE.md`/`AGENTS.md`-style file works in other coding
agents.

`JARVIS.md` is never created automatically — JARVIS won't invent one
unprompted. Create it yourself, or ask JARVIS to, using the normal file
tools (`write_file`, etc.) like any other project file. It's capped at
20,000 characters (longer files are truncated, not rejected) and must be
valid UTF-8 text; both cases are reported via the startup notice, never
silently.

Not sure where to start writing one? Type `scan` inside the REPL and
JARVIS will survey the project — list the top-level structure, read
whatever conventionally-meaningful files actually exist (README,
`package.json`, `pyproject.toml`, `requirements.txt`, `.gitignore`, etc. —
never inventing ones that aren't there), and check the git history if
present — then propose a `JARVIS.md` draft in the conversation for you to
review. **It is never written automatically**: the draft only becomes a
real file if you explicitly approve the `write_file` call afterward, the
same approval gate as any other file write. `scan` itself is entirely
read-only (listing directories and reading files, plus read-only git
inspection commands like `git-status`/`git-log`, which still require
their own approval like any shell command).

### `jarvis scan` — a non-interactive, structured scan (no API key needed)

Separately from the REPL's `scan` keyword above (which is conversational
and LLM-driven), running `jarvis scan` as a command-line argument performs
a **deterministic, non-interactive** project scan and exits — no REPL, no
LLM call, and no `ANTHROPIC_API_KEY` required:

```
jarvis scan
```

It reports, purely by reading the filesystem and running read-only git
commands (`git log --oneline`, `git status --short`): the top-level
directory listing, which README/manifest files (`pyproject.toml`,
`package.json`, `requirements.txt`, etc.) actually exist, whether
`.gitignore`/`JARVIS.md` are present, whether tests were found, and (if
it's a git repository) the commit count, latest commit summary, and
whether there are uncommitted changes. Nothing is ever written — it's
read-only in the same sense `review`/`status` are.

The underlying scan (`jarvis.core.project_scan.scan_project`) returns a
plain, structured `ProjectScanResult` object — separate from its
text-formatting (`jarvis.core.project_scan_view.format_scan_result`) — so
other consumers, including `jarvis plan` below, can use the scan's
findings programmatically instead of re-parsing printed text.

### `jarvis plan` — a structured development plan derived from the scan

```
jarvis plan
```

Like `jarvis scan`, this is non-interactive, deterministic, requires no
`ANTHROPIC_API_KEY`, and never modifies the project. It reuses the exact
same `scan_project()` used by `jarvis scan` — `plan` never inspects the
filesystem or runs git on its own; it only reasons about the scan result
already computed, so scan and plan stay modular (scan owns detection,
plan owns turning those findings into recommendations).

The output has four sections:

1. **Current state** — a plain-language summary of what the scan found
   (entry count, README/manifest/test presence, git status).
2. **Findings** — notable gaps or facts (reuses `scan`'s own notes, plus
   `JARVIS.md` and uncommitted-changes context).
3. **Recommended next steps** — concrete, rule-based suggestions for
   anything missing (initialize git, add a README/manifest/tests, create
   `JARVIS.md`, review uncommitted changes) — or a "no gaps detected"
   message if the project already has all of these.
4. **Suggested order of work** — the same recommended steps, ordered by a
   fixed priority (foundational setup like git/manifest first, then
   documentation/tests, then JARVIS-specific conveniences, then
   review/commit hygiene last) — deterministic, not an LLM judgment call.

Every project state is handled gracefully: no git repository, a git
repository with zero commits, one with existing commits and either a
clean or dirty working tree, and any combination of missing
README/manifest/tests/`JARVIS.md`.

### `jarvis work` — the single next step, and exactly how to do it

```
jarvis work
```

Like `jarvis scan` and `jarvis plan`, this is non-interactive,
deterministic, requires no `ANTHROPIC_API_KEY`, and never modifies the
project. It reuses the same `scan_project()` → `build_plan()` pipeline as
`jarvis plan`, then picks the single first item off the plan's
`suggested_order` and describes exactly how to act on it:

- If the step is purely mechanical and has one fixed, safe, no-judgment
  command (currently: initializing git), it prints that **exact command**
  to run yourself, e.g. `git init`.
- If the step needs judgment or generated content (writing a README,
  choosing what to commit, adding tests, drafting `JARVIS.md`, reviewing
  changes), it prints a **ready-to-use prompt** to paste into the
  interactive `jarvis` REPL, so JARVIS's LLM-driven agent does the work
  under the normal approval gate — the same one every other file write or
  git mutation already goes through.

**`jarvis work` itself never writes a file, never runs git, and never
calls the LLM.** v1 is deliberately execution-free: it only identifies and
clearly describes the next step. This keeps it as safe and auditable as
`scan`/`plan`, while still pointing you at a concrete, actionable next
move instead of just a list of recommendations.

If there's no plan data yet, it says so and points you to `jarvis plan`.
If the last plan found no gaps, it says there's nothing to do. Otherwise
it also notes how many more steps remain after the one it's showing.

### `jarvis task "<description>"` — analyze a request and propose a safe plan

```
jarvis task "write a README describing the project"
```

The first piece of JARVIS's **Task Execution layer**: given a free-text
description of something you want done, this is non-interactive,
deterministic, requires no `ANTHROPIC_API_KEY`, and never modifies the
project. `run_cli_task()` hands the description to `TaskPlanner`
(`jarvis.core.task_planner`), which builds a `Task`
(`jarvis.core.task`) — the layer's top-level model:

```
Task
  id                 - sequential, assigned by the TaskPlanner instance
  description        - the original request, verbatim
  status             - "blocked" or "pending_approval"
  plan               - the full ExecutionPlan (steps, risk, block reason)
  risk_level         - highest risk level across every step in the plan
  requires_approval  - True if any step needs human approval
```

Internally, `TaskPlanner.plan()` classifies the request
(`jarvis.core.task_intent.parse_task_intent` — rule-based, no LLM call),
reuses the same `scan_project()` → `build_plan()` pipeline as `jarvis
plan`/`jarvis work`, and derives a structured, **unexecuted**
`ExecutionPlan` (`jarvis.core.task_execution_plan.build_execution_plan`)
describing what would need to happen — then summarizes it into the `Task`
above:

- **Supported categories** (`git_operation`, `test_run`, `file_change`)
  produce status `pending_approval`: a list of proposed steps, each
  labelled with a risk level (`read_only` / `reversible` /
  `irreversible`) and a note that it requires approval in the JARVIS
  REPL — never a command it just runs. Every step in this stage requires
  approval, so `requires_approval` is always `True` here; any step whose
  wording implies a side effect (write, delete, commit, push, ...) is
  classified by `jarvis.core.task_safety.classify_risk` and never
  silently downgraded to "safe to auto-run".
- **Unsupported requests always produce status `blocked`, with a
  reason**: anything mentioning an external integration (Instagram,
  Facebook, Gmail, Stripe, and others — see `jarvis.core.task_safety
  .BLOCKED_EXTERNAL_SERVICES`) or shell/terminal command execution is
  rejected by fixed, deterministic keyword rules — not by asking the LLM
  to behave — so this is enforced in code, the same way `jarvis.tools
  .shell`'s allowlist enforces the shell tool's own boundaries. A request
  JARVIS can't confidently classify is also reported as blocked, with
  guidance to be more specific or ask directly in the REPL.

**`jarvis task` itself never writes a file, never runs git or shell
commands, and never calls the LLM.** Like `jarvis work`, this stage is
deliberately execution-free: it only analyzes the request, builds a
`Task`, and describes what a human (or the approval-gated REPL agent)
would still need to do. There is no "in progress"/"done" status yet and
no task store — a `Task`'s id is only sequential within one `TaskPlanner`
instance (each `jarvis task` invocation creates a fresh one, so the CLI
always reports `Task #1`). No shell execution and no external-service
integration (Instagram, Facebook, Gmail, Stripe, or otherwise) is enabled
by this or any other command yet.

### `jarvis run "<description>"` — controlled, approval-gated progression through a plan

```
jarvis run "write a README describing the project"
```

The Task Execution layer's execution stage. `TaskRunner`
(`jarvis.core.task_runner`) takes an already-planned `Task` (built via
`TaskPlanner`, exactly like `jarvis task`) and advances its
`ExecutionPlan` **one step at a time**, tracking each step's live
progress as a `RunStep` (`jarvis.core.task_run_state`) through a fixed
state machine:

```
pending -> approved -> running -> completed
                                -> failed
        -> blocked   (denied, pre-blocked by the planning layer, or a
                       prior step already failed/was blocked)
```

Unlike `jarvis scan`/`plan`/`work`/`task`, this command **can pause for
interactive input**: for every step that requires approval, it prints
the same `[JARVIS] About to: ... Proceed? [y/N]` prompt every other real
side effect in JARVIS already uses, and waits on stdin before continuing.
There is no flag to skip this in this stage — a denial stops the run
immediately. If the `Task` is already `blocked` (an unsupported request —
same rule as `jarvis task`), `jarvis run` shows the same plan/explanation
and never even reaches `TaskRunner`, so no prompt appears for a task that
was never going to do anything.

```python
from jarvis.core.task_planner import TaskPlanner
from jarvis.core.task_runner import TaskRunner
from jarvis.core.task_runner_view import format_task_run

task = TaskPlanner().plan("write a README describing the project")
run = TaskRunner().run(task)   # prompts for approval on each step, same as any other side effect
print(format_task_run(run))
```

Safety properties, enforced in code:

- **Steps run strictly one at a time, in plan order** — never in
  parallel, never out of order.
- **A step is never executed before an explicit approval decision** when
  `ExecutionStep.requires_approval` is `True`. The default approval
  function is the exact same `jarvis.core.approval.confirm_side_effect`
  prompt every other real side effect in JARVIS already goes through — no
  separate, looser approval path exists for the Task Runner.
- **A denial stops the whole run immediately** — the step is marked
  `blocked` and no later step is ever started. There is no "skip and
  continue" option in this stage.
- **A step the planning layer already marked `blocked`** (external
  integration, shell execution, an unrecognized request) never reaches
  the approval prompt or the executor at all — it goes straight to
  `blocked` and stops the run.
- **The only executor wired in is `DryRunExecutor`** — it performs no
  real action of any kind (no file write, no git command, no shell
  command, no network call); it only reports what it *would* have done.
  This keeps the Task Runner's state machine fully testable without
  granting any new real capability. Swapping in a real executor is a
  separate, deliberate future decision — not something this core does on
  its own.
- **Every state transition is audited** via the same append-only
  `jarvis.core.audit.log_event` used by every other approval-gated
  action: `task_run_started`, `task_step_approval`, `task_step_running`,
  `task_step_completed` / `task_step_failed` / `task_step_blocked`, and
  `task_run_finished`.
- No shell execution and no external-service integration (Instagram,
  Facebook, Gmail, Stripe, or otherwise) is enabled by the Task Runner —
  it inherits exactly the same restrictions `jarvis task` already
  enforces at the planning stage, and adds its own independent approval
  gate on top before anything is even attempted.

### `komanda: <text>` — natural-language commands inside the REPL (Lithuanian-friendly)

```
you> komanda: parašyk README failą aprašantį projektą
```

A REPL-only prefix that routes free text through the exact same Task
Execution chain as `jarvis run` — `TaskPlanner` → `Task` → `TaskRunner` →
approval → `DryRunExecutor` — via a single orchestration function,
`jarvis.core.command_console.handle_command`. It exists so that chain is
also reachable from inside an interactive session, without leaving the
REPL or typing a separate CLI argument, and it is entirely separate from
the free-form LLM agent conversation the rest of the REPL uses: text
after `komanda:` is never sent to the LLM.

`parse_task_intent` (classification) and `classify_risk` (risk labelling)
recognize both English and Lithuanian phrasings for every category and
risk keyword — e.g. `ištrink`/`pašalinti` (delete/remove, irreversible),
`parašyk`/`sukurk`/`pridėk` (write/create/add, reversible), `commit'ą`/
`šaką` (commit/branch, git operation), `testus` (tests, test run),
`paleisk komandą` (run a command, blocked shell execution) — the same
fixed, deterministic keyword rules in `jarvis.core.task_safety` and
`jarvis.core.task_intent` that already drive `jarvis task`/`jarvis run`,
just extended with Lithuanian entries alongside the English ones. A
request mentioning an external service (Instagram, Facebook, Gmail,
Stripe, and others) is blocked the same way regardless of which language
it's phrased in.

`handle_command` adds **no new reasoning, no new safety rule, and no new
capability** — it is pure orchestration on top of `TaskPlanner`/
`TaskRunner`, which are used completely unchanged: the exact same
`confirm_side_effect` y/N approval prompt, the exact same `DryRunExecutor`
(no real action of any kind), and the exact same audit log entries. A
blocked request (external integration, shell execution, unrecognized)
never reaches `TaskRunner` and never prompts for approval — it just shows
the same explanation `jarvis task` would.

### `jarvis review` — a structured pre-commit readiness check

```
jarvis review
```

Like `jarvis scan`/`plan`/`work`, this is non-interactive, deterministic,
requires no `ANTHROPIC_API_KEY`, and never modifies the project or git
history. It reuses the same `scan_project()` → `build_plan()` pipeline as
`jarvis plan`/`jarvis work`, then combines that with one fresh, read-only
git status check to answer: what changed, what might be a problem, what
to check before committing, and whether the project is ready to move on
to the commit step.

This is distinct from the REPL's `review` keyword (still unchanged —
`git status` + a full diff, for when you just want to see the raw
change). `jarvis review` instead produces a structured verdict in four
sections:

1. **Findings** — the current git state (clean/dirty working tree, file
   count, or "not a repository yet"), plus the latest plan's next
   recommended step for context.
2. **Possible concerns** — things worth double-checking: no git history,
   no test suite, no commits yet, or a git error.
3. **What to check** — concrete suggestions, e.g. run `review` for the
   full diff, run `precommit` to execute the test suite, or add a test if
   none exist.
4. **Ready for commit** — a yes/no verdict with a one-line reason (e.g.
   "nothing to commit — the working tree is already clean", or "changes
   are present and a test suite exists — run `precommit` to verify").

**`jarvis review` itself never writes a file, never mutates git, and
never calls the LLM.** It only reads: the already-computed scan/plan
objects plus one read-only `git status --short` check. Every state is
handled gracefully — no git repository, zero commits, clean or dirty
working tree, and missing tests/plan data.

### `jarvis precommit` — the final safe check before you commit by hand

```
jarvis precommit
```

Like `jarvis scan`/`plan`/`work`/`review`, this is non-interactive,
deterministic, requires no `ANTHROPIC_API_KEY`, and never modifies the
project or git history. It builds directly on `jarvis review`'s output
(`scan_project()` → `build_plan()` → `build_review()`) and adds the
concrete list of changed file names, producing the last checkpoint before
you decide to run `git commit` yourself:

1. **Findings** — the same git-state and next-plan-step summary as
   `jarvis review`.
2. **Changed files** — the actual file names with uncommitted changes
   (from a fresh, read-only `git status --short`), not just a count.
3. **Possible concerns** — anything `jarvis review` flagged: missing
   tests, no commits yet, an unreadable git state, and so on.
4. **Checks to run before committing** — `jarvis review`'s own
   suggestions, plus an explicit reminder that only a human decides to
   commit.
5. **Ready for commit** — the same yes/no verdict and reason as `jarvis
   review`.

**`jarvis precommit` itself never writes a file, never runs `git commit`
or any other git-mutating command, never calls the LLM, and — unlike the
REPL's `precommit` keyword — never runs the test suite.** It is
intentionally execution-free: a final, read-only summary to look over
before committing by hand (or asking JARVIS's REPL `precommit` command to
also run the tests first). Every state is handled gracefully, inherited
directly from `jarvis review`: no git repository, zero commits, clean or
dirty working tree, and any number of changed files.

### `jarvis commit` — the only command that can actually run `git commit`

```
jarvis commit
jarvis commit -m "your commit message"
```

Available both as a CLI argument and as a REPL keyword (`commit`). Unlike
`scan`/`plan`/`work`/`review`/`precommit`, this is the one command in the
whole workflow that can mutate git history — so it's built with several
independent safety layers instead of just one:

1. It first builds the exact same `scan` → `plan` → `review` →
   `precommit` pipeline used by `jarvis precommit`, and **stops
   immediately — before asking anything** — if: there's no git
   repository, the pre-commit check didn't pass (e.g. a clean tree, or
   whatever `jarvis precommit` itself flagged), or nothing is staged.
2. It always shows a structured plan first: which files are **staged**
   (what would actually be committed) and which are **unstaged** (changed
   but explicitly excluded, shown so nothing is committed by surprise).
3. **It never runs `git add` on your behalf.** If changes exist but
   nothing is staged, it tells you to stage them yourself (e.g. via
   `git-add` in the REPL) rather than guessing what you meant to include.
4. If everything above passes, it asks for a commit message — either
   `-m "..."` up front, or an interactive prompt if you didn't provide
   one. Without `-m`, the prompt first offers an **LLM-suggested
   message** (`jarvis.core.commit_message`, a single isolated request
   built from the staged files and `git diff --cached`, with no tools and
   no conversation history) as a starting point: press Enter to accept it
   as-is, or type your own message to use that instead. Leaving it blank
   with no suggestion available still aborts, exactly as before — no
   message is ever invented and silently used without you seeing it
   first. If there's no `ANTHROPIC_API_KEY` configured, or the suggestion
   call fails for any reason (network, rate limit, anything), this falls
   back silently to the original plain prompt — suggesting a message is
   an optional enhancement, never a requirement for `jarvis commit` to
   work.
5. Finally, it shows the commit message — suggested, typed, or passed via
   `-m`, JARVIS treats them identically from this point on — and staged
   file count in a **y/N confirmation prompt** (defaulting to No — a bare
   Enter or anything but `y` cancels) before running `git commit`. This
   reuses the exact same `_git_commit()` argv-builder and validation
   already used by the approval-gated agent tool (`git-commit` in the
   shell tool's allowlist) — no second, looser way to shape the command —
   and the same `confirm_side_effect()` approval/audit mechanism used
   everywhere else in JARVIS. A `Ctrl+C` at the confirmation prompt
   cancels, never approves.

**`git commit` is only ever invoked after that explicit `y`.** There is no
`--force`, no "skip confirmation" flag, and no path through this command
that stages files or commits without you seeing exactly what would happen
first. A suggested message is purely advisory text shown before that same
prompt — it never causes a commit by itself, and accepting it (Enter)
still requires the separate `y` that follows. Every state is handled
gracefully: no git repository, no pending changes, a failed `jarvis
precommit` check, changes present but nothing staged, files staged with
unstaged changes alongside them, and no API key or an unavailable LLM for
the message suggestion step.

## What JARVIS can do in v0.1

Eighteen tools are available to the agent. Eleven are routed through the
sandbox gate; `manage_tasks`, `create_plan`, `get_workflow_state`, and the
four email tools are the exceptions — none of them has any effect on the
project's files, and the email tools act on an external mailbox rather
than the sandboxed project at all (see below, and
[External integrations](#external-integrations)):

- **`read_file`** — reads a text file. No confirmation prompt (read-only).
  If the file isn't valid UTF-8 text (e.g. an image, archive, or compiled
  artifact), returns its size and file type instead of failing outright —
  binary content itself is never returned or written.
- **`write_file`** — creates or overwrites a text file. Always requires
  confirmation before writing, showing a unified diff against the file's
  current content (or a line-count preview if it doesn't exist yet) — not
  just a byte count, so you see exactly what would change. Still
  UTF-8-text-only; binary file writing is not supported.
- **`append_to_file`** — adds text to the end of a file without touching
  its existing content, creating the file (and missing parent directories)
  if it doesn't exist yet. Always requires confirmation, showing a diff of
  what the appended content adds. UTF-8 text files only.
- **`list_directory`** — lists files and subdirectories at a path, with
  file sizes shown for each file. No confirmation prompt (read-only). Pass
  `recursive=true` to walk the full subtree (housekeeping directories like
  `.venv`/`.git`/`__pycache__` are skipped automatically, output capped at
  500 entries).
- **`delete_file`** — deletes a single file. Always requires confirmation.
  Refuses to delete directories.
- **`create_directory`** — creates a directory, including missing parents.
  Requires confirmation. If the directory already exists this is a silent
  no-op (no prompt); if a file occupies the path, it refuses.
- **`move_file`** — moves or renames a single file. Source and destination
  are sandbox-checked independently (either can trigger its own one-time
  approval if outside the project directory), plus one confirmation for the
  move itself. Refuses if the destination already exists (no overwrite) or
  if the source is a directory.
- **`search_files`** — searches for a literal text substring (not regex)
  across files under a directory, recursively. No confirmation prompt
  (read-only). Skips binary files and housekeeping directories, capped at
  200 matches / 8000 output characters.
- **`replace_in_file`** — replaces occurrences of a literal text substring
  (not regex) within a single file, optionally capped to the first N
  occurrences via `count`. Always requires confirmation, showing both the
  occurrence count and a unified diff of the resulting change. Reports
  zero replacements rather than silently succeeding if the search text
  isn't found. UTF-8 text files only.
- **`edit_file_lines`** — replaces a specific 1-indexed, inclusive line
  range with new content, without retyping the rest of the file. Lines
  outside the range are preserved byte-for-byte, including their original
  line endings. Always requires confirmation, showing a unified diff of
  the change. Refuses out-of-range line numbers rather than silently
  clamping them; use an empty string as the new content to delete a range.
  UTF-8 text files only.
- **`run_command`** — runs a strictly allowlisted command: read-only
  inspection (`ls`, `cat`, `git-status`, `git-log`, `git-diff`); `pytest`,
  which runs this project's own test suite under its own venv interpreter
  (`sys.executable -m pytest`, restricted to safe flags like
  `-v`/`-x`/`-k`/`-q`/`--tb=...`, no plugin-loading or coverage-report
  flags); `python`, which runs a single `.py` script file with its own
  arguments (no `-c` inline code, no `-m` arbitrary module execution — only
  a specific project script file is permitted, same interpreter/`cwd`
  pinning as `pytest`); `git-init`, which initializes the project
  directory as a git repository (no arguments accepted — no branch-name
  flag, template directory, or bare-repo option — and refuses if it's
  already a repository); `git-add`, which stages explicit file paths only (no wildcards,
  no `-A`/`--all`/`-u`, no `-p`/`--patch`, no `-f`/`--force` — always
  trivially reversible with `git reset`); and `git-commit`, which commits
  currently staged changes and requires an explicit `-m "<message>"` (a
  bare `git commit` would hang on an interactive editor, so it's not
  permitted) — no `--amend` (would rewrite/destroy a prior commit), no
  `-a`/`--all` (would silently stage everything modified instead of only
  what `git-add` explicitly staged), no `--no-verify` (would bypass any
  hooks in the target repo). Neither git command ever pushes, and
  `git-commit` relies entirely on the target repo's own committer identity
  (`git config user.name`/`user.email`) — JARVIS never configures it on
  your behalf. No shell string is ever interpreted; arguments are passed
  as an argv list, every path argument is sandbox-checked, every
  invocation requires approval, and commands are subject to a timeout
  (30s default, 120s max).
- **`create_plan`** — the single, atomic "here is the plan" action: takes
  an ordered list of step descriptions and creates them as a fresh task
  list in one call, replacing any existing incomplete plan (the replaced
  count is always reported, never silently discarded). Distinct from
  `manage_tasks`' one-at-a-time mutations, which are used afterward to
  execute and track progress against the plan this creates. No approval
  prompt — same reasoning as `manage_tasks` below.
- **`manage_tasks`** — tracks a persistent, local to-do list
  (`.jarvis/tasks.json`) of steps for the current work: `add`, `complete`,
  `list`, `clear_completed`, `clear_all`. This is JARVIS's own working
  notes, not a project file — it has no effect on the sandboxed project
  itself, so unlike every other tool it does **not** require an approval
  prompt. Lets the model break a multi-step request into tracked steps and
  check them off as it completes them, so a plan is visible across the
  conversation and survives if the session is resumed later (also viewable
  any time via the `tasks` CLI command).
- **`get_workflow_state`** — lets the agent read the project's state
  through the same deterministic pipeline as the `jarvis scan` / `plan` /
  `work` / `review` / `precommit` CLI arguments, instead of piecing it
  together from several `run_command` calls (`git-status`, `git-diff`,
  etc.). Takes a single `stage` argument (`scan`/`plan`/`work`/`review`/
  `precommit`) and returns the exact same formatted text those CLI
  commands print — no new logic, just a bridge to output that's already
  deterministic and tested. Read-only, so like `manage_tasks`/
  `create_plan` it requires **no approval prompt**. Deliberately excludes
  `commit` as a stage — committing is a real side effect and always goes
  through the existing approval-gated `run_command`('git-commit') path,
  never through this tool.
- **`check_email_connection`** / **`get_email_account_info`** /
  **`list_recent_emails`** / **`read_email`** — the four read-only email
  tools, bridging the agent to `EmailConnector` (see
  [External integrations](#external-integrations) for the full
  description). None require approval — reading a mailbox has no effect
  on the project or the mailbox itself — and all four report a clear
  "not configured" result rather than attempting a connection if
  `EMAIL_IMAP_HOST`/`EMAIL_ADDRESS`/`EMAIL_PASSWORD` aren't set.

Recursive directory deletion, reading/writing files in encodings other than
UTF-8 (e.g. auto-detecting Latin-1/UTF-16), and binary file *writing* are
still deferred. This is intentional: capabilities are added incrementally
as the safety net around them is proven.

## Natural-language requests: how the agent uses the workflow

You can just ask JARVIS things in plain language in the REPL — e.g.
"check my project and tell me what to do next", or "I want to clean up
this codebase" — instead of running `scan`/`plan`/`review`/etc. yourself.
This isn't a separate feature: it's the same `Agent`/`LLMClient`/tool-call
loop that's always powered the REPL, with a v1 addition that makes it
behave consistently:

- **The system prompt now spells out an order of operations.** Before
  proposing or taking any action on a state-related request, the agent is
  instructed to call `get_workflow_state` first (starting with `scan`,
  then `plan`/`review`/`precommit` as relevant) rather than guessing the
  project's state or re-deriving it from several `run_command` calls. It's
  also told not to repeat a stage it's already seen earlier in the same
  conversation, and to prefer read-only tools over any change until it
  understands the current state.
- **The agent cannot bundle or skip approvals.** The system prompt tells
  it to explain what it's about to do in plain text before calling any
  file-writing or git-mutating tool, but the actual y/N approval prompt
  (`confirm_side_effect`) is enforced in code regardless of what the model
  says — the same mechanism used everywhere else in JARVIS, not a new one.
  Every mutating tool call still prompts separately, one at a time; the
  model has no way to pre-approve or combine several into one decision.
- **The agent is explicitly told never to use `git-add`/`git-commit` to
  "finish" a task on its own initiative.** Staging and committing remain
  things the user asks for and approves individually — see
  ["What JARVIS can do"](#what-jarvis-can-do-in-v01) above for the
  `git-commit` tool's own restrictions (mandatory message, no `--amend`,
  no `-a`/`--all`, no `--no-verify`, never pushes).
- **Every tool call the agent makes is now written to the audit log**
  (`jarvis.core.audit.log_event("tool_call", ...)`), not just ones that
  went through an approval prompt. A read-only call like
  `get_workflow_state` has no approval step, but it's still visible via
  `jarvis history` / the REPL `history` command, labeled `OK`/`ERROR`
  (distinct from `APPROVED`/`DENIED`, since no human was actually asked)
  — so you can always see what the agent decided to check or do, not only
  what it asked permission for. Bulk text arguments (a file's new
  `content`, a `replace_in_file` replacement string, etc.) are omitted
  from the logged summary — the log records *what* was called and on
  *which path*, not a second copy of file contents already visible in the
  tool's own result.

This is deliberately a small v1: no new tools, no new approval mechanism,
no new orchestration class — `Agent.step()` already was the orchestrator.
The work here is discipline (a clearer system prompt) and visibility (a
fuller audit trail), layered on infrastructure that was already tested.

### Multi-turn context: referring back to a previous answer

You can say "check my project" and then, in a later message, "ok, fix
the first one" — JARVIS resolves "the first one" against its own most
recent structured answer in the conversation, not a fresh guess.

This already worked at the infrastructure level before this addition:
`main()` loads the whole conversation once via `load_history()` and
passes the same `history` list into every `Agent.step()` call for the
life of the REPL session, and `save_history()` persists it after each
turn — so a later turn already sees everything the agent said earlier,
including a numbered list of problems it just reported (verified across
separate REPL process runs, not just within one process). The two things
actually added here are about *using* that context correctly and safely:

- **A system prompt instruction on resolving references.** The model is
  told to answer "fix the first one" / "what about the second issue" /
  "yes, do that" against its own last structured answer in this
  conversation, and — importantly — *not* to re-run `get_workflow_state`
  just to re-derive something it already told the user, since the answer
  is already sitting right there in the conversation. This is what keeps
  a two-turn exchange like the example above to the same number of API
  calls it would've taken as one turn.
- **An automatic staleness note after any mutating tool call.** Right
  after a successful call to a file-writing tool (`write_file`,
  `append_to_file`, `delete_file`, `create_directory`, `move_file`,
  `replace_in_file`, `edit_file_lines`) or `run_command`, a short system
  note — `[JARVIS session note] The '<tool>' action above just changed
  the project...` — is appended to the same message as that call's
  result, telling the model that anything it learned about the project
  before this point may now be out of date and should be re-checked
  before being relied on again. This is deterministic code, not
  something the model has to remember to do on its own: it's added by
  `jarvis.core.agent._run_loop()` regardless of what the model says next.
  `run_command` is treated as potentially state-changing for *any*
  successful call, even its read-only entries (`git-status`, `git-log`,
  etc.) — a conservative choice: the cost of an unnecessary re-check is
  one cheap `get_workflow_state` call, while treating a real change as
  still-fresh could mean acting on stale information.
- The note is inserted as an extra `text` block inside the *same* message
  as the `tool_result` blocks, not a separate conversational turn — the
  Anthropic API requires a `tool_use`-bearing turn to be followed
  immediately by a turn containing only that call's results, and
  `trim_history()` relies on that same pairing, so nothing about message
  boundaries changes.
- This also composes with the existing trim marker: if older messages
  were ever dropped to stay within the context budget (`trim_history()`,
  unchanged), that marker uses the same `[JARVIS session note]` prefix,
  and the model is told to treat either kind of note as a reason to
  re-check rather than assume.

No new storage, no new data structure, no new API calls beyond what a
plain re-check would already cost — this is a small correctness layer on
top of context that was already being saved and passed through.

## Safe file editing v1: what you see is what gets written

You can ask JARVIS to edit a file in plain language — "update the README
description", "add this function to my project", "fix this file" — and
before anything is written, you see exactly what would change and get to
approve or reject it. This builds on the four file-writing tools that
already existed (`write_file`, `append_to_file`, `replace_in_file`,
`edit_file_lines`); it doesn't add a new one.

- **Every approval prompt now shows a real diff**
  (`jarvis.core.file_diff.format_unified_diff`), not just a byte count or
  an occurrence count. `write_file`/`append_to_file` show a standard
  unified diff against the file's current content (or a line-count
  preview for a brand-new file); `replace_in_file` shows both the
  occurrence count and the resulting diff; `edit_file_lines` shows a
  unified diff instead of its previous ad hoc "old:/new:" block. All four
  tools now present the same, familiar `---`/`+++`/`@@` format.
- **A write-time staleness guard protects against editing based on
  outdated content.** Each tool re-reads the file immediately before
  writing — right after the y/N approval, right before `write_text()` —
  and compares it against what it read when the diff was built. If the
  file changed in between (edited by hand, or by anything else, while the
  approval prompt was waiting), the write is refused with a clear
  message instead of silently overwriting whatever is there now or
  applying a stale search/line-range against content that's moved. A
  brand-new file has nothing to go stale against, so this only applies
  once a file already exists.
- **The audit log now shows *how much* changed, not just *that* something
  changed.** Bulk text arguments (`content`, `new_content`, `replacement`)
  are replaced with a short size summary (e.g. `<420 char(s), 12
  line(s)>`) in the `tool_call` audit entry, rather than being stored
  verbatim (too large, and it would duplicate content already visible in
  the diff shown in the matching `side_effect_confirmation` entry) or
  dropped outright (too little visibility). The full diff itself is
  already captured in that `side_effect_confirmation` entry's
  `description` field, since it's what was actually shown to and approved
  by the user.

None of this changes the approval mechanism itself, the sandbox boundary,
or the four tools' write behavior once approved — it's a visibility and
correctness layer on an already-tested approval flow. `git-add` and
`git-commit` remain entirely outside these tools' reach; they're only
ever run through `run_command`'s own allowlist and approval prompt, which
this addition doesn't touch.

## TEST → ANALYZE → PROPOSE FIX → APPROVAL → APPLY → VERIFY

You can ask JARVIS to "check my project" or "find and fix the bug", and
it follows a disciplined sequence rather than guessing at what's wrong or
changing things speculatively. This is built entirely from tools that
already existed — `get_workflow_state`, `run_command('pytest')`,
`read_file`, and the file-writing tools' own diff+approval flow from
[Safe file editing v1](#safe-file-editing-v1-what-you-see-is-what-gets-written)
above — plus two additions:

1. **A system prompt instruction naming the sequence explicitly.** TEST
   (run the project's tests before assuming anything is broken) → ANALYZE
   (read only the specific file(s) the failure points to, not the whole
   project) → PROPOSE FIX (explain the change in plain text before
   writing anything) → APPROVAL (the normal diff + y/N prompt, never
   skipped) → APPLY (the write, only once approved) → VERIFY (re-run the
   tests afterward to confirm the fix actually worked, rather than
   assuming success because the write succeeded). If verification still
   fails, the model is told to stop and explain rather than attempt
   another fix on its own.
2. **A hard, code-enforced limit on mutating actions per turn**
   (`jarvis.core.agent.MAX_MUTATIONS_PER_STEP`, 8) — the actual guard
   against an unsupervised "fix → test → fix → test → ..." loop, not just
   a prompt asking the model to behave. It counts every successful
   file-write, `git-init`/`git-add`/`git-commit`, or other `run_command`
   call within a single `step()` (across all of that turn's LLM
   round-trips, not reset per round-trip). Once reached, a
   `[JARVIS session note]` is appended — the same mechanism the
   [staleness note](#multi-turn-context-referring-back-to-a-previous-answer)
   already uses — telling the model to stop, summarize what it did and
   what's still unresolved, and ask the user how to proceed instead of
   continuing on its own. This is deliberately a general safeguard (any
   run of mutating calls in one turn, not specifically "fix attempts"
   after a test failure) rather than a narrower, harder-to-verify
   heuristic.

Test runs themselves never modify the project — `pytest`'s allowlisted
flags (`-v`/`-x`/`-k`/`-q`/`--tb=...`) exclude anything that writes
coverage reports or other output files, unchanged from before this
addition. Every step in the sequence is already covered by the existing
audit trail: each `run_command('pytest')` call, each file read, and each
file write (with its diff shown in the matching
`side_effect_confirmation` entry) all appear in `jarvis history` in
order, so you can see exactly what was tested, what was read, what was
proposed, and what was actually approved.

## The safety boundary

JARVIS is restricted to its own project directory (`JARVIS_ROOT`, resolved once
at startup to the project root) by default. This is enforced in code, not just
by instructing the model:

- Every path a tool touches is resolved to its canonical absolute form
  (following symlinks and junctions) and checked against `JARVIS_ROOT` before
  any file operation happens.
- `..`-traversal, symlink escapes, and junction escapes are all caught by this
  check — see `tests/test_sandbox_symlinks.py`. Recursive directory walks
  (`list_directory` with `recursive=true`, `search_files`) re-check every
  subdirectory against the boundary before descending into it, since
  Python's `os.walk(followlinks=False)` does not by itself stop a Windows
  junction — `Path.is_symlink()` returns `False` for junctions, so this had
  to be handled explicitly rather than relied on implicitly.
- If a request genuinely needs a path outside the project directory, JARVIS
  pauses and asks for **explicit, one-time approval** — the default answer is
  always "no," and nothing is remembered across requests.
- Every write and every approval/denial decision is logged to
  `.jarvis/audit.log` (with any secret text redacted) for a full audit trail.
  The log rotates automatically once it exceeds ~2 MB, archiving the old
  file with a timestamp (`audit.log.<timestamp>.jsonl`) rather than
  deleting it — old audit data is never discarded automatically, only
  moved aside, since pruning it is a deliberate decision for you to make.
- Failures are closed: any ambiguity, error, or interrupted approval prompt
  results in denial, never silent access. Approval prompts also fail closed
  on malformed input — only a bare `y` (case-insensitive, whitespace
  trimmed) counts as approval; anything else, including `yes`, is a denial.
- If `.jarvis/session.json` is corrupted or unreadable, JARVIS starts a new
  empty session rather than crashing — but it prints a visible `[warning]`
  at startup so this is never silent data loss. The corrupted file is left
  in place, not deleted, so it can be inspected or recovered manually.
- If a tool raises an unexpected error mid-turn (a bug, not a user
  interrupt), it's converted into a normal error result and the
  conversation continues — it never leaves the in-memory session history
  in a corrupted state (an orphaned tool call with no matching result).
  Ctrl+C is unaffected by this and still rolls back the whole turn, as
  before.
- If the project JARVIS is pointed at is a git repository, JARVIS
  automatically ensures `.jarvis/` (its own session and audit files) is
  excluded via `.gitignore` at startup — creating the file if missing, or
  appending one line if it exists, never overwriting existing content. A
  visible `[notice]` is printed when this happens; nothing changes
  silently, and nothing happens at all if the project isn't a git repo yet
  or `.jarvis/` is already covered.

## External integrations

`jarvis/integrations/` is the shared foundation for connecting JARVIS to
external systems (email, Stripe, Instagram, Facebook, a website, ...) —
laid down so each real connector can be added without redesigning
approval, credentials, or auditing each time. Email and Stripe (both
read-only) are the first two real connectors; Facebook and Instagram are
planned as later stages on the same foundation.

- **`Connector` / `ExternalAction`** (`jarvis/integrations/base.py`) — the
  external-system counterpart to `jarvis.tools.base.Tool`. A connector
  declares a fixed, finite list of named actions (e.g. `send_email`,
  `list_recent_charges`) — never "make an arbitrary API call" — mirroring
  how `run_command`'s allowlist works today. Each action carries a
  `RiskLevel`: `READ_ONLY` (no approval, like `read_file`),
  `REVERSIBLE_WRITE` (ordinary y/N, like `write_file`), or
  `IRREVERSIBLE_WRITE` (a stricter gate — see below) for anything that
  can't be undone by JARVIS itself, like a sent email or a charge.
- **`confirm_external_action`** (`jarvis/integrations/approval.py`) — the
  external-action approval gate. For `IRREVERSIBLE_WRITE` actions, a
  plain `y` is not enough: the user must type the service's name back
  exactly (case-insensitive) to confirm — deliberately more friction than
  any existing local approval prompt, since undoing an external action
  (unlike overwriting a local file again) isn't always possible. Every
  outcome is logged as its own `external_action_confirmation` audit
  event, visible in `jarvis history` labeled `external action` — kept
  distinct from local `side effect` entries so external actions are easy
  to find in the log on their own.
- **Credentials** (`jarvis/integrations/credentials.py`) — the same
  environment-variable-only model `ANTHROPIC_API_KEY` already uses,
  generalized to any number of services: each connector registers which
  env vars it needs, and `credential_status()`/`format_credential_status()`
  report what's configured using `mask_secret()` — the raw value is never
  read from a file JARVIS manages, written anywhere, or logged unmasked.
- **`IntegrationRegistry`** (`jarvis/integrations/registry.py`) — the
  integrations-layer counterpart to `jarvis.tools.base.ToolRegistry`,
  tracking which connectors are registered and which are actually
  configured (credentials present) versus just wired up in code.

### Email connector (read-only v1, password or OAuth)

`jarvis/integrations/connectors/email.py`'s `EmailConnector` is the first
real connector, built on this foundation. It's IMAP-based — works
uniformly across providers rather than a provider-specific API — and
supports two independent, coexisting ways to authenticate: a static
password (an app-specific password for providers like Gmail that require
one), or OAuth tokens via IMAP's standard XOAUTH2 mechanism. **Only
reading is supported, regardless of which authentication method is
used.** There is no send, delete, move, or mark-as-read action anywhere
in this module — adding one later means adding a new `ExternalAction`
with a `REVERSIBLE_WRITE` or `IRREVERSIBLE_WRITE` risk level and going
through `confirm_external_action()`; it cannot happen by accident, since
no code path here can currently do it. Every mailbox is opened
`readonly=True` and message bodies are always fetched via `BODY.PEEK[]`
rather than `BODY[]`, so even reading a message never has the
IMAP-server side effect of marking it as read.

Four tools bridge this to the agent (`jarvis/tools/email_tools.py`), the
same "thin wrapper, no logic" pattern `get_workflow_state` uses — none of
them require approval, since all four are `READ_ONLY`:

- **`check_email_connection`** — verifies the configured credentials work.
- **`get_email_account_info`** — the configured address, available
  mailboxes, and the message count in a mailbox (default `INBOX`).
- **`list_recent_emails`** — recent message headers (id, from, subject,
  date) in a mailbox — never bodies.
- **`read_email`** — one specific message's headers and text content, by
  the id `list_recent_emails` returned.

**Password credentials are read only from the environment** —
`EMAIL_IMAP_HOST`, `EMAIL_ADDRESS`, `EMAIL_PASSWORD` — the same model
`ANTHROPIC_API_KEY` already uses; nothing here ever prompts for or stores
a password. If neither a password nor stored OAuth tokens are
configured, every one of the four tools returns a clear "email is not
configured" result immediately — no connection is attempted — and if a
real IMAP call fails (wrong host, bad credentials, network error), it's
reported the same way `run_command` reports a failed shell command: a
plain error result, never a crash.

### OAuth authentication layer

`jarvis/integrations/oauth.py` is a connector-agnostic OAuth foundation —
it knows nothing about IMAP or email specifically, so any future
connector needing OAuth (a future Instagram/Facebook connector, for
instance) can reuse it rather than reimplementing token handling.

- **No real OAuth flow is connected to a real account anywhere in this
  codebase.** `exchange_code_for_tokens()` and `refresh_access_token()` —
  the two functions that would make a real HTTP call to an OAuth
  provider's token endpoint — deliberately raise `NotImplementedError`.
  This means no code path in JARVIS can currently complete a real OAuth
  exchange or refresh, even by accident; wiring up a real HTTP call is a
  separate, explicit step for when an actual account is being connected.
- **`build_authorization_url()`** is pure string construction — it builds
  the URL a user would open in their own browser to grant access, using
  only public, well-known provider endpoints (Google/Microsoft) and a
  read-only-mail scope (`gmail.readonly`, matching what the email
  connector can actually do). JARVIS never opens a browser or submits the
  consent form itself; the human step of visiting that URL and approving
  access is what a real "connect my account" flow would still require.
- **`TokenStore`** persists an `OAuthTokens` pair (access token, refresh
  token, expiry, scope) via the OS keychain — Windows Credential Manager,
  macOS Keychain, or the Linux Secret Service, through the `keyring`
  package — never in a file JARVIS manages, never in the audit log, and
  never logged unmasked (`describe()` only ever shows a
  `mask_secret()`-masked preview). This is a different storage location
  from the plain env-var password above: OAuth tokens are a
  semi-persistent secret a running process refreshes on its own, which is
  what an OS-native secret store is for.
- `EmailConnector` prefers OAuth over a password when both happen to be
  present (OAuth is the more capable, revocable credential), but the
  original password path is completely unaffected when no tokens are
  stored — this is a pure addition, not a replacement. An expired stored
  token is refused with a clear error rather than attempting a refresh,
  since automatic refresh isn't implemented yet.

**No client ID or client secret exists anywhere in this codebase.**
When a real account is connected in a later stage, those will come from
the environment (the same model every other credential in JARVIS uses),
never hardcoded.

### Integrations Manager

`jarvis/integrations/manager.py`'s `IntegrationsManager` is the single
place to see every integration's status and connect/disconnect one,
without needing to import or know about each connector module
individually. It wraps an `IntegrationRegistry` (unchanged) and adds a
four-state status model:

- **`NOT_CONFIGURED`** — no credentials of any kind are present.
- **`CONNECTED`** — credentials (a password or stored OAuth tokens) are
  present and the connector's actions can be used. This doesn't guarantee
  they're still valid with the remote service — a rejected credential
  surfaces as an error the next time an action actually runs
  (`CredentialError`), not preemptively in the status check.
- **`DISCONNECTED`** — credentials were present and have since been
  explicitly cleared via `disconnect()`. Kept distinct from
  `NOT_CONFIGURED` so "deliberately disconnected" reads differently from
  "never set up" — this is tracked in memory per `IntegrationsManager`
  instance, not persisted, since there's nothing meaningful to remember
  once no credential is actually stored.
- **`ERROR`** — checking configuration itself failed unexpectedly, or the
  service name isn't registered at all.

`list_statuses()` / `get_status(service_name)` report every registered
connector's state; `connect(service_name)` / `disconnect(service_name)`
re-check or clear it. **None of this performs a real OAuth exchange or
network call** — `connect()` only re-checks `is_configured()` (there is
still nothing here to drive a real OAuth authorization end-to-end, since
`exchange_code_for_tokens()` remains deliberately unimplemented), and
`disconnect()` calls the connector's own `Connector.disconnect()` (a new
optional method, default "not supported" so existing/future connectors
that manage no local secret of their own aren't required to implement
it) — for email, this clears stored OAuth tokens via `TokenStore.clear()`
without touching `EMAIL_IMAP_HOST`/`ADDRESS`/`PASSWORD`, since those are
your own shell environment, not something a connector should alter.

Every status detail string is built from
`jarvis.integrations.credentials.format_credential_status()`, which is
already masked — **no status check, list, connect, or disconnect call
ever displays a raw token, password, or secret value.**

`EmailConnector` is the first connector managed this way; the same
`IntegrationsManager` also manages Stripe and Google Calendar (below)
without any changes to the manager itself, and will manage Instagram,
Facebook, Shopify, and any other future connector the same way — each
just registers with the same `IntegrationRegistry` and implements
`Connector`.

### Stripe connector (read-only v1)

`jarvis/integrations/connectors/stripe.py`'s `StripeConnector` is the
second real connector, using Stripe's REST API directly (Python's
standard-library `urllib.request` — no new dependency) rather than
Stripe's own SDK. Authenticates with a single API key
(`STRIPE_API_KEY`) via HTTP Basic auth, exactly as Stripe's API
documentation specifies (the key as the basic-auth username, an empty
password) — the same "one env var, read only from the environment"
model every other credential in JARVIS uses. **Only four read-only
actions exist, and nothing else does:**

- **`get_balance`** — the current account balance (available and
  pending amounts, by currency).
- **`list_recent_charges`** — recent charges (id, amount, currency,
  status, created timestamp), most recent first — answers "what were the
  recent payments" and "what's the amount/status of a payment".
- **`get_charge_status`** — one specific charge's status and details by
  its charge id — answers "was this specific payment successful".
- **`list_recent_payment_intents`** — recent payment intents ("orders" —
  Stripe has no single unified order object; `PaymentIntent` is the
  standard object representing one payment attempt's lifecycle), most
  recent first — answers "what were the recent orders".

There is no charge creation, refund, payout, or customer-update action
anywhere in this module — adding one later means adding a new
`ExternalAction` with a `REVERSIBLE_WRITE` or `IRREVERSIBLE_WRITE` risk
level and going through `confirm_external_action()`, exactly like every
other connector in this codebase. `_get()`, the only HTTP call this
connector can make, is hardcoded to `GET` against four specific paths
(`/v1/balance`, `/v1/charges`, `/v1/charges/{id}`,
`/v1/payment_intents`) — there is no generic "call any Stripe endpoint"
method for a future write action to accidentally reuse unsafely.

The API key is never logged, printed, or included in any result or error
message: HTTP/JSON failures are passed through a redaction step
(`_redact_key()`, mirroring `jarvis.core.secrets.redact_secret()`'s
approach) that scrubs the configured key out of the error text before it
can reach a `CredentialError` or an `ExternalActionResult`. If
`STRIPE_API_KEY` isn't set, every action returns a clear "not configured"
result immediately — no HTTP request is attempted — and any real request
failure (invalid key, network error, timeout, malformed response) is
reported the same way every other connector reports one: a plain error
result, never a crash.

Four tools bridge this to the agent (`jarvis/tools/stripe_tools.py`), the
same "thin wrapper, no logic" pattern the email tools use — none require
approval, since all four are `READ_ONLY`: `get_stripe_balance`,
`list_recent_charges`, `get_charge_status`, `list_recent_payment_intents`.
Ask in plain language (Lithuanian works too, since these are ordinary
LLM-agent tool calls, not the deterministic Task Execution layer's fixed
keyword rules) — e.g. *"kokie buvo naujausi mokėjimai?"*, *"ar mokėjimas
ch_1A2b3C buvo sėkmingas?"*, *"kokie buvo naujausi užsakymai?"*, *"kokia
šio mokėjimo suma ir statusas?"* — and the agent picks the matching tool.
**Not reachable through the Task Execution layer** — `jarvis task`,
`jarvis run`, and the REPL's `komanda:` prefix still treat any mention of
Stripe as blocked, exactly as before; Stripe questions go through the
ordinary REPL conversation with the LLM agent instead.

A Stripe **test-mode key** (`sk_test_...`) routes every request to
Stripe's test environment automatically — Stripe itself decides based on
the key's prefix, so no JARVIS setting changes between test and live
data. Every unit test for this connector and its tools mocks
`urllib.request.urlopen` — no real network access ever occurs during
`pytest`.

### Google Calendar connector (read-only v1, OAuth-only)

`jarvis/integrations/connectors/google_calendar.py`'s
`GoogleCalendarConnector` is the third real connector, and deliberately
architected differently from `EmailConnector`: **it supports OAuth only
— there is no password or API-key fallback path at all.** This is an
intentional contrast so the codebase demonstrates both shapes clearly: a
connector that accepts either credential type (email), and one that
accepts exactly one (calendar). `is_configured()` checks only whether
OAuth tokens are stored — setting unrelated environment variables has no
effect on it, unlike email's hybrid model.

Uses a separate `OAuthProviderConfig` from email's
(`GOOGLE_CALENDAR_OAUTH_PROVIDER` vs. `GOOGLE_OAUTH_PROVIDER` in
`jarvis/integrations/oauth.py`) with its own, narrower scope
(`calendar.readonly`, not `gmail.readonly`) — each connector can only
ever request the specific access its own action set needs. **Only two
read-only actions exist, and nothing else does:**

- **`list_upcoming_events`** — upcoming events on the primary calendar
  (id, summary, start time), soonest first.
- **`get_event`** — one specific event's details (summary, start/end,
  location, description) by its id.

There is no event-creation, update, deletion, or invite-response action
anywhere in this module — adding one later means adding a new
`ExternalAction` with a `REVERSIBLE_WRITE` or `IRREVERSIBLE_WRITE` risk
level and going through `confirm_external_action()`, exactly like every
other connector. `_get()`, the only HTTP call this connector can make, is
hardcoded to `GET` against two specific paths — there is no generic
"call any Calendar endpoint" method.

Authenticates with a Bearer access token from
`jarvis.integrations.oauth.TokenStore` (never an environment variable).
Exactly like `EmailConnector`'s OAuth path, an expired stored token is
refused with a clear `CredentialError` rather than attempting a refresh
— `exchange_code_for_tokens()`/`refresh_access_token()` remain
deliberately unimplemented, so no real OAuth exchange happens anywhere.
The access token is redacted from any error text before it can reach a
result (`_redact_token()`, the same defense-in-depth pattern
`StripeConnector`'s `_redact_key()` uses), and `disconnect()` clears the
stored tokens via `TokenStore.clear()` — no network call, no revocation
with Google, only local cleanup, identical in spirit to email's
`disconnect()`.

#### `jarvis integrations` — see every integration's status at a glance

```
jarvis integrations
```

Also available as the REPL keyword `integrations`. Both lists every
registered connector's status (`NOT CONFIGURED`/`CONNECTED`/
`DISCONNECTED`/`ERROR`) via `IntegrationsManager.list_statuses()` — no
API key or LLM call required, just like `jarvis scan`/`status`. The CLI
layer (`jarvis/cli/main.py`) never touches a `Connector` directly: it
only builds an `IntegrationRegistry`
(`build_integration_registry()`) and asks `IntegrationsManager` for the
status, exactly the same "thin wrapper around the manager, no logic of
its own" shape every other CLI entry point in JARVIS already follows.
Every line printed comes from the same masked detail strings
`IntegrationsManager` itself produces — **this command cannot display a
password, token, or secret, because nothing between it and the terminal
ever has access to an unmasked one.**

Further connectors are added as separate, later stages on this same
foundation (Facebook/Instagram next), each following the same cycle used
so far: proposal → approval → implementation → tests → live check →
README.

## Project layout

```
jarvis/
  config.py           # resolves JARVIS_ROOT and loads ANTHROPIC_API_KEY from env
  core/
    sandbox.py          # the path-boundary gate - the only place that decides in/out
    approval.py           # CLI confirmation prompts for side effects & out-of-sandbox access
    audit.py                # append-only log of every approval/denial decision
    secrets.py                # key masking/redaction, .env-gitignore check, auto-gitignore .jarvis/
    help_view.py                 # formats the 'help' command's orientation text
    history_view.py             # formats audit.log into a readable summary for 'history'
    review_view.py               # combined git status + diff summary for 'review'
    file_diff.py                  # unified diff formatting for file-write approval prompts
    status_view.py                # combines JARVIS.md/plan/git state for 'status'
    project_scan.py                 # deterministic project scan - structured result, no LLM
    project_scan_view.py              # formats ProjectScanResult for 'jarvis scan'
    project_plan.py                     # derives ProjectPlan from a ProjectScanResult - no LLM
    project_plan_view.py                  # formats ProjectPlan for 'jarvis plan'
    project_work.py                         # picks the next step from a ProjectPlan - no LLM, no writes
    project_work_view.py                      # formats WorkItem for 'jarvis work'
    task_safety.py                              # risk classification + blocked-service/shell rules - single source of truth
    task_intent.py                                # rule-based classification of a free-text task request - no LLM
    task_intent_view.py                             # formats TaskIntent
    task_execution_plan.py                            # intent + scan/plan -> unexecuted ExecutionPlan - no LLM, no writes
    task_execution_plan_view.py                         # formats ExecutionPlan for 'jarvis task'
    task.py                                             # the Task model: id/status/plan/risk_level/requires_approval
    task_planner.py                                       # TaskPlanner: wires intent+scan+plan+execution plan into a Task
    task_view.py                                            # formats Task for 'jarvis task'
    task_run_state.py                                        # RunStep/TaskRun: mutable runtime state for the Task Runner
    task_runner.py                                             # TaskRunner: approval-gated, step-by-step execution core (dry-run only)
    task_runner_view.py                                          # formats TaskRun
    command_console.py                                             # handle_command(): orchestrates TaskPlanner -> TaskRunner for 'komanda:' (REPL) and mirrors run_cli_run()
    project_review.py                           # structured pre-commit review from scan+plan+git - no LLM, no writes
    project_review_view.py                        # formats ProjectReview for 'jarvis review'
    project_precommit.py                            # final pre-commit check built on ProjectReview - no LLM, no writes, no tests run
    project_precommit_view.py                         # formats PrecommitCheck for 'jarvis precommit'
    project_commit.py                                   # staged/unstaged commit plan built on PrecommitCheck - no LLM, never runs git
    project_commit_view.py                                # formats CommitPlan for 'jarvis commit'
    commit_message.py                                       # isolated, tool-free LLM commit-message suggestion - advisory only, never auto-applied
    precommit_view.py              # review + real test run for 'precommit'
    project_notes.py              # loads optional JARVIS.md at startup
    llm.py                          # thin wrapper around the Claude API
    agent.py                          # orchestration loop: LLM <-> tools, with interrupt/error handling
  tools/
    base.py              # Tool interface + registry
    fs.py                  # read_file / write_file / append_to_file / list_directory / delete_file / create_directory / move_file / search_files / replace_in_file / edit_file_lines
    shell.py                 # run_command - allowlisted, sandboxed, approval-gated
    tasks.py                   # manage_tasks - no approval prompt, no sandbox effect
    plan.py                      # create_plan - atomic plan creation, no approval prompt
    workflow_state.py              # get_workflow_state - bridges the agent to scan/plan/work/review/precommit, no approval prompt
    email_tools.py                   # 4 read-only email tools - bridge the agent to EmailConnector
    stripe_tools.py                    # 4 read-only Stripe tools - bridge the agent to StripeConnector
  session/
    store.py                # JSON session persistence
    trim.py                   # keeps long sessions within the model's context budget
    tasks.py                   # persistent local task list (.jarvis/tasks.json)
  integrations/
    base.py              # Connector/ExternalAction interface + RiskLevel
    credentials.py         # env-var-only credential lookup/status, generalized from ANTHROPIC_API_KEY
    approval.py               # confirm_external_action - stricter gate for external actions
    registry.py                 # IntegrationRegistry - tracks registered/configured connectors
    oauth.py                      # connector-agnostic OAuth: OAuthTokens, TokenStore (OS keychain), authorization URL - no real token exchange implemented
    manager.py                      # IntegrationsManager - status (not_configured/connected/disconnected/error), connect/disconnect
    manager_view.py                   # formats IntegrationsManager.list_statuses() for 'jarvis integrations'
    connectors/
      email.py                    # EmailConnector - IMAP, read-only v1, password or OAuth (check/info/list/read)
      stripe.py                   # StripeConnector - REST API + API key, read-only v1 (get_balance/list_recent_charges/get_charge_status/list_recent_payment_intents)
      google_calendar.py          # GoogleCalendarConnector - REST API, OAuth-only, read-only v1 (list_upcoming_events/get_event)
  cli/
    main.py                     # the REPL entry point (+ 'jarvis scan'/'jarvis plan'/'jarvis work'/'jarvis task'/'jarvis run'/'jarvis review'/'jarvis precommit'/'jarvis commit'/'jarvis integrations' CLI arguments), including 'help', 'status', 'integrations', 'scan', 'history [N]', 'tasks', 'review', 'precommit', 'commit'
tests/                             # 1452 tests covering sandbox, secrets, interrupts, trimming, tools
```

## Running the tests

```
.venv\Scripts\python.exe -m pytest tests/ -v
```

## Type checking

Install dev dependencies (`pip install -r requirements-dev.txt`), then:

```
.venv\Scripts\python.exe -m pyright
```

Runs against `jarvis/` only (not `tests/` - test code, especially mocking,
doesn't benefit much from strict typing and isn't worth the annotation
overhead here). `mypy` was tried first but its compiled extension was
blocked by this machine's Application Control policy; `pyright` doesn't hit
that issue and is used instead. Config lives in `[tool.pyright]` in
`pyproject.toml`.

## What's out of scope for v0.1

Deliberately deferred to keep the first version small and trustworthy: mutating
shell commands (`git add`, etc.), recursive directory deletion, binary/non-text
file support, voice interface, a GUI, multi-agent orchestration, a plugin
system, and long-term memory/RAG.
