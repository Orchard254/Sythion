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

/** Owner-facing names for lifecycle states — no internal identifiers. */
const STATE_LABEL: Record<RemoteAction['state'], string> = {
  received: 'Received',
  understanding: 'Understanding',
  planned: 'Planned',
  awaiting_approval: 'Awaiting your approval',
  executing: 'Running',
  verifying: 'Verifying',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
  uncertain: 'Needs review',
};

type Outcome = 'effect_confirmed' | 'effect_not_performed' | 'abandoned';

/**
 * The single question an owner is asked, with plain answers mapped to the
 * backend's existing outcome values. "I'm not sure" fails closed to `abandoned`:
 * it never asserts the effect succeeded and never starts a fresh action.
 */
const CHOICES: Array<{ label: string; outcome: Outcome; hint: string }> = [
  { label: 'It worked', outcome: 'effect_confirmed', hint: 'The action reached the device.' },
  { label: "It didn't", outcome: 'effect_not_performed', hint: 'The action did not reach the device.' },
  { label: "I'm not sure", outcome: 'abandoned', hint: 'Leave it unsettled. Nothing will be retried.' },
];

const OUTCOME_LABEL: Record<Outcome, string> = {
  effect_confirmed: 'It worked',
  effect_not_performed: "It didn't go through",
  abandoned: 'Left unsettled',
};

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
 * The default view shows only plain language: no action ids, device ids, task
 * ids, evidence references or idempotency keys. Resolving an uncertain action
 * is one plain question whose answers map to the backend's existing outcomes;
 * the decision id and any other backend fields stay hidden implementation
 * detail. Reconciliation never revives or replays the uncertain effect.
 */
export function RemoteActionsPanel({ actions, onReconcile, onReconciled, loadHistory }: Props) {
  const [expanded, setExpanded] = useState<string | null>(null);
  const [selectedOutcome, setSelectedOutcome] = useState<Outcome | null>(null);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [historyFor, setHistoryFor] = useState<string | null>(null);
  const [history, setHistory] = useState<Record<string, ReconciliationRecord[]>>({});
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

  const resetResolve = () => {
    setExpanded(null);
    setSelectedOutcome(null);
    setAdvancedOpen(false);
    setEvidence('');
    setNewKey('');
    setError('');
  };

  const openResolve = (actionId: string) => {
    setExpanded(actionId);
    setSelectedOutcome(null);
    setAdvancedOpen(false);
    setEvidence('');
    setNewKey('');
    setError('');
  };

  const submit = async (actionId: string) => {
    if (!selectedOutcome) return;
    const handler = onReconcile ?? reconciliationService.reconcile;
    setBusy(true);
    setError('');
    try {
      await handler(
        actionId,
        newDecisionId(),
        selectedOutcome,
        evidence.trim() || undefined,
        newKey.trim() || undefined,
      );
      resetResolve();
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
                <strong>{action.uncertain ? 'Action needs your review' : 'Device action'}</strong>
                <Badge tone={action.uncertain ? 'danger' : STATE_TONE[action.state]}>
                  {action.uncertain ? 'Needs review' : STATE_LABEL[action.state]}
                </Badge>
              </div>
              {action.reason && <span>{action.reason}</span>}

              <button
                type="button"
                className="stat stat--clickable"
                data-testid={`history-toggle-${action.action_id}`}
                onClick={() => setHistoryFor(historyFor === action.action_id ? null : action.action_id)}
              >
                {historyFor === action.action_id ? 'Hide past decisions' : 'See past decisions'}
              </button>

              {historyFor === action.action_id && (
                <div className="stack stack--tight" data-testid="reconciliation-history">
                  {!history[action.action_id] ? (
                    <span className="t-faint">Loading history…</span>
                  ) : history[action.action_id].length === 0 ? (
                    <EmptyState title="No decisions yet" description="Nothing has been recorded for this action." />
                  ) : (
                    history[action.action_id].map((record) => (
                      <div key={record.id} className="stack stack--tight">
                        <Badge tone="info">
                          {OUTCOME_LABEL[record.outcome as Outcome] ?? record.outcome}
                        </Badge>
                        {record.note && <span>{record.note}</span>}
                        {record.created_at && <span className="t-faint">{record.created_at}</span>}
                      </div>
                    ))
                  )}
                </div>
              )}

              {action.uncertain && (
                <div className="stack stack--tight" style={{ marginTop: 8 }}>
                  <Notice tone="warn">
                    We're not sure this action reached the device. Resolving it
                    never repeats the original action.
                  </Notice>
                  {expanded === action.action_id ? (
                    <div className="stack stack--tight">
                      <span>Did this action go through?</span>
                      <div className="row row--wrap" role="group" aria-label="Did this action go through?">
                        {CHOICES.map((choice) => (
                          <Button
                            key={choice.outcome}
                            size="sm"
                            variant={selectedOutcome === choice.outcome ? 'primary' : 'default'}
                            aria-pressed={selectedOutcome === choice.outcome}
                            title={choice.hint}
                            onClick={() => { setSelectedOutcome(choice.outcome); setError(''); }}
                          >
                            {choice.label}
                          </Button>
                        ))}
                      </div>

                      <Button
                        size="sm"
                        variant="ghost"
                        aria-expanded={advancedOpen}
                        onClick={() => setAdvancedOpen(!advancedOpen)}
                      >
                        {advancedOpen ? 'Hide advanced options' : 'Advanced / for support'}
                      </Button>
                      {advancedOpen && (
                        <div className="stack stack--tight" data-testid="reconcile-advanced">
                          <span className="t-faint">
                            Optional, for support only. The decision is recorded without these.
                          </span>
                          <label>
                            <span>Evidence reference</span>
                            <input
                              value={evidence}
                              onChange={(e) => setEvidence(e.target.value)}
                              placeholder="evidence:task-system:checked"
                              aria-label="Independent evidence reference"
                            />
                          </label>
                          <label>
                            <span>New idempotency key (starts a fresh action)</span>
                            <input
                              value={newKey}
                              onChange={(e) => setNewKey(e.target.value)}
                              placeholder="fresh-key-..."
                              aria-label="New idempotency key"
                            />
                          </label>
                        </div>
                      )}

                      {error && <Notice tone="danger">{error}</Notice>}
                      <div className="row row--wrap" style={{ gap: 8 }}>
                        <Button
                          size="sm"
                          variant="primary"
                          disabled={busy || !selectedOutcome}
                          onClick={() => void submit(action.action_id)}
                        >
                          Record decision
                        </Button>
                        <Button size="sm" disabled={busy} onClick={resetResolve}>Cancel</Button>
                      </div>
                    </div>
                  ) : (
                    <Button size="sm" onClick={() => openResolve(action.action_id)}>
                      Resolve this action
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
