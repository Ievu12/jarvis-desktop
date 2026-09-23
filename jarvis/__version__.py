"""The single source of truth for JARVIS's version number - imported by
the GUI (for the "Current version: JARVIS vX.Y.Z" display and the
auto-updater's comparison against the latest GitHub release) and by the
build/release scripts (scripts/build_release.py, scripts/release.py),
so the version is never duplicated or allowed to drift between what's
displayed, what's built, and what's tagged in git.

Semantic versioning (https://semver.org): MAJOR.MINOR.PATCH.
  - MAJOR: breaking changes (e.g. a config file format change requiring
    manual migration, a removed feature).
  - MINOR: new functionality, backward compatible (e.g. a new tool, a
    new integration).
  - PATCH: bug fixes, no new functionality or behavior change.

Bumping this is one explicit step of the release process documented in
RELEASE.md - never done automatically by any test or build script.
"""

__version__ = "1.0.1"
