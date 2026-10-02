"""Professional Video Editor: a general-purpose, multi-source video
editing module for Instagram Reels/TikTok/YouTube Shorts - upload any
mix of video clips and photos, assemble them on a real timeline
(trim/merge/retime/transitions), export to 9:16/1:1/16:9 at
720p/1080p/4K, and (later stages) layer on AI-assisted editing
suggestions, animated Lithuanian captions, visual effects, AI
photo-to-motion clips, and a template library.

Architecture inspection (done before writing any code in this package,
per this feature's own request) confirmed this must be an entirely
SEPARATE, additive module, not an extension of jarvis.video_studio or
jarvis.reel_generator:

- jarvis.video_studio.reel.ReelEditPlan/PlannedClip is a SINGLE-SOURCE,
  linear, highlight-driven reel-assembly plan (one source video, clips
  selected from AI-detected highlight candidates) - genuinely a
  different shape of problem from "arbitrary multi-clip, multi-file,
  user-directed timeline editing," and it keeps serving
  jarvis.video_studio's own existing highlight-reel feature completely
  unchanged by this package's existence.
- jarvis.video_studio.export.export_reel() builds its ffmpeg filtergraph
  around exactly ONE `-i` input (`[0:v]`/`[0:a]`) - it cannot merge
  clips from multiple different uploaded files. This package's own
  jarvis.video_editor.multisource_export is new work built specifically
  to do that (N distinct `-i` inputs feeding one concat filter), not a
  modification of jarvis.video_studio.export.py, which remains
  untouched.
- jarvis.video_studio.storage.SUPPORTED_EXTENSIONS is video-only
  (.mp4/.mov/.m4v/.webm) - this package needs photo (.jpg/.jpeg/.png)
  support too, so it defines its own, separate extension constants
  (jarvis.video_editor.media_import) rather than importing/extending
  that symbol, so an existing jarvis.video_studio call site can never be
  affected by this package's own broader needs.

What IS reused, read-only, never modified, from sibling modules:
  - jarvis.video_studio.ffmpeg_utils: probe_video()/detect_scene_changes()/
    detect_silence()/extract_frame()/ffmpeg_available()/FFmpegError - the
    same subprocess-only, no-compiled-video-library convention this
    package follows too (see jarvis.video_editor.multisource_export's own
    docstring for why: this dev machine's Windows Smart App Control has
    already blocked other compiled Python extensions - matplotlib's
    ft2font, faster-whisper's PyAV dependency, per RELEASE.md - so this
    package stays ffmpeg-subprocess + Pillow only, same as every other
    video-handling module in this codebase; no Node.js/Remotion, no
    opencv-python/moviepy, ever).
  - jarvis.video_studio.transcribe.transcribe_video() (Stage 4+) - already
    real, Lithuanian-capable (LANGUAGE_LITHUANIAN), timestamped Whisper
    transcription via ffmpeg's own built-in whisper.cpp audio filter -
    reused as-is for animated-caption generation, rather than adding a
    new Azure Speech-to-Text integration this codebase doesn't need yet.
  - jarvis.reel_generator.video_generation.is_configured()/
    generate_scene_video()/save_scene_video() (Stage 5+) - the existing
    Runway image-to-video integration, confirmed generic (plain Path +
    prompt string, zero coupling to jarvis.reel_generator's own Scene/
    ScenePlan/SceneVisual types) - reused directly for this package's
    own "turn a photo into a moving clip" feature.
    jarvis.reel_generator.motion_engine is deliberately NOT reused (it is
    tightly coupled to jarvis.reel_generator's own Scene/ScenePlan types).

Staged rollout (this package's own agreed plan - see
C:\\Users\\navic\\.claude\\plans\\jolly-finding-pillow.md for the full
plan this docstring summarizes):
  Stage 1 (this stage): media import (video + photo), the Timeline data
    model, multi-source ffmpeg export (9:16/1:1/16:9), project
    save/resume. NO AI features at all in this stage.
  Stage 2: GUI wiring (new sidebar entry, dashboard + panels) and
    progress/cancellation worker infrastructure for long exports.
  Stage 3: AI auto-edit suggestions - honest disclosure up front: there
    is no object/person/action/scene-content recognition anywhere in
    this codebase (jarvis.core.llm.LLMClient has no vision support).
    "AI auto-editing" can only ever mean ffmpeg's own pixel-diff
    scene-cut detection + silence detection producing candidate cut
    points, plus one LLM call reasoning over TRANSCRIPT TEXT ONLY to
    rank segments and suggest captions/hooks - exactly what
    jarvis.video_studio.highlights.find_highlights() already does for
    the existing single-source flow, generalized onto this package's
    own multi-source timeline. Never real computer vision, never
    fabricated as more capable than this.
  Stage 4: animated Lithuanian captions (word-by-word reveal, built on
    transcribe_video()'s segment-level timestamps via a documented
    heuristic word-splitter - true word-level timestamps aren't
    available from that function today) + visual effects/stickers via
    ffmpeg drawtext/overlay filters with time-varying `enable=` window
    expressions (the first real use of that pattern in this codebase).
  Stage 5: AI photo-to-motion, reusing jarvis.reel_generator
    .video_generation as described above - offered as a clearly
    separate, per-photo opt-in action, never forced on every photo, so
    both plain-stills and AI-motion Reels stay possible in one project.
  Stage 6: a curated template library (yoga/beauty/home-fragrance/
    lifestyle/seasonal/dynamic-modern) - presets only, no new AI.
  Stage 7: hardening - full regression sweep, 4K export performance
    check, documentation.
"""
