"""Real photorealistic scene image generation via OpenAI's Images API
(gpt-image-1) - AI Reel Generator's "Visual Story Director" brief's own
explicit requirement: option (B) "GENERATED REALISTIC IMAGE", used
whenever a scene's own ScenePlan.visual_source calls for it
(ai_generated/b_roll/lifestyle-style scenes - see
jarvis.reel_generator.visual_plan's own VISUAL_SOURCE_CHOICES) and no
user-uploaded photo/video exists for that scene.

Architecture note (confirmed exhaustively, repeatedly, across this
entire codebase's history before this module was written): NO image or
video generation capability existed anywhere in JARVIS before this
module. This is the first and only module in the whole project that
calls an actual AI image-generation API - every other "visual" anywhere
else in this codebase (jarvis.design_studio, jarvis.reel_generator
.scenes/.scene_render, jarvis.story_generator) is Pillow-rendered
typography/gradient art, and stays that way; this module exists
specifically because the person explicitly asked for real generated
photography and explicitly chose to add a real paid image-generation
API rather than keep using Pillow-only rendering for every scene.

Same "plain urllib.request HTTPS call, no new SDK dependency" pattern
as jarvis.voice.text_to_speech's own Azure Speech integration (see that
module's own docstring for the identical reasoning) - openai's own
Python SDK is not a dependency of this project and is not added for
this one call shape.

Never fakes a successful generation: a missing OPENAI_API_KEY, a failed
HTTP request, a malformed response, or any other failure returns
(None, reason) - a real, specific description of what went wrong (HTTP
status + the API's own error message when one exists, never a generic
placeholder) - never raises, never returns a fabricated/placeholder
image path claimed as real. The caller (jarvis.reel_generator
.scene_render) falls back to this codebase's own Pillow text-card
rendering with that same specific reason shown as a clear, visible
notice, exactly the same honest-fallback contract
jarvis.voice.text_to_speech.speak() already established for its own
Azure/SAPI tiers."""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from jarvis.config import OPENAI_API_KEY

_API_URL = "https://api.openai.com/v1/images/generations"
_MODEL = "gpt-image-1"
_REQUEST_TIMEOUT_SECONDS = 90
# gpt-image-1 generation is meaningfully slower than a text completion
# (real image synthesis, not a token stream) - 90s gives real requests
# realistic headroom without hanging indefinitely on a genuinely stuck
# connection.

# gpt-image-1's own supported size values (OpenAI's official published
# list) - "1024x1536" is the closest portrait/vertical option to this
# codebase's own 1080x1920 (9:16) target; the returned image is
# resized/cropped to exactly 1080x1920 by the caller
# (jarvis.reel_generator.scene_render), same "generate close, then fit
# to our own exact frame" approach jarvis.design_studio.render already
# uses for uploaded images.
_IMAGE_SIZE = "1024x1536"
_IMAGE_QUALITY = "medium"
# "medium" (of low/medium/high) - a deliberate cost/quality balance
# for a per-scene, potentially 5-10-scenes-per-Reel generation volume;
# not configurable from the GUI in this first version.


class ImageGenerationError(Exception):
    """Raised only for this module's own internal use in tests - real
    callers use generate_scene_image()'s own None-on-failure contract
    instead of catching this, matching jarvis.voice.text_to_speech's
    own "never raise past the boundary, report via a plain value"
    convention."""


@dataclass(frozen=True)
class GeneratedImage:
    image_bytes: bytes
    width: int
    height: int


def is_configured() -> bool:
    """True if OPENAI_API_KEY is set - callers use this to decide
    whether to attempt real generation at all, or go straight to the
    Pillow fallback without ever making a doomed HTTP request (same
    "check before attempting" convention as
    jarvis.voice.text_to_speech's own Azure tier, which only attempts
    Azure when AZURE_SPEECH_KEY/AZURE_SPEECH_REGION are both set)."""
    return bool(OPENAI_API_KEY)


def _build_prompt(main_visual_prompt: str, *, visual_source: str) -> str:
    """Builds the actual prompt sent to gpt-image-1 - the ScenePlan's
    own main_visual_prompt (already a detailed, camera/subject/
    environment-aware description - see
    jarvis.reel_generator.storyboard's own Visual Story Director system
    prompt) plus a short style qualifier steering toward the
    photorealistic, cinematic lifestyle photography look the module
    brief's own example scenes describe, and an explicit instruction
    never to render any text/words in the image itself (this codebase's
    own on-screen text is composited separately, by
    jarvis.reel_generator.scene_render, over this image - baking text
    into the generated photo would fight with that separate text
    layer).

    Real, reported bug ("Lithuanian subtitle letters are garbled in the
    video" - investigated and traced to THIS prompt, not to
    jarvis.reel_generator.export's own subtitle/caption pipeline, which
    was hand-verified separately to render every Lithuanian diacritic
    correctly): gpt-image-1 does not always obey the "no text" line
    above - a scene whose own main_visual_prompt implies a phone/
    screen/sign/document/book (a subject where real photos usually DO
    show text) is meaningfully more likely to have the model paint its
    own hallucinated, garbled pseudo-text into that implied surface
    regardless of the instruction, since it has no actual language
    model backing the glyphs it draws - this is a well-documented
    limitation of current text-to-image models generally, not specific
    to Lithuanian or to this codebase's own prompt wording. The extra
    sentence below explicitly calls out phone/device/computer screens
    and signage as surfaces to render BLANK/OFF/turned-away rather than
    showing content on them - a real, concrete instruction the model
    can act on (unlike a generic "no text" that gives it no alternative
    for what to do with a screen it still wants to draw), which
    meaningfully reduces this specific failure mode. This is a
    probabilistic nudge, not a guarantee - gpt-image-1's own text
    rendering remains unreliable in any language when it does still
    decide to draw some, which is exactly why every REAL on-screen
    caption in this codebase (SRT subtitles, baked-in headlines, text
    cues) is deliberately composited separately by Pillow/ffmpeg
    afterwards, never generated by this model."""
    return (
        f"{main_visual_prompt.strip()} Photorealistic, cinematic lifestyle photography, natural "
        "lighting, vertical 9:16 composition, shallow depth of field. "
        "Absolutely no text, letters, words, captions, or writing anywhere in the image. "
        "If a phone, computer, TV, or other screen appears, show it blank, off, reflective, or "
        "angled away from the camera - never displaying any visible text or interface. "
        "Avoid signs, posters, labels, or documents with visible writing."
    )


def generate_scene_image(
    main_visual_prompt: str, *, visual_source: str = "ai_generated",
) -> tuple[GeneratedImage | None, str | None]:
    """Generates one real, photorealistic scene image via OpenAI's
    Images API. Returns (image, None) on success, or (None, reason) on
    any failure - never raises.

    Real, reported bug fix ("scenes show COMPLETED (fallback used) /
    AI image generation failed (network error or no usable response)"
    with no way to tell WHY): this used to return a plain
    GeneratedImage | None, collapsing every failure - a missing API
    key, an empty prompt, a genuine network error, AND every HTTP error
    response (401 bad key, 403 access denied, 429 rate-limited/
    insufficient_quota, 400 bad request, etc.) - into an identical bare
    None, discarding the HTTP status code and the response body's own
    specific error message every time. A real, live call against this
    exact endpoint (hand-verified while diagnosing this bug) confirmed
    OpenAI's error responses always carry a genuinely useful, specific
    reason (e.g. {"error": {"message": "You have no credits
    remaining...", "type": "insufficient_quota", ...}}) that was simply
    being thrown away - `reason` now carries that (or an equivalent
    description for a non-HTTP network failure) all the way up to the
    UI (see jarvis.reel_generator.scene_render's own
    _generate_real_scene_image()), instead of the same generic "network
    error or no usable response" message regardless of the real cause.
    The caller still falls back to Pillow rendering in every failure
    case, exactly like jarvis.voice.text_to_speech.speak()'s own
    tiered-fallback contract for its own external service call - only
    the REASON shown for that fallback is more specific now, nothing
    about the fallback behavior itself changed."""
    if not OPENAI_API_KEY:
        return None, "OPENAI_API_KEY is not configured"
    prompt = main_visual_prompt.strip()
    if not prompt:
        return None, "scene has no visual prompt to generate from"

    body = json.dumps({
        "model": _MODEL,
        "prompt": _build_prompt(prompt, visual_source=visual_source),
        "size": _IMAGE_SIZE,
        "quality": _IMAGE_QUALITY,
        "n": 1,
    }).encode("utf-8")

    request = urllib.request.Request(
        _API_URL, data=body, method="POST",
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_REQUEST_TIMEOUT_SECONDS) as response:
            response_body = response.read()
    except urllib.error.HTTPError as e:
        # The real, common failure class this bug fix targets - OpenAI
        # returns a genuinely specific JSON error body for every one of
        # these (401/403/400/429/5xx) that was previously discarded
        # entirely. Falls back to the raw status code alone only if the
        # body itself isn't the expected JSON shape (still real
        # information, never fabricated).
        try:
            error_body = e.read()
            error_json = json.loads(error_body)
            message = error_json.get("error", {}).get("message") or error_json.get("error")
            reason = f"HTTP {e.code}: {message}" if message else f"HTTP {e.code}: {error_body[:200]!r}"
        except Exception:
            reason = f"HTTP {e.code} {e.reason}"
        return None, reason
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, f"network error: {e}"

    try:
        parsed = json.loads(response_body)
        b64_data = parsed["data"][0]["b64_json"]
        image_bytes = base64.b64decode(b64_data)
    except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValueError) as e:
        return None, f"unexpected response shape: {e}"

    width, height = (int(x) for x in _IMAGE_SIZE.split("x"))
    return GeneratedImage(image_bytes=image_bytes, width=width, height=height), None


def save_scene_image(image: GeneratedImage, *, output_path: Path) -> None:
    """Writes a GeneratedImage's raw bytes to disk - a plain file write,
    kept as its own function so callers can save-then-composite (e.g.
    jarvis.reel_generator.scene_render pastes this file in as the base
    layer before compositing text/stickers on top) without re-decoding
    the same bytes twice."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(image.image_bytes)
