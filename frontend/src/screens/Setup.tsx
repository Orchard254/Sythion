import { useCallback, useEffect, useState } from 'react';
import { useAppStore } from '../store/appStore';
import { useVoiceCapture } from '../hooks/useVoiceCapture';
import { canStartChat, canUseModel } from '../lib/setupReadiness';
import { singleScreenContentStyle, singleScreenShellStyle } from '../lib/shellLayout';
import { Button, Notice, Panel } from '../components/ui';
import { SythionMark } from '../components/icons';
import { setupService, modelService, voiceService, type SetupReadiness } from '../services/api';

interface SetupProps { onComplete: (textFirst: boolean) => void; }

export function SetupScreen({ onComplete }: SetupProps) {
  const [step, setStep] = useState(0);
  const [apiKey, setApiKey] = useState('');
  const [selectedModel, setSelectedModel] = useState('');
  const [models, setModels] = useState<string[]>([]);
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
    if (report.selected_model) setSelectedModel(report.selected_model);
    return report;
  }, []);

  useEffect(() => {
    let cancelled = false;
    void setupService.readiness().then((report) => {
      if (cancelled) return;
      setReadiness(report);
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

  const connect = () => run(async () => {
    const result = await setupService.testCredentials(apiKey.trim());
    // This endpoint validates AND saves the key. Writing it a second time
    // needlessly invalidates the inference evidence on the server.
    setApiKey('');
    setModels(result.models.filter(Boolean));
    await refresh();
    setStep(1);
    setMessage(result.models.filter(Boolean).length ? 'Connected. Choose a model below, then test it.' : 'Connected, but no models were found. Check your Ollama Cloud account, then try finding models again.');
  }, 'Could not finish connecting. Check your key and internet connection, then try again or recheck the connection.');

  const findModels = () => run(async () => {
    const available = await modelService.available();
    setModels(available);
    await refresh();
    setStep(1);
    setMessage(available.length ? 'Choose a model below, then test it.' : 'No models are available. Check your Ollama Cloud account and try again.');
  }, 'Could not find models. Check your key and internet connection, then try again.');

  const testModel = () => run(async () => {
    await setupService.selectModel(selectedModel);
    const result = await setupService.inference(selectedModel);
    if (!result.success) throw new Error('The model did not respond. Try again or choose another model.');
    await refresh();
    setMessage('Synth answered the connection test. You can continue.');
  }, 'The model test did not finish. Check your API key and internet connection, then try again or choose another model.');

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
  const selectedModelReady = modelReady && selectedModel === readiness?.selected_model;
  const steps = ['Connect', 'Choose a model', 'Choose how to talk', 'Start chatting'];
  const checks: Array<[string, boolean]> = [
    ['Sythion is running on this computer', readiness?.checks.sidecar_ok === true && readiness?.checks.database_ok === true],
    ['Cloud connection and model tested', modelReady],
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
                <p className="t-muted">Synth answers through your Ollama Cloud account. Your key is saved in this computer's secure credential store.</p>
                {!readiness?.checks.ollama_api_key_ok && <div>
                  <p>No key yet? You'll need an Ollama account and an internet connection.</p>
                  <ol>
                    <li>In your browser, visit <strong>ollama.com/settings/keys</strong> and sign in or create an account.</li>
                    <li>Create an API key and copy it. Keep it private, like a password.</li>
                    <li>Return here, paste the key below, and select “Connect and save key”.</li>
                  </ol>
                  <p className="t-muted">You don’t need to install Ollama on this computer.</p>
                </div>}
                {readiness?.checks.ollama_api_key_ok && <Notice tone="info" icon="check"><span>A key is already configured. Use it to continue, or enter a replacement below.</span></Notice>}
                <label htmlFor="setup-key">Ollama Cloud API key</label>
                <input id="setup-key" type="password" autoComplete="off" placeholder="Paste your API key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} className="input" disabled={busy} />
                <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
                  <Button variant="primary" disabled={busy || !apiKey.trim()} onClick={() => void connect()}>Connect and save key</Button>
                  {readiness?.checks.ollama_api_key_ok && <Button variant="subtle" disabled={busy} onClick={() => void findModels()}>Use configured key</Button>}
                </div>
              </div>
            </Panel>}
            {step === 1 && <Panel title="2. Choose a model" icon="info">
              <div className="stack" style={{ gap: 12 }}>
                {(models.length > 0 || selectedModel) && <>
                  <label htmlFor="setup-model">Choose a model</label>
                  <p className="t-muted" id="setup-model-help">A model is the AI that writes Synth's answers. Pick one from the list — you can change it later.</p>
                  <select id="setup-model" aria-describedby="setup-model-help" className="input" value={selectedModel} disabled={busy} onChange={(e) => { setSelectedModel(e.target.value); setMessage(null); }}>
                    <option value="">Select a model</option>
                    {[...new Set([...(selectedModel ? [selectedModel] : []), ...models])].map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                  <Button disabled={busy || !selectedModel} onClick={() => void testModel()}>Test selected model</Button>
                </>}
                {models.length === 0 && !selectedModel && <p>No models found yet. Check your connection and try again.</p>}
                <Button variant="subtle" disabled={busy} onClick={() => void findModels()}>Refresh available models</Button>
                <p className="t-muted">{selectedModelReady ? 'Your connection is ready.' : 'The test sends a short request so you know the model can respond.'}</p>
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
