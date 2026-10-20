import { useCallback, useEffect, useState } from 'react';
import { useAppStore } from '../store/appStore';
import { useVoiceCapture } from '../hooks/useVoiceCapture';
import { canClaimFullReady } from '../lib/setupReadiness';
import { singleScreenContentStyle, singleScreenShellStyle } from '../lib/shellLayout';
import { Button, Notice, Panel } from '../components/ui';
import { SythionMark } from '../components/icons';
import {
  setupService,
  secretService,
  modelService,
  voiceService,
  settingsService,
  type SetupReadiness,
} from '../services/api';

interface SetupProps {
  onComplete: (textFirst: boolean) => void;
}

export function SetupScreen({ onComplete }: SetupProps) {
  const [apiKey, setApiKey] = useState('');
  const [selectedModel, setSelectedModel] = useState('');
  const [models, setModels] = useState<string[]>([]);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [readiness, setReadiness] = useState<SetupReadiness | null>(null);
  const [readinessLoading, setReadinessLoading] = useState(true);
  const [readinessError, setReadinessError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [voiceVerified, setVoiceVerified] = useState(false);
  const [ttsVerified, setTtsVerified] = useState(false);
  const setVoiceEnabled = useAppStore((st) => st.setVoiceEnabled);
  const setTranscriptDraft = useAppStore((st) => st.setTranscriptDraft);
  const voiceCapture = useVoiceCapture();

  const refreshReadiness = useCallback(async () => {
    setReadinessLoading(true);
    setReadinessError(null);
    try {
      const r = await setupService.readiness();
      setReadiness(r);
      if (r.selected_model) setSelectedModel(r.selected_model);
      return r;
    } catch (e) {
      // The readiness report is what tells the user which step is still
      // missing. Failing to load it is shown in place, next to the retry
      // button, instead of being swallowed.
      setReadinessError(e instanceof Error ? e.message : 'Readiness check failed');
      return null;
    } finally {
      setReadinessLoading(false);
    }
  }, []);

  // Load the readiness report as soon as the screen is shown. Without this the
  // readiness panel is empty on arrival and the user has to guess that
  // "Refresh readiness" is the thing that fills it in.
  useEffect(() => {
    void refreshReadiness();
  }, [refreshReadiness]);

  const testCredentials = async () => {
    setError(null); setMessage(null); setBusy(true);
    try {
      const res = await setupService.testCredentials(apiKey);
      await secretService.set('ollama_api_key', apiKey);
      setModels(res.models || []);
      setMessage(`Credentials valid. Discovered ${res.model_count} models.`);
      await refreshReadiness();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Credential test failed');
    } finally {
      setBusy(false);
    }
  };

  const discoverModels = async () => {
    setError(null); setBusy(true);
    try {
      const ms = await modelService.available();
      setModels(ms);
      setMessage(`Discovered ${ms.length} authenticated models.`);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Model discovery failed');
    } finally {
      setBusy(false);
    }
  };

  const selectModel = async () => {
    if (!selectedModel) return;
    setError(null); setBusy(true);
    try {
      // Persist via onboarding endpoint using settingsService? Use direct API through secret? Backend route /onboarding/models.
      await setupService.selectModel(selectedModel);
      const inf = await setupService.inference(selectedModel);
      setMessage(`Model selected and inference passed: ${inf.response_preview}`);
      await refreshReadiness();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Model selection or inference failed');
    } finally {
      setBusy(false);
    }
  };

  const installVoice = async () => {
    setError(null); setBusy(true);
    try {
      const st = await voiceService.installAssets();
      setMessage(`Voice assets: STT installed=${st.stt_installed}, TTS=${st.tts_voice || 'unavailable'}`);
      await refreshReadiness();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Voice install failed');
    } finally {
      setBusy(false);
    }
  };

  const testTts = async () => {
    setError(null); setBusy(true);
    try {
      const res = await voiceService.speak('Sythion voice test');
      const ok = Boolean(res.success);
      setTtsVerified(ok);
      setMessage(ok ? 'TTS playback succeeded.' : 'TTS playback failed.');
      await refreshReadiness();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'TTS test failed');
    } finally {
      setBusy(false);
    }
  };


  const testMicrophone = async () => {
    setError(null); setMessage(null); setBusy(true); setVoiceVerified(false);
    setTranscriptDraft('');
    try {
      setVoiceEnabled(true);
      await new Promise((resolve) => setTimeout(resolve, 50));
      await voiceCapture.startCapture();
      await new Promise((resolve) => setTimeout(resolve, 4000));
      await voiceCapture.stopCapture();
      setVoiceEnabled(false);
      // Allow transcript flush to settle
      await new Promise((resolve) => setTimeout(resolve, 300));
      const transcript = useAppStore.getState().transcriptDraft.trim();
      if (transcript.length > 0) {
        setVoiceVerified(true);
        setMessage(`Speech recognition succeeded: "${transcript}"`);
      } else {
        throw new Error('No transcript was produced. Ensure microphone access and speak clearly.');
      }
      await refreshReadiness();
    } catch (e) {
      try { await voiceCapture.stopCapture(); } catch { /* ignore */ }
      setVoiceEnabled(false);
      setError(e instanceof Error ? e.message : 'Microphone test failed');
    } finally {
      setBusy(false);
    }
  };

  const applySafeSettings = async () => {
    setError(null); setBusy(true);
    try {
      await settingsService.update({ autonomy_level: 'guarded', retention_days: 90 });
      await setupService.applySafeSettings();
      setMessage('Safe initial settings applied.');
      await refreshReadiness();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Settings update failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="shell" style={singleScreenShellStyle()}>
      <div className="shell__main" style={singleScreenContentStyle({ overflowY: 'auto', padding: 40 })}>
        <div className="stack" style={{ maxWidth: 760, margin: '0 auto', gap: 16 }}>
          <div className="row" style={{ gap: 12 }}>
            <SythionMark size={34} />
            <div>
              <h1 className="screen-head__title" style={{ fontSize: 20 }}>First-run setup</h1>
              <p className="t-muted" style={{ margin: '3px 0 0' }}>Configure Ollama Cloud, local speech, safe permissions, then verify readiness.</p>
            </div>
          </div>

          {error && <Notice tone="danger" icon="alert"><span>{error}</span></Notice>}
          {message && <Notice tone="info" icon="info"><span>{message}</span></Notice>}

          <Panel title="1. Ollama Cloud credentials" icon="key">
            <div className="stack stack--tight">
              <input type="password" placeholder="Paste API key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} className="input" />
              <div className="row" style={{ gap: 8 }}>
                <Button variant="primary" disabled={busy || !apiKey} onClick={() => void testCredentials()}>Test and save key</Button>
                <Button variant="subtle" disabled={busy} onClick={() => void discoverModels()}>Discover models</Button>
              </div>
              {models.length > 0 && (
                <select value={selectedModel} onChange={(e) => setSelectedModel(e.target.value)} className="input">
                  <option value="">Select model</option>
                  {models.map((m) => <option key={m} value={m}>{m}</option>)}
                </select>
              )}
              <Button variant="primary" disabled={busy || !selectedModel} onClick={() => void selectModel()}>Select and test inference</Button>
            </div>
          </Panel>

          <Panel title="2. Local speech" icon="info">
            <div className="stack stack--tight">
              <Button variant="primary" disabled={busy} onClick={() => void installVoice()}>Install / verify voice assets</Button>
              <Button variant="subtle" disabled={busy} onClick={() => void testTts()}>Test TTS playback</Button>
              <Button variant="primary" disabled={busy} onClick={() => void testMicrophone()}>Test microphone and speech recognition</Button>
              {voiceVerified && <Notice tone="info" icon="info"><span>Microphone-to-transcription verified.</span></Notice>}
              <p className="t-muted" style={{ margin: 0 }}>Microphone recognition requires explicit user action in the workspace voice capture flow.</p>
            </div>
          </Panel>

          <Panel title="3. Safe initial configuration" icon="shield">
            <div className="stack stack--tight">
              <Button variant="primary" disabled={busy} onClick={() => void applySafeSettings()}>Apply safe workspace, guarded autonomy, retention</Button>
            </div>
          </Panel>

          <Panel title="4. Readiness" icon="check">
            <div className="stack stack--tight">
              <div className="row" style={{ gap: 8 }}>
                <Button variant="subtle" disabled={busy || readinessLoading} onClick={() => void refreshReadiness()}>
                  {readinessLoading ? 'Checking readiness…' : 'Refresh readiness'}
                </Button>
              </div>
              {readinessError && (
                <Notice tone="danger" icon="alert">
                  <span>Could not read the readiness report: {readinessError}</span>
                </Notice>
              )}
              {readiness && (
                <pre className="t-mono" style={{ whiteSpace: 'pre-wrap', fontSize: 12 }}>{JSON.stringify(readiness, null, 2)}</pre>
              )}
              {!readiness && !readinessLoading && !readinessError && (
                <p className="t-muted" style={{ margin: 0 }}>No readiness report yet. Use “Refresh readiness” to try again.</p>
              )}
              <div className="row" style={{ gap: 8 }}>
                <Button variant="primary" disabled={!canClaimFullReady(readiness, ttsVerified, voiceVerified)} onClick={() => onComplete(false)}>Enter Sythion (Full Ready)</Button>
                <Button variant="subtle" onClick={() => onComplete(true)}>Continue in Limited — Text-First mode</Button>
              </div>
            </div>
          </Panel>
        </div>
      </div>
    </div>
  );
}
