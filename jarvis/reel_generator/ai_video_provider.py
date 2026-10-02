"""Provider-agnostic interface for real AI image-to-video / text-to-
video generation - AI Reel Generator's NATURAL MOTION / HYBRID modes'
own "Motion Engine" (jarvis.reel_generator.motion_engine) talks to a
provider only through this interface, never to a specific vendor's SDK
or HTTP shape directly.

This module has ZERO networking code and ZERO vendor-specific logic -
it exists purely so a second provider can be added later
(jarvis.reel_generator.video_generation is the one real implementation
today, built against Runway's REST API) without touching
motion_engine.py or any GUI call site. Same separation-of-concerns
reasoning as jarvis.voice.text_to_speech keeping its Azure-specific HTTP
calls behind a plain speak()/SpeakResult surface, rather than letting
Azure-specific request/response shapes leak into callers.

Contract every real implementation must follow (mirrors
jarvis.reel_generator.image_generation.generate_scene_image()'s own
established convention exactly): `generate_video()` NEVER raises for an
ordinary failure (missing API key, network error, provider-side
generation failure) - it always returns `(GeneratedVideo, None)` on
success or `(None, reason)` on failure, where `reason` is a real,
specific, human-readable string, never a generic placeholder. A missing/
unconfigured provider is reported via `is_configured() -> False`,
checked by the caller before ever attempting a network call - same
"check before attempting" convention as jarvis.voice.text_to_speech's
own Azure tier and image_generation.is_configured()."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable

VideoGenerationMode = Literal["image_to_video", "text_to_video"]


@dataclass(frozen=True)
class VideoGenerationRequest:
    """What the Motion Engine asks a provider to generate. `source_image_path`
    is required for `mode="image_to_video"` (animate this exact still -
    AI Reel Generator's only real use today, since every scene already
    has a rendered still image as its base frame - see
    jarvis.reel_generator.scene_render's own docstring) and is None for
    `mode="text_to_video"` (generate from `motion_prompt` alone, with no
    starting image - reserved for a possible future use, not exercised
    by this codebase's own call sites yet, since AI Reel Generator's
    Motion Engine always has a still image to animate from)."""

    motion_prompt: str
    duration_seconds: float
    mode: VideoGenerationMode = "image_to_video"
    source_image_path: Path | None = None


@dataclass(frozen=True)
class GeneratedVideo:
    """A real, provider-generated video clip's raw bytes plus the
    ACTUAL measured properties of that file - width/height/duration are
    always re-probed from the real downloaded bytes (via
    jarvis.video_studio.ffmpeg_utils.probe_video(), see
    jarvis.reel_generator.video_generation's own docstring for why) by
    the implementation that constructs this, never trusted blindly from
    whatever the provider's own API response claims."""

    video_bytes: bytes
    duration_seconds: float
    width: int
    height: int


@runtime_checkable
class AIVideoProvider(Protocol):
    """The interface jarvis.reel_generator.motion_engine programs
    against. Any real implementation (jarvis.reel_generator.video_generation's
    Runway-backed one today, or a future second provider) satisfies this
    Protocol structurally - no explicit subclassing/registration needed,
    matching this codebase's general preference for plain functions/
    dataclasses over class hierarchies wherever a Protocol is enough."""

    def is_configured(self) -> bool:
        """True only if this provider has everything it needs (API key,
        etc.) to attempt a real generation call. Checked by the Motion
        Engine BEFORE calling generate_video() - never used as an
        excuse to attempt a doomed call and catch the resulting error
        instead."""
        ...

    def generate_video(self, request: VideoGenerationRequest) -> tuple[GeneratedVideo | None, str | None]:
        """Never raises. Returns (GeneratedVideo, None) on success, or
        (None, reason) on ANY failure - a missing/invalid request field,
        a network error, a provider-side generation failure, or a
        malformed response. `reason` is always a specific, real
        description (never a generic "something went wrong"), matching
        jarvis.reel_generator.image_generation.generate_scene_image()'s
        own established (None, reason) contract exactly."""
        ...
