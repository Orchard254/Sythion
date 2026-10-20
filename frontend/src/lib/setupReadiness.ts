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
}

export interface SetupReadinessLike {
  full_ready?: boolean;
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

export function shouldOpenSetup(readiness: SetupReadinessLike | null, textFirst: boolean): boolean {
  if (textFirst) return false;
  if (readiness === null) return true;
  return !canClaimFullReady(readiness, false, false) || !readiness.full_ready;
}
