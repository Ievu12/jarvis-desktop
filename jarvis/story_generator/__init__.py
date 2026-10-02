"""AI Storytelling Generator - builds a complete short-form story
(Hook -> Setup -> Conflict -> Emotional development -> Turning point ->
Transformation -> Conclusion -> CTA), turns it into 5-10 visual scenes,
and renders/exports a vertical 9:16 video for Instagram Reels/TikTok/
Shorts.

Deliberately NOT a from-scratch reimplementation of AI Reel Generator's
pipeline - this package generates only what is genuinely NEW for a
story (its own narrative structure and its own scene-splitting, since
neither fits jarvis.reel_generator.script's fixed 3-segment hook/value/
cta shape or jarvis.reel_generator.storyboard's per-segment scene
splitting), and calls the EXACT SAME, unmodified reel_generator
functions for every step downstream of "here are some Scenes":

  - jarvis.reel_generator.storyboard.Scene is reused directly as this
    package's own scene type (extended with optional mood/transition
    fields specifically for this package's use - see that dataclass's
    own docstring). A Storyboard-shaped tuple of Scenes is all
    render_all_scenes()/export_reel_video() ever need; they don't care
    whether those scenes came from a Reel's script or a Story's
    structure.
  - jarvis.reel_generator.scenes.render_all_scenes() renders every
    scene's visual (a Pillow text-card, same as Reel Generator - no AI
    image/video generation exists anywhere in this codebase, see that
    module's own docstring) - called unmodified.
  - jarvis.reel_generator.export.export_reel_video() renders the final
    1080x1920 MP4 (ffmpeg looping each scene's still image, burned-in
    captions from each scene's own on_screen_text, silent - same
    reasoning as that module's own docstring) - called unmodified.

This package owns only:
  - structure.py: one LLM call, plain-language story idea ->
    StoryStructure (the 8 fixed narrative beats).
  - story_scenes.py: one LLM call, an approved StoryStructure -> 5-10
    Scene objects (voice_text/on_screen_text/visual_description/mood/
    transition per scene) - the story equivalent of
    jarvis.reel_generator.storyboard.generate_storyboard(), but
    splitting 8 beats into scenes rather than 3 script segments.
  - storage.py / db.py: this module's OWN project directory/database,
    mirroring jarvis.reel_generator.storage/.db's exact pattern (a
    Story project is not a Reel project - separate id space, separate
    "Recent Stories" list - even though rendering/export are shared
    code, not shared state).

Modularity for future story types (module brief requirement: personal/
educational/product/brand/emotional/motivational/customer stories):
structure.py's generate_story_structure() takes an optional
`story_type` used only to steer the LLM prompt's own framing - adding a
new story type is a prompt-level change in that one function, never a
new package or a change to story_scenes.py/storage.py/db.py/the GUI
dashboard, which all stay type-agnostic (they only ever see the
resulting StoryStructure/Scene objects, never `story_type` itself).

AI Reel Generator itself is never imported for its OWN sake here beyond
the two rendering/export function calls named above - this package
never touches jarvis.reel_generator.db/.storage/.brief/.script (a Story
project's own metadata lives entirely in this package's own db.py/
storage.py, never mixed into REEL_GENERATOR_DB_FILE)."""
