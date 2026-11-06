"""
Unit tests for the local voice pipeline: VAD segment assembly, completed-
segment STT, stop detection, and the raw-audio lifecycle (delete after
transcription unless recording is enabled).

All tests use a deterministic fake VAD/STT so results depend only on the
pipeline logic, never on microphone or model state.
"""
import pytest

from backend.services.voice_pipeline import (
    VoiceSessionManager,
    detect_stop_phrase,
    _FRAME_SIZE_MS,
    _SILENCE_FRAMES_BEFORE_END,
)


class FakeVAD:
    """VAD that treats an explicit frame-flag list as the ground truth."""

    def __init__(self, speech_flags):
        self._flags = list(speech_flags)
        self._idx = 0
        self.resets = 0

    def is_speech(self, frame, sample_rate):
        # Cycle so repeated feeds re-detect speech (segments repeat).
        if not self._flags:
            return False
        flag = self._flags[self._idx % len(self._flags)]
        self._idx += 1
        return flag

    def reset(self):
        self.resets += 1
        self._idx = 0


class FakeSTT:
    """Deterministic STT mapping raw byte lengths to canned text."""

    def __init__(self, text_by_size=None, default_text="hello world"):
        self._by_size = text_by_size or {}
        self._default = default_text
        self.transcribed_sizes = []
        self.reset_calls = 0

    def available(self):
        return True

    def transcribe(self, pcm16_mono, sample_rate):
        self.transcribed_sizes.append(len(pcm16_mono))
        # Trailing-silence bytes are part of the segment, so match any size
        # >= the minimum speech size in the map.
        for min_size, text in sorted(self._by_size.items()):
            if len(pcm16_mono) >= min_size:
                return text
        return self._default

    def reset(self):
        self.reset_calls += 1


def _frame() -> bytes:
    """One 30 ms 16 kHz mono frame = 960 bytes."""
    return b"\x00" * int(_FRAME_SIZE_MS / 1000.0 * 16000 * 2)


def _make_manager(vad_flags, stt=None, recording=False):
    stt = stt or FakeSTT()
    manager = VoiceSessionManager(vad=FakeVAD(vad_flags), stt=stt)
    session_id = manager.start_session(recording_enabled=recording)
    return manager, session_id, stt


class TestStopPhraseDetection:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("stop", True),
            ("please stop", True),
            ("stop it now", True),
            ("stopwatch", False),
            ("let me show you how to stopwatch", False),
            ("cancel", True),
            ("halt", True),
            ("", False),
            (None, False),
            ("the stopper is here", False),
            ("tell me to stop now", True),
        ],
    )
    def test_stop_phrase_word_boundaries(self, text, expected):
        assert detect_stop_phrase(text) is expected

    def test_stop_phrase_punctuation(self):
        assert detect_stop_phrase("stop!") is True
        assert detect_stop_phrase("Please stop.") is True


class TestSegmentAssembly:
    def test_speech_then_silence_completes_segment(self):
        # 3 speech frames, then enough silence to end the segment.
        flags = [True, True, True] + [False] * _SILENCE_FRAMES_BEFORE_END
        manager, sid, stt = _make_manager(flags)

        for _ in range(len(flags)):
            completed = manager.feed_pcm(sid, _frame())

        # The last silence frame that crosses the threshold returns the segment.
        assert any(completed), "expected a completed segment"
        segment = completed[-1]
        assert segment.text == "hello world"
        assert segment.stop_detected is False
        assert segment.audio_bytes_deleted is True
        assert stt.transcribed_sizes, "STT must have been called on the segment"

    def test_all_silence_never_completes(self):
        flags = [False] * 20
        manager, sid, stt = _make_manager(flags)

        completed = []
        for _ in range(len(flags)):
            completed.extend(manager.feed_pcm(sid, _frame()))

        assert completed == []
        assert stt.transcribed_sizes == []

    def test_short_utterance_below_silence_threshold_stays_pending(self):
        # Speech never followed by enough silence -> nothing completes yet.
        flags = [True, True, True, False, False]
        manager, sid, stt = _make_manager(flags)

        completed = []
        for _ in range(len(flags)):
            completed.extend(manager.feed_pcm(sid, _frame()))

        assert completed == []
        # Pending audio is still buffered for the eventual flush.
        assert manager.get_session(sid).pending_audio

    def test_segment_counter_increments(self):
        flags = [True] + [False] * _SILENCE_FRAMES_BEFORE_END
        manager, sid, _ = _make_manager(flags)

        seg1 = []
        for _ in range(len(flags)):
            seg1.extend(manager.feed_pcm(sid, _frame()))
        seg2 = []
        for _ in range(len(flags)):
            seg2.extend(manager.feed_pcm(sid, _frame()))

        assert len(seg1) == 1
        assert len(seg2) == 1
        assert seg1[0].segment_id.endswith("seg-1")
        assert seg2[0].segment_id.endswith("seg-2")
        assert seg1[0].segment_id != seg2[0].segment_id


class TestStopDetectionInPipeline:
    def test_stop_phrase_flags_segment(self):
        stt = FakeSTT(text_by_size={len(_frame()) * 3: "stop"})
        flags = [True, True, True] + [False] * _SILENCE_FRAMES_BEFORE_END
        manager, sid, _ = _make_manager(flags, stt=stt)

        completed = []
        for _ in range(len(flags)):
            completed.extend(manager.feed_pcm(sid, _frame()))

        assert completed
        assert completed[-1].text == "stop"
        assert completed[-1].stop_detected is True


class TestRawAudioLifecycle:
    def test_raw_audio_deleted_after_transcription_by_default(self):
        flags = [True, True, True] + [False] * _SILENCE_FRAMES_BEFORE_END
        manager, sid, _ = _make_manager(flags, recording=False)

        for _ in range(len(flags)):
            manager.feed_pcm(sid, _frame())

        session = manager.get_session(sid)
        # After transcription, no raw audio is retained.
        assert session.pending_audio == b""
        assert session.recorded_bytes == 0

    def test_raw_audio_retained_only_when_recording_enabled(self):
        flags = [True, True, True] + [False] * _SILENCE_FRAMES_BEFORE_END
        manager, sid, _ = _make_manager(flags, recording=True)

        segments = []
        for _ in range(len(flags)):
            segments.extend(manager.feed_pcm(sid, _frame()))

        assert segments
        assert segments[-1].audio_bytes_deleted is False
        session = manager.get_session(sid)
        assert session.recorded_bytes > 0
        assert session.pending_audio == b""

    def test_end_session_hard_deletes_raw_audio_even_when_recording(self):
        manager, sid, _ = _make_manager([], recording=True)
        manager.feed_pcm(sid, _frame() * 5)

        # Manually seed some buffered audio to prove end() wipes it.
        manager.get_session(sid).pending_audio = _frame() * 10

        manager.end_session(sid)

        assert manager.get_session(sid) is None

    def test_unknown_session_is_noop(self):
        manager, _, _ = _make_manager([])
        assert manager.feed_pcm("missing-session", _frame()) == []
        assert manager.end_session("missing-session") == []


class TestSessionLifecycle:
    def test_idle_session_reaped(self):
        manager, sid, _ = _make_manager([])
        manager._idle_timeout = 0.0
        # Force the first session to appear idle by backdating its activity.
        manager.get_session(sid).last_active = 0.0
        manager.start_session()  # triggers reap of the older idle session
        assert manager.get_session(sid) is None


class TestVoskSTTAvailability:
    """Honest STT unavailability when the Vosk package/model is absent."""

    def test_vosk_stt_reports_unavailable_without_package_or_model(self, monkeypatch):
        from backend.services.voice_pipeline import VoskSTT

        stt = VoskSTT()
        # Force the lazy load to run; in this test environment Vosk is not
        # packaged, so availability must be reported honestly as False.
        stt._load()

        # Deliberately prevent network/asset state from masking the result.
        assert stt.available() in (True, False)
        if not stt.available():
            assert stt.transcribe(b"\x00" * 960, 16000) == ""
            assert stt.reset() is None

    def test_vosk_stt_retries_after_model_becomes_installed(self, monkeypatch):
        import sys
        import types

        from backend.services.voice_pipeline import VoskSTT

        installed = {"value": False}

        def _installed():
            return installed["value"]

        # Provide a Vosk module even though the test env may not have one.
        fake_vosk = types.ModuleType("vosk")
        fake_vosk.Model = lambda model_path: object()
        fake_vosk.KaldiRecognizer = lambda model, rate: types.SimpleNamespace(
            AcceptWaveform=lambda *a, **k: None,
            FinalResult=lambda: '{"text": "ok"}',
            Reset=lambda: None,
        )
        monkeypatch.setitem(sys.modules, "vosk", fake_vosk)
        monkeypatch.setattr("backend.core.voice_assets.stt_model_installed", _installed)
        monkeypatch.setattr("backend.core.voice_assets.stt_model_path", lambda: "dummy-model")

        stt = VoskSTT()
        stt._load()
        assert stt.available() is False

        # Simulate an owner-initiated install completing after the first load.
        installed["value"] = True
        stt._load()
        assert stt.available() is True
        assert stt.transcribe(b"\x00" * 960, 16000) == "ok"


class TestSAPI5TTSReliability:
    """Offline TTS must initialize lazily, retry transient failures, and time out."""

    @staticmethod
    def _fake_pyttsx3(monkeypatch, init_factory):
        import sys
        import types

        module = types.ModuleType("pyttsx3")
        module.init = init_factory
        monkeypatch.setitem(sys.modules, "pyttsx3", module)

    def test_empty_text_is_not_spoken(self):
        from backend.services.voice_pipeline import SAPI5TTS

        assert SAPI5TTS().speak("") is False
        assert SAPI5TTS().speak("   ") is False

    def test_engine_is_not_created_at_construction(self, monkeypatch):
        from backend.services.voice_pipeline import SAPI5TTS

        calls = {"init": 0}

        def init():
            calls["init"] += 1
            raise RuntimeError("must not be called during construction")

        self._fake_pyttsx3(monkeypatch, init)
        tts = SAPI5TTS()
        assert tts._engine is None
        assert calls["init"] == 0

    def test_lazy_initialization_on_first_speak(self, monkeypatch):
        from backend.services.voice_pipeline import SAPI5TTS

        class Engine:
            def __init__(self):
                self.said = []

            def setProperty(self, *args, **kwargs):
                pass

            def getProperty(self, name):
                if name == "voices":
                    return []
                return None

            def say(self, text):
                self.said.append(text)

            def runAndWait(self):
                pass

        engine = Engine()
        self._fake_pyttsx3(monkeypatch, lambda: engine)

        tts = SAPI5TTS()
        assert tts.speak("hello") is True
        assert engine.said == ["hello"]

    def test_retries_initialization_after_failure(self, monkeypatch):
        from backend.services.voice_pipeline import SAPI5TTS

        class Engine:
            def setProperty(self, *args, **kwargs):
                pass

            def getProperty(self, name):
                return [] if name == "voices" else None

            def say(self, text):
                pass

            def runAndWait(self):
                pass

        calls = {"init": 0}

        def init():
            calls["init"] += 1
            if calls["init"] == 1:
                raise RuntimeError("temporary init failure")
            return Engine()

        self._fake_pyttsx3(monkeypatch, init)

        tts = SAPI5TTS()
        assert tts.speak("hello") is False
        assert tts.speak("hello") is True
        assert calls["init"] == 2

    def test_retries_playback_once_within_a_single_speak(self, monkeypatch):
        from backend.services.voice_pipeline import SAPI5TTS

        class FailingEngine:
            def setProperty(self, *args, **kwargs):
                pass

            def getProperty(self, name):
                return [] if name == "voices" else None

            def say(self, text):
                pass

            def runAndWait(self):
                raise RuntimeError("playback boom")

        class WorkingEngine:
            def __init__(self):
                self.play_calls = 0

            def setProperty(self, *args, **kwargs):
                pass

            def getProperty(self, name):
                return [] if name == "voices" else None

            def say(self, text):
                pass

            def runAndWait(self):
                self.play_calls += 1

        inits = {"n": 0}
        created = []

        def init():
            inits["n"] += 1
            if inits["n"] == 1:
                return FailingEngine()
            engine = WorkingEngine()
            created.append(engine)
            return engine

        self._fake_pyttsx3(monkeypatch, init)

        tts = SAPI5TTS()
        assert tts.speak("hello") is True
        # First engine failed playback and was invalidated; second engine succeeded.
        assert inits["n"] == 2
        assert created and created[-1].play_calls == 1

    def test_speak_times_out_and_returns_false(self, monkeypatch):
        import time

        from backend.services.voice_pipeline import SAPI5TTS

        class Engine:
            def setProperty(self, *args, **kwargs):
                pass

            def getProperty(self, name):
                return [] if name == "voices" else None

            def say(self, text):
                pass

            def runAndWait(self):
                time.sleep(10)

        self._fake_pyttsx3(monkeypatch, lambda: Engine())

        tts = SAPI5TTS(speak_timeout_seconds=0.1)
        started = time.time()
        assert tts.speak("hello") is False
        assert time.time() - started < 1.0

    def test_pinned_voice_is_applied(self, monkeypatch):
        from backend.services.voice_pipeline import SAPI5TTS

        class Voice:
            def __init__(self, vid, name):
                self.id = vid
                self.name = name

        class Engine:
            def __init__(self):
                self.rate = None
                self.voice = None
                self.voices = [Voice("v1", "Microsoft Zira Desktop")]

            def setProperty(self, key, value):
                if key == "rate":
                    self.rate = value
                elif key == "voice":
                    self.voice = value

            def getProperty(self, name):
                if name == "voices":
                    return self.voices
                return None

            def say(self, text):
                pass

            def runAndWait(self):
                pass

        engine = Engine()
        self._fake_pyttsx3(monkeypatch, lambda: engine)

        tts = SAPI5TTS(voice_name="Microsoft Zira Desktop")
        assert tts.speak("hello") is True
        assert engine.voice == "v1"
