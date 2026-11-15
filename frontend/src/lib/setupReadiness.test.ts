import { describe, expect, it } from 'vitest';
import {
  canClaimFullReady,
  canStartChat,
  canUseModel,
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

describe('provider-neutral setup readiness', () => {
  const base = {
    resources_ok: true, database_ok: true, sidecar_ok: true,
    selected_model_ok: true, inference_verified_ok: true, safe_settings_ok: true,
    ollama_api_key_ok: false, ollama_health_ok: false,
  };
  it('accepts the REAL backend cloud readiness response, with authority at top level', () => {
    // /setup/readiness returns active_provider_ready_ok alongside checks.
    // This exact response shape previously left the Continue button disabled
    // despite the backend reporting a successfully activated model.
    const report = {
      active_provider_id: 'openrouter',
      active_provider_ready_ok: true,
      checks: { ...base },
    };
    expect(canUseModel(report)).toBe(true);
    expect(canStartChat(report)).toBe(true);
    expect(canStartChat({
      active_provider_id: 'groq',
      active_provider_ready_ok: true,
      checks: { ...base },
    })).toBe(true);
  });
  it('permits model confirmation before guarded settings are applied, not chat access', () => {
    const report = {
      active_provider_id: 'openrouter',
      active_provider_ready_ok: true,
      checks: { ...base, safe_settings_ok: false },
    };
    expect(canUseModel(report)).toBe(true);
    expect(canStartChat(report)).toBe(false);
    expect(shouldOpenSetup(report, true)).toBe(true);
  });
  it('rejects a staged candidate, unknown active provider or unavailable key', () => {
    expect(canStartChat({
      active_provider_id: 'openrouter',
      active_provider_ready_ok: false,
      checks: { ...base },
    })).toBe(false);
    // A forged/legacy nested readiness hint is NOT authoritative.
    expect(canUseModel({
      active_provider_id: 'openrouter',
      checks: { ...base, active_provider_ready_ok: true },
    })).toBe(false);
    expect(canStartChat({
      active_provider_id: 'unavailable',
      checks: { ...base, ollama_api_key_ok: true, ollama_health_ok: true },
    })).toBe(false);
  });
});
