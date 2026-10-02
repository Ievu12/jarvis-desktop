"""AI Reel Generator: turns a plain-language topic ("Create a 20 second
Reel about 3 morning yoga exercises.") into a complete, user-approved
short-form Instagram Reel concept, and - after explicit approval at
each stage - a rendered MP4.

Architecture inspection (done before writing any code in this package,
per this module's own brief) confirmed:

- jarvis.core.llm.LLMClient is text-only - no vision, no audio, no
  image/video generation of any kind. Every "generation" step in this
  package that needs a visual is either (a) typography/gradient
  rendering via jarvis.design_studio.render.render_design() (reused
  directly, unmodified - "reel_cover" and "story" are already
  first-class 1080x1920 formats there), or (b) a user-uploaded
  image/video clip. There is no AI image or AI video generation
  anywhere in this codebase, and this package never implies otherwise.

- No reliable, file-based TTS/voiceover capability exists.
  jarvis.voice.text_to_speech.speak() only plays audio live through
  the speaker - it never saves a voiceover file - and its one
  Lithuanian path (Azure Neural TTS) depends on optional, often-unset
  AZURE_SPEECH_KEY/AZURE_SPEECH_REGION credentials. Stage 1/2 of this
  package's own staged rollout therefore ship WITHOUT spoken
  voiceover: Reels are silent, built from on-screen text (burned-in
  captions written directly from the approved script, not a
  transcription of audio that doesn't exist) plus a suggested music
  mood/tempo the person adds their own track against. This is stated
  plainly in the GUI, never silently implied as "coming soon" without
  saying so.

- jarvis.video_studio already has real, working ffmpeg-based
  concat/crop/subtitle-burn-in/H.264+AAC export machinery
  (jarvis.video_studio.export/.ffmpeg_utils) and Instagram handoff
  machinery (jarvis.video_studio.instagram_handoff, a direct-function-
  call pattern into jarvis.instagram_ai_manager.ai_services/.db) - this
  package follows both patterns closely (adapted, not imported
  verbatim, since video_studio's export.py is typed specifically
  against video-clip trims, not still-image scene segments) rather
  than reinventing either.

Staged rollout (this package's own agreed plan):
  Stage 1 (this stage): Reel Brief + Script generation, with a
    mandatory approval gate before anything downstream is built -
    storage only, no visuals/export yet.
  Stage 2: Storyboard + Mode B (from-idea, text-card) visuals + cover +
    caption/hashtags + final MP4 export.
  Stage 3: Mode A (create from uploaded footage, reusing
    jarvis.video_studio's analysis/highlights/export machinery).
  Stage 4: Instagram AI Manager handoff + Content Package + quality
    control checks.
"""
