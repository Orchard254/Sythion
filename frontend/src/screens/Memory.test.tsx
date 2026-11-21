import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { MemoryScreen } from './Memory';

const mock = vi.hoisted(() => ({
  items: [] as Array<Record<string, unknown>>,
  report: {} as Record<string, unknown>,
  notify: vi.fn(),
}));

vi.mock('../hooks/useBackend', () => ({
  useMemoryReport: () => ({ data: mock.report, isLoading: false }),
  useMemoryEdges: () => ({ data: [], isLoading: false, refetch: vi.fn() }),
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

describe('Memory screen presentability', () => {
  let view: ReactTestRenderer;

  beforeEach(() => {
    mock.items = [item];
    mock.report = { counts: { memory_entries: 1 }, memory_context_injection: { runs: 2, totals: { relevant: 4 } } };
    vi.clearAllMocks();
  });

  afterEach(() => { act(() => view?.unmount()); });

  it('renders real endpoint items and labels the disabled Edit control honestly', async () => {
    await act(async () => { view = create(<MemoryScreen />); });
    const text = JSON.stringify(view.toJSON());
    expect(text).toContain('favourite colour');
    expect(text).not.toContain('illustrative only');
    expect(text).not.toContain('demo');
    const edit = view.root.findAllByType('button').find((b) => b.children.includes('Edit'))!;
    expect(edit.props.title).toContain('not available in this version');
  });

  it('labels missing controls as unavailable rather than pointing at a missing endpoint', async () => {
    await act(async () => { view = create(<MemoryScreen />); });
    const text = JSON.stringify(view.toJSON());
    expect(text).toContain('not available in this version');
    expect(text).not.toContain('no endpoint exists');
  });
});
