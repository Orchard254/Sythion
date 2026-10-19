import { useCallback, useEffect, useRef, useState } from 'react';
import { voiceService } from '../services/api';
import { useAppStore } from '../store/appStore';

/**
 * Dashboard-owned microphone capture.
 *
 * Privacy rules (Sythion (Synth)_FINAL_HANDOFF_BRIEF.md:176):
 * - Capture runs only while the dashboard is visible AND voice mode is on.
 * - It stops on close, minimize, lock, permission loss, or toggle-off.
 * - The dashboard only sends short 16 kHz mono PCM frames over the
 *   authenticated loopback connection; the sidecar does local VAD/STT.
 *
 * No raw audio is retained by the dashboard beyond the current frame.
 */
const TARGET_SAMPLE_RATE = 16000;
const FRAME_BYTES = 960; // 30 ms at 16 kHz mono 16-bit (matches sidecar VAD)

function float32ToInt16(input: Float32Array): Int16Array {
  const output = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    output[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return output;
}

export function useVoiceCapture() {
  const {
    isVoiceEnabled,
    isListening,
    setListening,
    setRecording,
    setVoiceSpeaking,
    setVoiceSttAvailable,
    appendTranscriptSegment,
    setTranscriptDraft,
  } = useAppStore();

  const [stopDetected, setStopDetected] = useState(false);

  const audioContextRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const frameBufferRef = useRef<Uint8Array>(new Uint8Array(0));
  const stoppedRef = useRef(false);
  const isListeningRef = useRef(false);

  useEffect(() => {
    isListeningRef.current = isListening;
  }, [isListening]);

  const stopCapture = useCallback(async () => {
    stoppedRef.current = true;
    isListeningRef.current = false;

    const processor = processorRef.current;
    const context = audioContextRef.current;
    const stream = streamRef.current;
    const sessionId = sessionIdRef.current;

    processorRef.current = null;
    audioContextRef.current = null;
    streamRef.current = null;
    sessionIdRef.current = null;

    if (processor && context) {
      try {
        processor.disconnect();
        processor.onaudioprocess = null;
      } catch {
        // ignore teardown errors
      }
    }
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
    }
    if (context && context.state !== 'closed') {
      try {
        await context.close();
      } catch {
        // ignore teardown errors
      }
    }
    if (sessionId) {
      try {
        await voiceService.endSession(sessionId);
      } catch {
        // session may already be gone
      }
    }

    setListening(false);
    setRecording(false);
    setVoiceSpeaking(false);
    frameBufferRef.current = new Uint8Array(0);
  }, [setListening, setRecording, setVoiceSpeaking]);

  const flushFrames = useCallback(() => {
    const sessionId = sessionIdRef.current;
    if (!sessionId || !isListeningRef.current) return;

    const buffer = frameBufferRef.current;
    let offset = 0;
    while (buffer.length - offset >= FRAME_BYTES) {
      const frame = buffer.slice(offset, offset + FRAME_BYTES);
      offset += FRAME_BYTES;
      voiceService
        .sendAudioFrame(sessionId, frame)
        .then((result) => {
          if (result.stt_available) {
            setVoiceSttAvailable(true);
          }
          // The sidecar's VAD reports whether the user is speaking right now.
          // This is a real measurement, not an animation timer, so the UI can
          // show a genuine 'speaking' state while the mic is open.
          setVoiceSpeaking(Boolean(result.is_speaking));
          for (const segment of result.segments) {
            if (segment.text) {
              appendTranscriptSegment(segment.segment_id, segment.text);
            }
            if (segment.stop_detected) {
              setStopDetected(true);
            }
          }
        })
        .catch(() => {
          // A single dropped frame must not break the session.
        });
    }
    frameBufferRef.current = buffer.slice(offset);
  }, [appendTranscriptSegment, setVoiceSpeaking, setVoiceSttAvailable]);

  const startCapture = useCallback(async () => {
    // Capture only while visible and voice mode is on.
    if (!isVoiceEnabled || isListeningRef.current) return;
    if (typeof document !== 'undefined' && document.visibilityState !== 'visible') return;
    if (!navigator.mediaDevices?.getUserMedia) return;

    stoppedRef.current = false;
    setStopDetected(false);

    try {
      const startResult = await voiceService.startSession();
      sessionIdRef.current = startResult.session_id;
      if (typeof startResult.stt_available === 'boolean') {
        setVoiceSttAvailable(startResult.stt_available);
      }

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      streamRef.current = stream;

      // Surface permission loss / device revocation by stopping capture.
      stream.getTracks().forEach((track) => {
        track.addEventListener('ended', () => {
          stopCapture();
        });
      });

      const AudioContextCtor =
        window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const context = new AudioContextCtor({ sampleRate: TARGET_SAMPLE_RATE });
      audioContextRef.current = context;

      const source = context.createMediaStreamSource(stream);
      // ScriptProcessorNode is the widely-supported downsampling path.
      const processor = context.createScriptProcessor(2048, 1, 1);
      processorRef.current = processor;

      processor.onaudioprocess = (event) => {
        if (stoppedRef.current || !isListeningRef.current) return;
        const pcm = float32ToInt16(event.inputBuffer.getChannelData(0));
        const bytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength);
        const combined = new Uint8Array(frameBufferRef.current.length + bytes.length);
        combined.set(frameBufferRef.current, 0);
        combined.set(bytes, frameBufferRef.current.length);
        frameBufferRef.current = combined;
        flushFrames();
      };

      source.connect(processor);
      processor.connect(context.destination);

      isListeningRef.current = true;
      setListening(true);
      setRecording(true);
      setVoiceSpeaking(false);
    } catch (error) {
      // Permission denied or no device: keep the dashboard usable, no capture.
      await stopCapture();
      throw error;
    }
  }, [flushFrames, isVoiceEnabled, setListening, setRecording, setVoiceSpeaking, setVoiceSttAvailable, stopCapture]);

  // Stop on minimize/close/lock (document hidden) or window blur.
  useEffect(() => {
    if (!isListening) return;

    const onVisibility = () => {
      if (document.visibilityState === 'hidden') {
        stopCapture();
      }
    };
    const onBlur = () => {
      stopCapture();
    };
    const onLockHint = () => {
      // Some lock paths hide the document without a blur; also catch here.
      stopCapture();
    };

    document.addEventListener('visibilitychange', onVisibility);
    window.addEventListener('blur', onBlur);
    window.addEventListener('resize', onLockHint);
    return () => {
      document.removeEventListener('visibilitychange', onVisibility);
      window.removeEventListener('blur', onBlur);
      window.removeEventListener('resize', onLockHint);
    };
  }, [isListening, stopCapture]);

  // Toggle-off and unmount cleanup.
  useEffect(() => {
    if (!isVoiceEnabled && isListening) {
      stopCapture();
    }
  }, [isVoiceEnabled, isListening, stopCapture]);

  useEffect(() => {
    return () => {
      stopCapture();
    };
  }, [stopCapture]);

  const toggleListening = useCallback(async () => {
    if (isListeningRef.current) {
      await stopCapture();
    } else {
      try {
        await startCapture();
      } catch {
        // Surface via store error state handled by the caller.
      }
    }
  }, [startCapture, stopCapture]);

  const clearStopDetected = useCallback(() => setStopDetected(false), []);
  const resetTranscriptDraft = useCallback(() => setTranscriptDraft(''), [setTranscriptDraft]);

  return {
    isListening,
    stopDetected,
    startCapture,
    stopCapture,
    toggleListening,
    clearStopDetected,
    resetTranscriptDraft,
  };
}
