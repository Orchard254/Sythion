import { act, create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import { CompanionSurface } from './CompanionSurface';
import { activateCompanion } from '../services/companion';

const shell = vi.hoisted(() => ({
  visible: true,
  close: vi.fn(),
  invoke: vi.fn(async (command: string, args?: { visible?: boolean }) => {
    if (command === 'set_companion_visible') shell.visible = Boolean(args?.visible);
  }),
}));

vi.mock('@tauri-apps/api/core', () => ({ invoke: shell.invoke }));
vi.mock('@tauri-apps/api/window', () => ({ getCurrentWindow: () => ({ close: shell.close }) }));
vi.mock('../services/bridge', () => ({ isTauri: () => true }));
vi.mock('../hooks/useBackend', () => ({
  useVoiceAssets: () => ({ data: undefined }),
  useActions: () => ({ live: false }),
}));
vi.mock('../store/appStore', () => ({
  useAppStore: (select: (state: unknown) => unknown) => select({ dataMode: 'live', setTranscriptDraft: vi.fn() }),
}));
vi.mock('./Companion', () => ({
  Companion: ({ onClose }: { onClose: () => void }) => <button onClick={onClose}>Dismiss</button>,
}));

describe('Companion window lifecycle', () => {
  it('hides on dismissal and can subsequently be revealed', async () => {
    const view = create(<CompanionSurface />);
    await act(async () => {
      view.root.findByType('button').props.onClick();
    });
    await vi.waitFor(() => expect(shell.invoke).toHaveBeenCalledWith('set_companion_visible', { visible: false }));
    expect(shell.visible).toBe(false);
    expect(shell.close).not.toHaveBeenCalled();

    await act(async () => { await activateCompanion(); });
    expect(shell.invoke).toHaveBeenCalledWith('set_companion_visible', { visible: true });
    expect(shell.visible).toBe(true);
    view.unmount();
  });
});

describe('Companion window lifecycle – hide failure', () => {
  it('does not silently swallow a failed hide and records the failure', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const view = create(<CompanionSurface />);
    shell.invoke.mockRejectedValueOnce(new Error('hide failed'));

    await act(async () => {
      view.root.findByType('button').props.onClick();
    });

    await vi.waitFor(() => expect(warn).toHaveBeenCalled());
    expect(shell.invoke).toHaveBeenCalledWith('set_companion_visible', { visible: false });
    expect(shell.close).not.toHaveBeenCalled();
    view.unmount();
    warn.mockRestore();
  });
});
