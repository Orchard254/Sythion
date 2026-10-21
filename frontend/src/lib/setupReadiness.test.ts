import { describe, expect, it } from 'vitest';
import {
  canClaimFullReady,
  canStartChat,
  shouldOpenSetup,
  hasVerifiedTts,
  hasVerifiedVoice,
  type SetupChecks,
} from './setupReadiness';

const completeChecks: SetupChecks = {
  resources_ok: true,
  database_ok: true,
  sidecar_ok: true,
  ollama_api_key_ok: true,
  ollama_health_ok: true,
  selected_model_ok: true,
  inference_verified_ok: true,
  stt_model_installed: true,
  tts_voice_ok: true,
  safe_settings_ok: true,
};

describe('setup readiness decision logic', () => {
  it('supports TTS and voice as verified booleans', () => {
    expect(hasVerifiedTts(true)).toBe(true);
    expect(hasVerifiedVoice(false)).toBe(false);
  });

  it('claims Full Ready only when all backend and explicit checks pass', () => {
    expect(canClaimFullReady({ checks: completeChecks }, true, true)).toBe(true);
  });

  it('rejects missing inference evidence', () => {
    expect(
      canClaimFullReady(
        { checks: { ...completeChecks, inference_verified_ok: false } },
        true,
        true
      )
    ).toBe(false);
  });

  it('rejects missing or unverified speech assets', () => {
    expect(
      canClaimFullReady({ checks: { ...completeChecks, stt_model_installed: false } }, true, true)
    ).toBe(false);
  });

  it('rejects failed TTS playback', () => {
    expect(canClaimFullReady({ checks: completeChecks }, false, true)).toBe(false);
  });

  it('rejects failed microphone transcription', () => {
    expect(canClaimFullReady({ checks: completeChecks }, true, false)).toBe(false);
  });

  it('rejects missing safe settings', () => {
    expect(
      canClaimFullReady({ checks: { ...completeChecks, safe_settings_ok: false } }, true, true)
    ).toBe(false);
  });

  it('returns false when readiness is null', () => {
    expect(canClaimFullReady(null, true, true)).toBe(false);
  });
});



describe('first conversation entry', () => {
  it('allows tested text chat without speech assets or microphone permission', () => {
    expect(canStartChat({ checks: { ...completeChecks, stt_model_installed: false, tts_voice_ok: false } })).toBe(true);
  });
  it.each(['ollama_api_key_ok', 'selected_model_ok', 'inference_verified_ok', 'ollama_health_ok', 'safe_settings_ok', 'database_ok', 'sidecar_ok'] as const)('blocks chat when %s fails, even with saved text-first preference', (key) => {
    const report = { checks: { ...completeChecks, [key]: false } };
    expect(canStartChat(report)).toBe(false);
    expect(shouldOpenSetup(report, true)).toBe(true);
  });
  it('blocks unknown readiness and allows configured text chat after relaunch', () => {
    expect(shouldOpenSetup(null, true)).toBe(true);
    expect(shouldOpenSetup({ checks: completeChecks }, false)).toBe(false);
  });
});
