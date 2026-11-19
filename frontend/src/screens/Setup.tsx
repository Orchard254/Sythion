import { useCallback, useEffect, useState } from 'react';
import { useAppStore } from '../store/appStore';
import { useVoiceCapture } from '../hooks/useVoiceCapture';
import { canStartChat, canUseModel } from '../lib/setupReadiness';
import { singleScreenContentStyle, singleScreenShellStyle } from '../lib/shellLayout';
import { Button, Notice, Panel } from '../components/ui';
import { SythionMark } from '../components/icons';
import { ActiveCloudSetup } from '../components/ActiveCloudSetup';
import { setupService, voiceService, CLOUD_PREVIEW_PROVIDERS, CLOUD_PROVIDER_LABELS,
  isCloudPreviewProviderId, type CloudPreviewProviderId,
  type SetupReadiness } from '../services/api';

interface SetupProps {
  onComplete: (textFirst: boolean) => void;
  onCloudConversationOnly?: () => void;
}

export function SetupScreen({ onComplete }: SetupProps) {
  const [step, setStep] = useState(0);
  const [provider, setProvider] = useState<CloudPreviewProviderId>('ollama-cloud');
  const [selectedModel, setSelectedModel] = useState('');
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [readiness, setReadiness] = useState<SetupReadiness | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [replyMode, setReplyMode] = useState<'text' | 'voice'>(() => localStorage.getItem('sythion.replyMode') === 'text' ? 'text' : 'voice');
  const [ttsVerified, setTtsVerified] = useState(false);
  const [voiceVerified, setVoiceVerified] = useState(false);
  const voiceCapture = useVoiceCapture();

  const refresh = useCallback(async () => {
    const report = await setupService.readiness();
    setReadiness(report);
    if (report.selected_model && report.active_provider_id === provider) {
      setSelectedModel(report.selected_model);
    }
    return report;
  }, [provider]);

  useEffect(() => {
    let cancelled = false;
    void setupService.readiness().then((report) => {
      if (cancelled) return;
      setReadiness(report);
      if (isCloudPreviewProviderId(report.active_provider_id)) {
        setProvider(report.active_provider_id);
      }
      setSelectedModel(report.selected_model || '');
    }).catch(() => {
      if (!cancelled) setError('Could not check setup. Retry when Sythion has connected.');
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, []);

  const run = async (action: () => Promise<void>, failureHint?: string) => {
    setError(null); setMessage(null); setBusy(true);
    try { await action(); }
    catch (e) { setError(failureHint || (e instanceof Error ? e.message : 'That step did not finish. Please try again.')); }
    finally { setBusy(false); }
  };

  const testMicrophone = () => run(async () => {
    setVoiceVerified(false);
    useAppStore.getState().setTranscriptDraft('');
    useAppStore.getState().setVoiceEnabled(true);
    try {
      await voiceCapture.startCapture();
      setMessage('Listening for four seconds. Say “Hello Synth”, then pause.');
      await new Promise((resolve) => setTimeout(resolve, 4000));
      await voiceCapture.stopCapture();
      const transcript = useAppStore.getState().transcriptDraft.trim();
      if (!transcript) throw new Error('No speech was recognised. Check microphone permission and try again. You can still type.');
      setVoiceVerified(true);
      setMessage(`Microphone works: “${transcript}”. In chat, stop the microphone, review your words, then send.`);
    } finally {
      await voiceCapture.stopCapture();
      useAppStore.getState().setVoiceEnabled(false);
      useAppStore.getState().setTranscriptDraft('');
    }
  });

  const finish = () => run(async () => {
    // Recheck the backend on entry; UI state and old local preferences cannot
    // establish that credentials or inference still work.
    const report = await refresh();
    if (!canStartChat(report)) throw new Error('The connection needs attention. Go back and test your model again.');
    if (replyMode === 'voice' && (!ttsVerified || !report.checks.tts_voice_ok)) {
      throw new Error('Test the speaker first, or choose text replies.');
    }
    localStorage.setItem('sythion.replyMode', replyMode);
    useAppStore.getState().setTranscriptDraft('');
    useAppStore.getState().setVoiceEnabled(false);
    onComplete(replyMode === 'text');
  });

  const modelReady = canUseModel(readiness);
  const selectedModelReady = modelReady && selectedModel === readiness?.selected_model &&
    readiness?.active_provider_id === provider;
  const steps = ['Connect', 'Choose a model', 'Choose how to talk', 'Start chatting'];
  const checks: Array<[string, boolean]> = [
    ['Sythion is running on this computer', readiness?.checks.sidecar_ok === true && readiness?.checks.database_ok === true],
    ['Selected provider and model verified', modelReady],
    ['Guarded permissions and configured history retention', readiness?.checks.safe_settings_ok === true],
    [replyMode === 'voice' ? 'Spoken replies tested' : 'Text replies selected', replyMode === 'text' || ttsVerified],
  ];

  return (
    <div className="shell" style={singleScreenShellStyle()}>
      <main className="shell__main" style={singleScreenContentStyle({ overflowY: 'auto', padding: '32px 20px' })}>
        <div className="stack" style={{ maxWidth: 680, margin: '0 auto', gap: 20 }}>
          <div className="row" style={{ gap: 12 }}>
            <SythionMark size={40} />
            <div><h1 className="screen-head__title">Welcome to Sythion</h1><p className="t-muted">Let’s get Synth ready for your first conversation.</p></div>
          </div>
          <ol className="row" aria-label="Setup progress" style={{ padding: 0, listStyle: 'none', gap: 16, flexWrap: 'wrap' }}>
            {steps.map((label, i) => <li key={label} aria-current={step === i ? 'step' : undefined} style={{ opacity: i === step ? 1 : 0.55 }}>{i + 1}. {label}</li>)}
          </ol>
          <div role="alert">{error && <Notice tone="danger" icon="alert"><span>{error}</span></Notice>}</div>
          <div role="status" aria-live="polite">{message && <Notice tone="info" icon="info"><span>{message}</span></Notice>}</div>
          {loading ? <p role="status">Checking your setup…</p> : <>
            {step === 0 && <Panel title="1. Connect Synth" icon="key">
              <div className="stack" style={{ gap: 12 }}>
                <label htmlFor="setup-provider">Choose your AI provider</label>
                <select id="setup-provider" className="input" value={provider} disabled={busy}
                  onChange={(event) => {
                    const next = event.target.value;
                    if (!isCloudPreviewProviderId(next)) return;
                    setProvider(next);
                    setSelectedModel('');
                    setStep(0); setError(null); setMessage(null);
                  }}>
                  {CLOUD_PREVIEW_PROVIDERS.map((id) => (
                    <option key={id} value={id}>{CLOUD_PROVIDER_LABELS[id]}</option>
                  ))}
                </select>
                <p className="t-muted">Choose who provides the reasoning model. You will still use the same Sythion assistant. Your key is saved in this computer's secure credential store.</p>
                <ActiveCloudSetup provider={provider} phase="connect"
                  onConnected={() => { setError(null); setMessage(null); setStep(1); }} />
              </div>
            </Panel>}
            {step === 1 && <Panel title="2. Choose a model" icon="info">
              <div className="stack" style={{ gap: 12 }}>
                <ActiveCloudSetup provider={provider} phase="model"
                  onVerified={(modelId) => {
                    setSelectedModel(modelId);
                    void refresh();
                  }} />
                <p className="t-muted">You can change the model later under Settings → AI Models.</p>
                <p className="t-muted">{selectedModelReady ? 'Your connection is ready.' : 'Select and verify a model to continue.'}</p>
                <p className="t-muted">Continue applies guarded permissions and 90-day history retention.</p>
                <div className="row" style={{ gap: 8 }}>
                <Button disabled={busy} onClick={() => { setError(null); setMessage(null); setStep(0); }}>Back</Button>
                <Button variant="primary" disabled={busy || !selectedModelReady} onClick={() => void run(async () => {
                  const result = await setupService.applySafeSettings();
                  if (result?.ok !== true) throw new Error('Safe setup could not be applied. Please try again.');
                  const report = await refresh();
                  if (!canStartChat(report)) throw new Error('Guarded permissions and history retention are not ready yet.');
                  setStep(2);
                })}>Continue</Button>
                </div>
              </div>
            </Panel>}
            {step === 2 && <Panel title="3. Choose how to talk" icon="info">
              <div className="stack" style={{ gap: 14 }}>
                <fieldset disabled={busy} style={{ border: 0, padding: 0 }}>
                  <legend>How should Synth reply?</legend>
                  <label className="row" style={{ gap: 8, marginTop: 12 }}><input type="radio" name="reply-mode" checked={replyMode === 'text'} onChange={() => setReplyMode('text')} />Text — read replies in the conversation</label>
                  <label className="row" style={{ gap: 8, marginTop: 12 }}><input type="radio" name="reply-mode" checked={replyMode === 'voice'} onChange={() => setReplyMode('voice')} />Voice and text — also read new replies aloud</label>
                </fieldset>
                <p className="t-muted">Speech runs locally; the microphone opens only when you tap it. Change in Settings → Voice.</p>
                {replyMode === 'voice' && <Button disabled={busy} onClick={() => void run(async () => {
                  setTtsVerified(false);
                  const result = await voiceService.speak('Hello, I’m Synth. I’m ready to help.');
                  if (!result.success) throw new Error('Speaker playback is unavailable. Check your audio output or continue with text.');
                  setTtsVerified(true); await refresh(); setMessage('Speaker test completed.');
                })}>Test speaker</Button>}
                <details><summary>Optional: use your microphone</summary><div className="stack" style={{ gap: 12, marginTop: 12 }}>
                  <p className="t-muted">One-time download, optional. Needed only for spoken replies.</p>
                  <Button disabled={busy} onClick={() => void run(async () => { await voiceService.installAssets(); await refresh(); setMessage('Speech assets checked. You can test your microphone.'); })}>Install speech recognition</Button>
                  <Button disabled={busy || !readiness?.checks.stt_model_installed} onClick={() => void testMicrophone()}>Test microphone</Button>
                  {voiceVerified && <p>Microphone and speech recognition verified.</p>}
                </div></details>
                <div className="row" style={{ gap: 8 }}><Button disabled={busy} onClick={() => { setError(null); setMessage(null); setStep(1); }}>Back</Button><Button variant="primary" disabled={busy || (replyMode === 'voice' && !ttsVerified)} onClick={() => { setMessage(null); setStep(3); }}>Continue</Button></div>
              </div>
            </Panel>}
            {step === 3 && <Panel title="4. Say hello to Synth" icon="check">
              <div className="stack" style={{ gap: 12 }}>
                <ul>{checks.map(([label, ok]) => <li key={label}>{ok ? '✓' : 'Needs attention:'} {label}</li>)}</ul>
                <p>Try “Hello Synth, what can you help me with?” Type and press Enter — or tap the microphone and speak.</p>
                <p className="t-muted">Progress is shown while sending; your message and the answer appear in the conversation{replyMode === 'voice' ? ', and the answer is read aloud.' : '.'}</p>
                <div className="row" style={{ gap: 8 }}><Button disabled={busy} onClick={() => { setError(null); setStep(2); }}>Back</Button><Button variant="primary" disabled={busy || !canStartChat(readiness)} onClick={() => void finish()}>Start conversation</Button></div>
              </div>
            </Panel>}
            <Button variant="subtle" disabled={busy} onClick={() => void run(async () => { await refresh(); })}>Recheck connection</Button>
          </>}
          {busy && <p role="status">Working… please keep Sythion open.</p>}
        </div>
      </main>
    </div>
  );
}
