"""Tests for jarvis.reel_generator.ai_video_provider: the pure,
provider-agnostic interface (no networking, no vendor logic) that
jarvis.reel_generator.motion_engine programs against. Confirms the
dataclasses construct with their documented defaults, and that a
plain, hand-written fake class satisfies the AIVideoProvider Protocol
structurally (runtime_checkable) - proving a real implementation (e.g.
jarvis.reel_generator.video_generation's Runway-backed one) needs no
explicit registration/subclassing to be usable wherever this Protocol
is expected."""

from __future__ import annotations

from pathlib import Path

from jarvis.reel_generator.ai_video_provider import (
    AIVideoProvider,
    GeneratedVideo,
    VideoGenerationRequest,
)


def test_video_generation_request_defaults_to_image_to_video():
    request = VideoGenerationRequest(motion_prompt="gentle camera pan", duration_seconds=5.0)
    assert request.mode == "image_to_video"
    assert request.source_image_path is None


def test_video_generation_request_accepts_explicit_source_image_path(tmp_path):
    image_path = tmp_path / "scene_01.jpg"
    request = VideoGenerationRequest(
        motion_prompt="subtle sway", duration_seconds=4.0, source_image_path=image_path,
    )
    assert request.source_image_path == image_path
    assert request.mode == "image_to_video"


def test_video_generation_request_supports_text_to_video_mode():
    request = VideoGenerationRequest(motion_prompt="a calm morning scene", duration_seconds=5.0, mode="text_to_video")
    assert request.mode == "text_to_video"
    assert request.source_image_path is None


def test_generated_video_holds_real_measured_properties():
    video = GeneratedVideo(video_bytes=b"fake-mp4-bytes", duration_seconds=5.0, width=768, height=1280)
    assert video.video_bytes == b"fake-mp4-bytes"
    assert video.duration_seconds == 5.0
    assert video.width == 768
    assert video.height == 1280


class _FakeProvider:
    """A minimal hand-written class implementing AIVideoProvider's own
    shape, with no inheritance from it at all - proving the Protocol is
    satisfied structurally."""

    def is_configured(self) -> bool:
        return True

    def generate_video(self, request: VideoGenerationRequest) -> tuple[GeneratedVideo | None, str | None]:
        return GeneratedVideo(video_bytes=b"x", duration_seconds=request.duration_seconds, width=768, height=1280), None


def test_fake_provider_satisfies_the_protocol_structurally():
    provider: AIVideoProvider = _FakeProvider()
    assert isinstance(provider, AIVideoProvider)
    assert provider.is_configured() is True
    video, reason = provider.generate_video(VideoGenerationRequest(motion_prompt="x", duration_seconds=5.0))
    assert video is not None
    assert reason is None


class _NotConfiguredProvider:
    def is_configured(self) -> bool:
        return False

    def generate_video(self, request: VideoGenerationRequest) -> tuple[GeneratedVideo | None, str | None]:
        return None, "not configured"


def test_not_configured_provider_still_satisfies_the_protocol():
    provider: AIVideoProvider = _NotConfiguredProvider()
    assert isinstance(provider, AIVideoProvider)
    assert provider.is_configured() is False
    video, reason = provider.generate_video(VideoGenerationRequest(motion_prompt="x", duration_seconds=5.0))
    assert video is None
    assert reason == "not configured"


def test_a_plain_object_without_the_methods_does_not_satisfy_the_protocol():
    class _NotAProvider:
        pass

    assert not isinstance(_NotAProvider(), AIVideoProvider)
