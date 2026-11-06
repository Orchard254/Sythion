"""Focused tests for capture/VAD loop state consistency."""
import pytest

from backend.services.voice_pipeline import VoiceSessionManager, _FRAME_SIZE_MS


def _frame() -> bytes:
    return b"\x00" * int(_FRAME_SIZE_MS / 1000.0 * 16000 * 2)


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
    def available(self):
        return True

    def transcribe(self, pcm16_mono, sample_rate):
        return "partial tail"

    def reset(self):
        pass


def test_end_session_drains_trailing_partial_frame():
    manager = VoiceSessionManager(vad=FakeVAD([True]), stt=FakeSTT())
    sid = manager.start_session()
    # One complete speech frame plus a trailing partial frame.
    manager.feed_pcm(sid, _frame() + b"\x11" * 100)
    results = manager.end_session(sid)
    assert results, "trailing partial frame must not be dropped on end"
    assert results[-1].text == "partial tail"
    assert manager.get_session(sid) is None


def test_flush_clears_pending_frame_buffer():
    manager = VoiceSessionManager(vad=FakeVAD([True] + [False] * 5), stt=FakeSTT())
    sid = manager.start_session()
    manager.feed_pcm(sid, _frame() + b"\x22" * 50)
    session = manager.get_session(sid)
    assert session is not None
    # Trigger a complete segment.
    for _ in range(5):
        manager.feed_pcm(sid, _frame())
    session = manager.get_session(sid)
    assert session.pending_frame_buffer == b""
    assert session.pending_audio == b""
