import type { ReactElement } from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { Sidebar } from './Sidebar';
import { TopBar } from './TopBar';
import { routeById } from '../lib/routes';

let view: ReactTestRenderer;
const render = async (node: ReactElement) => {
  await act(async () => { view = create(node); });
};
const labels = (node: { children: unknown[] }): string =>
  node.children.map((c): string =>
    typeof c === 'string' ? c :
    c && typeof c === 'object' && 'children' in c
      ? labels(c as { children: unknown[] }) : '').join('');
afterEach(() => { if (view) act(() => view.unmount()); });

describe('main shell in cloud answer-only mode', () => {
  it('shows the normal sidebar but disables all execution-related destinations', async () => {
    const navigate = vi.fn();
    const settings = vi.fn();
    await render(<Sidebar
      route="assistant" onNavigate={navigate} onOpenSettings={settings}
      pendingApprovals={0} agent={undefined} overallHealth={undefined}
      connected runtimeState="healthy" runtimeAgeMs={0} version="test"
      cloudAnswerOnly
    />);
    const buttons = view.root.findAllByType('button');
    const assistant = buttons.find((b) => labels(b) === 'AI Assistant');
    expect(assistant?.props.disabled).toBe(false);
    const locked = ['Operations', 'Goals', 'Memory & Knowledge', 'Automations',
      'Security & Control', 'System & Integrations'];
    for (const label of locked) {
      const button = buttons.find((b) => labels(b) === label);
      expect(button, label).toBeDefined();
      expect(button?.props.disabled).toBe(true);
    }
    expect(buttons.find((b) => labels(b) === 'Cloud Text Chat')).toBeUndefined();
    const settingsButton = buttons.find((button) => button.props['aria-label'] === 'Settings');
    expect(settingsButton?.props.disabled).not.toBe(true);
    await act(async () => settingsButton?.props.onClick());
    expect(settings).toHaveBeenCalledOnce();
    expect(navigate).not.toHaveBeenCalled();
    expect(labels(view.root)).toContain('Answer only');
  });

  it('does not expose Companion or notification actions in the cloud top bar', async () => {
    const navigate = vi.fn();
    await render(<TopBar
      route={routeById('assistant')} connected runtimeState="healthy"
      attentionCount={0} onNavigate={navigate} cloudAnswerOnly
    />);
    expect(labels(view.root)).toContain('AI Assistant');
    expect(labels(view.root)).not.toContain('Reveal Companion');
    const buttons = view.root.findAllByType('button');
    expect(buttons.some((button) =>
      button.props['aria-label']?.toString().includes('Notifications'))).toBe(false);
    expect(navigate).not.toHaveBeenCalled();
  });
});
