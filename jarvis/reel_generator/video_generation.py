"""Real AI image-to-video generation via Runway's REST API - AI Reel
Generator's NATURAL MOTION / HYBRID modes' one real, working
jarvis.reel_generator.ai_video_provider.AIVideoProvider implementation
(see that module's own docstring for the interface this satisfies, and
jarvis.reel_generator.motion_engine for the layer that decides WHAT
motion to ask for and turns any failure here into a well-formed,
never-raised result).

Same "plain urllib.request HTTPS call, no new SDK dependency" pattern
as jarvis.reel_generator.image_generation's own OpenAI integration (see
that module's own docstring for the identical reasoning) - Runway's own
Python SDK is not a dependency of this project and is not added for
this one call shape.

Submit -> poll -> download (a genuinely new pattern for this codebase -
nothing like a polling loop exists anywhere else in JARVIS today, since
every other external-API call here is a single synchronous request/
response): Runway's `image_to_video` generation is not synchronous like
gpt-image-1's - a submit call returns a task id immediately, and the
actual video is only ready some time later, found by polling a
`/v1/tasks/{id}` endpoint until it reports SUCCEEDED or FAILED. The
poll loop here uses an exponential backoff (3s, x1.5 each attempt,
capped at 20s between polls) with a hard 10-minute wall-clock ceiling
(tracked via time.monotonic(), not attempt count, so a slow individual
poll response never silently extends the real budget) - long enough for
a real multi-second video generation to complete under normal
conditions, short enough that a genuinely stuck/misbehaving task is
reported back to the person instead of hanging indefinitely.

Never fakes a successful generation: a missing RUNWAY_API_KEY, a failed
HTTP request at any of the three real network calls (submit/poll/
download), a provider-reported generation failure, a timeout, or a
malformed response all return (None, reason) - a real, specific
description of what went wrong, never a generic placeholder - never
raises, never returns a fabricated/placeholder clip path claimed as
real. The caller (jarvis.reel_generator.motion_engine) falls back to
that scene's own still image with that same specific reason shown as a
clear, visible notice, exactly the same honest-fallback contract
jarvis.reel_generator.image_generation.generate_scene_image() already
established for its own OpenAI integration."""

from __future__ import annotations

import base64
import json
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from jarvis.config import RUNWAY_API_BASE_URL, RUNWAY_API_KEY
from jarvis.reel_generator.ai_video_provider import GeneratedVideo

_DEFAULT_API_BASE_URL = "https://api.runwayml.com"
_API_VERSION_HEADER = "2024-11-06"
# Runway's own documented required request header pinning which version
# of their API shape this integration was built and tested against -
# without it, Runway may serve a different (possibly incompatible)
# response shape in the future without this code ever being told.

_MODEL = "gen3a_turbo"
_DEFAULT_RATIO = "768:1280"
# Runway's own documented supported portrait ratio closest to this
# codebase's 1080x1920 (9:16) target - the downloaded clip is scaled/
# cropped to exactly 1080x1920 at export/splice time
# (jarvis.reel_generator.export's own _clip_input_filter()), same
# "generate close, then fit to our own exact frame" approach
# image_generation.py already uses for gpt-image-1's own closest
# supported size.

_SUBMIT_TIMEOUT_SECONDS = 90
_POLL_TIMEOUT_SECONDS = 30
_DOWNLOAD_TIMEOUT_SECONDS = 90

_POLL_INITIAL_DELAY_SECONDS = 3.0
_POLL_BACKOFF_MULTIPLIER = 1.5
_POLL_MAX_DELAY_SECONDS = 20.0
_POLL_WALL_CLOCK_CEILING_SECONDS = 600.0  # 10 minutes

_STATUS_SUCCEEDED = "SUCCEEDED"
_STATUS_FAILED = "FAILED"
_STATUS_PENDING = "PENDING"
_STATUS_RUNNING = "RUNNING"
# Runway's own documented task lifecycle values. Any OTHER string is
# treated as transient exactly once (a forward-compatible allowance for
# an intermediate status this integration doesn't yet know the name of)
# before being surfaced as a real failure - never silently swallowed or
# assumed to mean success.


def _api_base_url() -> str:
    return RUNWAY_API_BASE_URL or _DEFAULT_API_BASE_URL


def is_configured() -> bool:
    """True if RUNWAY_API_KEY is set - callers (jarvis.reel_generator
    .motion_engine) use this to decide whether to attempt real
    generation at all, or go straight to that scene's still-image
    fallback without ever making a doomed HTTP request - same "check
    before attempting" convention as jarvis.reel_generator
    .image_generation.is_configured()."""
    return bool(RUNWAY_API_KEY)


def _parse_http_error(e: urllib.error.HTTPError) -> str:
    """Shared HTTPError-to-reason parsing for all three network calls
    below - identical approach to jarvis.reel_generator.image_generation
    .generate_scene_image()'s own HTTPError handling: parse Runway's own
    JSON error body for a specific message, fall back to the raw body/
    status only if the body isn't the expected JSON shape."""
    try:
        error_body = e.read()
        error_json = json.loads(error_body)
        message = error_json.get("error") or error_json.get("message")
        return f"HTTP {e.code}: {message}" if message else f"HTTP {e.code}: {error_body[:200]!r}"
    except Exception:
        return f"HTTP {e.code} {e.reason}"


def _submit_image_to_video(source_image_path: Path, motion_prompt: str, *, duration_seconds: float) -> tuple[str | None, str | None]:
    """Submits a real image_to_video generation task. Returns (task_id,
    None) on success, or (None, reason) on any failure - never raises."""
    try:
        image_bytes = source_image_path.read_bytes()
    except OSError as e:
        return None, f"couldn't read source image: {e}"

    mime = "image/png" if source_image_path.suffix.lower() == ".png" else "image/jpeg"
    prompt_image = f"data:{mime};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    body = json.dumps({
        "model": _MODEL,
        "promptImage": prompt_image,
        "promptText": motion_prompt,
        "duration": int(round(duration_seconds)),
        "ratio": _DEFAULT_RATIO,
    }).encode("utf-8")

    request = urllib.request.Request(
        f"{_api_base_url()}/v1/image_to_video", data=body, method="POST",
        headers={
            "Authorization": f"Bearer {RUNWAY_API_KEY}",
            "Content-Type": "application/json",
            "X-Runway-Version": _API_VERSION_HEADER,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=_SUBMIT_TIMEOUT_SECONDS) as response:
            response_body = response.read()
    except urllib.error.HTTPError as e:
        return None, _parse_http_error(e)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, f"network error: {e}"

    try:
        parsed = json.loads(response_body)
        task_id = parsed["id"]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        return None, f"unexpected submit response shape: {e}"
    return task_id, None


def _poll_task_status(task_id: str) -> tuple[dict | None, str | None]:
    """Polls Runway's task endpoint until SUCCEEDED/FAILED or the wall-
    clock ceiling is reached. Returns (task_json, None) once SUCCEEDED,
    or (None, reason) for FAILED/timeout/any network error - never
    raises."""
    request = urllib.request.Request(
        f"{_api_base_url()}/v1/tasks/{task_id}", method="GET",
        headers={"Authorization": f"Bearer {RUNWAY_API_KEY}", "X-Runway-Version": _API_VERSION_HEADER},
    )
    start = time.monotonic()
    delay = _POLL_INITIAL_DELAY_SECONDS
    saw_unrecognized_status_once = False

    while True:
        try:
            with urllib.request.urlopen(request, timeout=_POLL_TIMEOUT_SECONDS) as response:
                response_body = response.read()
        except urllib.error.HTTPError as e:
            return None, _parse_http_error(e)
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return None, f"network error while polling: {e}"

        try:
            task = json.loads(response_body)
            status = task["status"]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            return None, f"unexpected poll response shape: {e}"

        if status == _STATUS_SUCCEEDED:
            return task, None
        if status == _STATUS_FAILED:
            failure_reason = task.get("failure") or task.get("failureCode") or "unknown reason"
            return None, f"Runway generation failed: {failure_reason}"
        if status not in (_STATUS_PENDING, _STATUS_RUNNING):
            if saw_unrecognized_status_once:
                return None, f"Runway returned an unrecognized task status: {status!r}"
            saw_unrecognized_status_once = True

        elapsed = time.monotonic() - start
        if elapsed >= _POLL_WALL_CLOCK_CEILING_SECONDS:
            return None, f"Runway generation timed out after 10 minutes (task id: {task_id})"

        time.sleep(min(delay, _POLL_WALL_CLOCK_CEILING_SECONDS - elapsed))
        delay = min(delay * _POLL_BACKOFF_MULTIPLIER, _POLL_MAX_DELAY_SECONDS)


def _download_video(video_url: str) -> tuple[bytes | None, str | None]:
    """Downloads the finished clip's raw bytes. Returns (bytes, None) on
    success, or (None, reason) on any failure - never raises."""
    request = urllib.request.Request(video_url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response:
            return response.read(), None
    except urllib.error.HTTPError as e:
        return None, _parse_http_error(e)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return None, f"network error while downloading: {e}"


def generate_scene_video(
    source_image_path: Path, motion_prompt: str, *, duration_seconds: float = 5.0,
) -> tuple[GeneratedVideo | None, str | None]:
    """Generates one real AI video clip from `source_image_path` via
    Runway's image_to_video API (submit -> poll -> download, see this
    module's own docstring for the full mechanism). Returns
    (GeneratedVideo, None) on success, or (None, reason) on any failure
    - never raises, matching jarvis.reel_generator.image_generation
    .generate_scene_image()'s own contract exactly.

    width/height/duration on the returned GeneratedVideo are always
    RE-PROBED from the real downloaded bytes via
    jarvis.video_studio.ffmpeg_utils.probe_video() - never trusted
    blindly from whatever Runway's own API response claims, matching
    this codebase's established "measure the real file, don't trust a
    provider's self-reported metadata" convention (see
    jarvis.reel_generator.quality_control's own docstring for the same
    principle applied to export quality checks)."""
    if not RUNWAY_API_KEY:
        return None, "RUNWAY_API_KEY is not configured"
    prompt = motion_prompt.strip()
    if not prompt:
        return None, "scene has no motion prompt to generate from"
    if not source_image_path.is_file():
        return None, f"source image does not exist: {source_image_path}"

    task_id, error = _submit_image_to_video(source_image_path, prompt, duration_seconds=duration_seconds)
    if task_id is None:
        return None, error

    task, error = _poll_task_status(task_id)
    if task is None:
        return None, error

    try:
        video_url = task["output"][0]
    except (KeyError, IndexError, TypeError) as e:
        return None, f"unexpected succeeded-task response shape: {e}"

    video_bytes, error = _download_video(video_url)
    if video_bytes is None:
        return None, error

    try:
        from jarvis.video_studio.ffmpeg_utils import FFmpegError, probe_video

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp_file:
            tmp_file.write(video_bytes)
            tmp_path = Path(tmp_file.name)
        try:
            probe = probe_video(tmp_path)
        finally:
            tmp_path.unlink(missing_ok=True)
    except FFmpegError as e:
        return None, f"couldn't probe the downloaded clip: {e}"

    return GeneratedVideo(
        video_bytes=video_bytes, duration_seconds=probe.duration_seconds,
        width=probe.width or 0, height=probe.height or 0,
    ), None


def save_scene_video(video: GeneratedVideo, *, output_path: Path) -> None:
    """Writes a GeneratedVideo's raw bytes to disk - a plain file write,
    mirrors jarvis.reel_generator.image_generation.save_scene_image()
    exactly."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(video.video_bytes)
