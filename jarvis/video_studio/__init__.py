"""AI Video Studio: a modular JARVIS desktop feature for turning an
uploaded video into an Instagram Reel - analysis, highlight detection,
transcription/subtitles, hooks/cover/caption generation, a simple
timeline editor, and export.

Submodules (mirroring jarvis.instagram_ai_manager's split):
  - storage.py: project directory management (jarvis.config
    .VIDEO_STUDIO_PROJECTS_DIR) - copying an uploaded video into its own
    project folder, resolving a project's asset paths. Never touches
    the original file the person picked (always copies).
  - db.py: SQLite metadata (jarvis.config.VIDEO_STUDIO_DB_FILE) - one
    project row per video, plus clips/transcript/subtitles/exports
    records. Same JSON-blob-per-row pattern as
    jarvis.instagram_ai_manager.db - see that module's docstring.
  - ffmpeg_utils.py: thin wrappers around the system `ffmpeg`/`ffprobe`
    binaries (subprocess calls) - probing a video's metadata, detecting
    scene changes and silence. No Python video-processing dependency
    (moviepy/opencv/PIL) is used; FFmpeg is the one processing engine,
    per the module's own brief ("use a reliable video processing
    engine such as FFmpeg where appropriate").
  - analysis.py: analyze_video() - combines ffmpeg_utils' probes into
    one structured VideoAnalysis result (duration, resolution, fps,
    audio presence, scene count, silence gaps). No LLM call.
  - transcribe.py: transcribe_video() - speech-to-text with timestamps,
    via FFmpeg's own built-in whisper.cpp audio filter (`-af whisper`)
    rather than a Python speech-recognition library - see that module's
    docstring for why (this machine's Smart App Control policy blocks
    every third-party compiled Python extension tried, including
    faster-whisper's PyAV dependency; the native FFmpeg binary is
    unaffected since it isn't a Python extension).

UI code (jarvis.gui.views.video_studio) is entirely separate, exactly
like jarvis.instagram_ai_manager - this package has no customtkinter
import anywhere.
"""
