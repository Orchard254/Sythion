import { useMemo, useState } from 'react';
import { Icon } from '../components/icons';
import {
  Badge,
  Bar,
  Button,
  Chips,
  DataBadge,
  DemoTag,
  EmptyState,
  KV,
  LoadingBlock,
  Notice,
  Panel,
  Rail,
  ScreenHead,
  SearchInput,
  Segmented,
  Stat,
} from '../components/ui';
import { useActions, useMemoryEdges, useMemoryReport } from '../hooks/useBackend';
import { useAppStore } from '../store/appStore';
import { demoMemoryItems } from '../data/demo';
import { humanize, relTime } from '../lib/format';

/* Memory & Knowledge.
 *
 * Live: GET /api/v1/memory/report (store counts, injection stats, relation
 * vocabulary) and GET /api/v1/memory/edges (individual relations, with real
 * reinforce/remove review endpoints).
 * Demo: the item browser — there is no REST endpoint that lists individual
 * remembered facts, so the per-item view is illustrative only.
 */

type Tab = 'items' | 'relations' | 'stores';

const EDGE_TONE: Record<string, 'ok' | 'warn' | 'idle'> = {
  active: 'ok',
  stale: 'warn',
  removed: 'idle',
};

export function MemoryScreen() {
  const report = useMemoryReport();
  const edges = useMemoryEdges();
  const actions = useActions();
  const dataMode = useAppStore((s) => s.dataMode);
  const notify = useAppStore((s) => s.notify);

  const [tab, setTab] = useState<Tab>('items');
  const [search, setSearch] = useState('');
  const [kind, setKind] = useState<'__all__' | string>('__all__');
  const [busyEdge, setBusyEdge] = useState<number | null>(null);

  const stores = (report.data?.stores ?? {}) as Record<string, { count?: number }>;
  const storeEntries = Object.entries(stores).filter(([, v]) => typeof v?.count === 'number');
  const totalItems = storeEntries.reduce((n, [, v]) => n + (v.count ?? 0), 0);

  const relationStats = (report.data?.relations ?? {}) as Record<string, number>;
  const injection = (report.data?.memory_context_injection ?? {}) as Record<string, number>;

  const edgeList = edges.data ?? [];
  const kinds = useMemo(() => Array.from(new Set(demoMemoryItems.map((m) => m.kind))), []);

  const itemList = useMemo(() => {
    const q = search.trim().toLowerCase();
    return demoMemoryItems.filter((m) => {
      if (kind !== '__all__' && m.kind !== kind) return false;
      if (!q) return true;
      return (
        m.key.toLowerCase().includes(q) ||
        m.value.toLowerCase().includes(q) ||
        m.source.toLowerCase().includes(q)
      );
    });
  }, [search, kind]);

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
          description="Everything Sythion believes about you, where it came from, and how confident it is — with the controls to correct it."
          actions={
            <>
              {dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
              <Segmented
                value={tab}
                onChange={setTab}
                ariaLabel="Memory view"
                options={[
                  { value: 'items', label: 'What it remembers' },
                  { value: 'relations', label: 'Relations' },
                  { value: 'stores', label: 'Stores' },
                ]}
              />
            </>
          }
        />

        <div className="grid grid--4">
          <Stat
            label="Stored items"
            icon="database"
            value={report.isLoading ? '—' : totalItems}
            foot="Across all memory stores"
            tone="accent"
          />
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
            value={report.isLoading ? '—' : (injection.mean_items_injected ?? '—')}
            suffix="items"
            foot="Mean items injected into model context"
          />
        </div>

        {tab === 'items' && (
          <>
            <Notice tone="warn" icon="alert">
              <span>
                <strong>The item browser is a demonstration.</strong> Sythion has no REST endpoint that lists
                individual remembered facts, so these rows illustrate the intended review experience. Store totals and
                relation counts elsewhere on this screen are live.
              </span>
            </Notice>

            <div className="grid grid--aside">
              <div className="stack">
                <Panel title="Search & filter" icon="search" tight>
                  <div className="stack stack--tight">
                    <SearchInput value={search} onChange={setSearch} placeholder="Search memory…" />
                    <Chips
                      value={kind}
                      onChange={setKind}
                      options={kinds.map((k) => ({ value: k, label: k }))}
                    />
                  </div>
                </Panel>

                <Panel title="Your controls" icon="shield" tight>
                  <div className="stack stack--tight">
                    <span className="row t-muted" style={{ gap: 7, alignItems: 'flex-start' }}>
                      <Icon name="check" size={12} style={{ color: 'var(--ok)', flex: '0 0 auto', marginTop: 2 }} />
                      <span>Reinforce or withdraw a relation (real endpoints)</span>
                    </span>
                    <span className="row t-muted" style={{ gap: 7, alignItems: 'flex-start' }}>
                      <Icon name="close" size={12} style={{ color: 'var(--text-3)', flex: '0 0 auto', marginTop: 2 }} />
                      <span>Edit an individual remembered item — no endpoint exists</span>
                    </span>
                    <span className="row t-muted" style={{ gap: 7, alignItems: 'flex-start' }}>
                      <Icon name="close" size={12} style={{ color: 'var(--text-3)', flex: '0 0 auto', marginTop: 2 }} />
                      <span>Bulk export — no endpoint exists</span>
                    </span>
                    <p className="t-faint" style={{ marginTop: 4 }}>
                      Retention purging is real but automatic: receipts are listed under System &amp; Integrations.
                    </p>
                  </div>
                </Panel>
              </div>

              <Panel title="Remembered items" icon="memory" flush actions={<DemoTag />}>
                {itemList.length === 0 ? (
                  <EmptyState
                    icon="search"
                    title="Nothing matches"
                    description="Try a different search term or category."
                  />
                ) : (
                  <div className="list">
                    {itemList.map((m) => (
                      <div className="list__row" key={m.id}>
                        <Rail
                          tone={m.confidence >= 0.9 ? 'ok' : m.confidence >= 0.7 ? 'accent' : 'warn'}
                        />
                        <div className="list__main">
                          <div className="row row--between" style={{ gap: 10 }}>
                            <span className="t-title t-truncate" style={{ fontSize: 12.5 }}>
                              {m.key}
                            </span>
                            <Badge tone="plain">{m.kind}</Badge>
                          </div>
                          <span className="t-muted">{m.value}</span>
                          <div className="list__meta">
                            <span>Source: {m.source}</span>
                            <span>· used {m.used}×</span>
                            <span>· {relTime(m.updated)}</span>
                          </div>
                          <div className="row" style={{ gap: 8, marginTop: 4, maxWidth: 240 }}>
                            <div style={{ flex: 1 }}>
                              <Bar
                                value={m.confidence * 100}
                                tone={m.confidence >= 0.9 ? 'ok' : m.confidence >= 0.7 ? 'accent' : 'warn'}
                              />
                            </div>
                            <span className="t-faint t-mono">{Math.round(m.confidence * 100)}%</span>
                          </div>
                        </div>
                        <div className="list__trail">
                          <Button size="sm" variant="ghost" disabled>
                            Edit
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </Panel>
            </div>
          </>
        )}

        {tab === 'relations' && (
          <Panel
            title="Memory relations"
            icon="link"
            flush
            actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
          >
            <div className="panel__body--tight">
              <p className="t-faint">
                Relations decay in weight when unused and surface as <strong>stale</strong> for your review. They are
                never auto-deleted. Withdrawing a relation removes it from recall walks but keeps the record.
              </p>
            </div>
            {edges.isLoading ? (
              <div className="panel__body">
                <LoadingBlock rows={4} />
              </div>
            ) : edgeList.length === 0 ? (
              <EmptyState
                icon="link"
                title="No relations recorded"
                description="Relations appear once Sythion has linked remembered items to each other."
              />
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
                          disabled={busyEdge === e.id || dataMode === 'preview'}
                          title={
                            dataMode === 'preview'
                              ? 'Preview mode — no backend to reinforce against'
                              : `Reinforce relation #${e.id}`
                          }
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
                          disabled={busyEdge === e.id || dataMode === 'preview'}
                          title={
                            dataMode === 'preview'
                              ? 'Preview mode — no backend to withdraw from'
                              : `Withdraw relation #${e.id} from recall`
                          }
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
        )}

        {tab === 'stores' && (
          <div className="grid grid--2">
            <Panel
              title="Memory stores"
              icon="database"
              actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}
            >
              {report.isLoading ? (
                <LoadingBlock rows={4} />
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

            <Panel title="Context injection" icon="brain" actions={dataMode === 'preview' ? <DemoTag /> : <DataBadge origin="live" />}>
              {report.isLoading ? (
                <LoadingBlock rows={3} />
              ) : (
                <KV
                  items={Object.entries(injection).map(([k, v]) => ({
                    k: humanize(k),
                    v: typeof v === 'number' ? v.toFixed(1) : String(v),
                  }))}
                />
              )}
              <p className="t-faint" style={{ marginTop: 10 }}>
                Sampled from the last 50 model-engine rollout runs. This is what Sythion actually recalls per turn.
              </p>
            </Panel>
          </div>
        )}
      </div>
    </div>
  );
}