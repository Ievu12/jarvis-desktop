"""PyInstaller entry point for the JARVIS.exe desktop build - the file
named in jarvis.spec's Analysis(["jarvis_launcher.py"], ...). Kept as a
thin, separate top-level script (not jarvis/gui/app.py itself) so the
built executable's entry point is obvious from the project root and
independent of jarvis.gui.app's internal structure. All real logic
lives in jarvis.gui.app.main() - this file only calls it.
"""

from __future__ import annotations

from jarvis.gui.app import main

if __name__ == "__main__":
    main()
