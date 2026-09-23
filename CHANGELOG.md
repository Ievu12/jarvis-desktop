# Changelog

All notable changes to JARVIS are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/); versions
follow [Semantic Versioning](https://semver.org/).

## [1.1.0] - 2026-09-23

### Added
- Redesigned the desktop app into a premium multi-panel dashboard:
  a sidebar (Home, Chat, Tasks, Instagram, Gmail, Stripe, Content,
  Analytics, Automations, Settings) with an ONLINE/THINKING/WORKING/
  WAITING/ERROR status indicator, replacing the single chat window.
  Dark graphite/blue-violet visual theme with subtle status-pulse and
  fade animations.
- `jarvis/gui/dashboard_data.py`: a new read-only service layer
  exposing integration status, tasks, recent activity, and scheduled
  automations (read from Windows Task Scheduler) to the new panels -
  no duplicated business logic; every panel either reads through this
  layer or triggers the existing chat/agent path.
- Quick Actions across Home/Instagram/Gmail/Stripe/Content/Analytics
  panels all route through one shared entry point
  (`JarvisApp.submit_chat_message()`), the same `Agent.step()` path
  chat and voice already used.
- `scripts/dev_watch.py`: a live-reload development runner that
  automatically restarts `python -m jarvis.gui.app` when `jarvis/**
  *.py` changes, so source-run development no longer requires a manual
  restart after every edit. Affects source runs only, never the
  packaged `.exe`.
- Settings window gained tabs for Updates (unchanged behavior),
  General, Appearance, Notifications, Integrations, Automations, AI,
  and Security (the last five currently informational/read-only,
  pending their own settings model).

### Changed
- `jarvis/gui/app.py` restructured into a shell around the existing,
  unmodified chat/voice/update logic - every existing widget attribute
  and method (`self.transcript`, `self.send_button`, `self.mic_button`,
  `self.status_label`, `self.voice_toggle`, `_submit_user_input()`,
  etc.) keeps its exact name and behavior; the full existing test
  suite passes unmodified except for one test adapted to the Settings
  window's new nested tab structure.

## [1.0.2] - 2026-09-23

### Fixed
- **Critical**: `download_update()` and `install_update()` used the
  same staging directory name ("JARVIS_new"), so `install_update()`
  deleted the just-downloaded, checksum-verified update file before
  ever reading it - every real update attempt would have failed at the
  install step. Found via a live end-to-end test against the real
  v1.0.1 release; fixed by giving the download its own directory
  ("JARVIS_download", separate from install's internal staging area).
  A regression test (a full real-file round trip through both
  functions) now guards against this recurring.

## [1.0.1] - 2026-09-23

### Changed
- Made the `jarvis-desktop` GitHub repository public. The private
  repository required an authenticated request to even list releases,
  which would have meant embedding a GitHub token in the distributed
  JARVIS.exe to make auto-update work - against this project's own
  "no API keys in source/binaries" principle. No integration
  credential (Instagram, Gmail, Stripe, Azure, Anthropic) was ever in
  source control; only the application code itself is now public.

### Fixed
- End-to-end verification of the real auto-update flow against a
  published GitHub release (this version exists specifically to prove
  that flow works before relying on it).

## [1.0.0] - 2026-09-23

### Added
- Initial versioned release. JARVIS desktop GUI (customtkinter):
  chat window, microphone voice input (Lithuanian, lt-LT), spoken
  replies (Windows SAPI / Azure Neural TTS fallback), conversation
  history, tool-approval confirmation dialogs, a Settings window with
  update preferences.
- Full existing agent/tool/integration stack carried over unchanged:
  file tools, shell tool, task planning, Gmail, Google Calendar,
  Stripe, Instagram (read-only Insights, daily reports, historical
  comparison, morning content briefing).
- Git repository (private, `Ievu12/jarvis-desktop`) and GitHub
  Releases-based auto-update system: checks the latest release on
  startup/on demand, downloads and SHA256-verifies updates before
  installing, and rolls back automatically if a newly installed
  version fails to start. See RELEASE.md for the full process.
- Windows build via PyInstaller (`jarvis.spec`), code-signed with a
  self-signed certificate so it runs on machines enforcing WDAC/
  Application Control.
- Personal data (conversation history, Instagram data, settings)
  stored separately from the installed program directory
  (`%LOCALAPPDATA%\JARVIS\.jarvis\` for a packaged build), so an
  update never touches it.
