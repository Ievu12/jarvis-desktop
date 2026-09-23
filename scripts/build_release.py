"""Builds one JARVIS release artifact set: runs the test suite and
Pyright, builds JARVIS.exe via PyInstaller, code-signs it, packages it
as release_artifacts/JARVIS-vX.Y.Z.zip, and writes
release_artifacts/SHA256SUMS.txt (the same format jarvis.gui.updater
._extract_expected_checksum() parses). Does NOT bump the version, edit
CHANGELOG.md, or publish anything to GitHub - see RELEASE.md for the
full release process this script is one step of, and scripts/release.py
for the step that publishes what this script produces.

Usage: python scripts/build_release.py [--skip-tests] [--skip-signing]

Stops at the first failing step (tests, Pyright, or the build itself) -
never proceeds to build/sign/package on top of a codebase that doesn't
pass its own test suite or type check. --skip-tests/--skip-signing exist
only for fast local iteration on the packaging steps themselves; a real
release always runs with both checks enabled (see RELEASE.md - CI/the
person doing the release must never pass these flags for an actual
published release).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
DIST_DIR = PROJECT_ROOT / "dist" / "JARVIS"
RELEASE_ARTIFACTS_DIR = PROJECT_ROOT / "release_artifacts"

# Matches jarvis.gui.updater's expectations: a .zip release asset plus a
# SHA256SUMS.txt asset, both attached to the GitHub release.
_ZIP_ASSET_NAME_TEMPLATE = "JARVIS-v{version}.zip"


def _run(description: str, args: list[str]) -> None:
    print(f"==> {description}")
    result = subprocess.run(args, cwd=PROJECT_ROOT)
    if result.returncode != 0:
        print(f"FAILED: {description} (exit code {result.returncode})")
        sys.exit(1)


def _read_version() -> str:
    namespace: dict[str, str] = {}
    exec((PROJECT_ROOT / "jarvis" / "__version__.py").read_text(encoding="utf-8"), namespace)
    return namespace["__version__"]


def _sign_executable(exe_path: Path) -> bool:
    """Signs exe_path with the JARVIS Desktop self-signed code-signing
    certificate (see RELEASE.md for how it was created and why a
    self-signed cert is acceptable for this project's current
    distribution model - a single person's own machines, not public
    distribution). Returns False (never raises) if no matching
    certificate is found, so a machine without the cert installed can
    still build (just produces an unsigned .exe, which WDAC-enforced
    machines will then refuse to run - see RELEASE.md)."""
    find_cert_script = (
        "$cert = Get-ChildItem Cert:\\CurrentUser\\My -CodeSigningCert | "
        "Where-Object { $_.Subject -like '*JARVIS Desktop*' } | Select-Object -First 1; "
        "if ($cert) { "
        f"  $result = Set-AuthenticodeSignature -FilePath '{exe_path}' -Certificate $cert "
        "-TimestampServer 'http://timestamp.digicert.com'; "
        "  if ($result.Status -eq 'Valid') { exit 0 } else { exit 1 } "
        "} else { exit 2 }"
    )
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", find_cert_script], cwd=PROJECT_ROOT
    )
    return result.returncode == 0


def build(*, skip_tests: bool, skip_signing: bool) -> Path:
    if not skip_tests:
        _run("Running test suite", [str(PYTHON), "-m", "pytest", "-q"])
        _run("Running Pyright", [str(PYTHON), "-m", "pyright"])
    else:
        print("==> Skipping tests/Pyright (--skip-tests)")

    version = _read_version()
    print(f"==> Building JARVIS v{version}")

    if DIST_DIR.exists():
        shutil.rmtree(DIST_DIR)
    build_dir = PROJECT_ROOT / "build"
    if build_dir.exists():
        shutil.rmtree(build_dir)

    _run(
        "Running PyInstaller",
        [str(PYTHON), "-m", "PyInstaller", "jarvis.spec", "--noconfirm"],
    )

    exe_path = DIST_DIR / "JARVIS.exe"
    if not exe_path.exists():
        print(f"FAILED: expected {exe_path} to exist after PyInstaller build")
        sys.exit(1)

    if not skip_signing:
        print("==> Code-signing JARVIS.exe")
        if not _sign_executable(exe_path):
            print(
                "FAILED: could not sign JARVIS.exe - no 'JARVIS Desktop' code-signing "
                "certificate found in CurrentUser\\My. See RELEASE.md to create one, "
                "or pass --skip-signing for a local-only test build."
            )
            sys.exit(1)
    else:
        print("==> Skipping code signing (--skip-signing) - this build will be "
              "refused by any machine enforcing WDAC/Application Control.")

    RELEASE_ARTIFACTS_DIR.mkdir(exist_ok=True)
    zip_name = _ZIP_ASSET_NAME_TEMPLATE.format(version=version)
    zip_path = RELEASE_ARTIFACTS_DIR / zip_name
    if zip_path.exists():
        zip_path.unlink()

    print(f"==> Packaging {zip_name}")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in DIST_DIR.rglob("*"):
            if file_path.is_file():
                zf.write(file_path, file_path.relative_to(DIST_DIR))

    print("==> Writing SHA256SUMS.txt")
    import hashlib

    digest = hashlib.sha256()
    with zip_path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    checksum_hex = digest.hexdigest()

    checksums_path = RELEASE_ARTIFACTS_DIR / "SHA256SUMS.txt"
    # Two lines for the SAME file's checksum, under two filenames:
    # jarvis.gui.updater.download_update() always saves the downloaded
    # release asset locally as "update.zip" regardless of its name on
    # GitHub (see that function's docstring) and looks up its checksum
    # by that fixed name - the second line (the real, versioned asset
    # filename) is for a human inspecting this file, not read by code.
    checksums_path.write_text(
        f"{checksum_hex}  update.zip\n{checksum_hex}  {zip_name}\n", encoding="utf-8",
    )

    print(f"==> Done. Artifacts in {RELEASE_ARTIFACTS_DIR}")
    print(f"    {zip_path.name}")
    print(f"    SHA256SUMS.txt")
    return zip_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-signing", action="store_true")
    args = parser.parse_args()
    build(skip_tests=args.skip_tests, skip_signing=args.skip_signing)


if __name__ == "__main__":
    main()
