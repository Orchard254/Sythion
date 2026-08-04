"""
Local voice pipeline: VAD, completed-segment STT, stop detection, TTS, and
strict raw-audio lifecycle.

Privacy contract (Sythion (Synth)_FINAL_HANDOFF_BRIEF.md:174-186):
- The dashboard owns microphone permission and capture; the sidecar only
  receives short PCM frames over the authenticated loopback connection.
- VAD and STT run locally in the sidecar. Segments are transcribed only when
  they complete (no word-by-word interim promises).
- A local text check on each completed segment recognizes the stop phrase
  before ordinary task submission.
- Raw audio is kept in memory only while a segment is being assembled and is
  discarded after transcription unless the owner enabled the separate
  recording setting.
- TTS is local/offline (Windows SAPI5 via pyttsx3) and is used only for the
  concise evidence-based final result.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Protocol, Sequence

logger = logging.getLogger(__name__)

# WebRTC VAD works on 10/20/30 ms frames. 30 ms at 16 kHz mono 16-bit = 960 B.
_FRAME_SIZE_MS = 30
# Completed segment: N consecutive non-speech frames after speech.
_SILENCE_FRAMES_BEFORE_END = 5
_MAX_SEGMENT_FRAMES = 400  # ~12 s ceiling so a single segment cannot grow unbounded

_STOP_PHRASES = ("stop", "halt", "cancel", "stop it", "please stop")


@dataclass
class SegmentResult:
    """A completed speech segment, ready for the editable transcript."""

    text: str
    stop_detected: bool
    segment_id: str
    audio_bytes_deleted: bool = True
    stt_available: bool = True


@dataclass
class VoiceSessionState:
    """Per-session runtime state for one capture lifecycle."""

    session_id: str
    sample_rate: int
    channels: int
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    segments: List[SegmentResult] = field(default_factory=list)
    # Raw PCM bytes for the in-progress utterance (discarded on segment end).
    pending_audio: bytes = b""
    pending_frame_buffer: bytes = b""
    speaking: bool = False
    silence_frames: int = 0
    segment_counter: int = 0
    recording_enabled: bool = False
    raw_bytes_retained: int = 0
    # Bytes retained solely because recording is enabled (for reporting).
    recorded_bytes: int = 0


class VADEngine(Protocol):
    def reset(self) -> None: ...
    def is_speech(self, pcm16_mono_frame: bytes, sample_rate: int) -> bool: ...


class STTEngine(Protocol):
    def available(self) -> bool: ...
    def transcribe(self, pcm16_mono: bytes, sample_rate: int) -> str: ...
    def reset(self) -> None: ...


class WebRTCVAD:
    """webrtcvad-based VAD that handles arbitrary chunk sizes by framing."""

    def __init__(self, frame_size_ms: int = _FRAME_SIZE_MS) -> None:
        self._frame_size_ms = frame_size_ms
        self._vad = None
        try:
            import webrtcvad  # type: ignore

            self._vad = webrtcvad.Vad(2)
        except ImportError:
            logger.warning("webrtcvad not available; VAD disabled (no segments will complete)")

    def _frame_bytes(self, sample_rate: int) -> int:
        return int(self._frame_size_ms / 1000.0 * sample_rate * 2)

    def is_speech(self, pcm16_mono_frame: bytes, sample_rate: int) -> bool:
        if self._vad is None:
            return False
        try:
            return bool(self._vad.is_speech(pcm16_mono_frame, sample_rate))
        except Exception as e:  # pragma: no cover - malformed frame edge case
            logger.debug("VAD frame error: %s", e)
            return False

    def reset(self) -> None:
        pass


class VoskSTT:
    """Local Vosk STT, lazily loaded. Honest ``available()`` when absent.

    Matches the Phase D OCR pattern: when the pinned model or the vosk package
    is not installed, we report unavailable rather than fabricating output.
    """

    def __init__(self) -> None:
        self._model = None
        self._recognizer = None
        self._ready = False
        self._tried = False

    def _load(self) -> None:
        if self._tried:
            return
        self._tried = True
        try:
            from backend.core.voice_assets import stt_model_path, stt_model_installed

            if not stt_model_installed():
                logger.info("Pinned Vosk model not installed; STT unavailable")
                return
            import vosk  # type: ignore

            self._model = vosk.Model(model_path=stt_model_path())
            self._recognizer = vosk.KaldiRecognizer(self._model, 16000)
            self._ready = True
            logger.info("Vosk STT loaded")
        except ImportError:
            logger.info("vosk package not installed; STT unavailable")
        except Exception as e:  # pragma: no cover - model/load dependent
            logger.warning("Failed to load Vosk STT: %s", e)

    def available(self) -> bool:
        self._load()
        return self._ready

    def transcribe(self, pcm16_mono: bytes, sample_rate: int) -> str:
        if not self.available():
            return ""
        try:
            self._recognizer.AcceptWaveform(pcm16_mono, sample_rate)
            result = self._recognizer.FinalResult()
            import json

            text = json.loads(result).get("text", "")
            return (text or "").strip()
        except Exception as e:  # pragma: no cover - engine dependent
            logger.error("STT transcription failed: %s", e)
            return ""

    def reset(self) -> None:
        if self._recognizer is not None:
            try:
                self._recognizer.Reset()
            except Exception:  # pragma: no cover
                pass


class SAPI5TTS:
    """Local/offline Windows SAPI5 TTS via pyttsx3."""

    def __init__(self) -> None:
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            try:
                import pyttsx3

                self._engine = pyttsx3.init()
                self._engine.setProperty("rate", 175)
            except Exception as e:  # pragma: no cover - OS dependent
                logger.warning("SAPI5 TTS unavailable: %s", e)
                self._engine = None
        return self._engine

    def speak(self, text: str) -> bool:
        """Speak ``text``. Only call with concise evidence-based results."""
        if not text or not text.strip():
            return False
        engine = self._get_engine()
        if engine is None:
            return False
        try:
            engine.say(text)
            engine.runAndWait()
            return True
        except Exception as e:  # pragma: no cover - OS dependent
            logger.error("TTS playback failed: %s", e)
            return False


def detect_stop_phrase(text: str) -> bool:
    """Return True when the completed segment contains a local stop phrase.

    Matches word boundaries so "stopwatch" or "stopper" do not trigger.
    """
    if not text:
        return False
    normalized = " ".join(text.lower().split())
    for phrase in _STOP_PHRASES:
        if re.search(rf"(^|\s){re.escape(phrase)}(\s|$|[.!?,;:])", normalized):
            return True
    return False


class VoiceSessionManager:
    """Owns all active voice sessions. Thread-affine to the sidecar event loop."""

    def __init__(
        self,
        vad: Optional[VADEngine] = None,
        stt: Optional[STTEngine] = None,
        tts: Optional[object] = None,
        idle_timeout_seconds: float = 300.0,
    ) -> None:
        self._vad = vad or WebRTCVAD()
        self._stt = stt or VoskSTT()
        self._tts = tts or SAPI5TTS()
        self._idle_timeout = idle_timeout_seconds
        self._sessions: Dict[str, VoiceSessionState] = {}

    # -- Session lifecycle --------------------------------------------------

    def start_session(
        self, sample_rate: int = 16000, channels: int = 1, recording_enabled: bool = False
    ) -> str:
        self._reap_idle()
        session_id = uuid.uuid4().hex
        self._sessions[session_id] = VoiceSessionState(
            session_id=session_id,
            sample_rate=sample_rate,
            channels=channels,
            recording_enabled=recording_enabled,
        )
        self._vad.reset()
        self._stt.reset()
        logger.info("Voice session started: %s", session_id)
        return session_id

    def get_session(self, session_id: str) -> Optional[VoiceSessionState]:
        session = self._sessions.get(session_id)
        if session:
            session.last_active = time.time()
        return session

    def end_session(self, session_id: str) -> List[SegmentResult]:
        """Flush any pending segment, then delete all raw audio and drop the session."""
        session = self._sessions.pop(session_id, None)
        if session is None:
            return []
        results = []
        pending = self._flush_pending(session)
        if pending:
            results.append(pending)
        # Hard-delete raw audio regardless of recording state at session end.
        session.pending_audio = b""
        session.pending_frame_buffer = b""
        session.raw_bytes_retained = 0
        session.recorded_bytes = 0
        self._vad.reset()
        self._stt.reset()
        logger.info("Voice session ended: %s", session_id)
        return results

    # -- Frame ingestion ----------------------------------------------------

    def feed_pcm(self, session_id: str, pcm16_mono: bytes) -> List[SegmentResult]:
        """Feed a short 16 kHz mono PCM frame; return any completed segments."""
        session = self.get_session(session_id)
        if session is None:
            return []
        session.last_active = time.time()

        completed: List[SegmentResult] = []
        frame_bytes = int(_FRAME_SIZE_MS / 1000.0 * session.sample_rate * 2)
        session.pending_frame_buffer += pcm16_mono

        while len(session.pending_frame_buffer) >= frame_bytes:
            frame = session.pending_frame_buffer[:frame_bytes]
            session.pending_frame_buffer = session.pending_frame_buffer[frame_bytes:]

            # Retain raw audio only while assembling a segment (or recording).
            session.pending_audio += frame

            speech = self._vad.is_speech(frame, session.sample_rate)
            if speech:
                session.speaking = True
                session.silence_frames = 0
            elif session.speaking:
                session.silence_frames += 1
                if session.silence_frames >= _SILENCE_FRAMES_BEFORE_END:
                    segment = self._flush_pending(session)
                    if segment:
                        completed.append(segment)

            if len(session.pending_audio) >= frame_bytes * _MAX_SEGMENT_FRAMES:
                segment = self._flush_pending(session)
                if segment:
                    completed.append(segment)

        return completed

    # -- Segment assembly ---------------------------------------------------

    def _flush_pending(self, session: VoiceSessionState) -> Optional[SegmentResult]:
        """Transcribe the accumulated utterance, then delete its raw audio."""
        if not session.pending_audio or not session.speaking:
            session.pending_audio = b""
            session.speaking = False
            session.silence_frames = 0
            return None

        audio = session.pending_audio
        session.pending_audio = b""
        session.speaking = False
        session.silence_frames = 0
        session.segment_counter += 1

        stt_available = self._stt.available()
        text = self._stt.transcribe(audio, session.sample_rate) if stt_available else ""

        # Privacy: raw audio is deleted after transcription unless recording.
        if session.recording_enabled:
            session.recorded_bytes += len(audio)
            audio_deleted = False
        else:
            audio = b""
            audio_deleted = True

        result = SegmentResult(
            text=text,
            stop_detected=detect_stop_phrase(text),
            segment_id=f"{session.session_id}-seg-{session.segment_counter}",
            audio_bytes_deleted=audio_deleted,
            stt_available=stt_available,
        )
        session.segments.append(result)
        logger.debug(
            "Segment completed id=%s text=%r stop=%s audio_deleted=%s",
            result.segment_id,
            text,
            result.stop_detected,
            audio_deleted,
        )
        return result

    # -- TTS -----------------------------------------------------------------

    def speak(self, text: str) -> bool:
        return self._tts.speak(text)

    # -- Maintenance ----------------------------------------------------------

    def stt_available(self) -> bool:
        """Report whether local STT is ready (honest unavailability)."""
        return self._stt.available()

    def _reap_idle(self) -> None:
        now = time.time()
        stale = [
            sid
            for sid, s in self._sessions.items()
            if now - s.last_active > self._idle_timeout
        ]
        for sid in stale:
            logger.info("Reaping idle voice session %s", sid)
            self.end_session(sid)


# Module-level manager shared by the API routes.
_voice_manager: Optional[VoiceSessionManager] = None


def get_voice_manager() -> VoiceSessionManager:
    global _voice_manager
    if _voice_manager is None:
        _voice_manager = VoiceSessionManager()
    return _voice_manager


def set_voice_manager_for_testing(manager: Optional[VoiceSessionManager]) -> None:
    global _voice_manager
    _voice_manager = manager
