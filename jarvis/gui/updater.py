"""Auto-update: checks GitHub Releases for a newer JARVIS version,
downloads it, verifies its SHA256 checksum, and installs it - all
several distinct, independently-callable steps (never one big "just
update" function), so jarvis.gui.app's Settings screen can offer
"check", "download", and "install" as separate user actions/toggles per
the person's requested settings (Automatically check / Automatically
download / Automatically install - the last one defaulting OFF, since
silently replacing a running program's files is the one step this
module never does without a person's explicit action, regardless of
settings, in this version - see install_update()'s docstring).

Update source: GitHub Releases on the project's own repository
(read from jarvis.__version__ alongside the public, tokenless GitHub
REST API - api.github.com/repos/<owner>/<repo>/releases/latest - no
GITHUB_TOKEN or any secret is needed to READ a public release's
metadata, even on a private repository's releases endpoint when called
with no auth token it simply 404s cleanly, handled below). Nothing here
ever uploads or sends anything - it only reads release metadata and
downloads release assets.

Safety properties (see each function's docstring for detail):
  - download_update() writes to a temp file, never touching the running
    installation until checksum verification has already passed.
  - install_update() only replaces files after SHA256 verification
    succeeds, and moves the CURRENT installation aside (never deletes
    it) before the new one takes its place - see install_update()'s
    docstring for the exact sequence and how rollback_update() reverses
    it if the newly installed version fails to start.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from packaging.version import InvalidVersion, Version

from jarvis.__version__ import __version__

# The GitHub repository releases are published to - see RELEASE.md for
# how a new release gets here. Not user-configurable (yet): pointing
# this at an arbitrary repository would mean downloading and running
# arbitrary code, so it is a fixed constant, not a Settings field.
GITHUB_OWNER = "Ievu12"
GITHUB_REPO = "jarvis-desktop"

_RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
_REQUEST_TIMEOUT_SECONDS = 15
_DOWNLOAD_TIMEOUT_SECONDS = 120

# Where update artifacts are staged/backed up, relative to the running
# installation's directory - never inside the git working tree the
# person edits with Claude Code (this matters for the desktop-installed
# copy, which is a separate PyInstaller build directory, not this
# checked-out source tree - see RELEASE.md).
#
# Deliberately TWO DIFFERENT directories for the downloaded zip
# (_DOWNLOAD_DIRNAME) vs. install_update()'s extraction staging area
# (_STAGING_DIRNAME): install_update() deletes and recreates
# _STAGING_DIRNAME as its very first step (see install_update()'s own
# code) - if the caller's already-downloaded, checksum-verified zip
# lived in that same directory, install_update() would delete the zip
# out from under itself before ever reading it. Confirmed as a real bug
# via a live end-to-end test against a real published GitHub release
# before this fix - never reuse these two directory names for each
# other's purpose.
_DOWNLOAD_DIRNAME = "JARVIS_download"
_STAGING_DIRNAME = "JARVIS_new"
_BACKUP_DIRNAME = "JARVIS_backup"


class UpdateError(Exception):
    """Raised by download_update()/install_update() for any failure
    that must stop the update WITHOUT touching the currently-running
    installation - callers (jarvis.gui.app) catch this and show the
    person a clear error, never a partial/broken install."""


@dataclass
class UpdateCheckResult:
    """check_for_update()'s outcome. update_available is False whenever
    the check could not be completed (network error, malformed release)
    as well as when the installed version is already current - callers
    that need to distinguish "no update" from "check failed" should
    inspect `error`."""

    update_available: bool
    current_version: str
    latest_version: str | None
    changelog: str | None
    download_url: str | None
    checksum_url: str | None
    error: str | None = None


def _parse_version(raw: str) -> Version | None:
    # GitHub tags conventionally look like "v1.2.0" - Version() rejects
    # the leading "v", so it's stripped before parsing. Returns None
    # (never raises) for anything that still doesn't parse, so a
    # malformed tag degrades to "no update detected" rather than
    # crashing the check.
    try:
        return Version(raw.lstrip("vV"))
    except InvalidVersion:
        return None


def check_for_update() -> UpdateCheckResult:
    """Queries the GitHub Releases API for the latest published release
    and compares it against jarvis.__version__.__version__ using
    semantic-version ordering (not a string comparison - "1.10.0" must
    correctly compare greater than "1.9.0"). Never raises: any network
    or parsing failure comes back as UpdateCheckResult(error=...) with
    update_available=False, so a failed check never blocks the rest of
    the app from working."""
    request = urllib.request.Request(
        _RELEASES_API_URL,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "jarvis-updater"},
    )
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # No release has been published yet - not an error the
            # person needs to see, just "nothing to update to".
            return UpdateCheckResult(
                update_available=False, current_version=__version__, latest_version=None,
                changelog=None, download_url=None, checksum_url=None,
            )
        return UpdateCheckResult(
            update_available=False, current_version=__version__, latest_version=None,
            changelog=None, download_url=None, checksum_url=None,
            error=f"GitHub API klaida ({e.code}) tikrinant naujinimus.",
        )
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return UpdateCheckResult(
            update_available=False, current_version=__version__, latest_version=None,
            changelog=None, download_url=None, checksum_url=None,
            error=f"Nepavyko pasiekti GitHub - patikrink interneto ryšį ({e}).",
        )
    except json.JSONDecodeError:
        return UpdateCheckResult(
            update_available=False, current_version=__version__, latest_version=None,
            changelog=None, download_url=None, checksum_url=None,
            error="GitHub grąžino neteisingą atsakymą tikrinant naujinimus.",
        )

    latest_tag = payload.get("tag_name", "")
    latest_version_obj = _parse_version(latest_tag)
    current_version_obj = _parse_version(__version__)
    if latest_version_obj is None or current_version_obj is None:
        return UpdateCheckResult(
            update_available=False, current_version=__version__, latest_version=latest_tag or None,
            changelog=None, download_url=None, checksum_url=None,
            error="Nepavyko palyginti versijų numerių.",
        )

    assets = payload.get("assets", [])
    download_url = next(
        (a.get("browser_download_url") for a in assets if str(a.get("name", "")).endswith(".zip")),
        None,
    )
    checksum_url = next(
        (a.get("browser_download_url") for a in assets if a.get("name") == "SHA256SUMS.txt"),
        None,
    )

    return UpdateCheckResult(
        update_available=latest_version_obj > current_version_obj,
        current_version=__version__,
        latest_version=str(latest_version_obj),
        changelog=payload.get("body") or None,
        download_url=download_url,
        checksum_url=checksum_url,
    )


def _sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_download_dir(install_dir: Path) -> Path:
    """The recommended `destination_dir` for download_update(), given
    the running installation's directory - a sibling of install_dir,
    named distinctly from install_update()'s own _STAGING_DIRNAME (see
    the comment above that constant for why they must never collide).
    jarvis.gui.app uses this rather than hardcoding the directory name
    itself, so this module stays the one place that name is defined."""
    return install_dir.parent / _DOWNLOAD_DIRNAME


def download_update(update: UpdateCheckResult, *, destination_dir: Path) -> Path:
    """Downloads the release zip AND its SHA256SUMS.txt, verifies the
    zip's checksum, and returns the path to the verified zip. Raises
    UpdateError (never a bare exception) on any failure - missing URLs,
    network error, or a checksum mismatch - and in every failure case
    the currently-running installation is untouched, since this
    function only ever writes into `destination_dir` (see
    default_download_dir() for the recommended location - MUST NOT be
    the same directory install_update() uses internally for extraction,
    see _STAGING_DIRNAME's comment), never over the running program's
    own files."""
    if not update.download_url:
        raise UpdateError("Naujos versijos atsisiuntimo nuoroda nerasta.")
    if not update.checksum_url:
        raise UpdateError("Naujos versijos checksum (SHA256SUMS.txt) nuoroda nerasta.")

    destination_dir.mkdir(parents=True, exist_ok=True)
    zip_path = destination_dir / "update.zip"
    checksums_path = destination_dir / "SHA256SUMS.txt"

    try:
        urllib.request.urlretrieve(update.checksum_url, checksums_path)  # noqa: S310 - fixed GitHub host
        urllib.request.urlretrieve(update.download_url, zip_path)  # noqa: S310 - fixed GitHub host
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise UpdateError(f"Atsisiuntimas nepavyko: {e}") from None

    expected_sha256 = _extract_expected_checksum(checksums_path, zip_path.name)
    if expected_sha256 is None:
        raise UpdateError("Nepavyko rasti šio failo checksum reikšmės SHA256SUMS.txt faile.")

    actual_sha256 = _sha256_of_file(zip_path)
    if actual_sha256.lower() != expected_sha256.lower():
        # Never install a file whose checksum doesn't match - delete the
        # bad download from the staging area so a retry starts clean,
        # but the running installation was never touched.
        zip_path.unlink(missing_ok=True)
        raise UpdateError(
            "Atsisiųsto failo checksum NEATITINKA - failas galėjo būti sugadintas arba "
            "pakeistas. Diegimas nutrauktas, nieko nekeičiant."
        )

    return zip_path


def _extract_expected_checksum(checksums_path: Path, filename: str) -> str | None:
    # SHA256SUMS.txt format (the standard `sha256sum` output format):
    # "<hex digest>  <filename>" per line - matches what
    # scripts/build_release.py generates for each release.
    try:
        text = checksums_path.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[1].strip() == filename:
            return parts[0].strip()
    return None


def install_update(
    verified_zip_path: Path, *, install_dir: Path, exe_name: str = "JARVIS.exe"
) -> Path:
    """Installs an already-checksum-verified update zip. Sequence
    (each step only proceeds if the previous one succeeded):
      1. Extract the zip into a fresh JARVIS_new/ directory (never
         touching install_dir yet).
      2. Move the CURRENT install_dir contents into JARVIS_backup/
         (overwriting any previous backup) - a move, not a delete, so
         the previous working version still exists on disk.
      3. Move JARVIS_new/'s contents into install_dir.
    If step 1 or the extraction fails, install_dir is never touched at
    all. If step 2 or 3 fails partway, rollback_update() can restore
    JARVIS_backup/ back into install_dir - see that function. Returns
    the path to the newly installed executable, for the caller to
    launch and verify it starts (jarvis.gui.app's responsibility, not
    this function's - installing and verifying-it-runs are kept
    separate so a caller can decide its own verification strategy).
    """
    staging_dir = install_dir.parent / _STAGING_DIRNAME
    backup_dir = install_dir.parent / _BACKUP_DIRNAME

    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True)

    try:
        shutil.unpack_archive(str(verified_zip_path), extract_dir=str(staging_dir))
    except (shutil.ReadError, OSError) as e:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise UpdateError(f"Nepavyko išpakuoti naujos versijos: {e}") from None

    if not (staging_dir / exe_name).exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise UpdateError(
            f"Išpakuotame archyve nerasta {exe_name} - naujos versijos diegimas nutrauktas."
        )

    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    if install_dir.exists():
        shutil.move(str(install_dir), str(backup_dir))

    try:
        shutil.move(str(staging_dir), str(install_dir))
    except OSError as e:
        # Best-effort rollback: put the previous install back exactly
        # where it was, since install_dir may now be missing/partial.
        if backup_dir.exists() and not install_dir.exists():
            shutil.move(str(backup_dir), str(install_dir))
        raise UpdateError(
            f"Nepavyko įdiegti naujos versijos ({e}) - grąžinta ankstesnė versija."
        ) from None

    return install_dir / exe_name


def rollback_update(*, install_dir: Path) -> bool:
    """Restores the JARVIS_backup/ directory (created by install_update()
    just before replacing install_dir) back into install_dir - used
    when a newly installed version fails to start. Returns False (never
    raises) if there is no backup to restore from, so a caller can
    report "rollback unavailable" rather than crashing. The failed new
    install currently in `install_dir`, if any, is moved aside rather
    than deleted, for post-mortem inspection."""
    backup_dir = install_dir.parent / _BACKUP_DIRNAME
    if not backup_dir.exists():
        return False

    failed_dir = install_dir.parent / "JARVIS_failed_update"
    if failed_dir.exists():
        shutil.rmtree(failed_dir)
    if install_dir.exists():
        shutil.move(str(install_dir), str(failed_dir))

    shutil.move(str(backup_dir), str(install_dir))
    return True


def verify_executable_starts(exe_path: Path, *, timeout_seconds: float = 10.0) -> bool:
    """Best-effort check that a newly installed executable can at least
    launch without immediately crashing - runs it with a flag/short
    timeout and checks it didn't exit with a non-zero code within that
    window. Used by jarvis.gui.app after install_update() to decide
    whether to call rollback_update(). Returns False (never raises) for
    any failure to start, including the exe not existing."""
    if not exe_path.exists():
        return False
    try:
        process = subprocess.Popen(
            [str(exe_path), "--verify-startup"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            exit_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            # Still running after the window - treat as a successful
            # start (a real GUI app is expected to keep running, not
            # exit on its own), and leave it running for the person.
            return True
        return exit_code == 0
    except OSError:
        return False
