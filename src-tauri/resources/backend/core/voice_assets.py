"""
Voice asset pinning and explicit owner-initiated installation.

Policy (Sythion (Synth)_FINAL_HANDOFF_BRIEF.md:182): choose one STT model and one TTS
voice, pin their version/license/checksum, and either bundle them in the signed
installer or offer an explicit owner-initiated verified download. Voice assets
are NEVER downloaded at import or application startup; every download is
explicit, checksum-verified, and recorded under the user-only LocalAppData
directory.

The STT asset is the pinned Vosk small English model. The TTS asset is the
Windows SAPI5 engine (pyttsx3), which ships with the OS and requires no
download; we pin the preferred voice name and verify availability at install.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
import threading
import zipfile
from typing import Dict, List, Optional

from .storage import data_dir

logger = logging.getLogger(__name__)

# Serialize the entire STT install transaction across worker threads. This is
# process-local only; a cross-process install collision is not prevented by it.
_STT_INSTALL_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Pinned manifest
# ---------------------------------------------------------------------------

STT_MODEL_NAME = "vosk-model-small-en-us-0.15"
STT_MODEL_LICENSE = "Apache-2.0"
STT_MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
# Computed over the 41,205,931-byte distribution zip on 2026-08-03.
STT_MODEL_SHA256 = "30f26242c4eb449f948e42cb302dd7a686cb29a3423a8367f99ff41780942498"
STT_MODEL_VERSION = "0.15"

TTS_PROVIDER = "sapi5"
# Preferred SAPI5 voices, in order; the first available one is pinned.
TTS_PREFERRED_VOICES = ["Microsoft Zira Desktop", "Microsoft David Desktop"]


def voice_assets_dir() -> str:
    """Return (and create) the voice-asset directory under LocalAppData."""
    path = os.path.join(data_dir(), "voice_assets")
    os.makedirs(path, exist_ok=True)
    return path


def stt_model_path() -> str:
    """Return the expected extracted STT model directory path."""
    return os.path.join(voice_assets_dir(), STT_MODEL_NAME)


def manifest() -> Dict[str, object]:
    """Return the immutable pinned asset manifest (non-secret metadata)."""
    return {
        "stt": {
            "name": STT_MODEL_NAME,
            "version": STT_MODEL_VERSION,
            "license": STT_MODEL_LICENSE,
            "url": STT_MODEL_URL,
            "sha256": STT_MODEL_SHA256,
            "downloads_on_import": False,
            "downloads_on_startup": False,
        },
        "tts": {
            "provider": TTS_PROVIDER,
            "preferred_voices": list(TTS_PREFERRED_VOICES),
            "downloads_on_import": False,
            "downloads_on_startup": False,
        },
    }


# ---------------------------------------------------------------------------
# Status and verification
# ---------------------------------------------------------------------------


def _expected_checksum() -> str:
    return STT_MODEL_SHA256


def _sha256_of_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def verify_checksum(archive_path: str) -> bool:
    """Return True when ``archive_path`` matches the pinned SHA-256."""
    return _sha256_of_file(archive_path).lower() == _expected_checksum()


def _vosk_model_dir_complete(model_dir: str) -> bool:
    """Return True when ``model_dir`` has a complete Vosk V2 model layout.

    This is the shared completeness predicate used by both ``stt_model_installed``
    and ``_extract_stt_model``. It deliberately mirrors the native Vosk V2
    loader (``model.cc``): ``am/final.mdl`` plus ``conf/model.conf`` and either
    ``conf/mfcc.conf`` or ``conf/fbank.conf`` must be present; the decoding
    graph may be either one static ``graph/HCLG.fst`` or the complete split
    ``graph/HCLr.fst`` + ``graph/Gr.fst`` + ``graph/disambig_tid.int``. An
    incomplete split graph or a missing acoustic/config file is rejected.
    ``graph/words.txt`` is not universally required because ``HCLG.fst`` or
    ``Gr.fst`` can supply the output symbol table (the pinned small model has
    no ``words.txt``); when present it is only an optional fallback.
    """
    if not os.path.isdir(model_dir):
        return False

    am_dir = os.path.join(model_dir, "am")
    conf_dir = os.path.join(model_dir, "conf")
    graph_dir = os.path.join(model_dir, "graph")

    if not os.path.isfile(os.path.join(am_dir, "final.mdl")):
        return False
    if not os.path.isfile(os.path.join(conf_dir, "model.conf")):
        return False
    if not (
        os.path.isfile(os.path.join(conf_dir, "mfcc.conf"))
        or os.path.isfile(os.path.join(conf_dir, "fbank.conf"))
    ):
        return False

    # Static single-graph family.
    if os.path.isfile(os.path.join(graph_dir, "HCLG.fst")):
        return True

    # Dynamic/split graph family (pinned small model).
    if (
        os.path.isfile(os.path.join(graph_dir, "HCLr.fst"))
        and os.path.isfile(os.path.join(graph_dir, "Gr.fst"))
        and os.path.isfile(os.path.join(graph_dir, "disambig_tid.int"))
    ):
        return True

    return False


def stt_model_installed() -> bool:
    """Report whether the pinned STT model is present (never downloads)."""
    model_dir = stt_model_path()
    return os.path.isdir(model_dir) and _vosk_model_dir_complete(model_dir)


def asset_status() -> Dict[str, object]:
    """Return installation status for the pinned assets (never downloads)."""
    stt_ready = stt_model_installed()
    return {
        "stt_installed": stt_ready,
        "stt_model": STT_MODEL_NAME,
        "stt_version": STT_MODEL_VERSION,
        "stt_license": STT_MODEL_LICENSE,
        "stt_sha256_verified": _stt_checksum_verified(),
        "tts_provider": TTS_PROVIDER,
        "tts_voice": _detect_tts_voice(),
        "assets_dir": voice_assets_dir(),
        "downloads_on_import": False,
        "downloads_on_startup": False,
    }


def _stt_checksum_verified() -> bool:
    """Report whether the installed model passed checksum verification.

    We verify the extraction marker we write after a verified install; a
    manually copied model without the marker reports unverified rather than
    silently trusting it.
    """
    marker = os.path.join(stt_model_path(), ".sythion-verified")
    return os.path.isfile(marker)


def _detect_tts_voice() -> str:
    """Return the pinned SAPI5 voice name, or an empty string if unavailable."""
    try:
        import pyttsx3
    except ImportError:
        return ""
    try:
        engine = pyttsx3.init()
        voices = engine.getProperty("voices")
        engine.stop()
    except Exception as e:  # pragma: no cover - OS/engine dependent
        logger.warning("Could not enumerate SAPI5 voices: %s", e)
        return ""
    for preferred in TTS_PREFERRED_VOICES:
        for voice in voices:
            name = getattr(voice, "name", "") or ""
            if preferred.lower() in name.lower():
                return name
    if voices:
        return getattr(voices[0], "name", "") or ""
    return ""


# ---------------------------------------------------------------------------
# Explicit owner-initiated installation
# ---------------------------------------------------------------------------


def install_stt_assets(
    archive_path: Optional[str] = None, force: bool = False
) -> None:
    """Install only the pinned STT model; no TTS work, worker-safe.

    Owns the STT filesystem/download/checksum/extract/publish transaction. Does
    not touch pyttsx3 or TTS status. Serialized by _STT_INSTALL_LOCK; rechecks
    installed state after acquiring the lock. Intended to run on a worker thread.
    """
    with _STT_INSTALL_LOCK:
        # Recheck inside the lock so a concurrent or prior install wins.
        if stt_model_installed() and _stt_checksum_verified() and not force:
            return

        target_archive = archive_path
        cleanup_temp = False
        if target_archive is None:
            target_archive, cleanup_temp = _download_pinned_archive()

        try:
            if not verify_checksum(target_archive):
                raise VoiceAssetVerificationError(
                    "Pinned STT model checksum mismatch; refusing to install"
                )
            _extract_stt_model(target_archive)
        finally:
            if cleanup_temp and target_archive:
                try:
                    os.remove(target_archive)
                except OSError:  # pragma: no cover - temp cleanup
                    pass


def install_voice_assets(
    archive_path: Optional[str] = None, force: bool = False
) -> Dict[str, object]:
    """Synchronous compatibility wrapper.

    Runs the STT-only helper, then computes full asset status (including TTS
    detection) on the calling thread. Existing sync callers retain the status
    contract. Async callers invoke install_stt_assets on a worker and
    asset_status afterward on the owning thread.
    """
    install_stt_assets(archive_path=archive_path, force=force)
    return asset_status()


# Worker-helper naming aliases.
install_stt_model = install_stt_assets
install_stt = install_stt_assets
_install_stt_assets = install_stt_assets
install_stt_voice_assets = install_stt_assets


class VoiceAssetVerificationError(RuntimeError):
    """Raised when a pinned voice asset fails verification."""


def _download_pinned_archive() -> "tuple[str, bool]":
    """Download the pinned STT archive to a temporary file.

    Returns ``(path, True)``; the caller removes the temp file. Uses
    urllib only, so the module itself stays import-safe (no network at import).
    """
    import urllib.request

    fd, tmp_path = tempfile.mkstemp(suffix=".zip", prefix="sythion-stt-")
    os.close(fd)
    logger.info("Downloading pinned STT model from %s", STT_MODEL_URL)
    try:
        urllib.request.urlretrieve(STT_MODEL_URL, tmp_path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:  # pragma: no cover
            pass
        raise
    return tmp_path, True


def _extract_stt_model(archive_path: str) -> None:
    """Extract the verified archive into the voice-assets directory.

    The zip contains a top-level directory named ``STT_MODEL_NAME``. We
    extract to a staging directory and atomically move it into place so a
    failed/interrupted extraction never leaves a half-installed model.
    """
    target_dir = voice_assets_dir()
    staging = os.path.join(
        target_dir, f".staging-{STT_MODEL_NAME}-{os.getpid()}"
    )
    if os.path.exists(staging):
        shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging, exist_ok=True)

    try:
        with zipfile.ZipFile(archive_path, "r") as zf:
            # Reject absolute paths / traversal before extracting.
            for member in zf.namelist():
                normalized = member.replace("\\", "/")
                if normalized.startswith("/") or ".." in normalized.split("/"):
                    raise VoiceAssetVerificationError(
                        f"Unsafe archive member: {member!r}"
                    )
            zf.extractall(staging)

        extracted = os.path.join(staging, STT_MODEL_NAME)
        if not os.path.isdir(extracted):
            # Some distributions nest a single subdirectory; find it safely.
            candidates = [
                d
                for d in os.listdir(staging)
                if os.path.isdir(os.path.join(staging, d))
                and not d.startswith(".")
            ]
            if len(candidates) != 1:
                raise VoiceAssetVerificationError(
                    "Archive does not contain the expected single model directory"
                )
            extracted = os.path.join(staging, candidates[0])

        if not _vosk_model_dir_complete(extracted):
            raise VoiceAssetVerificationError(
                "Archive does not contain a usable Vosk model layout"
            )

        final_dir = os.path.join(target_dir, STT_MODEL_NAME)
        if os.path.exists(final_dir):
            shutil.rmtree(final_dir, ignore_errors=True)
        os.replace(extracted, final_dir)
        # Write the verification marker only after a successful move.
        with open(os.path.join(final_dir, ".sythion-verified"), "w") as f:
            f.write(_expected_checksum() + "\n")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
