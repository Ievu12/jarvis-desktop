"""Tests for jarvis.gui.updater: GitHub Releases version checking,
checksum-verified download, and install/rollback. All network access
(urllib.request) is mocked - no real HTTP call to GitHub. All filesystem
operations use tmp_path - no test touches the real JARVIS installation
or repository files. Confirms: semantic (not string) version comparison,
a checksum mismatch aborts the update without touching the running
install, install_update() moves (never deletes) the previous version
before replacing it, and rollback_update() restores it."""

from __future__ import annotations

import hashlib
import json
import shutil
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from jarvis.gui import updater


def _mock_json_response(payload: dict):
    body = json.dumps(payload).encode("utf-8")
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = body
    return cm


def _release_payload(*, tag: str, zip_url: str = "https://x/update.zip", checksum_url: str = "https://x/SHA256SUMS.txt", body: str = "notes"):
    return {
        "tag_name": tag,
        "body": body,
        "assets": [
            {"name": "update.zip", "browser_download_url": zip_url},
            {"name": "SHA256SUMS.txt", "browser_download_url": checksum_url},
        ],
    }


# --- check_for_update(): semantic version comparison ---------------------------------


def test_check_for_update_detects_newer_version():
    payload = _release_payload(tag="v9.9.9")
    with patch("urllib.request.urlopen", return_value=_mock_json_response(payload)):
        with patch("jarvis.gui.updater.__version__", "1.0.0"):
            result = updater.check_for_update()
    assert result.update_available is True
    assert result.latest_version == "9.9.9"
    assert result.current_version == "1.0.0"


def test_check_for_update_no_update_when_same_version():
    payload = _release_payload(tag="v1.0.0")
    with patch("urllib.request.urlopen", return_value=_mock_json_response(payload)):
        with patch("jarvis.gui.updater.__version__", "1.0.0"):
            result = updater.check_for_update()
    assert result.update_available is False


def test_check_for_update_no_update_when_older_version_published():
    payload = _release_payload(tag="v0.9.0")
    with patch("urllib.request.urlopen", return_value=_mock_json_response(payload)):
        with patch("jarvis.gui.updater.__version__", "1.0.0"):
            result = updater.check_for_update()
    assert result.update_available is False


def test_check_for_update_uses_semantic_not_string_comparison():
    # String comparison would say "1.9.0" > "1.10.0" (wrong) - semantic
    # version comparison must get this right.
    payload = _release_payload(tag="v1.10.0")
    with patch("urllib.request.urlopen", return_value=_mock_json_response(payload)):
        with patch("jarvis.gui.updater.__version__", "1.9.0"):
            result = updater.check_for_update()
    assert result.update_available is True
    assert result.latest_version == "1.10.0"


def test_check_for_update_extracts_download_and_checksum_urls():
    payload = _release_payload(
        tag="v2.0.0", zip_url="https://example/JARVIS-2.0.0.zip", checksum_url="https://example/SHA256SUMS.txt",
    )
    with patch("urllib.request.urlopen", return_value=_mock_json_response(payload)):
        with patch("jarvis.gui.updater.__version__", "1.0.0"):
            result = updater.check_for_update()
    assert result.download_url == "https://example/JARVIS-2.0.0.zip"
    assert result.checksum_url == "https://example/SHA256SUMS.txt"


def test_check_for_update_includes_changelog_from_release_body():
    payload = _release_payload(tag="v2.0.0", body="- Added PDF reading\n- Fixed a bug")
    with patch("urllib.request.urlopen", return_value=_mock_json_response(payload)):
        with patch("jarvis.gui.updater.__version__", "1.0.0"):
            result = updater.check_for_update()
    assert result.changelog == "- Added PDF reading\n- Fixed a bug"


# --- check_for_update(): failure modes never raise ------------------------------------


def test_check_for_update_no_releases_yet_returns_no_update_no_error():
    with patch("urllib.request.urlopen", side_effect=urllib.error.HTTPError("url", 404, "not found", {}, None)):
        result = updater.check_for_update()
    assert result.update_available is False
    assert result.error is None


def test_check_for_update_network_error_returns_clear_error_not_exception():
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no network")):
        result = updater.check_for_update()
    assert result.update_available is False
    assert result.error is not None


def test_check_for_update_malformed_json_returns_clear_error():
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = b"{not valid json"
    with patch("urllib.request.urlopen", return_value=cm):
        result = updater.check_for_update()
    assert result.update_available is False
    assert result.error is not None


def test_check_for_update_malformed_tag_returns_clear_error_not_exception():
    payload = _release_payload(tag="not-a-version")
    with patch("urllib.request.urlopen", return_value=_mock_json_response(payload)):
        result = updater.check_for_update()  # must not raise
    assert result.update_available is False
    assert result.error is not None


# --- download_update(): checksum verification --------------------------------------


def _make_zip(tmp_path: Path, name: str = "release.zip") -> Path:
    zip_path = tmp_path / name
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("JARVIS.exe", b"fake exe bytes")
    return zip_path


def test_download_update_succeeds_with_matching_checksum(tmp_path):
    source_zip = _make_zip(tmp_path, "source.zip")
    sha256 = hashlib.sha256(source_zip.read_bytes()).hexdigest()

    check_result = updater.UpdateCheckResult(
        update_available=True, current_version="1.0.0", latest_version="2.0.0",
        changelog=None, download_url="https://x/update.zip", checksum_url="https://x/SHA256SUMS.txt",
    )
    destination_dir = tmp_path / "staging"

    def _fake_urlretrieve(url, filename):
        if url.endswith("SHA256SUMS.txt"):
            Path(filename).write_text(f"{sha256}  update.zip\n", encoding="utf-8")
        else:
            shutil.copy(source_zip, filename)

    with patch("urllib.request.urlretrieve", side_effect=_fake_urlretrieve):
        result_path = updater.download_update(check_result, destination_dir=destination_dir)

    assert result_path.exists()
    assert result_path.name == "update.zip"


def test_download_update_raises_on_checksum_mismatch(tmp_path):
    source_zip = _make_zip(tmp_path, "source.zip")
    wrong_sha256 = "0" * 64

    check_result = updater.UpdateCheckResult(
        update_available=True, current_version="1.0.0", latest_version="2.0.0",
        changelog=None, download_url="https://x/update.zip", checksum_url="https://x/SHA256SUMS.txt",
    )
    destination_dir = tmp_path / "staging"

    def _fake_urlretrieve(url, filename):
        if url.endswith("SHA256SUMS.txt"):
            Path(filename).write_text(f"{wrong_sha256}  update.zip\n", encoding="utf-8")
        else:
            shutil.copy(source_zip, filename)

    with patch("urllib.request.urlretrieve", side_effect=_fake_urlretrieve):
        with pytest.raises(updater.UpdateError, match="checksum"):
            updater.download_update(check_result, destination_dir=destination_dir)


def test_download_update_mismatch_deletes_the_bad_download(tmp_path):
    source_zip = _make_zip(tmp_path, "source.zip")
    destination_dir = tmp_path / "staging"

    def _fake_urlretrieve(url, filename):
        if url.endswith("SHA256SUMS.txt"):
            Path(filename).write_text("0" * 64 + "  update.zip\n", encoding="utf-8")
        else:
            shutil.copy(source_zip, filename)

    check_result = updater.UpdateCheckResult(
        update_available=True, current_version="1.0.0", latest_version="2.0.0",
        changelog=None, download_url="https://x/update.zip", checksum_url="https://x/SHA256SUMS.txt",
    )
    with patch("urllib.request.urlretrieve", side_effect=_fake_urlretrieve):
        with pytest.raises(updater.UpdateError):
            updater.download_update(check_result, destination_dir=destination_dir)

    assert not (destination_dir / "update.zip").exists()


def test_download_update_missing_download_url_raises_without_network_call():
    check_result = updater.UpdateCheckResult(
        update_available=True, current_version="1.0.0", latest_version="2.0.0",
        changelog=None, download_url=None, checksum_url="https://x/SHA256SUMS.txt",
    )
    with patch("urllib.request.urlretrieve") as mock_retrieve:
        with pytest.raises(updater.UpdateError):
            updater.download_update(check_result, destination_dir=Path("/x"))
    mock_retrieve.assert_not_called()


# --- install_update(): moves (never deletes) the previous version ------------------


def test_install_update_extracts_and_replaces_install_dir(tmp_path):
    zip_path = tmp_path / "verified.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("JARVIS.exe", b"new version bytes")

    install_dir = tmp_path / "JARVIS"
    install_dir.mkdir()
    (install_dir / "JARVIS.exe").write_bytes(b"old version bytes")

    exe_path = updater.install_update(zip_path, install_dir=install_dir)

    assert exe_path == install_dir / "JARVIS.exe"
    assert exe_path.read_bytes() == b"new version bytes"


def test_install_update_moves_previous_version_to_backup_not_deleting_it(tmp_path):
    zip_path = tmp_path / "verified.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("JARVIS.exe", b"new version bytes")

    install_dir = tmp_path / "JARVIS"
    install_dir.mkdir()
    (install_dir / "JARVIS.exe").write_bytes(b"old version bytes")

    updater.install_update(zip_path, install_dir=install_dir)

    backup_dir = tmp_path / "JARVIS_backup"
    assert backup_dir.exists()
    assert (backup_dir / "JARVIS.exe").read_bytes() == b"old version bytes"


def test_install_update_raises_and_never_touches_install_dir_if_exe_missing_from_zip(tmp_path):
    zip_path = tmp_path / "bad.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("readme.txt", b"not an exe")

    install_dir = tmp_path / "JARVIS"
    install_dir.mkdir()
    (install_dir / "JARVIS.exe").write_bytes(b"old version bytes")

    with pytest.raises(updater.UpdateError):
        updater.install_update(zip_path, install_dir=install_dir)

    # The original install must be completely untouched.
    assert (install_dir / "JARVIS.exe").read_bytes() == b"old version bytes"


# --- rollback_update(): restores the backup ------------------------------------------


def test_rollback_update_restores_backup_into_install_dir(tmp_path):
    install_dir = tmp_path / "JARVIS"
    backup_dir = tmp_path / "JARVIS_backup"
    backup_dir.mkdir()
    (backup_dir / "JARVIS.exe").write_bytes(b"old working version")

    install_dir.mkdir()
    (install_dir / "JARVIS.exe").write_bytes(b"broken new version")

    result = updater.rollback_update(install_dir=install_dir)

    assert result is True
    assert (install_dir / "JARVIS.exe").read_bytes() == b"old working version"
    assert not backup_dir.exists()


def test_rollback_update_returns_false_when_no_backup_exists(tmp_path):
    install_dir = tmp_path / "JARVIS"
    install_dir.mkdir()
    result = updater.rollback_update(install_dir=install_dir)
    assert result is False


def test_rollback_update_preserves_the_failed_install_for_inspection(tmp_path):
    install_dir = tmp_path / "JARVIS"
    backup_dir = tmp_path / "JARVIS_backup"
    backup_dir.mkdir()
    (backup_dir / "JARVIS.exe").write_bytes(b"old working version")

    install_dir.mkdir()
    (install_dir / "JARVIS.exe").write_bytes(b"broken new version")

    updater.rollback_update(install_dir=install_dir)

    failed_dir = tmp_path / "JARVIS_failed_update"
    assert failed_dir.exists()
    assert (failed_dir / "JARVIS.exe").read_bytes() == b"broken new version"


# --- verify_executable_starts(): never raises -----------------------------------------


def test_verify_executable_starts_false_when_exe_does_not_exist(tmp_path):
    assert updater.verify_executable_starts(tmp_path / "nonexistent.exe") is False


def test_verify_executable_starts_true_on_zero_exit_code(tmp_path):
    fake_exe = tmp_path / "JARVIS.exe"
    fake_exe.write_bytes(b"x")
    mock_process = MagicMock()
    mock_process.wait.return_value = 0
    with patch("subprocess.Popen", return_value=mock_process):
        assert updater.verify_executable_starts(fake_exe) is True


def test_verify_executable_starts_false_on_nonzero_exit_code(tmp_path):
    fake_exe = tmp_path / "JARVIS.exe"
    fake_exe.write_bytes(b"x")
    mock_process = MagicMock()
    mock_process.wait.return_value = 1
    with patch("subprocess.Popen", return_value=mock_process):
        assert updater.verify_executable_starts(fake_exe) is False


def test_verify_executable_starts_never_raises_on_popen_failure(tmp_path):
    fake_exe = tmp_path / "JARVIS.exe"
    fake_exe.write_bytes(b"x")
    with patch("subprocess.Popen", side_effect=OSError("cannot launch")):
        assert updater.verify_executable_starts(fake_exe) is False


# --- API keys never appear in any request or output ------------------------------


def test_check_for_update_request_carries_no_secret():
    payload = _release_payload(tag="v1.0.0")
    captured_requests = []

    def _capture(request, timeout=None):
        captured_requests.append(request)
        return _mock_json_response(payload)

    with patch("urllib.request.urlopen", side_effect=_capture):
        updater.check_for_update()

    for request in captured_requests:
        assert "Authorization" not in request.headers
