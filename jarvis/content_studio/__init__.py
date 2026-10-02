"""AI Content Studio: turns a single plain-language topic ("5 minučių
rytinė joga geresnei dienos pradžiai") into a full social-media content
plan spanning Instagram Reel, Story, Post, Carousel, and PDF, then
orchestrates JARVIS's OWN existing, already-tested generation modules
to actually produce each piece.

Architecture inspection (done before writing any code in this package,
per this feature's own request) confirmed this must be a thin
ORCHESTRATION layer, not a reimplementation:

- A Reel content item is a genuine jarvis.reel_generator project,
  created via jarvis.reel_generator.storage.create_project() and driven
  through that package's own brief -> script -> storyboard -> scenes ->
  cover -> caption -> export pipeline exactly as
  jarvis.gui.views.reel_generator.dashboard already does. This package
  never reimplements script/storyboard/scene generation - see
  jarvis.reel_generator's own docstring for that pipeline.
- A Story/Post/Carousel content item is a genuine jarvis.design_studio
  project, created via jarvis.design_studio.storage.create_project()
  and driven through generate_design_brief() -> render_design()/
  generate_variants() exactly as jarvis.gui.views.design_studio
  .dashboard already does.
- A PDF content item has no existing JARVIS module to reuse (confirmed
  by this package's own architecture inspection: no PDF generation
  exists anywhere in this codebase) - jarvis.content_studio.pdf_export
  is new code, but even it composes jarvis.design_studio.render
  .render_design() per page rather than building its own rendering
  pipeline; only the "assemble rendered pages into one PDF file" step
  (via Pillow's own built-in multi-page PDF writer - Image.save(...,
  save_all=True, append_images=[...]) - zero new dependencies, since a
  PDF-generation library such as reportlab is a compiled Python
  extension and a real risk under this machine's documented Smart App
  Control policy) is genuinely new.

Content Studio's own database (jarvis.content_studio.db,
CONTENT_STUDIO_DB_FILE) stores, per content item, only a POINTER to the
real project living in REEL_GENERATOR_DB_FILE or DESIGN_STUDIO_DB_FILE
(that project's own id) plus this package's own workflow status
(DRAFT/REVIEW/APPROVED/EXPORTED) - never a duplicated copy of that
project's brief/script/render data. The linked module's own database
remains the single source of truth for its own project's content;
Content Studio reads it back via that module's own get_project()/
list_projects() functions, exactly as jarvis.reel_generator.footage
already does when reading back a linked jarvis.video_studio project's
own data (see that module's own docstring for the identical pattern
applied in a different direction).

Nothing in this package ever auto-publishes or silently finalizes
content - every content item's workflow status only ever advances one
step at a time via an explicit person action (module brief's own
DRAFT -> REVIEW -> APPROVED -> EXPORTED states), matching every other
generation module in this codebase's own "require explicit approval
before anything irreversible" convention (see jarvis.reel_generator's
own script-approval gate for the closest precedent).

Staged rollout (this package's own agreed plan):
  Stage 1 (this stage): Content Plan generation + Content Studio's own
    project storage/db + DRAFT/REVIEW/APPROVED/EXPORTED workflow states
    + a Content Library listing, GUI not yet built.
  Stage 2: Reel/Design Studio orchestration - creating real linked
    projects from a content plan item, with Preview/Regenerate/Edit/
    Approve/Export actions.
  Stage 3: PDF Creator (idea -> outline -> content -> design -> preview
    -> approve -> export, module brief's own explicit approval gate
    before any PDF file is ever written).
  Stage 4: Full GUI (CTkTabview with New Content/My Projects/Reels/
    Stories/Posts/PDFs tabs, following jarvis.gui.views
    .instagram_ai_manager.content_studio's own established tabbed-
    container precedent) tying every earlier stage together, plus one
    new top-level sidebar nav item.
"""
