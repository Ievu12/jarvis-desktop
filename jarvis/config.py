"""Central configuration. Resolves JARVIS_ROOT once, at import time, so every
other module sees the same canonical boundary."""

import os
import sys
from pathlib import Path

# The project directory itself (parent of the jarvis/ package) is the
# default sandbox root when running from source (jarvis/cli/main.py,
# 'python -m jarvis...', pytest, etc.) - __file__ here is a real file on
# disk in that case, and this is the root jarvis.core.sandbox enforces
# for file tools (read_file/write_file/etc.) - unrelated to where
# personal data lives (see JARVIS_DATA_DIR below).
#
# When running as a PyInstaller-packaged JARVIS.exe (sys.frozen is set
# by PyInstaller's bootloader - see jarvis.spec/jarvis_launcher.py),
# __file__ instead resolves to a path INSIDE the bundle's _internal/
# directory - not a meaningful "project directory" to sandbox file
# tools to, but harmless here since JARVIS_ROOT is not used to decide
# where personal data goes (see JARVIS_DATA_DIR).
if getattr(sys, "frozen", False):
    _DEFAULT_ROOT = Path(sys.executable).resolve().parent
else:
    _DEFAULT_ROOT = Path(__file__).resolve().parent.parent

JARVIS_ROOT: Path = Path(
    os.environ.get("JARVIS_ROOT", str(_DEFAULT_ROOT))
).resolve(strict=True)

# Where the .jarvis/ personal-data directory (session history, Instagram
# data, update settings, audit log) actually lives - DELIBERATELY NOT
# always JARVIS_ROOT, for one critical reason: in a PyInstaller-packaged
# JARVIS.exe, JARVIS_ROOT resolves to the exe's own directory (dist/
# JARVIS/), and jarvis.gui.updater.install_update() replaces that
# directory's ENTIRE contents on every update (see its own docstring).
# If .jarvis/ lived there, every single update would destroy the
# person's conversation history, Instagram data, and settings - exactly
# what the auto-update system exists to prevent (see RELEASE.md).
#
# So when frozen, personal data lives in the standard per-user Windows
# app-data location (%LOCALAPPDATA%\JARVIS\.jarvis\), completely outside
# any directory an update ever touches, and survives an update, a
# reinstall to a different folder, or even deleting and re-extracting
# dist/JARVIS/ entirely. When running from source, it stays exactly
# where it has always been (JARVIS_ROOT/.jarvis) - unchanged behavior,
# so existing local data for anyone running from source is untouched by
# this distinction.
if getattr(sys, "frozen", False):
    _default_data_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "JARVIS"
else:
    _default_data_dir = JARVIS_ROOT

JARVIS_DATA_DIR: Path = Path(os.environ.get("JARVIS_DATA_DIR", str(_default_data_dir)))

ANTHROPIC_API_KEY: str | None = os.environ.get("ANTHROPIC_API_KEY")
"""Loaded once from the environment. Never write this value to a file,
log, or print it directly - use jarvis.core.secrets.mask_secret() for any
display purpose."""

AZURE_SPEECH_KEY: str | None = os.environ.get("AZURE_SPEECH_KEY")
AZURE_SPEECH_REGION: str | None = os.environ.get("AZURE_SPEECH_REGION")
"""Optional Azure Cognitive Services Speech credentials, used only by
jarvis.voice.text_to_speech as a fallback Lithuanian voice when no
Lithuanian SAPI voice is installed in Windows (see that module's
docstring). A service credential, not a per-user OAuth token, so it
follows ANTHROPIC_API_KEY's pattern (plain environment variable, not
jarvis.integrations.oauth.TokenStore) rather than the OAuth connectors'
pattern. Voice mode works without these set - speak() falls back to a
clear "install a Lithuanian voice" message instead of Azure. Never write
either value to a file, log, or print it directly - use
jarvis.core.secrets.mask_secret() for any display purpose."""

OPENAI_API_KEY: str | None = os.environ.get("OPENAI_API_KEY")
"""Optional OpenAI credential, used only by
jarvis.reel_generator.image_generation to generate real photorealistic
scene images (gpt-image-1) for AI Reel Generator's "Visual Story
Director" - see that module's own docstring. Same "plain environment
variable, service credential" pattern as ANTHROPIC_API_KEY/
AZURE_SPEECH_KEY above, not jarvis.integrations.oauth.TokenStore.
AI Reel Generator works without this set - scene image generation
falls back to this codebase's own Pillow-rendered text-card/typography
treatment with a clear, visible notice, never a fake/placeholder image
claimed as real. Never write this value to a file, log, or print it
directly - use jarvis.core.secrets.mask_secret() for any display
purpose."""

RUNWAY_API_KEY: str | None = os.environ.get("RUNWAY_API_KEY")
RUNWAY_API_BASE_URL: str | None = os.environ.get("RUNWAY_API_BASE_URL")
"""Optional Runway ML credential, used only by
jarvis.reel_generator.video_generation to generate real AI video clips
(image-to-video) for AI Reel Generator's NATURAL MOTION / HYBRID modes
- see that module's own docstring. Same "plain environment variable,
service credential" pattern as OPENAI_API_KEY/ANTHROPIC_API_KEY above,
not jarvis.integrations.oauth.TokenStore. AI Reel Generator's STATIC
mode (the default, and every mode's own still-image base frame) works
completely unaffected without this set; NATURAL MOTION/HYBRID scenes
simply fall back to their still image (with an optional Ken Burns pan/
zoom, jarvis.reel_generator.export's own pre-existing motion_by_scene)
and a clear, visible "RUNWAY_API_KEY is not configured" notice - never
a fake/placeholder clip claimed as real. RUNWAY_API_BASE_URL is
optional and only ever needed to point at a non-default API base (e.g.
a test double) - jarvis.reel_generator.video_generation falls back to
Runway's own public API base when unset. Never write either value to a
file, log, or print it directly - use jarvis.core.secrets.mask_secret()
for any display purpose."""

SESSION_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "session.json"
AUDIT_LOG_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "audit.log"
TASKS_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "tasks.json"
INSTAGRAM_INSIGHTS_HISTORY_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "instagram_insights_history.json"
"""One JSON object per stored calendar date (see
jarvis.integrations.instagram_history) - JARVIS's own local record of
daily Instagram Insights snapshots, kept because Meta's API does not
retain account-level insights indefinitely. Contains no OAuth token or
other credential - only numeric metrics and dates."""

CONTENT_TOPICS_HISTORY_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "content_topics_history.json"
"""One JSON object per stored calendar date (see jarvis.content_manager)
- a local record of the content topics/ideas jarvis.morning_routine has
already suggested, kept so the next morning briefing can avoid repeating
recent topics. Contains no credential - only dates and short topic
strings."""

UPDATE_SETTINGS_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "update_settings.json"
"""The desktop GUI's auto-update preferences (see
jarvis.gui.settings_store) - three booleans (check/download/install),
no credential."""

INSTAGRAM_AI_MANAGER_DB_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "instagram_ai_manager.db"
"""SQLite database for the Instagram AI Manager module (see
jarvis.instagram_ai_manager) - generated content (reel ideas, hooks,
captions, CTAs, hashtag sets, story sequences, weekly plans, AI
recommendations). A separate file from every other .jarvis/*.json file
here - deliberately not merged with jarvis.integrations
.instagram_history's INSTAGRAM_INSIGHTS_HISTORY_FILE (that file holds
real Instagram performance data fetched from the Graph API; this one
holds JARVIS/LLM-generated drafts, a different kind of data with
different query needs - date-range/keyword filtering - that a growing
set of separate JSON files would make awkward). Contains no credential;
schema is created idempotently by jarvis.instagram_ai_manager.db on
first use, so nothing needs to run a migration step separately."""

VIDEO_STUDIO_DB_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "video_studio.db"
"""SQLite database for the AI Video Studio module (see
jarvis.video_studio) - one row per video project plus its clips,
transcript segments, subtitle styles, generated hooks/covers/captions,
and exports. Same JSON-blob-per-row pattern as
INSTAGRAM_AI_MANAGER_DB_FILE (see that constant's docstring) - a
separate file rather than reusing that DB, since a video project's
schema (durations, timestamps, file paths) is unrelated to Instagram AI
Manager's content-generation records. Contains no credential; schema is
created idempotently by jarvis.video_studio.db on first use."""

VIDEO_STUDIO_PROJECTS_DIR: Path = JARVIS_DATA_DIR / ".jarvis" / "video_studio" / "projects"
"""Root directory for AI Video Studio's per-project binary assets
(original video, extracted audio, generated clips, subtitles, cover
images, exports) - one subdirectory per project, named by the project's
id (see jarvis.video_studio.storage). Deliberately NOT under
JARVIS_ROOT's sandboxed file-tool tree (jarvis.core.sandbox) - video
files are large, per-user media the desktop GUI reads/writes directly
(same "direct file I/O from a GUI view, no agent-tool sandbox check"
precedent as jarvis.instagram_ai_manager.db - see that module's
docstring), never something the LLM agent's read_file/write_file tools
touch. Lives under JARVIS_DATA_DIR (not JARVIS_ROOT) for the same
survives-an-update reason INSTAGRAM_AI_MANAGER_DB_FILE does - see
JARVIS_DATA_DIR's own docstring above."""

VIDEO_STUDIO_MODELS_DIR: Path = JARVIS_DATA_DIR / ".jarvis" / "models"
"""Local cache for the whisper.cpp GGML transcription model file(s) AI
Video Studio downloads on first use (see jarvis.video_studio.transcribe)
- shared across every video project rather than duplicated per-project.
Not a credential; safe to delete (the next transcription attempt
re-downloads it) - jarvis.video_studio.transcribe treats a missing
model file as "not yet downloaded", not an error, so this directory not
existing yet is a normal state."""

DESIGN_STUDIO_DB_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "design_studio.db"
"""SQLite database for the AI Design Studio module (see
jarvis.design_studio) - one row per design project plus its generated
brief, variants, selected design, and exports. Same JSON-blob-per-row
pattern as VIDEO_STUDIO_DB_FILE/INSTAGRAM_AI_MANAGER_DB_FILE (see those
constants' docstrings) - a separate file rather than reusing either,
since a design project's schema (design briefs, variant image paths,
brand kit) is unrelated to both a video project's and an Instagram
content draft's own records. Contains no credential; schema is created
idempotently by jarvis.design_studio.db on first use."""

DESIGN_STUDIO_PROJECTS_DIR: Path = JARVIS_DATA_DIR / ".jarvis" / "design_studio" / "projects"
"""Root directory for AI Design Studio's per-project binary assets
(generated variant images, the selected/exported design, any uploaded
source images used in that project) - one subdirectory per project,
named by the project's id (see jarvis.design_studio.storage). Same
"direct file I/O from a GUI view, no agent-tool sandbox check"
precedent as VIDEO_STUDIO_PROJECTS_DIR - see that constant's own
docstring for the full reasoning (equally applicable here). Lives
under JARVIS_DATA_DIR (not JARVIS_ROOT) for the same survives-an-update
reason."""

DESIGN_STUDIO_BRAND_KIT_DIR: Path = JARVIS_DATA_DIR / ".jarvis" / "design_studio" / "brand_kit"
"""Storage for the user's personal Brand Kit assets (module brief,
section 6: logo, brand images, product images, personal photos) -
separate from DESIGN_STUDIO_PROJECTS_DIR since brand assets are shared
across every design project (uploaded once, reused many times), not
scoped to one project the way a project's own generated variants are.
The Brand Kit's own preference fields (name, colors, fonts, Instagram
username) are stored in DESIGN_STUDIO_DB_FILE, not here - this
directory holds only the uploaded image files themselves."""

REEL_GENERATOR_DB_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "reel_generator.db"
"""SQLite database for the AI Reel Generator module (see
jarvis.reel_generator) - one row per Reel project plus its brief,
script, storyboard, cover path, caption/hashtag handoff record, and
exports. Same JSON-blob-per-row pattern as DESIGN_STUDIO_DB_FILE/
VIDEO_STUDIO_DB_FILE (see those constants' docstrings) - a separate
file rather than reusing either, since a Reel project's own schema
(script timing, storyboard scenes) is unrelated to both a design
project's and a video project's own records, even though this module
reuses AI Design Studio's rendering and AI Video Studio's export
machinery directly. Contains no credential; schema is created
idempotently by jarvis.reel_generator.db on first use."""

REEL_GENERATOR_PROJECTS_DIR: Path = JARVIS_DATA_DIR / ".jarvis" / "reel_generator" / "projects"
"""Root directory for AI Reel Generator's per-project binary assets
(scene text-card images, uploaded scene images/clips, generated cover,
final exported MP4) - one subdirectory per project, named by the
project's id (see jarvis.reel_generator.storage). Same "direct file
I/O from a GUI view, no agent-tool sandbox check" precedent as
VIDEO_STUDIO_PROJECTS_DIR/DESIGN_STUDIO_PROJECTS_DIR - see either
constant's own docstring for the full reasoning (equally applicable
here). Lives under JARVIS_DATA_DIR (not JARVIS_ROOT) for the same
survives-an-update reason."""

CONTENT_STUDIO_DB_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "content_studio.db"
"""SQLite database for the AI Content Studio module (see
jarvis.content_studio) - one row per content project: its original
topic, generated content plan, chosen content type(s), and a
DRAFT/REVIEW/APPROVED/EXPORTED workflow status. Content Studio is a
thin ORCHESTRATION layer over jarvis.reel_generator/jarvis.design_studio
- a Reel or Post item here stores only a POINTER (that module's own
project id) to the real project living in REEL_GENERATOR_DB_FILE/
DESIGN_STUDIO_DB_FILE, never a duplicated copy of that project's own
brief/script/render data (see jarvis.content_studio's own docstring).
Same JSON-blob-per-row pattern as those modules' own DB files. Contains
no credential; schema is created idempotently by jarvis.content_studio
.db on first use."""

CONTENT_STUDIO_PROJECTS_DIR: Path = JARVIS_DATA_DIR / ".jarvis" / "content_studio" / "projects"
"""Root directory for AI Content Studio's own per-project binary assets
- currently only generated PDF files (jarvis.content_studio.pdf_export),
since every other content type's actual assets (Reel scenes/exports,
Design Studio renders) live in THEIR OWN projects directories
(REEL_GENERATOR_PROJECTS_DIR/DESIGN_STUDIO_PROJECTS_DIR) - Content
Studio never copies those files into its own tree, only points at them
by path (see CONTENT_STUDIO_DB_FILE's own docstring). One subdirectory
per project, named by the project's id (see
jarvis.content_studio.storage). Same "direct file I/O from a GUI view,
no agent-tool sandbox check" precedent as the other modules' own
PROJECTS_DIR constants. Lives under JARVIS_DATA_DIR (not JARVIS_ROOT)
for the same survives-an-update reason."""

STORY_GENERATOR_DB_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "story_generator.db"
"""SQLite database for the AI Storytelling Generator module (see
jarvis.story_generator) - one row per Story project plus its 8-beat
StoryStructure (Hook/Setup/Conflict/Emotional development/Turning
point/Transformation/Conclusion/CTA), its 5-10 Scene objects, and its
exports. Same JSON-blob-per-row pattern as REEL_GENERATOR_DB_FILE (see
that constant's docstring) - a separate file rather than reusing it,
since a Story project's own structure/scene schema is a distinct
record even though this module reuses jarvis.reel_generator.scenes'
visual rendering and jarvis.reel_generator.export's video export
machinery directly (both take a plain tuple of
jarvis.reel_generator.storyboard.Scene objects, which this module
constructs itself - see jarvis.story_generator's own docstring).
Contains no credential; schema is created idempotently by
jarvis.story_generator.db on first use."""

STORY_GENERATOR_PROJECTS_DIR: Path = JARVIS_DATA_DIR / ".jarvis" / "story_generator" / "projects"
"""Root directory for AI Storytelling Generator's per-project binary
assets (scene text-card images, final exported MP4) - one subdirectory
per project, named by the project's id (see
jarvis.story_generator.storage). Same "direct file I/O from a GUI
view, no agent-tool sandbox check" precedent as
REEL_GENERATOR_PROJECTS_DIR - see that constant's own docstring for
the full reasoning (equally applicable here). Lives under
JARVIS_DATA_DIR (not JARVIS_ROOT) for the same survives-an-update
reason."""

VIDEO_EDITOR_DB_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "video_editor.db"
"""SQLite database for the Professional Video Editor module (see
jarvis.video_editor) - one row per edit project: its Timeline (ordered
clips/stills, trim points, retime, transitions - see
jarvis.video_editor.timeline), its imported MediaItems, and its last-
used export settings. A SEPARATE file from VIDEO_STUDIO_DB_FILE,
deliberately never an added column/table on that database - a Video
Editor timeline project is a genuinely different shape of record from
AI Video Studio's own single-source, highlight-driven reel project (see
jarvis.video_editor's own package docstring for the full architectural
reasoning), and keeping them in separate files means a bug in one
module's own schema/migration code can never corrupt the other's data.
Same JSON-blob-per-row pattern as REEL_GENERATOR_DB_FILE/
VIDEO_STUDIO_DB_FILE (see either constant's docstring). Contains no
credential; schema is created idempotently by jarvis.video_editor.db on
first use."""

VIDEO_EDITOR_PROJECTS_DIR: Path = JARVIS_DATA_DIR / ".jarvis" / "video_editor" / "projects"
"""Root directory for Professional Video Editor's per-project binary
assets (imported video clips/photos, rendered exports) - one
subdirectory per project, named by the project's id (see
jarvis.video_editor.storage). A SEPARATE tree from
VIDEO_STUDIO_PROJECTS_DIR (see VIDEO_EDITOR_DB_FILE's own docstring for
why the two modules' projects are never mixed). Same "direct file I/O
from a GUI view, no agent-tool sandbox check" precedent as
VIDEO_STUDIO_PROJECTS_DIR/REEL_GENERATOR_PROJECTS_DIR - see either
constant's own docstring for the full reasoning. Lives under
JARVIS_DATA_DIR (not JARVIS_ROOT) for the same survives-an-update
reason."""

VIDEO_EDITOR_STICKER_LIBRARY_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "video_editor_sticker_library.json"
"""A small JSON file holding the person's own sticker favorites and
saved collections (jarvis.video_editor.sticker_library) - deliberately
GLOBAL, not per-project (a favorited sticker/a saved collection is
useful across every Video Editor project, not scoped to one), so this
lives as its own standalone file rather than a row in
VIDEO_EDITOR_DB_FILE's own per-project table. Contains no credential;
created idempotently with an empty default shape on first use."""

CAROUSEL_STUDIO_DB_FILE: Path = JARVIS_DATA_DIR / ".jarvis" / "carousel_studio.db"
"""SQLite index for the Instagram carousel studio (jarvis.carousel_studio):
one row per carousel project (name, format, slide count, timestamps) so
the project library lists quickly. The project itself lives as
project.json in CAROUSEL_STUDIO_PROJECTS_DIR. Contains no credential."""

CAROUSEL_STUDIO_PROJECTS_DIR: Path = JARVIS_DATA_DIR / ".jarvis" / "carousel_studio" / "projects"
"""One subdirectory per carousel project: project.json, its imported
images (assets/) and a cover thumbnail."""

CAROUSEL_STUDIO_LIBRARY_DIR: Path = JARVIS_DATA_DIR / ".jarvis" / "carousel_studio" / "library"
"""Person-level reusable files for the carousel studio (uploaded
photos/logos, saved templates, custom fonts), shared by every project."""
