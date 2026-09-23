"""Live-reload development runner for the JARVIS desktop app
(jarvis.gui.app): watches jarvis/**/*.py for changes and automatically
restarts 'python -m jarvis.gui.app' whenever a file changes, so you
don't have to manually stop/rebuild/relaunch after every small edit
while developing.

WHY a restart-on-change watcher, not true in-process hot-swapping:
this project uses customtkinter (Tkinter) - see jarvis.gui.app's own
module docstring on the technology this GUI is actually built on.
Tkinter has no supported mechanism for safely swapping a running
widget's underlying code (importlib.reload() against live Tkinter
widgets routinely corrupts event bindings/widget state) - restarting
the whole Python process is the correct, safe reload strategy for this
stack, giving a clean, correctly-initialized window on every change
rather than a fragile partial hot-swap.

Scope: this ONLY affects `python -m jarvis.gui.app` / this script's own
launches during development. It has no effect on and makes no change to
the packaged JARVIS.exe (PyInstaller build) or scripts/build_release.py -
those remain exactly as built, unaffected by this watcher's existence.

Usage: python scripts/dev_watch.py
Stop with Ctrl+C - the currently-running JARVIS process (if any) is
terminated before this script exits.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WATCHED_DIR = PROJECT_ROOT / "jarvis"
PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"

# How long (seconds) to wait after the LAST detected change before
# actually restarting - a single save can trigger several filesystem
# events in quick succession (e.g. an editor's atomic-save-via-rename),
# and restarting once per burst is both faster and avoids launching
# several overlapping JARVIS processes.
_DEBOUNCE_SECONDS = 0.6


class _RestartOnPyChange(FileSystemEventHandler):
    def __init__(self, on_change) -> None:
        self._on_change = on_change

    def on_any_event(self, event) -> None:
        if event.is_directory:
            return
        if not str(event.src_path).endswith(".py"):
            return
        self._on_change()


def _launch_jarvis() -> subprocess.Popen:
    print(f"[dev_watch] Starting JARVIS ({PYTHON} -m jarvis.gui.app)...")
    return subprocess.Popen(
        [str(PYTHON), "-m", "jarvis.gui.app"], cwd=PROJECT_ROOT,
    )


def _stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()


def main() -> None:
    if not PYTHON.exists():
        print(f"[dev_watch] Could not find {PYTHON} - is the virtualenv set up?")
        sys.exit(1)

    state = {"process": _launch_jarvis(), "pending_restart": False, "last_change_at": 0.0}

    def _mark_change() -> None:
        state["pending_restart"] = True
        state["last_change_at"] = time.monotonic()

    handler = _RestartOnPyChange(on_change=_mark_change)
    observer = Observer()
    observer.schedule(handler, str(WATCHED_DIR), recursive=True)
    observer.start()

    print(f"[dev_watch] Watching {WATCHED_DIR} for .py changes. Press Ctrl+C to stop.")

    try:
        while True:
            time.sleep(0.2)

            # If the running JARVIS process was closed manually (e.g.
            # the person closed the window), just wait - don't relaunch
            # it on our own until a code change asks for a restart, so
            # closing the window during development behaves the same as
            # it normally would, not like a crash-loop.
            if state["pending_restart"] and (time.monotonic() - state["last_change_at"]) >= _DEBOUNCE_SECONDS:
                state["pending_restart"] = False
                print("[dev_watch] Change detected - restarting JARVIS...")
                _stop(state["process"])
                state["process"] = _launch_jarvis()
    except KeyboardInterrupt:
        print("\n[dev_watch] Stopping...")
    finally:
        observer.stop()
        observer.join(timeout=5)
        _stop(state["process"])


if __name__ == "__main__":
    main()
