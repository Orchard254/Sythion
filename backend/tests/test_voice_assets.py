"""
Tests for voice-asset pinning and explicit installation.

Privacy contract: voice assets NEVER download at import or startup. The only
download path is an explicit owner-initiated install, and it verifies the
pinned SHA-256 before use. These tests exercise the manifest, checksum logic,
and install flow against local fixture files — no network is touched.
"""
import hashlib
import os
import zipfile

import pytest

from backend.core import voice_assets


def _make_zip(path: str, model_dir_name: str) -> str:
    """Create a minimal Vosk-like archive with the expected layout."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{model_dir_name}/am/final.mdl", b"AM")
        zf.writestr(f"{model_dir_name}/graph/HCLG.fst", b"GRAPH")
        zf.writestr(f"{model_dir_name}/graph/words.txt", b"WORD")
    return path


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


class TestManifest:
    def test_manifest_is_pinned_and_non_secret(self):
        manifest = voice_assets.manifest()
        stt = manifest["stt"]
        assert stt["name"] == "vosk-model-small-en-us-0.15"
        assert stt["version"] == "0.15"
        assert stt["license"] == "Apache-2.0"
        assert stt["downloads_on_import"] is False
        assert stt["downloads_on_startup"] is False
        assert stt["url"].startswith("https://")
        # A pinned checksum must be present and parse as hex of 32 bytes.
        assert len(stt["sha256"]) == 64
        int(stt["sha256"], 16)

    def test_tts_is_local_with_no_download(self):
        tts = voice_assets.manifest()["tts"]
        assert tts["provider"] == "sapi5"
        assert tts["downloads_on_import"] is False
        assert tts["downloads_on_startup"] is False

    def test_import_does_not_download(self, monkeypatch):
        """Importing the module must never initiate a download."""
        called = {}

        def _fail_download(*args, **kwargs):
            called["downloaded"] = True
            raise AssertionError("voice_assets must not download at import")

        monkeypatch.setattr(voice_assets, "_download_pinned_archive", _fail_download)
        import importlib

        importlib.reload(voice_assets)
        assert "downloaded" not in called


class TestChecksum:
    def test_verify_checksum_matches(self, tmp_path):
        archive = tmp_path / "model.zip"
        _make_zip(str(archive), "vosk-model-small-en-us-0.15")
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(voice_assets, "STT_MODEL_SHA256", _sha256(str(archive)))
        try:
            assert voice_assets.verify_checksum(str(archive)) is True
        finally:
            monkeypatch.undo()

    def test_verify_checksum_rejects_tampered(self, tmp_path):
        archive = tmp_path / "model.zip"
        _make_zip(str(archive), "vosk-model-small-en-us-0.15")
        # Tamper: append bytes so the digest differs.
        with open(str(archive), "ab") as f:
            f.write(b"\x00\x01")
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(voice_assets, "STT_MODEL_SHA256", "0" * 64)
        try:
            assert voice_assets.verify_checksum(str(archive)) is False
        finally:
            monkeypatch.undo()


class TestInstall:
    def test_install_from_verified_archive(self, tmp_path, monkeypatch):
        """Explicit install extracts a checksum-verified archive and writes a marker."""
        archive = tmp_path / "model.zip"
        _make_zip(str(archive), "vosk-model-small-en-us-0.15")

        assets_dir = str(tmp_path / "assets")
        monkeypatch.setattr(voice_assets, "voice_assets_dir", lambda: assets_dir)
        monkeypatch.setattr(
            voice_assets, "STT_MODEL_SHA256", _sha256(str(archive))
        )

        status = voice_assets.install_voice_assets(archive_path=str(archive))

        assert status["stt_installed"] is True
        assert status["stt_sha256_verified"] is True
        model_dir = os.path.join(assets_dir, "vosk-model-small-en-us-0.15")
        assert os.path.isfile(os.path.join(model_dir, "am", "final.mdl"))
        assert os.path.isfile(os.path.join(model_dir, ".jarvis-verified"))

    def test_install_refuses_tampered_archive(self, tmp_path, monkeypatch):
        """A checksum mismatch aborts installation and leaves nothing installed."""
        archive = tmp_path / "model.zip"
        _make_zip(str(archive), "vosk-model-small-en-us-0.15")

        assets_dir = str(tmp_path / "assets")
        monkeypatch.setattr(voice_assets, "voice_assets_dir", lambda: assets_dir)
        monkeypatch.setattr(voice_assets, "STT_MODEL_SHA256", "0" * 64)

        with pytest.raises(voice_assets.VoiceAssetVerificationError):
            voice_assets.install_voice_assets(archive_path=str(archive))

        model_dir = os.path.join(assets_dir, "vosk-model-small-en-us-0.15")
        assert not os.path.exists(model_dir)

    def test_install_rejects_traversal_archive(self, tmp_path, monkeypatch):
        """Archive members escaping the target directory are rejected."""
        archive = tmp_path / "evil.zip"
        with zipfile.ZipFile(str(archive), "w") as zf:
            zf.writestr("../evil", b"nope")

        assets_dir = str(tmp_path / "assets")
        monkeypatch.setattr(voice_assets, "voice_assets_dir", lambda: assets_dir)
        monkeypatch.setattr(voice_assets, "STT_MODEL_SHA256", _sha256(str(archive)))

        with pytest.raises(voice_assets.VoiceAssetVerificationError):
            voice_assets.install_voice_assets(archive_path=str(archive))
        assert not os.path.exists(os.path.join(assets_dir, "..", "evil"))

    def test_install_is_noop_when_already_verified(self, tmp_path, monkeypatch):
        """Re-installing a verified install does not re-download or re-extract."""
        archive = tmp_path / "model.zip"
        _make_zip(str(archive), "vosk-model-small-en-us-0.15")
        assets_dir = str(tmp_path / "assets")
        monkeypatch.setattr(voice_assets, "voice_assets_dir", lambda: assets_dir)
        monkeypatch.setattr(voice_assets, "STT_MODEL_SHA256", _sha256(str(archive)))

        first = voice_assets.install_voice_assets(archive_path=str(archive))
        marker = os.path.join(
            assets_dir, "vosk-model-small-en-us-0.15", ".jarvis-verified"
        )
        mtime_before = os.path.getmtime(marker)

        second = voice_assets.install_voice_assets(archive_path=str(archive))
        assert second["stt_installed"] is True
        assert os.path.getmtime(marker) == mtime_before

    def test_asset_status_never_downloads(self, monkeypatch):
        """asset_status() reports state without triggering any download."""
        called = {}

        def _fail_download(*args, **kwargs):
            called["downloaded"] = True
            raise AssertionError("asset_status must never download")

        monkeypatch.setattr(voice_assets, "_download_pinned_archive", _fail_download)
        status = voice_assets.asset_status()
        assert "downloaded" not in called
        assert status["downloads_on_import"] is False
        assert status["downloads_on_startup"] is False
