"""
Acceptance tests: voice privacy, local segment processing, and asset pinning.

Phase E acceptance map row: "Voice assets — STT/TTS assets never download at
startup; explicit installation verifies pinned version, license, and checksum."

These tests drive the real router + auth middleware with an isolated voice
manager and deterministic fake VAD/STT, so the results depend only on the
pipeline, never on microphone or model state. No network is touched.
"""
import base64

import pytest

from backend.tests.acceptance.conftest import ACCEPTANCE_CONTROL_TOKEN
from backend.services.voice_pipeline import (
    VoiceSessionManager,
    set_voice_manager_for_testing,
    _FRAME_SIZE_MS,
    _SILENCE_FRAMES_BEFORE_END,
)
from backend.core import voice_assets

AUTH = {
    "X-Control-Token": ACCEPTANCE_CONTROL_TOKEN,
    "Origin": "tauri://localhost",
}


class FakeVAD:
    def __init__(self, speech_flags):
        self._flags = list(speech_flags)
        self._idx = 0

    def is_speech(self, frame, sample_rate):
        if not self._flags:
            return False
        flag = self._flags[self._idx % len(self._flags)]
        self._idx += 1
        return flag

    def reset(self):
        self._idx = 0


class FakeSTT:
    def __init__(self, text_by_size=None):
        self._by_size = text_by_size or {}

    def available(self):
        return True

    def transcribe(self, pcm16_mono, sample_rate):
        for min_size, text in sorted(self._by_size.items()):
            if len(pcm16_mono) >= min_size:
                return text
        return "transcribed segment"

    def reset(self):
        pass


@pytest.fixture
def voice_manager():
    """A deterministic manager: speech then silence completes one segment."""
    frame_bytes = int(_FRAME_SIZE_MS / 1000.0 * 16000 * 2)
    flags = [True, True, True] + [False] * _SILENCE_FRAMES_BEFORE_END
    manager = VoiceSessionManager(vad=FakeVAD(flags), stt=FakeSTT())
    set_voice_manager_for_testing(manager)
    yield manager
    set_voice_manager_for_testing(None)


def _frame_b64() -> str:
    frame = b"\x00" * int(_FRAME_SIZE_MS / 1000.0 * 16000 * 2)
    return base64.b64encode(frame).decode("utf-8")


class TestVoiceSegmentIngestion:
    def test_completed_segment_returns_editable_transcript(self, client, voice_manager):
        """Feeding speech frames yields a completed, editable transcript segment."""
        start = client.post("/api/v1/voice/start", headers=AUTH).json()
        session_id = start["session_id"]

        last = None
        for _ in range(10):
            response = client.post(
                f"/api/v1/voice/{session_id}/audio",
                headers=AUTH,
                json={
                    "session_id": session_id,
                    "audio_data": _frame_b64(),
                    "sample_rate": 16000,
                    "channels": 1,
                },
            )
            assert response.status_code == 200
            payload = response.json()
            if payload["segments"]:
                last = payload["segments"][-1]

        assert last is not None, "expected a completed segment"
        assert last["text"] == "transcribed segment"
        assert last["segment_id"]
        assert last["audio_deleted"] is True
        assert last["stop_detected"] in (True, False)

    def test_stop_phrase_detected_locally(self, client, voice_manager):
        """A stop phrase in the transcript is flagged before any submission."""
        manager = voice_manager
        # Swap STT to return the stop phrase for this session's segments.
        manager._stt = FakeSTT(text_by_size={0: "stop"})

        start = client.post("/api/v1/voice/start", headers=AUTH).json()
        session_id = start["session_id"]

        last = None
        for _ in range(10):
            response = client.post(
                f"/api/v1/voice/{session_id}/audio",
                headers=AUTH,
                json={
                    "session_id": session_id,
                    "audio_data": _frame_b64(),
                    "sample_rate": 16000,
                    "channels": 1,
                },
            )
            payload = response.json()
            if payload["segments"]:
                last = payload["segments"][-1]

        assert last is not None
        assert last["stop_detected"] is True
        assert last["text"] == "stop"

    def test_raw_audio_never_returned_to_client(self, client, voice_manager):
        """Raw PCM never crosses the API boundary; only text segments do."""
        start = client.post("/api/v1/voice/start", headers=AUTH).json()
        session_id = start["session_id"]

        for _ in range(10):
            response = client.post(
                f"/api/v1/voice/{session_id}/audio",
                headers=AUTH,
                json={
                    "session_id": session_id,
                    "audio_data": _frame_b64(),
                    "sample_rate": 16000,
                    "channels": 1,
                },
            )
            payload = response.json()
            assert "audio" not in payload
            for segment in payload.get("segments", []):
                assert "audio" not in segment
                assert "raw" not in segment


class TestVoiceAssetsNeverDownload:
    def test_startup_paths_report_no_download(self, client, voice_manager):
        """Status endpoints never trigger a download."""
        response = client.get("/api/v1/voice/assets", headers=AUTH)
        assert response.status_code == 200
        data = response.json()
        assert data["downloads_on_import"] is False
        assert data["downloads_on_startup"] is False
        assert data["stt_model"] == "vosk-model-small-en-us-0.15"
        assert data["stt_license"] == "Apache-2.0"

    def test_manifest_pins_checksum(self):
        """The pinned manifest carries a version, license, and SHA-256."""
        stt = voice_assets.manifest()["stt"]
        assert stt["version"]
        assert stt["license"]
        assert len(stt["sha256"]) == 64
