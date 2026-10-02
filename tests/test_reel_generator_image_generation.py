"""Tests for jarvis.reel_generator.image_generation: real photorealistic
scene image generation via OpenAI's Images API (gpt-image-1). Mocked
HTTP throughout (no real network call, no real OPENAI_API_KEY needed
for these tests) - confirms: is_configured() reflects OPENAI_API_KEY,
generate_scene_image() returns (None, reason) (never raises) when
unconfigured/empty prompt/HTTP failure/malformed response - with a REAL,
specific reason string in every failure case (real, reported bug fix:
this used to collapse every failure into a bare None with no way to
tell an auth error from a rate limit from a genuine network outage -
see generate_scene_image()'s own docstring for the real, live API call
that found this exact gap), decodes a real base64 image payload
correctly on success, and save_scene_image() writes the exact bytes to
disk."""

from __future__ import annotations

import base64
import json
import urllib.error
from unittest.mock import MagicMock, patch

from jarvis.reel_generator import image_generation


def _fake_response(body: dict) -> MagicMock:
    response = MagicMock()
    response.read.return_value = json.dumps(body).encode("utf-8")
    response.__enter__ = lambda self: response
    response.__exit__ = lambda self, *a: None
    return response


def _valid_body(image_bytes: bytes) -> dict:
    return {"data": [{"b64_json": base64.b64encode(image_bytes).decode("ascii")}]}


def _http_error(code: int, reason_phrase: str, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        url="https://api.openai.com/v1/images/generations", code=code, msg=reason_phrase,
        hdrs=None, fp=MagicMock(read=MagicMock(return_value=body)),
    )


def test_is_configured_reflects_api_key(monkeypatch):
    monkeypatch.setattr(image_generation, "OPENAI_API_KEY", "sk-test-key")
    assert image_generation.is_configured() is True
    monkeypatch.setattr(image_generation, "OPENAI_API_KEY", None)
    assert image_generation.is_configured() is False


def test_generate_scene_image_returns_none_and_reason_when_not_configured(monkeypatch):
    monkeypatch.setattr(image_generation, "OPENAI_API_KEY", None)
    image, reason = image_generation.generate_scene_image("a scene")
    assert image is None
    assert reason is not None and "OPENAI_API_KEY" in reason


def test_generate_scene_image_returns_none_and_reason_for_empty_prompt(monkeypatch):
    monkeypatch.setattr(image_generation, "OPENAI_API_KEY", "sk-test-key")
    image, reason = image_generation.generate_scene_image("   ")
    assert image is None
    assert reason is not None


def test_generate_scene_image_success_decodes_real_bytes(monkeypatch):
    monkeypatch.setattr(image_generation, "OPENAI_API_KEY", "sk-test-key")
    fake_image_bytes = b"\xff\xd8\xff\xe0fake jpeg bytes for testing"
    with patch.object(image_generation.urllib.request, "urlopen", return_value=_fake_response(_valid_body(fake_image_bytes))):
        image, reason = image_generation.generate_scene_image("a woman drinking tea on a sofa")
    assert image is not None
    assert reason is None
    assert image.image_bytes == fake_image_bytes
    assert image.width > 0 and image.height > 0


def test_generate_scene_image_returns_none_on_network_error(monkeypatch):
    monkeypatch.setattr(image_generation, "OPENAI_API_KEY", "sk-test-key")
    with patch.object(
        image_generation.urllib.request, "urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        image, reason = image_generation.generate_scene_image("a scene")
    assert image is None
    assert reason is not None and "connection refused" in reason


def test_generate_scene_image_returns_none_on_timeout(monkeypatch):
    monkeypatch.setattr(image_generation, "OPENAI_API_KEY", "sk-test-key")
    with patch.object(image_generation.urllib.request, "urlopen", side_effect=TimeoutError()):
        image, reason = image_generation.generate_scene_image("a scene")
    assert image is None
    assert reason is not None


def test_generate_scene_image_returns_none_on_malformed_json(monkeypatch):
    monkeypatch.setattr(image_generation, "OPENAI_API_KEY", "sk-test-key")
    response = MagicMock()
    response.read.return_value = b"not valid json"
    response.__enter__ = lambda self: response
    response.__exit__ = lambda self, *a: None
    with patch.object(image_generation.urllib.request, "urlopen", return_value=response):
        image, reason = image_generation.generate_scene_image("a scene")
    assert image is None
    assert reason is not None and "unexpected response shape" in reason


def test_generate_scene_image_returns_none_on_missing_data_field(monkeypatch):
    monkeypatch.setattr(image_generation, "OPENAI_API_KEY", "sk-test-key")
    with patch.object(image_generation.urllib.request, "urlopen", return_value=_fake_response({"unexpected": "shape"})):
        image, reason = image_generation.generate_scene_image("a scene")
    assert image is None
    assert reason is not None


def test_generate_scene_image_returns_none_on_invalid_base64(monkeypatch):
    monkeypatch.setattr(image_generation, "OPENAI_API_KEY", "sk-test-key")
    with patch.object(
        image_generation.urllib.request, "urlopen",
        return_value=_fake_response({"data": [{"b64_json": "not-valid-base64!!!"}]}),
    ):
        image, reason = image_generation.generate_scene_image("a scene")
    assert image is None
    assert reason is not None


def test_never_raises_on_unexpected_exception(monkeypatch):
    # request construction itself failing (e.g. an OSError from the
    # underlying socket layer) must still return (None, reason), never
    # propagate.
    monkeypatch.setattr(image_generation, "OPENAI_API_KEY", "sk-test-key")
    with patch.object(image_generation.urllib.request, "urlopen", side_effect=OSError("no route to host")):
        image, reason = image_generation.generate_scene_image("a scene")
    assert image is None
    assert reason is not None and "no route to host" in reason


# --- HTTP error reason capture (real, reported bug fix) -------------------------------------
#
# Real, hand-verified via a live call to https://api.openai.com/v1/images/generations
# while diagnosing this exact bug: OpenAI's own error responses always
# carry a specific, genuinely useful "error.message" - previously
# discarded entirely by a bare `except (urllib.error.URLError, ...):
# return None`. urllib.error.HTTPError IS a URLError subclass, so it was
# silently swallowed along with every other network failure.


def test_insufficient_quota_error_surfaces_the_real_openai_message(monkeypatch):
    # The EXACT failure this bug report was about, reproduced from the
    # real API's own JSON error shape (hand-verified live, not guessed).
    monkeypatch.setattr(image_generation, "OPENAI_API_KEY", "sk-test-key")
    error_body = json.dumps({
        "error": {
            "message": "You have no credits remaining. Add credits to continue using the API at "
                       "https://platform.openai.com/settings/organization/billing/.",
            "type": "insufficient_quota", "param": None, "code": "credit_balance_exhausted",
        },
    }).encode("utf-8")
    with patch.object(
        image_generation.urllib.request, "urlopen",
        side_effect=_http_error(429, "Too Many Requests", error_body),
    ):
        image, reason = image_generation.generate_scene_image("a scene")
    assert image is None
    assert reason is not None
    assert "429" in reason
    assert "no credits remaining" in reason.lower()


def test_unauthorized_error_surfaces_the_real_reason(monkeypatch):
    monkeypatch.setattr(image_generation, "OPENAI_API_KEY", "sk-invalid-key")
    error_body = json.dumps({
        "error": {"message": "Incorrect API key provided.", "type": "invalid_request_error"},
    }).encode("utf-8")
    with patch.object(
        image_generation.urllib.request, "urlopen",
        side_effect=_http_error(401, "Unauthorized", error_body),
    ):
        image, reason = image_generation.generate_scene_image("a scene")
    assert image is None
    assert reason is not None
    assert "401" in reason
    assert "incorrect api key" in reason.lower()


def test_http_error_with_non_json_body_still_surfaces_status_code(monkeypatch):
    monkeypatch.setattr(image_generation, "OPENAI_API_KEY", "sk-test-key")
    with patch.object(
        image_generation.urllib.request, "urlopen",
        side_effect=_http_error(500, "Internal Server Error", b"<html>not json</html>"),
    ):
        image, reason = image_generation.generate_scene_image("a scene")
    assert image is None
    assert reason is not None and "500" in reason


def test_save_scene_image_writes_exact_bytes(tmp_path):
    image_bytes = b"\x89PNG\r\n\x1a\nfake png bytes"
    image = image_generation.GeneratedImage(image_bytes=image_bytes, width=1024, height=1536)
    output_path = tmp_path / "nested" / "scene.png"
    image_generation.save_scene_image(image, output_path=output_path)
    assert output_path.is_file()
    assert output_path.read_bytes() == image_bytes


def test_request_includes_prompt_and_expected_fields(monkeypatch):
    monkeypatch.setattr(image_generation, "OPENAI_API_KEY", "sk-test-key")
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        return _fake_response(_valid_body(b"fake-bytes"))

    with patch.object(image_generation.urllib.request, "urlopen", side_effect=fake_urlopen):
        image_generation.generate_scene_image("a cozy morning scene", visual_source="ai_generated")

    request = captured["request"]
    assert request.get_header("Authorization") == "Bearer sk-test-key"
    body = json.loads(request.data)
    assert "a cozy morning scene" in body["prompt"]
    assert "no text" in body["prompt"].lower()
    assert body["model"] == "gpt-image-1"


def test_prompt_explicitly_steers_screens_away_from_showing_text(monkeypatch):
    # Real, reported bug ("Lithuanian subtitle letters are garbled in
    # the video") traced to gpt-image-1 itself painting its own
    # hallucinated pseudo-text onto a phone/screen it decided to
    # generate, ignoring the existing generic "no text" instruction -
    # jarvis.reel_generator.export's own subtitle/caption pipeline was
    # separately hand-verified to render every Lithuanian diacritic
    # correctly, so this prompt is the actual, real fix location. This
    # is a probabilistic nudge, not a hard guarantee the model obeys -
    # this test only confirms the instruction is actually sent, not that
    # gpt-image-1 always follows it.
    monkeypatch.setattr(image_generation, "OPENAI_API_KEY", "sk-test-key")
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        return _fake_response(_valid_body(b"fake-bytes"))

    with patch.object(image_generation.urllib.request, "urlopen", side_effect=fake_urlopen):
        image_generation.generate_scene_image("a person holding up a phone", visual_source="ai_generated")

    body = json.loads(captured["request"].data)
    prompt_lower = body["prompt"].lower()
    assert "screen" in prompt_lower
    assert "blank" in prompt_lower or "off" in prompt_lower
