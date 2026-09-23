"""Publishes a JARVIS release to GitHub Releases from the artifacts
scripts/build_release.py already produced (release_artifacts/JARVIS-vX.Y.Z.zip
and release_artifacts/SHA256SUMS.txt). Does not build anything itself -
run scripts/build_release.py first. See RELEASE.md for the full,
step-by-step release process this script is one step of.

Usage: python scripts/release.py [--notes-file PATH] [--draft]

Requires the GitHub CLI ('gh') to be installed and authenticated
(gh auth status) with a token that has the 'repo' scope on
jarvis.gui.updater.GITHUB_OWNER/GITHUB_REPO - the same account used to
create the repository (see RELEASE.md for the one-time setup). Publishes
a tag matching "v{jarvis.__version__.__version__}" - refuses to proceed
if that tag already exists (a release must never silently overwrite an
already-published version).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RELEASE_ARTIFACTS_DIR = PROJECT_ROOT / "release_artifacts"


def _read_version() -> str:
    namespace: dict[str, str] = {}
    exec((PROJECT_ROOT / "jarvis" / "__version__.py").read_text(encoding="utf-8"), namespace)
    return namespace["__version__"]


def _read_changelog_section(version: str) -> str:
    """Extracts the CHANGELOG.md section for this version (the text
    between "## [X.Y.Z]" and the next "## [" heading, or end of file) to
    use as the GitHub release's notes - so release notes are written
    once, in CHANGELOG.md, not duplicated into a separate release-notes
    file. Falls back to a generic message if the version's heading
    isn't found (RELEASE.md's process is to always add one first, so
    this is a safety net, not the expected path)."""
    changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    heading = f"## [{version}]"
    if heading not in changelog:
        return f"JARVIS v{version}. See CHANGELOG.md for details."

    start = changelog.index(heading)
    rest = changelog[start:]
    next_heading_idx = rest.find("\n## [", 1)
    section = rest if next_heading_idx == -1 else rest[:next_heading_idx]
    # Drop the "## [X.Y.Z] - date" heading line itself - GitHub already
    # shows the tag/version as the release title.
    return section.split("\n", 1)[1].strip() if "\n" in section else ""


def _tag_exists(tag: str) -> bool:
    result = subprocess.run(
        ["git", "tag", "--list", tag], cwd=PROJECT_ROOT, capture_output=True, text=True
    )
    return tag in result.stdout.split()


def _find_gh_executable() -> str:
    """The 'gh' executable is at a fixed install path on this machine
    (not yet on PATH for every shell - see RELEASE.md's one-time setup
    notes) - checked first, then falls back to plain 'gh' in case PATH
    has since been updated."""
    fixed_path = Path("C:/Program Files/GitHub CLI/gh.exe")
    if fixed_path.exists():
        return str(fixed_path)
    return "gh"


def publish(*, notes_file: str | None, draft: bool) -> None:
    version = _read_version()
    tag = f"v{version}"

    zip_path = RELEASE_ARTIFACTS_DIR / f"JARVIS-v{version}.zip"
    checksums_path = RELEASE_ARTIFACTS_DIR / "SHA256SUMS.txt"
    if not zip_path.exists() or not checksums_path.exists():
        print(
            f"FAILED: expected both {zip_path.name} and SHA256SUMS.txt in "
            f"{RELEASE_ARTIFACTS_DIR} - run scripts/build_release.py first."
        )
        sys.exit(1)

    if _tag_exists(tag):
        print(f"FAILED: tag {tag} already exists - a release must never be republished "
              "under the same version. Bump jarvis/__version__.py first.")
        sys.exit(1)

    notes = Path(notes_file).read_text(encoding="utf-8") if notes_file else _read_changelog_section(version)

    gh = _find_gh_executable()
    print(f"==> Creating GitHub release {tag}")
    result = subprocess.run(
        [
            gh, "release", "create", tag,
            str(zip_path), str(checksums_path),
            "--title", f"JARVIS {tag}",
            "--notes", notes,
            *(["--draft"] if draft else []),
        ],
        cwd=PROJECT_ROOT,
    )
    if result.returncode != 0:
        print(f"FAILED: gh release create exited with code {result.returncode}")
        sys.exit(1)

    print(f"==> Published {tag}. Desktop installs will detect it on their next "
          "startup check (or 'Check for updates' click).")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notes-file", default=None, help="Path to a file to use as release notes instead of CHANGELOG.md's section for this version.")
    parser.add_argument("--draft", action="store_true", help="Create the release as a draft (not visible to the update checker until published).")
    args = parser.parse_args()
    publish(notes_file=args.notes_file, draft=args.draft)


if __name__ == "__main__":
    main()
