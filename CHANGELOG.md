# Changelog

All notable changes to JARVIS are documented here. Format loosely
follows [Keep a Changelog](https://keepachangelog.com/); versions
follow [Semantic Versioning](https://semver.org/).

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
