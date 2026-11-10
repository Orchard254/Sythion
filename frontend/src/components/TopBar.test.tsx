import { act, create } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TopBar } from './TopBar';
import { useAppStore } from '../store/appStore';
import type { RouteDef } from '../lib/routes';

const shell = vi.hoisted(() => ({
  invoke: vi.fn(async () => {}),
}));

vi.mock('../services/bridge', () => ({ isTauri: () => true }));
vi.mock('@tauri-apps/api/core', () => ({ invoke: shell.invoke }));

const route = {
  id: 'overview',
  label: 'Operations',
  blurb: 'Control Center landing view',
  icon: 'overview',
  group: 'operate',
  data: 'live',
} as RouteDef;

describe('Control Center tray reveal action', () => {
  let view: ReturnType<typeof create>;

  beforeEach(() => {
    vi.clearAllMocks();
    useAppStore.setState({ notifications: [], unreadNotifications: 0 });
    view = create(
      <TopBar
        route={route}
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

  it('marks viewed notifications read but retains notification history', async () => {
    act(() => {
      useAppStore.getState().notify({
        title: 'Task needs attention', tone: 'warn', body: 'Review required',
        route: 'assistant',
      });
      useAppStore.getState().notify({
        title: 'Task failed', tone: 'danger', body: 'Inspect the error',
        route: 'assistant',
      });
    });
    expect(useAppStore.getState().unreadNotifications).toBe(2);
    act(() => {
      view.root.findByProps({ 'aria-label': 'Notifications (2 unread)' }).props.onClick();
    });
    expect(useAppStore.getState().unreadNotifications).toBe(0);
    expect(useAppStore.getState().notifications).toHaveLength(2);
    expect(JSON.stringify(view.toJSON())).toContain('Review required');
    expect(JSON.stringify(view.toJSON())).toContain('Inspect the error');
    act(() => {
      view.root.findByProps({ 'aria-label': 'Dismiss Task failed' }).props.onClick();
    });
    expect(useAppStore.getState().unreadNotifications).toBe(0);
  });

  it('keeps the unread count clear for events received while viewing the tray', async () => {
    act(() => {
      view.root.findByProps({ 'aria-label': 'Notifications' }).props.onClick();
    });
    act(() => {
      useAppStore.getState().notify({ title: 'New task alert', tone: 'warn', body: 'An event arrived' });
    });
    expect(useAppStore.getState().unreadNotifications).toBe(0);
    expect(JSON.stringify(view.toJSON())).toContain('An event arrived');
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