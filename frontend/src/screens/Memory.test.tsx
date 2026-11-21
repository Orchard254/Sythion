import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryScreen } from './Memory';

const mock = vi.hoisted(() => ({
  items: [] as Array<Record<string, unknown>>,
  edges: [] as Array<Record<string, unknown>>,
  report: {} as Record<string, unknown>,
  notify: vi.fn(),
}));

vi.mock('../hooks/useBackend', () => ({
  useMemoryReport: () => ({ data: mock.report, isLoading: false }),
  useMemoryEdges: () => ({ data: mock.edges, isLoading: false, refetch: vi.fn() }),
  useMemoryItems: () => ({ data: { items: mock.items, has_more: false }, isLoading: false }),
  useActions: () => ({ reinforceEdge: vi.fn(), removeEdge: vi.fn() }),
}));
vi.mock('../store/appStore', () => ({ useAppStore: (sel: (s: unknown) => unknown) => sel({ notify: mock.notify }) }));

const item = {
  id: 1,
  kind: 'PREFERENCE',
  scope: 'user',
  key: 'favourite colour',
  value: 'green',
  confidence: 0.95,
  verification_status: 'verified',
  source_type: 'conversation',
  observed_at: '2026-11-01T00:00:00Z',
  updated_at: '2026-11-01T00:00:00Z',
  score: 1,
};

const INTERNALS = [
  // raw memory-kind enums / filter chips
  'ASSUMPTION', 'BELIEF', 'FACT', 'INSTRUCTION', 'EPISODIC', 'PREFERENCE', 'PROCEDURAL', 'UNKNOWN',
  // source / verification strings and raw item fields
  'source_type', 'verification_status', 'verified', 'conversation', 'id 1',
  // relation IDs / actions / weights
  'Reinforce relation #', 'Withdraw relation #', 'weight ', 'Relations', 'Needing review',
  // store / injection telemetry
  'Context injection', 'Runs sampled', 'Available memories', 'Relevant injected',
  'Connected links', 'Mean injected', 'Last 50 model-engine runs', 'Context per run',
  'Memory stores', 'Mean items per model turn',
  // paging / offset internals and purge receipts
  'Showing up to 100 items', 'offset ', 'purge receipts', 'real endpoints',
];

describe('Memory screen presentability', () => {
  let view: ReactTestRenderer;

  beforeEach(() => {
    mock.items = [item];
    mock.edges = [];
    mock.report = { counts: { memory_entries: 1 }, memory_context_injection: { runs: 2, totals: { relevant: 4 } } };
    vi.clearAllMocks();
  });

  afterEach(() => { act(() => view?.unmount()); });

  // React splits text into separate nodes ("id ", "1"), so combine the raw
  // serialized tree (covers title/aria props) with flattened visible text.
  const flattenText = (node: unknown): string => {
    if (node == null) return '';
    if (typeof node === 'string' || typeof node === 'number') return String(node);
    if (Array.isArray(node)) return node.map(flattenText).join('');
    if (typeof node === 'object') return flattenText((node as { children?: unknown }).children);
    return '';
  };

  const render = async () => {
    await act(async () => { view = create(<MemoryScreen />); });
    const tree = view.toJSON();
    return `${JSON.stringify(tree)}\n${flattenText(tree)}`;
  };

  it('renders real endpoint items and labels the disabled Edit control honestly', async () => {
    const text = await render();
    expect(text).toContain('favourite colour');
    expect(text).not.toContain('illustrative only');
    expect(text).not.toContain('demo');
    const edit = view.root.findAllByType('button').find((b) => b.children.includes('Edit'))!;
    expect(edit.props.title).toContain('not available in this version');
  });

  it('labels missing controls as unavailable rather than pointing at a missing endpoint', async () => {
    const text = await render();
    expect(text).toContain('not available in this version');
    expect(text).not.toContain('no endpoint exists');
  });

  it('default view shows the human-readable memory list and no raw enums, telemetry or IDs', async () => {
    const text = await render();
    // Owner-meaningful content is present.
    expect(text).toContain('What Sythion remembers');
    expect(text).toContain('favourite colour');
    expect(text).toContain('green');
    expect(text).toContain('Things remembered');
    // No developer internals leak into the default view.
    for (const needle of INTERNALS) {
      expect(text, `default view must not contain ${JSON.stringify(needle)}`).not.toContain(needle);
    }
  });

  it('is honest when there are no memories (no fake "all clear")', async () => {
    mock.items = [];
    mock.report = { counts: { memory_entries: 0 }, memory_context_injection: { runs: 2, totals: { relevant: 4 } } };
    const text = await render();
    expect(text).toContain('Nothing remembered yet');
    expect(text).not.toContain('all clear');
  });

  it('has a single Advanced disclosure that is closed by default and reveals detail when opened', async () => {
    // A stale edge exercises the relation-ID action titles too.
    mock.edges = [{
      id: 5, source_key: 'favourite colour', target_key: 'green', relation: 'RELATES_TO',
      status: 'stale', weight: 0.8, updated_at: '2026-11-01T00:00:00Z',
    }];
    await render();

    const advancedButton = () => view.root.findAllByType('button')
      .find((b) => b.props['data-testid'] === 'memory-advanced')!;

    const toggle = advancedButton();
    expect(toggle).toBeTruthy();
    expect(toggle.props['aria-expanded']).toBe(false);
    // Closed by default: no raw detail in the tree yet.
    expect(JSON.stringify(view.toJSON())).not.toContain('PREFERENCE');
    expect(JSON.stringify(view.toJSON())).not.toContain('Context injection');

    // Open it.
    await act(async () => { toggle.props.onClick(); });

    expect(advancedButton().props['aria-expanded']).toBe(true);
    const tree = view.toJSON();
    const openText = `${JSON.stringify(tree)}\n${flattenText(tree)}`;
    // All the internals moved under Advanced are still present.
    for (const needle of INTERNALS) {
      expect(openText, `Advanced must still expose ${JSON.stringify(needle)}`).toContain(needle);
    }
    expect(openText).toContain('Reinforce relation #5');
    expect(openText).toContain('Withdraw relation #5 from recall');
    // Human-readable memory is still visible too.
    expect(openText).toContain('favourite colour');
  });
});
