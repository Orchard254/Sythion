export interface SetupChecks {
  resources_ok?: boolean;
  database_ok?: boolean;
  sidecar_ok?: boolean;
  ollama_api_key_ok?: boolean;
  ollama_health_ok?: boolean;
  selected_model_ok?: boolean;
  inference_verified_ok?: boolean;
  stt_model_installed?: boolean;
  tts_voice_ok?: boolean;
  safe_settings_ok?: boolean;
  active_provider_ready_ok?: boolean;
}

export interface SetupReadinessLike {
  full_ready?: boolean;
  active_provider_id?: string;
  /** Backend's authenticated active-provider readiness result, returned at top level. */
  active_provider_ready_ok?: boolean;
  checks?: SetupChecks;
}

export function hasVerifiedTts(ttsVerified: boolean): boolean {
  return ttsVerified === true;
}

export function hasVerifiedVoice(voiceVerified: boolean): boolean {
  return voiceVerified === true;
}

export function canClaimFullReady(
  readiness: SetupReadinessLike | null,
  ttsVerified: boolean,
  voiceVerified: boolean
): boolean {
  if (!readiness?.checks) return false;
  const c = readiness.checks;
  return (
    c.resources_ok === true &&
    c.database_ok === true &&
    c.sidecar_ok === true &&
    c.ollama_api_key_ok === true &&
    c.ollama_health_ok === true &&
    c.selected_model_ok === true &&
    c.inference_verified_ok === true &&
    c.stt_model_installed === true &&
    c.tts_voice_ok === true &&
    c.safe_settings_ok === true &&
    hasVerifiedTts(ttsVerified) &&
    hasVerifiedVoice(voiceVerified)
  );
}

/** Text chat requires real model evidence, independently of optional audio. */
export function canUseModel(readiness: SetupReadinessLike | null): boolean {
  const c = readiness?.checks;
  if (!c?.resources_ok || !c.database_ok || !c.sidecar_ok ||
      !c.selected_model_ok || !c.inference_verified_ok) return false;
  // The active cloud model was bound server-side to its verified credential.
  // A cloud user must not be required to configure Ollama as well.
  if (readiness?.active_provider_id === 'openrouter' ||
      readiness?.active_provider_id === 'nvidia' ||
      readiness?.active_provider_id === 'groq') {
    // GET /setup/readiness returns this authority at the TOP LEVEL, not
    // inside checks. Reading checks.active_provider_ready_ok incorrectly
    // disabled Continue after a successfully activated cloud model.
    return readiness.active_provider_ready_ok === true;
  }
  // Unknown/corrupt active selections fail closed rather than using Ollama.
  if (readiness?.active_provider_id &&
      readiness.active_provider_id !== 'ollama-cloud') return false;
  return c.ollama_api_key_ok === true && c.ollama_health_ok === true;
}

export function canStartChat(readiness: SetupReadinessLike | null): boolean {
  return canUseModel(readiness) && readiness?.checks?.safe_settings_ok === true;
}

export function shouldOpenSetup(readiness: SetupReadinessLike | null, textFirst: boolean): boolean {
  // Retained for compatibility; a preference is never readiness evidence.
  void textFirst;
  return !canStartChat(readiness);
}
