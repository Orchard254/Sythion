"""
Test Voice API Endpoints.

The Phase E voice contract: the sidecar never captures audio; the dashboard
sends short 16 kHz mono PCM frames over the authenticated loopback connection.
Local VAD + completed-segment STT produce editable transcript segments. The
response shapes here are the stable API contract.
"""
import pytest
from fastapi.testclient import TestClient
from backend.main import app
from backend.models.database import get_db
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import base64

from backend.services.voice_pipeline import (
    VoiceSessionManager,
    set_voice_manager_for_testing,
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    from backend.models.database import Base

    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    import backend.main as backend_main

    previous_token = backend_main.control_token
    backend_main.control_token = "voice-test-control-token"
    try:
        yield TestClient(
            app,
            headers={
                "Origin": "tauri://localhost",
                "X-Control-Token": "voice-test-control-token",
            },
        )
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()


@pytest.fixture
def voice_manager():
    """Isolated voice manager so sessions never leak across tests."""
    manager = VoiceSessionManager()
    set_voice_manager_for_testing(manager)
    yield manager
    set_voice_manager_for_testing(None)


def _pcm_bytes(count: int = 2) -> bytes:
    """Two 30 ms frames of mono 16-bit silence (960 B per frame @16 kHz)."""
    return b"\x00" * (960 * count)


class TestVoiceEndpoints:
    """Test the stable Phase E voice API contract."""

    def test_start_voice_session(self, client, voice_manager):
        """Starting a session returns an ID and honest STT availability."""
        response = client.post("/api/v1/voice/start")

        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert data["status"] == "listening"
        assert len(data["session_id"]) > 0
        assert "stt_available" in data
        assert "recording_enabled" in data
        assert data["recording_enabled"] is False

    def test_end_voice_session(self, client, voice_manager):
        """Ending a session flushes pending segments and returns them."""
        start_response = client.post("/api/v1/voice/start")
        session_id = start_response.json()["session_id"]

        response = client.post(f"/api/v1/voice/{session_id}/end")

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == session_id
        assert data["status"] == "ended"
        assert "segments" in data

    def test_process_voice_audio(self, client, voice_manager):
        """Silence frames produce no completed segments and valid response shape."""
        start_response = client.post("/api/v1/voice/start")
        session_id = start_response.json()["session_id"]

        audio_data = _pcm_bytes()
        audio_base64 = base64.b64encode(audio_data).decode("utf-8")

        response = client.post(
            f"/api/v1/voice/{session_id}/audio",
            json={
                "session_id": session_id,
                "audio_data": audio_base64,
                "sample_rate": 16000,
                "channels": 1,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["session_id"] == session_id
        assert "segments" in data
        assert "is_speaking" in data
        assert isinstance(data["segments"], list)

    def test_process_voice_audio_invalid_base64(self, client, voice_manager):
        """Malformed base64 audio is rejected cleanly."""
        start_response = client.post("/api/v1/voice/start")
        session_id = start_response.json()["session_id"]

        response = client.post(
            f"/api/v1/voice/{session_id}/audio",
            json={
                "session_id": session_id,
                "audio_data": "invalid_base64!!!",
                "sample_rate": 16000,
                "channels": 1,
            },
        )

        assert response.status_code == 400
        assert "Invalid audio data" in response.json()["detail"]

    def test_process_voice_audio_session_mismatch(self, client, voice_manager):
        """A session ID mismatch in the body is rejected."""
        start_response = client.post("/api/v1/voice/start")
        session_id = start_response.json()["session_id"]

        response = client.post(
            f"/api/v1/voice/{session_id}/audio",
            json={
                "session_id": "some-other-session",
                "audio_data": base64.b64encode(_pcm_bytes()).decode(),
                "sample_rate": 16000,
                "channels": 1,
            },
        )

        assert response.status_code == 400
        assert "Session ID mismatch" in response.json()["detail"]

    def test_speak_text(self, client, voice_manager):
        """TTS accepts concise final-result text."""
        response = client.post(
            "/api/v1/voice/speak",
            json={"text": "Task completed"},
        )

        assert response.status_code == 200
        data = response.json()
        assert "success" in data
        assert "text" in data

    def test_speak_text_empty(self, client, voice_manager):
        """Empty TTS text is rejected."""
        response = client.post("/api/v1/voice/speak", json={"text": ""})
        assert response.status_code == 400
        assert "Text is required" in response.json()["detail"]

    def test_speak_text_oversized(self, client, voice_manager):
        """Overlong TTS text is rejected (concise final results only)."""
        response = client.post(
            "/api/v1/voice/speak", json={"text": "x" * 2000}
        )
        assert response.status_code == 400
        assert "too long" in response.json()["detail"]

    def test_voice_assets_status(self, client, voice_manager):
        """Asset status is reported without downloading anything."""
        response = client.get("/api/v1/voice/assets")

        assert response.status_code == 200
        data = response.json()
        assert data["stt_model"] == "vosk-model-small-en-us-0.15"
        assert data["stt_license"] == "Apache-2.0"
        assert data["downloads_on_import"] is False
        assert data["downloads_on_startup"] is False
        assert "stt_installed" in data
        assert "stt_sha256_verified" in data


class TestVoiceWorkflow:
    """Complete voice session lifecycle through the API."""

    def test_complete_voice_session_workflow(self, client, voice_manager):
        """Start -> frames -> speak -> end follows the full contract."""
        start_response = client.post("/api/v1/voice/start")
        assert start_response.status_code == 200
        session_id = start_response.json()["session_id"]

        audio_data = _pcm_bytes()
        audio_base64 = base64.b64encode(audio_data).decode("utf-8")

        for _ in range(3):
            audio_response = client.post(
                f"/api/v1/voice/{session_id}/audio",
                json={
                    "session_id": session_id,
                    "audio_data": audio_base64,
                    "sample_rate": 16000,
                    "channels": 1,
                },
            )
            assert audio_response.status_code == 200

        speak_response = client.post(
            "/api/v1/voice/speak",
            json={"text": "Task completed"},
        )
        assert speak_response.status_code == 200

        end_response = client.post(f"/api/v1/voice/{session_id}/end")
        assert end_response.status_code == 200
        assert end_response.json()["status"] == "ended"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
