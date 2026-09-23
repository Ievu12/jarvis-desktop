# JARVIS Release Process

This is the exact process Claude Code follows when asked to "create a
new JARVIS release" (or similar), and what a person can follow to do it
by hand. It always runs in this order; never skip a step.

## One-time setup (already done for this project - reference only)

1. **GitHub repository**: `github.com/Ievu12/jarvis-desktop` (private).
   Created via `gh repo create jarvis-desktop --private --source=. --remote=origin --push`.
2. **GitHub CLI authenticated**: `gh auth login --hostname github.com --git-protocol https --web`,
   with the `repo` scope. Installed at `C:\Program Files\GitHub CLI\gh.exe`.
3. **Code-signing certificate**: a self-signed certificate ("JARVIS
   Desktop (Ieva Navickiene)"), installed into the CurrentUser `Root`
   and `TrustedPublisher` certificate stores. Created with:
   ```powershell
   $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject "CN=JARVIS Desktop (Ieva Navickiene)" -CertStoreLocation "Cert:\CurrentUser\My" -KeyUsage DigitalSignature -FriendlyName "JARVIS Desktop Code Signing" -NotAfter (Get-Date).AddYears(5)
   ```
   then added to the Root/TrustedPublisher stores (see git history for
   the exact commands). **Why self-signed**: this machine enforces WDAC
   (Windows Defender Application Control) in Enforced mode, which
   refuses to run ANY unsigned .exe - confirmed by testing. A
   self-signed certificate installed into this machine's own trust
   stores satisfies that for this machine and any other machine you
   explicitly install the same certificate on. It does **not** make
   JARVIS.exe trusted on an arbitrary third party's machine - that would
   require a certificate from a public CA, which is not set up (there is
   currently no plan to distribute JARVIS beyond your own machines). If
   that changes, replace the self-signed cert with a real one in
   `scripts/build_release.py`'s `_sign_executable()` - nothing else
   needs to change.
4. **Local dependencies for building**: `customtkinter`, `pyinstaller`,
   `packaging` (already in `pyproject.toml`'s dependencies, installed in
   `.venv`).

## Every release: the exact steps

### 1. Make the code change
Normal Claude Code work: edit `jarvis/**`, add/update tests, keep
`pytest -q` and `pyright` passing. Nothing release-specific here.

### 2. Decide the version bump
Semantic versioning (see `jarvis/__version__.py`'s own docstring):
- **PATCH** (1.0.0 → 1.0.1): bug fix, no behavior/feature change.
- **MINOR** (1.0.0 → 1.1.0): new feature, backward compatible.
- **MAJOR** (1.0.0 → 2.0.0): breaking change (e.g. a `.jarvis/` data
  format change requiring migration, a removed feature).

### 3. Bump the version in BOTH places
- `jarvis/__version__.py`: `__version__ = "X.Y.Z"`
- `pyproject.toml`: `version = "X.Y.Z"` under `[project]`

These must always match - `jarvis/__version__.py` is what the running
app/updater actually reads; `pyproject.toml`'s copy is metadata for
packaging tools. Nothing currently derives one from the other
automatically.

### 4. Add a CHANGELOG.md entry
Add a new `## [X.Y.Z] - YYYY-MM-DD` section at the top of the version
list (below the `# Changelog` header), following the existing sections'
`### Added` / `### Fixed` / `### Changed` / `### Breaking` shape. This
text becomes the GitHub release's notes automatically (see step 6) -
write it for a person reading the release, not as a commit-message-style
list.

### 5. Run the full verification (do this yourself; build_release.py also runs it)
```
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m pyright
```
Both must be clean. Never proceed to building on a failing check.

### 6. Commit the version bump
```
git add jarvis/__version__.py pyproject.toml CHANGELOG.md
git commit -m "Release vX.Y.Z"
git push
```

### 7. Build the release artifacts
```
.venv\Scripts\python.exe scripts\build_release.py
```
This runs the test suite and Pyright again (redundant with step 5 on
purpose - a release build must never trust "it was clean a few edits
ago"), builds `JARVIS.exe` via PyInstaller (`jarvis.spec`), code-signs
it with the certificate from the one-time setup, and writes:
- `release_artifacts/JARVIS-vX.Y.Z.zip`
- `release_artifacts/SHA256SUMS.txt`

If any step fails, the script stops immediately (exit code 1) - fix the
failure and re-run rather than working around it.

### 8. Publish the GitHub release
```
.venv\Scripts\python.exe scripts\release.py
```
This creates a git tag `vX.Y.Z`, a GitHub release titled "JARVIS vX.Y.Z"
using CHANGELOG.md's section for this version as the release notes, and
attaches both artifacts from step 7. Refuses to run if that tag already
exists - a version is never republished silently.

### 9. Confirm it's live
```
"C:\Program Files\GitHub CLI\gh.exe" release view vX.Y.Z --repo Ievu12/jarvis-desktop
```
Should show the release, both assets, and the changelog notes.

## How desktop installs detect the new version

`jarvis.gui.updater.check_for_update()` calls the public, tokenless
GitHub REST endpoint `GET /repos/Ievu12/jarvis-desktop/releases/latest`
and compares the tag's version (semantic comparison via `packaging
.version.Version`, not a string comparison) against the running
`jarvis.__version__.__version__`. This happens:
- **Automatically on startup**, if Settings → "Automatically check for
  updates" is on (default: on).
- **On demand**, via the Settings window's "Check for updates" button.

If a newer version is found and "Automatically download updates" is on
(default: on), it downloads the release zip and `SHA256SUMS.txt`,
verifies the zip's SHA256 checksum matches before doing anything else,
and only then either installs immediately (if "Automatically install
updates" is on - **default: off**) or waits for the person to click
"Update now".

## How your data is protected during an update

`jarvis.config.JARVIS_DATA_DIR` (where `.jarvis/session.json`, Instagram
history, update settings, and the audit log live) is **not** the same
directory `jarvis.gui.updater.install_update()` replaces:
- Running a packaged `JARVIS.exe`: data lives in
  `%LOCALAPPDATA%\JARVIS\.jarvis\` - a sibling of, not inside,
  `dist/JARVIS/` (where the .exe and its `_internal/` bundle live, which
  IS what gets replaced on update).
- Running from source (`python -m jarvis.cli.main`, etc.): data lives in
  the project directory's `.jarvis/`, exactly as before this system
  existed - unaffected by any of this.

`install_update()` also never deletes the current installation outright:
it moves the current `dist/JARVIS/` contents to `JARVIS_backup/` (a
sibling directory) before putting the new version in place, and
`jarvis.gui.app` calls `verify_executable_starts()` right after
installing - if the new version fails to launch, `rollback_update()`
automatically restores `JARVIS_backup/` and preserves the failed attempt
under `JARVIS_failed_update/` for inspection.

## Toggling auto-update behavior

Settings window (⚙️ Settings button, top-right of the main window):
- **Automatically check for updates** - on by default. Turn off to
  never have JARVIS contact GitHub on its own; "Check for updates" still
  works on demand.
- **Automatically download updates** - on by default. Only takes effect
  when a check finds a newer version.
- **Automatically install updates** - **off by default**. When off (the
  normal case), a downloaded update waits for you to click "Update now"
  before anything on disk changes. Turning this on means a checksum-
  verified update installs without asking - only do this if you're
  comfortable with that.

These persist locally (`jarvis.gui.settings_store`, in
`JARVIS_DATA_DIR/.jarvis/update_settings.json`) and survive updates,
same as your other data.

## Building the very first .exe (or rebuilding from scratch)

```
.venv\Scripts\python.exe scripts\build_release.py
```
Produces a signed `dist\JARVIS\JARVIS.exe` plus the zipped release
artifacts. Run `dist\JARVIS\JARVIS.exe` directly to try it locally
(double-click, or `& .\dist\JARVIS\JARVIS.exe` in PowerShell).

## Verifying the whole update flow end-to-end (manual)

There is no automated integration test that publishes a real GitHub
release (that would spam the repo's release list on every test run) -
`tests/test_gui_updater.py` covers the logic with mocked network calls.
To manually verify the real path before trusting a release to real
users:
1. Publish a release (steps above) with a test version bump.
2. On a machine with an OLDER version installed, open Settings → "Check
   for updates" and confirm it reports the new version.
3. Click "Update now" and confirm: download succeeds, checksum
   verification passes (try corrupting a downloaded zip once to confirm
   a mismatch is refused), install succeeds, and the app still has your
   previous conversation history/settings after relaunching.
