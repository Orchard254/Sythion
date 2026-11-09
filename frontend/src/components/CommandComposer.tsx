import { useEffect, useRef, useState } from 'react';
import { Icon } from './icons';
import { Badge, Button } from './ui';
import { useAppStore } from '../store/appStore';
import { useActions } from '../hooks/useBackend';

const SUGGESTIONS = [
  'Brief me on today',
  'Review my goals',
  'Check device status',
  'Summarise anything that failed',
];

export function CommandComposer({
  onStarted,
  placeholder = 'Describe a task and Sythion will plan and execute it…',
  compact,
}: {
  onStarted?: (taskId: string) => void;
  placeholder?: string;
  compact?: boolean;
}) {
  const [value, setValue] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const notify = useAppStore((s) => s.notify);
  const transcriptDraft = useAppStore((s) => s.transcriptDraft);
  const actions = useActions();
  const ref = useRef<HTMLTextAreaElement>(null);

  const text = transcriptDraft.trim() || value.trim();

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 190)}px`;
  }, [value]);

  const submit = async () => {
    const request = text.trim();
    if (!request || busy) return;
    setBusy(true);
    setError(null);
    try {
      const task = await actions.createTask(request);
      setValue('');
      useAppStore.setState({ transcriptDraft: '' });
      notify({
        tone: 'accent',
        title: 'Task started',
        body: request.slice(0, 120),
        route: 'assistant',
      });
      onStarted?.(task.id);
    } catch (err) {
      const message = err instanceof Error ? err.message : 'Failed to start the task';
      setError(message);
      notify({ tone: 'danger', title: 'Could not start task', body: message });
    } finally {
      setBusy(false);
    }
  };

  if (!actions.live) {
    return (
      <div className="composer" style={{ opacity: 0.85 }}>
        <textarea
          ref={ref}
          className="composer__input"
          rows={compact ? 1 : 2}
          placeholder="Connect a Sythion backend to delegate work from here."
          aria-label="Task command"
          disabled
        />
        <div className="composer__bar">
          <Badge tone="warn">Offline</Badge>
          <span className="spacer" />
          <Button icon="send" variant="primary" disabled>
            Start mission
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="composer">
        <textarea
          ref={ref}
          className="composer__input"
          rows={compact ? 1 : 2}
          value={text}
          placeholder={placeholder}
          aria-label="Describe a task for Sythion"
          onChange={(e) => {
            setValue(e.target.value);
            if (transcriptDraft) useAppStore.setState({ transcriptDraft: '' });
          }}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              void submit();
            }
          }}
        />
        <div className="composer__bar">
          <span className="t-faint row" style={{ gap: 6 }}>
            <Icon name="shield" size={12} />
            Executes under your current autonomy mode
          </span>
          <span className="spacer" />
          <span className="t-faint" style={{ marginRight: 4 }}>
            Enter to send · Shift+Enter for a new line
          </span>
          <Button icon="send" variant="primary" onClick={() => void submit()} disabled={!text || busy}>
            {busy ? 'Starting…' : 'Start mission'}
          </Button>
        </div>
      </div>

      {!compact && (
        <div className="chips" style={{ marginTop: 9 }}>
          {SUGGESTIONS.map((s) => (
            <button key={s} type="button" className="chip chip--suggest" onClick={() => setValue(s)}>
              {s}
            </button>
          ))}
        </div>
      )}

      {error && (
        <p className="t-danger" style={{ margin: '9px 0 0', fontSize: 12 }}>
          {error}
        </p>
      )}
    </div>
  );
}