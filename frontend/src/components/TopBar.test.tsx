import { act, create } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TopBar } from './TopBar';
import type { RouteDef } from '../lib/routes';

const shell = vi.hoisted(() => ({
  invoke: vi.fn(async () => {}),
}));

vi.mock('../services/bridge', () => ({ isTauri: () => true }));
vi.mock('@tauri-apps/api/core', () => ({ invoke: shell.invoke }));

const route = {
  id: 'operations',
  label: 'Operations',
  blurb: 'Control Center landing view',
  icon: 'home',
  group: 'main',
  data: 'live',
} as RouteDef;

describe('Control Center tray reveal action', () => {
  let view: ReturnType<typeof create>;

  beforeEach(() => {
    vi.clearAllMocks();
    view = create(
      <TopBar
        route={route}
        origin="live"
        connected={true}
        runtimeState="healthy"
        attentionCount={0}
        onNavigate={vi.fn()}
      />,
    );
  });

  afterEach(() => {
    act(() => view.unmount());
  });

  it('reaches the real reveal command from the tray entry point', async () => {
    const button = view.root
      .findAllByType('button')
      .find((b) => (b.children || []).some((c: unknown) => c === 'Reveal Companion'));
    expect(button).toBeDefined();

    await act(async () => {
      button!.props.onClick();
    });

    await vi.waitFor(() =>
      expect(shell.invoke).toHaveBeenCalledWith('set_companion_visible', { visible: true }),
    );
  });
});