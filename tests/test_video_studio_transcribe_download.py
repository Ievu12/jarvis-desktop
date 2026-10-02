"""Tests for jarvis.video_studio.transcribe.download_model() - the
whisper.cpp model download itself, with urllib mocked (no real network
call, no real ~148MB download in the test suite). Separate file from
test_video_studio_transcribe.py because that file's real-model tests
are skipped when the model isn't downloaded yet, but this download
logic should always be tested regardless of whether the actual model
file happens to be present on the machine running the suite.
"""

from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest

from jarvis.video_studio import transcribe


@pytest.fixture(autouse=True)
def _isolated_models_dir(tmp_path, monkeypatch):
    models_dir = tmp_path / "models"
    monkeypatch.setattr(transcribe, "VIDEO_STUDIO_MODELS_DIR", models_dir)
    return models_dir


def _fake_response(data: bytes, *, report_length: bool = True):
    response = MagicMock()
    response.length = len(data) if report_length else 0
    response.__enter__.return_value = response
    response.__exit__.return_value = False
    buf = io.BytesIO(data)
    response.read = buf.read
    return response


def test_download_model_writes_file():
    fake_data = b"x" * 1000
    with patch("jarvis.video_studio.transcribe.urllib.request.urlopen", return_value=_fake_response(fake_data)):
        transcribe.download_model()
    assert transcribe.model_path().is_file()
    assert transcribe.model_path().read_bytes() == fake_data


def test_download_model_is_idempotent_when_already_present():
    transcribe.model_path().parent.mkdir(parents=True)
    transcribe.model_path().write_bytes(b"already here")
    with patch("jarvis.video_studio.transcribe.urllib.request.urlopen") as mock_urlopen:
        transcribe.download_model()
    mock_urlopen.assert_not_called()
    assert transcribe.model_path().read_bytes() == b"already here"


def test_download_model_reports_progress():
    fake_data = b"x" * (5 * 1024 * 1024)  # 5MB, several 1MB read() chunks
    progress_values = []
    with patch("jarvis.video_studio.transcribe.urllib.request.urlopen", return_value=_fake_response(fake_data)):
        transcribe.download_model(progress_callback=progress_values.append)
    assert progress_values
    assert progress_values[-1] == pytest.approx(1.0)
    assert all(0.0 <= v <= 1.0 for v in progress_values)


def test_download_model_network_failure_raises_and_cleans_up():
    with patch("jarvis.video_studio.transcribe.urllib.request.urlopen", side_effect=OSError("network down")):
        with pytest.raises(transcribe.TranscriptionError, match="network"):
            transcribe.download_model()
    assert not transcribe.model_path().exists()
    assert not transcribe.model_path().with_suffix(".bin.partial").exists()


def test_model_is_downloaded_false_when_absent():
    assert transcribe.model_is_downloaded() is False


def test_model_is_downloaded_true_after_download():
    with patch("jarvis.video_studio.transcribe.urllib.request.urlopen", return_value=_fake_response(b"data")):
        transcribe.download_model()
    assert transcribe.model_is_downloaded() is True
