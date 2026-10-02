"""Tests for jarvis.reel_generator.video_generation: real AI video clip
generation via Runway's REST API (submit -> poll -> download). Mocked
HTTP throughout (no real network call, no real RUNWAY_API_KEY needed for
these tests) - confirms: is_configured() reflects RUNWAY_API_KEY,
generate_scene_video() returns (None, reason) (never raises) for every
failure case - unconfigured, missing source image, empty prompt, HTTP
failure at any of the three network calls, a provider-reported FAILED
status, and a wall-clock poll timeout - with a REAL, specific reason
string in every case, and that the poll loop actually terminates on
SUCCEEDED (asserted via exact call count) rather than looping forever."""

from __future__ import annotations

import json
import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis.reel_generator import video_generation
from jarvis.reel_generator.ai_video_provider import GeneratedVideo
from jarvis.video_studio.ffmpeg_utils import ffmpeg_available


def _fake_response(body: bytes) -> MagicMock:
    response = MagicMock()
    response.read.return_value = body
    response.__enter__ = lambda self: response
    response.__exit__ = lambda self, *a: None
    return response


def _json_response(body: dict) -> MagicMock:
    return _fake_response(json.dumps(body).encode("utf-8"))


def _http_error(code: int, reason_phrase: str, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.runwayml.com/v1/image_to_video", code=code, msg=reason_phrase,
        hdrs=None, fp=MagicMock(read=MagicMock(return_value=body)),
    )


@pytest.fixture()
def source_image(tmp_path) -> Path:
    path = tmp_path / "scene_01.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0fake jpeg bytes")
    return path


def _make_real_clip_bytes(tmp_path: Path, *, duration_seconds: float = 5.0) -> bytes:
    clip_path = tmp_path / "real_clip.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=blue:s=768x1280:d={duration_seconds}",
            "-c:v", "libx264", "-t", str(duration_seconds), str(clip_path),
        ],
        capture_output=True, timeout=30, check=True,
    )
    return clip_path.read_bytes()


def test_is_configured_reflects_api_key(monkeypatch):
    monkeypatch.setattr(video_generation, "RUNWAY_API_KEY", "key-test")
    assert video_generation.is_configured() is True
    monkeypatch.setattr(video_generation, "RUNWAY_API_KEY", None)
    assert video_generation.is_configured() is False


def test_generate_scene_video_returns_none_and_reason_when_not_configured(source_image, monkeypatch):
    monkeypatch.setattr(video_generation, "RUNWAY_API_KEY", None)
    with patch.object(video_generation.urllib.request, "urlopen") as fake_urlopen:
        video, reason = video_generation.generate_scene_video(source_image, "gentle pan")
    assert video is None
    assert reason is not None and "RUNWAY_API_KEY" in reason
    fake_urlopen.assert_not_called()


def test_generate_scene_video_returns_none_and_reason_for_empty_prompt(source_image, monkeypatch):
    monkeypatch.setattr(video_generation, "RUNWAY_API_KEY", "key-test")
    video, reason = video_generation.generate_scene_video(source_image, "   ")
    assert video is None
    assert reason is not None and "motion prompt" in reason


def test_generate_scene_video_returns_none_and_reason_for_missing_source_image(tmp_path, monkeypatch):
    monkeypatch.setattr(video_generation, "RUNWAY_API_KEY", "key-test")
    missing_path = tmp_path / "does_not_exist.jpg"
    video, reason = video_generation.generate_scene_video(missing_path, "gentle pan")
    assert video is None
    assert reason is not None and "does not exist" in reason


@pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not on PATH")
def test_full_success_flow_pending_then_running_then_succeeded(source_image, tmp_path, monkeypatch):
    monkeypatch.setattr(video_generation, "RUNWAY_API_KEY", "key-test")
    clip_bytes = _make_real_clip_bytes(tmp_path, duration_seconds=5.0)
    call_log = []

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        call_log.append(url)
        if url.endswith("/v1/image_to_video"):
            return _json_response({"id": "task-123"})
        if url.endswith("/v1/tasks/task-123"):
            if call_log.count(url) == 1:
                return _json_response({"status": "PENDING"})
            if call_log.count(url) == 2:
                return _json_response({"status": "RUNNING"})
            return _json_response({"status": "SUCCEEDED", "output": ["https://cdn.example.com/clip.mp4"]})
        if url == "https://cdn.example.com/clip.mp4":
            return _fake_response(clip_bytes)
        raise AssertionError(f"unexpected URL: {url}")

    with patch.object(video_generation.urllib.request, "urlopen", side_effect=fake_urlopen), \
         patch.object(video_generation.time, "sleep", return_value=None):
        video, reason = video_generation.generate_scene_video(source_image, "gentle pan", duration_seconds=5.0)

    assert reason is None
    assert video is not None
    assert video.video_bytes == clip_bytes
    assert video.width == 768
    assert video.height == 1280
    assert abs(video.duration_seconds - 5.0) < 0.5
    # Proves the loop terminates: submit once, poll exactly 3 times (PENDING, RUNNING, SUCCEEDED), download once.
    assert call_log.count("https://api.runwayml.com/v1/image_to_video") == 1
    assert call_log.count("https://api.runwayml.com/v1/tasks/task-123") == 3


def test_failed_status_returns_specific_reason(source_image, monkeypatch):
    monkeypatch.setattr(video_generation, "RUNWAY_API_KEY", "key-test")

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        if url.endswith("/v1/image_to_video"):
            return _json_response({"id": "task-456"})
        return _json_response({"status": "FAILED", "failure": "unsafe content detected"})

    with patch.object(video_generation.urllib.request, "urlopen", side_effect=fake_urlopen):
        video, reason = video_generation.generate_scene_video(source_image, "gentle pan")

    assert video is None
    assert reason is not None
    assert "unsafe content detected" in reason


def test_wall_clock_timeout_returns_reason_with_task_id(source_image, monkeypatch):
    monkeypatch.setattr(video_generation, "RUNWAY_API_KEY", "key-test")

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        if url.endswith("/v1/image_to_video"):
            return _json_response({"id": "task-789"})
        return _json_response({"status": "RUNNING"})

    fake_time = [0.0]

    def fake_monotonic():
        return fake_time[0]

    def fake_sleep(seconds):
        fake_time[0] += 700.0  # jump straight past the 600s ceiling on the first sleep

    with patch.object(video_generation.urllib.request, "urlopen", side_effect=fake_urlopen), \
         patch.object(video_generation.time, "monotonic", side_effect=fake_monotonic), \
         patch.object(video_generation.time, "sleep", side_effect=fake_sleep):
        video, reason = video_generation.generate_scene_video(source_image, "gentle pan")

    assert video is None
    assert reason is not None
    assert "timed out" in reason
    assert "task-789" in reason


def test_submit_http_error_returns_specific_reason(source_image, monkeypatch):
    monkeypatch.setattr(video_generation, "RUNWAY_API_KEY", "key-test")
    error_body = json.dumps({"error": "invalid API key"}).encode("utf-8")

    with patch.object(video_generation.urllib.request, "urlopen", side_effect=_http_error(401, "Unauthorized", error_body)):
        video, reason = video_generation.generate_scene_video(source_image, "gentle pan")

    assert video is None
    assert reason is not None
    assert "401" in reason
    assert "invalid API key" in reason


def test_submit_network_error_returns_reason(source_image, monkeypatch):
    monkeypatch.setattr(video_generation, "RUNWAY_API_KEY", "key-test")

    with patch.object(video_generation.urllib.request, "urlopen", side_effect=urllib.error.URLError("connection refused")):
        video, reason = video_generation.generate_scene_video(source_image, "gentle pan")

    assert video is None
    assert reason is not None
    assert "network error" in reason


def test_download_http_error_returns_specific_reason(source_image, monkeypatch):
    monkeypatch.setattr(video_generation, "RUNWAY_API_KEY", "key-test")

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        if url.endswith("/v1/image_to_video"):
            return _json_response({"id": "task-999"})
        if url.endswith("/v1/tasks/task-999"):
            return _json_response({"status": "SUCCEEDED", "output": ["https://cdn.example.com/gone.mp4"]})
        raise _http_error(404, "Not Found", b"")

    with patch.object(video_generation.urllib.request, "urlopen", side_effect=fake_urlopen):
        video, reason = video_generation.generate_scene_video(source_image, "gentle pan")

    assert video is None
    assert reason is not None
    assert "404" in reason


def test_save_scene_video_writes_exact_bytes(tmp_path):
    video = GeneratedVideo(video_bytes=b"fake-mp4-bytes", duration_seconds=5.0, width=768, height=1280)
    output_path = tmp_path / "nested" / "clip.mp4"
    video_generation.save_scene_video(video, output_path=output_path)
    assert output_path.is_file()
    assert output_path.read_bytes() == b"fake-mp4-bytes"
