import { useEffect, useRef, useState } from 'react';
import { modelService, setupService, type SetupReadiness } from '../services/api';
import { Button, Notice } from './ui';

/** Explicit Ollama model editing from Settings; never changes permissions. */
export function OllamaModelSettings({ onReady }: { onReady?: () => void } = {}) {
  const [readiness, setReadiness] = useState<SetupReadiness | null>(null);
  const [key, setKey] = useState('');
  const [model, setModel] = useState('');
  const [models, setModels] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  useEffect(() => {
    let active = true;
    void setupService.readiness().then((report) => {
      if (!active) return;
      setReadiness(report);
      setModel(report.selected_model || '');
    }).catch(() => {
      if (active) setError('Could not read Ollama Cloud configuration.');
    });
    return () => { active = false; };
  }, []);

  const keyConfigured = readiness?.checks.ollama_api_key_ok === true;

  const browse = async () => {
    if (inFlight.current || (!keyConfigured && !key.trim())) return;
    inFlight.current = true; setBusy(true); setError(''); setNotice('');
    try {
      let available: string[];
      if (key.trim()) {
        const result = await setupService.testCredentials(key.trim());
        if (result.valid !== true) throw new Error('Ollama key did not verify');
        available = result.models;
        setKey('');
      } else {
        available = await modelService.available();
      }
      if (!Array.isArray(available)) throw new Error('Invalid model listing');
      setModels(Array.from(new Set(available.filter(
        (item) => typeof item === 'string' && item.trim().length > 0,
      ))));
      const report = await setupService.readiness();
      setReadiness(report);
      setNotice('Select a model. Listing does not prove inference availability.');
    } catch {
      setError('Could not connect to Ollama Cloud or load its models.');
    } finally { inFlight.current = false; setBusy(false); }
  };

  const verify = async () => {
    if (inFlight.current || !keyConfigured || !model.trim() || key.trim()) return;
    inFlight.current = true; setBusy(true); setError(''); setNotice('');
    try {
      await setupService.selectModel(model.trim());
      const result = await setupService.inference(model.trim());
      if (result.success !== true) throw new Error('Model did not respond');
      const latest = await setupService.readiness();
      if (latest.selected_model !== model.trim() ||
          latest.checks.inference_verified_ok !== true) {
        throw new Error('Model verification not current');
      }
      setReadiness(latest);
      setNotice('Ollama model selected and verified. Existing task permissions remain unchanged.');
      if (onReady) onReady();
    } catch {
      setError('Could not verify this Ollama model. Check its availability and your account.');
    } finally { inFlight.current = false; setBusy(false); }
  };

  return (
    <div className="stack" style={{ gap: 10 }}>
      <p className="t-muted">The Ollama connection remains separate from alternative providers.</p>
      <label htmlFor="ollama-settings-key">Ollama API key</label>
      <input id="ollama-settings-key" className="input" type="password" autoComplete="off"
        value={key} disabled={busy} placeholder={keyConfigured ? 'Saved key available — leave empty to keep it' : 'Paste Ollama API key'}
        onChange={(e) => setKey(e.target.value)} />
      <Button disabled={busy || (!keyConfigured && !key.trim())} onClick={() => void browse()}>
        {key.trim() ? 'Save key and browse Ollama models' : 'Browse Ollama models'}
      </Button>
      {models.length > 0 && <select id="ollama-settings-catalog" className="input" value=""
        disabled={busy} onChange={(e) => setModel(e.target.value)}>
        <option value="">Select an Ollama model</option>
        {models.map((item) => <option key={item} value={item}>{item}</option>)}
      </select>}
      <label htmlFor="ollama-settings-model">Selected Ollama model</label>
      <input id="ollama-settings-model" className="input" value={model} disabled={busy}
        onChange={(e) => setModel(e.target.value)} placeholder="Select above or enter exact model ID" />
      <Button variant="primary" disabled={busy || !keyConfigured || !model.trim() || !!key.trim()}
        onClick={() => void verify()}>Verify and use Ollama model</Button>
      <p className="t-muted">Verification sends one explicit model request; no task is created.</p>
      {notice && <Notice tone="info" icon="info">{notice}</Notice>}
      {error && <Notice tone="danger" icon="alert">{error}</Notice>}
    </div>
  );
}
