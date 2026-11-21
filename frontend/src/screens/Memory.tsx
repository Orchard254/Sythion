import { useMemo, useState } from 'react';
import { Icon } from '../components/icons';
import {
  Badge,
  Bar,
  Button,
  Chips,
  EmptyState,
  KV,
  LoadingBlock,
  Panel,
  Rail,
  ScreenHead,
  SearchInput,
  Stat,
} from '../components/ui';
import { useActions, useMemoryEdges, useMemoryItems, useMemoryReport } from '../hooks/useBackend';
import { useAppStore } from '../store/appStore';
import { humanize, relTime } from '../lib/format';

/* Memory & Knowledge.
 *
 * The default view is the owner-facing one: "what Sythion remembers", in plain
 * language, plus a short honest note about the available controls. Everything
 * that is really a developer/diagnostic concern (raw kind enums, source and
 * verification fields, relation IDs and actions, store/injection telemetry,
 * paging offsets) lives behind a single collapsed "Advanced" disclosure.
 *
 * All panels are backed by real read endpoints:
 *  - GET /api/v1/memory/report  — store counts, injection stats, relation vocabulary
 *  - GET /api/v1/memory/edges   — individual relations (real reinforce/withdraw endpoints)
 *  - GET /api/v1/memory/items   — the "What it remembers" item browser (paged, searchable)
 *
 * This is presentation only: no read/write behaviour, permissions or data changed.
 */

const EDGE_TONE: Record<string, 'ok' | 'warn' | 'idle'> = {
  active: 'ok',
  stale: 'warn',
  removed: 'idle',
};

export function MemoryScreen() {
  const report = useMemoryReport();
  const edges = useMemoryEdges();
  const actions = useActions();
  const notify = useAppStore((s) => s.notify);

  const [search, setSearch] = useState('');
  const [kind, setKind] = useState<'__all__' | string>('__all__');
  const [busyEdge, setBusyEdge] = useState<number | null>(null);
  const [offset, setOffset] = useState(0);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const memoryItems = useMemoryItems({
    query: search.trim(),
    kind: kind !== '__all__' ? kind : undefined,
    limit: 100,
    offset,
  });

  // The current /memory/report contract is counts/memory_context_injection.
  // Older payloads may also carry stores/relations/mean_items_injected. Read
  // both shapes so the figures render correctly whichever the backend returns.
  const stores = (report.data?.stores ?? {}) as Record<string, { count?: number }>;
  const storeEntries = Object.entries(stores).filter(([, v]) => typeof v?.count === 'number');
  const counts = (report.data?.counts ?? {}) as { memory_entries?: number };
  const totalItems = storeEntries.length > 0
    ? storeEntries.reduce((n, [, v]) => n + (v.count ?? 0), 0)
    : typeof counts.memory_entries === 'number' ? counts.memory_entries : undefined;

  const relationStats = (report.data?.relations ?? {}) as Record<string, number>;
  const injectionRaw = (report.data?.memory_context_injection ?? {}) as Record<string, unknown>;
  const injectionRuns = typeof injectionRaw.runs === 'number' ? injectionRaw.runs : undefined;
  const injectionTotals = (injectionRaw.totals ?? {}) as {
    available?: number; relevant?: number; connected?: number; links?: number;
  };
  const meanRelevant = typeof injectionRaw.mean_items_injected === 'number'
    ? injectionRaw.mean_items_injected
    : (injectionRuns && injectionRuns > 0 && typeof injectionTotals.relevant === 'number')
      ? injectionTotals.relevant / injectionRuns
      : undefined;

  const edgeList = edges.data ?? [];
  const itemsData = useMemo(() => memoryItems.data?.items ?? [], [memoryItems.data]);
  const hasMore = memoryItems.data?.has_more ?? false;
  const kinds = useMemo(() => [
    "ASSUMPTION",
    "BELIEF",
    "FACT",
    "INSTRUCTION",
    "EPISODIC",
    "PREFERENCE",
    "PROCEDURAL",
    "UNKNOWN",
  ], []);

  const itemList = itemsData;

  const actOnEdge = async (id: number, action: 'reinforce' | 'remove') => {
    setBusyEdge(id);
    try {
      if (action === 'reinforce') await actions.reinforceEdge(id);
      else await actions.removeEdge(id);
      edges.refetch();
      notify({
        tone: 'ok',
        title: action === 'reinforce' ? 'Relation reinforced' : 'Relation withdrawn from recall',
        body: `Memory edge #${id} updated.`,
      });
    } catch (err) {
      notify({
        tone: 'danger',
        title: 'Update failed',
        body: err instanceof Error ? err.message : 'Unknown error',
      });
    } finally {
      setBusyEdge(null);
    }
  };

  return (
    <div className="screen">
      <div className="screen__inner">
        <ScreenHead
          eyebrow="Personal"
          title="Memory & Knowledge"
          description="What Sythion knows about you — and how to correct it."
        />

        {/* Owner-facing summary — no internal counters or model telemetry. */}
        <div className="grid grid--4">
          <Stat
            label="Things remembered"
            icon="database"
            value={report.isLoading ? '—' : (typeof totalItems === 'number' ? totalItems : '—')}
            foot="Facts and preferences about you"
            tone="accent"
          />
        </div>

        {memoryItems.isLoading ? (
          <LoadingBlock rows={3} />
        ) : itemList.length === 0 ? (
          <EmptyState
            icon="memory"
            title="Nothing remembered yet"
            description="As you talk with Sythion, facts and preferences you share will appear here."
          />
        ) : null}

        <div className="grid grid--aside">
          <div className="stack">
            <Panel title="Find a memory" icon="search" tight>
              <div className="stack stack--tight">
                <SearchInput value={search} onChange={(v) => { setSearch(v); setOffset(0); }} placeholder="Search memory…" />
              </div>
            </Panel>

            <Panel title="Your controls" icon="shield" tight>
              <div className="stack stack--tight">
                <span className="row t-muted" style={{ gap: 7, alignItems: 'flex-start' }}>
                  <Icon name="close" size={12} style={{ color: 'var(--text-3)', flex: '0 0 auto', marginTop: 2 }} />
                  <span>Edit an individual remembered item — not available in this version</span>
                </span>
                <span className="row t-muted" style={{ gap: 7, alignItems: 'flex-start' }}>
                  <Icon name="close" size={12} style={{ color: 'var(--text-3)', flex: '0 0 auto', marginTop: 2 }} />
                  <span>Bulk export — not available in this version</span>
                </span>
              </div>
            </Panel>
          </div>

          <Panel title="What Sythion remembers" icon="memory" flush>
            {itemList.length === 0 ? (
              <EmptyState
                icon="search"
                title="Nothing to show"
                description="Try a different search term."
              />
            ) : (
              <div className="list">
                {itemList.map((m) => (
                  <div className="list__row" key={m.id}>
                    <Rail
                      tone={m.confidence == null ? 'idle' : (m.confidence ?? 0) >= 0.9 ? 'ok' : (m.confidence ?? 0) >= 0.7 ? 'accent' : 'warn'}
                    />
                    <div className="list__main">
                      <div className="row row--between" style={{ gap: 10 }}>
                        <span className="t-title t-truncate" style={{ fontSize: 12.5 }}>
                          {m.key}
                        </span>
                      </div>
                      <span className="t-muted">{m.value}</span>
                      {m.updated_at ? (
                        <div className="list__meta">
                          <span>Remembered {relTime(m.updated_at)}</span>
                        </div>
                      ) : null}
                    </div>
                    <div className="list__trail">
                      <Button
                        size="sm"
                        variant="ghost"
                        disabled
                        title="Editing an individual remembered item is not available in this version."
                      >
                        Edit
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Panel>
        </div>

        {/* Single, clearly-labelled disclosure. Closed by default; nothing here
            is deleted — it is all still reachable for diagnostics. */}
        <div className="panel" style={{ marginTop: 14 }}>
          <div className="panel__head">
            <Button
              size="sm"
              variant="ghost"
              aria-expanded={advancedOpen}
              data-testid="memory-advanced"
              onClick={() => setAdvancedOpen((v) => !v)}
            >
              {advancedOpen ? 'Hide advanced details' : 'Advanced — diagnostic details'}
            </Button>
          </div>
          {advancedOpen && (
            <div className="panel__body stack">
              <div className="grid grid--3">
                <Stat
                  label="Relations"
                  icon="link"
                  value={report.isLoading ? '—' : (relationStats.edges ?? edgeList.length)}
                  foot="Links between remembered things"
                />
                <Stat
                  label="Needing review"
                  icon="alert"
                  value={report.isLoading ? '—' : (relationStats.stale ?? edgeList.filter((e) => e.status === 'stale').length)}
                  tone="warn"
                  foot="Stale relations awaiting your call"
                />
                <Stat
                  label="Context per run"
                  icon="brain"
                  value={report.isLoading ? '—' : (typeof meanRelevant === 'number' ? meanRelevant.toFixed(1) : '—')}
                  suffix="items"
                  foot="Mean items per model turn"
                />
              </div>

              <Panel title="Raw item data" icon="database" tight>
                <p className="t-faint" style={{ marginTop: 0 }}>
                  The same remembered items with their internal fields, for diagnostics.
                </p>
                <div className="stack stack--tight">
                  <Chips
                    value={kind}
                    onChange={(v) => { setKind(v); setOffset(0); }}
                    options={kinds.map((k) => ({ value: k, label: k }))}
                  />
                  {itemList.length === 0 ? (
                    <EmptyState icon="search" title="No raw items to show" />
                  ) : (
                    <div className="list">
                      {itemList.map((m) => (
                        <div className="list__row" key={`raw-${m.id}`}>
                          <Rail
                            tone={m.confidence == null ? 'idle' : (m.confidence ?? 0) >= 0.9 ? 'ok' : (m.confidence ?? 0) >= 0.7 ? 'accent' : 'warn'}
                          />
                          <div className="list__main">
                            <div className="row row--wrap" style={{ gap: 7, alignItems: 'center' }}>
                              <span className="t-title t-truncate" style={{ fontSize: 12.5 }}>
                                {m.key}
                              </span>
                              <Badge tone="plain">{m.kind}</Badge>
                            </div>
                            <span className="t-muted">{m.value}</span>
                            <div className="list__meta">
                              <span>id {m.id}</span>
                              <span>• source_type {m.source_type}</span>
                              <span>• verification_status {m.verification_status}</span>
                              {m.updated_at ? <span>• {relTime(m.updated_at)}</span> : null}
                            </div>
                            {m.confidence != null ? (
                              <div className="row" style={{ gap: 8, marginTop: 4, maxWidth: 240 }}>
                                <div style={{ flex: 1 }}>
                                  <Bar
                                    value={m.confidence * 100}
                                    tone={(m.confidence ?? 0) >= 0.9 ? 'ok' : (m.confidence ?? 0) >= 0.7 ? 'accent' : 'warn'}
                                  />
                                </div>
                                <span className="t-faint t-mono">{Math.round(m.confidence * 100)}%</span>
                              </div>
                            ) : (
                              <span className="t-faint t-mono">Confidence unassessed</span>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                  {itemList.length > 0 && (
                    <div className="row row--between" style={{ gap: 8 }}>
                      <span className="t-faint">
                        Showing up to 100 items (offset {offset}). Keyword match over key and value.
                      </span>
                      <div className="row" style={{ gap: 6 }}>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={offset === 0}
                          onClick={() => setOffset((v) => Math.max(0, v - 100))}
                        >
                          Previous
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          disabled={!hasMore}
                          onClick={() => setOffset((v) => v + 100)}
                        >
                          Next
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              </Panel>

              <Panel title="Memory relations" icon="link" flush>
                <div className="panel__body--tight">
                  <p className="t-faint">
                    Unused relations surface as <strong>stale</strong> for review; they are never auto-deleted. Withdrawing
                    removes them from recall but keeps the record.
                  </p>
                </div>
                {edges.isLoading ? (
                  <div className="panel__body">
                    <LoadingBlock rows={4} />
                  </div>
                ) : edgeList.length === 0 ? (
                  <EmptyState icon="link" title="No relations recorded" />
                ) : (
                  <div className="list">
                    {edgeList.map((e) => (
                      <div className="list__row" key={e.id}>
                        <Rail tone={EDGE_TONE[e.status] ?? 'idle'} />
                        <div className="list__main">
                          <div className="row row--wrap" style={{ gap: 7, alignItems: 'center' }}>
                            <span className="t-mono" style={{ color: 'var(--text-0)', fontSize: 11.5 }}>
                              {e.source_key}
                            </span>
                            <Badge tone="plain">{humanize(e.relation)}</Badge>
                            <span className="t-mono" style={{ color: 'var(--text-0)', fontSize: 11.5 }}>
                              {e.target_key}
                            </span>
                          </div>
                          <div className="list__meta">
                            <Badge tone={EDGE_TONE[e.status] ?? 'idle'}>{e.status}</Badge>
                            <span>weight {e.weight.toFixed(2)}</span>
                            <span>· updated {relTime(e.updated_at)}</span>
                          </div>
                          <div style={{ maxWidth: 220, marginTop: 3 }}>
                            <Bar
                              value={e.weight * 100}
                              tone={e.status === 'stale' ? 'warn' : e.weight > 0.7 ? 'ok' : 'info'}
                            />
                          </div>
                        </div>
                        <div className="list__trail">
                          {e.status === 'stale' && (
                            <Button
                              size="sm"
                              variant="subtle"
                              disabled={busyEdge === e.id}
                              title={`Reinforce relation #${e.id}`}
                              onClick={() => void actOnEdge(e.id, 'reinforce')}
                            >
                              Reinforce
                            </Button>
                          )}
                          {e.status !== 'removed' && (
                            <Button
                              size="sm"
                              variant="ghost"
                              icon="trash"
                              disabled={busyEdge === e.id}
                              title={`Withdraw relation #${e.id} from recall`}
                              onClick={() => void actOnEdge(e.id, 'remove')}
                            >
                              Withdraw
                            </Button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </Panel>

              <div className="grid grid--2">
                <Panel title="Memory stores" icon="database">
                  {report.isLoading ? (
                    <LoadingBlock rows={4} />
                  ) : storeEntries.length === 0 && typeof counts.memory_entries === 'number' ? (
                    <div className="stack stack--tight">
                      <div className="row row--between">
                        <span className="t-body">memory (general)</span>
                        <span className="t-mono t-title">{counts.memory_entries}</span>
                      </div>
                      <span className="t-faint" style={{ marginTop: 4 }}>
                        Scoped stores are not exposed by this backend; this is the single shared store.
                      </span>
                    </div>
                  ) : storeEntries.length === 0 ? (
                    <EmptyState icon="database" title="No store statistics available" />
                  ) : (
                    <div className="stack stack--tight">
                      {storeEntries.map(([name, v]) => (
                        <div key={name} className="row row--between">
                          <span className="t-body">{humanize(name)}</span>
                          <span className="t-mono t-title">{v.count}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </Panel>

                <Panel title="Context injection" icon="brain">
                  {report.isLoading ? (
                    <LoadingBlock rows={3} />
                  ) : (
                    <KV
                      items={injectionRuns !== undefined
                        ? [
                            { k: 'Runs sampled', v: String(injectionRuns) },
                            { k: 'Available memories (window)', v: String(injectionTotals.available ?? '—') },
                            { k: 'Relevant injected (window)', v: String(injectionTotals.relevant ?? '—') },
                            { k: 'Connected links (window)', v: String(injectionTotals.connected ?? 0) },
                            { k: 'Mean injected per run', v: typeof meanRelevant === 'number' ? meanRelevant.toFixed(2) : '—' },
                          ]
                        : Object.entries(injectionRaw).map(([k, v]) => ({
                            k: humanize(k),
                            v: typeof v === 'number' ? v.toFixed(1) : String(v),
                          }))}
                    />
                  )}
                  <p className="t-faint" style={{ marginTop: 10 }}>
                    Last 50 model-engine runs — what Sythion recalls per turn.
                  </p>
                </Panel>
              </div>

              <Panel title="Technical controls" icon="shield" tight>
                <div className="stack stack--tight">
                  <span className="row t-muted" style={{ gap: 7, alignItems: 'flex-start' }}>
                    <Icon name="check" size={12} style={{ color: 'var(--ok)', flex: '0 0 auto', marginTop: 2 }} />
                    <span>Reinforce or withdraw a relation (real endpoints)</span>
                  </span>
                  <p className="t-faint" style={{ marginTop: 4 }}>
                    Purging is never automatic; purge receipts appear under System &amp; Integrations.
                  </p>
                </div>
              </Panel>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
