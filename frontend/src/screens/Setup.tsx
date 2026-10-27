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
  const [replyMode, setReplyMode] = useState<'text' | 'voice'>('text');
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

  const run = async (action: () => Promise<void>) => {
    setError(null); setMessage(null); setBusy(true);
    try { await action(); }
    catch (e) { setError(e instanceof Error ? e.message : 'That step did not finish. Please try again.'); }
    finally { setBusy(false); }
  };

  const connect = () => run(async () => {
    const result = await setupService.testCredentials(apiKey.trim());
    // This endpoint validates AND saves the key. Writing it a second time
    // needlessly invalidates the inference evidence on the server.
    setApiKey('');
    setModels(result.models.filter(Boolean));
    await refresh();
    setMessage('Connected. Choose a model below, then test it.');
  });

  const testModel = () => run(async () => {
    await setupService.selectModel(selectedModel);
    const result = await setupService.inference(selectedModel);
    if (!result.success) throw new Error('The model did not respond. Try again or choose another model.');
    await refresh();
    setMessage('Synth answered the connection test. You can continue.');
  });

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
  const steps = ['Connect Synth', 'Choose how to talk', 'Start chatting'];
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
                <p className="t-muted">Synth uses your Ollama Cloud account to answer. Your messages go to the selected cloud model. Your API key is saved in this computer’s secure credential store.</p>
                {readiness?.checks.ollama_api_key_ok && <Notice tone="info" icon="check"><span>A key is already saved. You can discover models or replace it below.</span></Notice>}
                <label htmlFor="setup-key">Ollama Cloud API key</label>
                <input id="setup-key" type="password" autoComplete="off" placeholder="Paste your API key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} className="input" disabled={busy} />
                <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
                  <Button variant="primary" disabled={busy || !apiKey.trim()} onClick={() => void connect()}>Connect and save key</Button>
                  <Button variant="subtle" disabled={busy} onClick={() => void run(async () => { setModels(await modelService.available()); await refresh(); })}>Find available models</Button>
                </div>
                {(models.length > 0 || selectedModel) && <>
                  <label htmlFor="setup-model">Choose a model</label>
                  <select id="setup-model" className="input" value={selectedModel} disabled={busy} onChange={(e) => setSelectedModel(e.target.value)}>
                    <option value="">Select a model</option>
                    {[...new Set([...(selectedModel ? [selectedModel] : []), ...models])].map((m) => <option key={m} value={m}>{m}</option>)}
                  </select>
                  <Button disabled={busy || !selectedModel} onClick={() => void testModel()}>Test selected model</Button>
                </>}
                <p className="t-muted">{modelReady ? 'Your connection is ready.' : 'A successful model test is required before your first message.'}</p>
                <Button variant="primary" disabled={busy || !modelReady || selectedModel !== readiness?.selected_model} onClick={() => void run(async () => {
                  const result = await setupService.applySafeSettings();
                  if (result?.ok !== true) throw new Error('Safe setup could not be applied. Please try again.');
                  const report = await refresh();
                  if (!canStartChat(report)) throw new Error('Guarded permissions and history retention are not ready yet.');
                  setStep(1);
                })}>Continue</Button>
              </div>
            </Panel>}
            {step === 1 && <Panel title="2. Choose how to talk" icon="info">
              <div className="stack" style={{ gap: 14 }}>
                <fieldset disabled={busy} style={{ border: 0, padding: 0 }}>
                  <legend>How should Synth reply?</legend>
                  <label className="row" style={{ gap: 8, marginTop: 12 }}><input type="radio" name="reply-mode" checked={replyMode === 'text'} onChange={() => setReplyMode('text')} />Text — read replies in the conversation</label>
                  <label className="row" style={{ gap: 8, marginTop: 12 }}><input type="radio" name="reply-mode" checked={replyMode === 'voice'} onChange={() => setReplyMode('voice')} />Voice and text — also read new replies aloud</label>
                </fieldset>
                <p className="t-muted">Speech recognition and playback run locally. The microphone opens only when you choose it. You can type in either mode and change reply mode in chat.</p>
                {replyMode === 'voice' && <Button disabled={busy} onClick={() => void run(async () => {
                  setTtsVerified(false);
                  const result = await voiceService.speak('Hello, I’m Synth. I’m ready to help.');
                  if (!result.success) throw new Error('Speaker playback is unavailable. Check your audio output or continue with text.');
                  setTtsVerified(true); await refresh(); setMessage('Speaker test completed.');
                })}>Test speaker</Button>}
                <details><summary>Optional: use your microphone</summary><div className="stack" style={{ gap: 12, marginTop: 12 }}>
                  <p className="t-muted">Install speech recognition once, then test your microphone. The download may take a few minutes. Microphone access is optional for spoken replies.</p>
                  <Button disabled={busy} onClick={() => void run(async () => { await voiceService.installAssets(); await refresh(); setMessage('Speech assets checked. You can test your microphone.'); })}>Install speech recognition</Button>
                  <Button disabled={busy || !readiness?.checks.stt_model_installed} onClick={() => void testMicrophone()}>Test microphone</Button>
                  {voiceVerified && <p>Microphone and speech recognition verified.</p>}
                </div></details>
                <div className="row" style={{ gap: 8 }}><Button disabled={busy} onClick={() => { setError(null); setMessage(null); setStep(0); }}>Back</Button><Button variant="primary" disabled={busy || (replyMode === 'voice' && !ttsVerified)} onClick={() => { setMessage(null); setStep(2); }}>Continue</Button></div>
              </div>
            </Panel>}
            {step === 2 && <Panel title="3. Say hello to Synth" icon="check">
              <div className="stack" style={{ gap: 12 }}>
                <ul>{checks.map(([label, ok]) => <li key={label}>{ok ? '✓' : 'Needs attention:'} {label}</li>)}</ul>
                <p>Try “Hello Synth, what can you help me with?” Type your message and press Enter. For voice input, tap the microphone, speak, tap it again, review the transcript, then send.</p>
                <p className="t-muted">While your message is being sent, Sythion shows its progress. Your message and Synth’s answer appear in the conversation{replyMode === 'voice' ? ', and the answer is read aloud. Text remains available if playback fails.' : '.'}</p>
                <div className="row" style={{ gap: 8 }}><Button disabled={busy} onClick={() => { setError(null); setStep(1); }}>Back</Button><Button variant="primary" disabled={busy || !canStartChat(readiness)} onClick={() => void finish()}>Start conversation</Button></div>
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
