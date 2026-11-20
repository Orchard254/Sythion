import { useEffect, useState } from 'react';
import { Badge, Button, EmptyState, Notice, Panel } from './ui';
import type { ReconciliationRecord, RemoteAction } from '../types';
import { reconciliationService } from '../services/api';

const STATE_TONE = {
  received: 'idle',
  understanding: 'info',
  planned: 'info',
  awaiting_approval: 'warn',
  executing: 'accent',
  verifying: 'accent',
  completed: 'ok',
  failed: 'danger',
  cancelled: 'idle',
  uncertain: 'danger',
} as const;

type Outcome = 'effect_confirmed' | 'effect_not_performed' | 'abandoned';

function newDecisionId(): string {
  const random = typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `owner-ui-rec-${random}`;
}

interface Props {
  actions: RemoteAction[];
  /** Injectable for tests; defaults to the real owner reconciliation API. */
  onReconcile?: (
    actionId: string, decisionId: string, outcome: Outcome,
    evidenceRef?: string, newIdempotencyKey?: string,
  ) => Promise<unknown>;
  onReconciled?: () => void;
  /** Injectable for tests; defaults to the read-only reconciliation history API. */
  loadHistory?: (actionId: string) => Promise<ReconciliationRecord[]>;
}

/**
 * Control Center view of remote-device action lifecycle states.
 *
 * The panel itself only reports state. Reconciliation of an uncertain action is
 * an explicit owner action bound to a UI-generated decision id; it never
 * revives or replays the uncertain effect.
 */
export function RemoteActionsPanel({ actions, onReconcile, onReconciled, loadHistory }: Props) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [historyFor, setHistoryFor] = useState<string | null>(null);
  const [history, setHistory] = useState<Record<string, ReconciliationRecord[]>>({});
  const [outcome, setOutcome] = useState<Outcome>('effect_not_performed');
  const [evidence, setEvidence] = useState('');
  const [newKey, setNewKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const fetchHistory = loadHistory ?? (async (actionId: string) => {
    const result = await reconciliationService.list(actionId);
    return result.reconciliations ?? [];
  });

  useEffect(() => {
    if (!historyFor || history[historyFor]) return;
    let active = true;
    fetchHistory(historyFor).then(
      (records) => { if (active) setHistory((old) => ({ ...old, [historyFor]: records })); },
      (err) => { if (active) setError(String(err)); },
    );
    return () => { active = false; };
  }, [historyFor, history, fetchHistory]);

  const ordered = [...actions].sort((a, b) => {
    if (a.uncertain !== b.uncertain) return a.uncertain ? -1 : 1;
    return (b.updated_at ?? '').localeCompare(a.updated_at ?? '');
  });

  const submit = async (actionId: string) => {
    const handler = onReconcile ?? reconciliationService.reconcile;
    setBusy(true);
    setError('');
    try {
      await handler(
        actionId,
        newDecisionId(),
        outcome,
        evidence.trim() || undefined,
        newKey.trim() || undefined,
      );
      setExpanded(null);
      setEvidence('');
      setNewKey('');
      setHistory((old) => {
        const next = { ...old };
        delete next[actionId];
        return next;
      });
      if (historyFor === actionId) {
        fetchHistory(actionId).then(
          (records) => setHistory((old) => ({ ...old, [actionId]: records })),
          () => { /* history is read-only; ignore refresh failure */ },
        );
      }
      onReconciled?.();
    } catch (err) {
      setError(String(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Panel title="Device actions" hint="What Synth is doing on devices">
      {ordered.length === 0 ? (
        <EmptyState title="No device actions" description="No remote actions have been recorded." />
      ) : (
        <div className="devices__list" data-testid="remote-actions-panel">
          {ordered.map((action) => (
            <div key={action.action_id} className="devices__item">
              <div className="row row--wrap" style={{ gap: 8 }}>
                <strong>{action.action_id}</strong>
                <Badge tone={action.uncertain ? 'danger' : STATE_TONE[action.state]}>
                  {action.uncertain ? 'uncertain — needs reconciliation' : action.state}
                </Badge>
                <span className="t-mono t-faint">{action.device_id}</span>
              </div>
              {action.task_id && (
                <span className="t-faint">Task: {action.task_id}</span>
              )}
              {action.reason && <span>{action.reason}</span>}
              {action.evidence_ref && (
                <span className="t-faint">Evidence: {action.evidence_ref}</span>
              )}

              <button
                type="button"
                className="stat stat--clickable"
                data-testid={`history-toggle-${action.action_id}`}
                onClick={() => setHistoryFor(historyFor === action.action_id ? null : action.action_id)}
              >
                {historyFor === action.action_id ? 'Hide history' : 'View reconciliation history'}
              </button>

              {historyFor === action.action_id && (
                <div className="stack stack--tight" data-testid="reconciliation-history">
                  {!history[action.action_id] ? (
                    <span className="t-faint">Loading history…</span>
                  ) : history[action.action_id].length === 0 ? (
                    <EmptyState title="No reconciliations" description="No decisions recorded for this action." />
                  ) : (
                    history[action.action_id].map((record) => (
                      <div key={record.id} className="stack stack--tight">
                        <div className="row row--wrap" style={{ gap: 8 }}>
                          <Badge tone="info">{record.outcome}</Badge>
                          <span className="t-mono t-faint">{record.decision_id}</span>
                        </div>
                        {record.evidence_ref && (
                          <span className="t-faint">Evidence: {record.evidence_ref}</span>
                        )}
                        {record.note && <span>{record.note}</span>}
                        {record.new_action_id && (
                          <span className="t-faint">Fresh action: {record.new_action_id}</span>
                        )}
                        {record.created_at && (
                          <span className="t-faint">{record.created_at}</span>
                        )}
                      </div>
                    ))
                  )}
                </div>
              )}

              {action.uncertain && (
                <div className="stack stack--tight" style={{ marginTop: 8 }}>
                  <Notice tone="warn">
                    The outcome is uncertain. Reconcile from independent evidence;
                    this never repeats the original action.
                  </Notice>
                  {expanded === action.action_id ? (
                    <div className="stack stack--tight">
                      <label>
                        <span>Established outcome</span>
                        <select
                          value={outcome}
                          onChange={(e) => setOutcome(e.target.value as Outcome)}
                          aria-label="Established outcome"
                        >
                          <option value="effect_not_performed">Effect not performed</option>
                          <option value="effect_confirmed">Effect confirmed</option>
                          <option value="abandoned">Abandoned</option>
                        </select>
                      </label>
                      <label>
                        <span>Independent evidence reference</span>
                        <input
                          value={evidence}
                          onChange={(e) => setEvidence(e.target.value)}
                          placeholder="evidence:task-system:checked"
                          aria-label="Independent evidence reference"
                        />
                      </label>
                      <label>
                        <span>New idempotency key (optional — for a fresh action)</span>
                        <input
                          value={newKey}
                          onChange={(e) => setNewKey(e.target.value)}
                          placeholder="fresh-key-..."
                          aria-label="New idempotency key"
                        />
                      </label>
                      {error && <Notice tone="danger">{error}</Notice>}
                      <div className="row row--wrap" style={{ gap: 8 }}>
                        <Button size="sm" variant="primary" disabled={busy}
                          onClick={() => void submit(action.action_id)}>
                          Record reconciliation
                        </Button>
                        <Button size="sm" disabled={busy}
                          onClick={() => setExpanded(null)}>Cancel</Button>
                      </div>
                    </div>
                  ) : (
                    <Button size="sm" onClick={() => {
                      setExpanded(action.action_id);
                      setError('');
                    }}>
                      Reconcile
                    </Button>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}
