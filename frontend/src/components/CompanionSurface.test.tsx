import { act, create } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CompanionSurface } from './CompanionSurface';
import { Companion } from './Companion';
import { activateCompanion } from '../services/companion';

const shell = vi.hoisted(() => ({
  visible: true,
  close: vi.fn(),
  invoke: vi.fn(async (command: string, args?: { visible?: boolean }) => {
    if (command === 'set_companion_visible') shell.visible = Boolean(args?.visible);
  }),
}));

const harness = vi.hoisted(() => ({
  live: false,
  turn: vi.fn(),
  setDraft: vi.fn(),
  draft: '' as string,
}));

vi.mock('@tauri-apps/api/core', () => ({ invoke: shell.invoke }));
vi.mock('@tauri-apps/api/window', () => ({ getCurrentWindow: () => ({ close: shell.close }) }));
vi.mock('../services/bridge', () => ({ isTauri: () => true }));
vi.mock('../hooks/useBackend', () => ({
  useVoiceAssets: () => ({ data: undefined }),
  useActions: () => ({ live: harness.live, assistantTurn: harness.turn }),
}));
vi.mock('../store/appStore', () => ({
  useAppStore: (select: (state: unknown) => unknown) => select({
    setTranscriptDraft: harness.setDraft,
    transcriptDraft: harness.draft,
  }),
}));
vi.mock('./Companion', () => ({
  Companion: ({ onClose, onSubmit, error, caption }: {
    onClose: () => void;
    onSubmit: (text: string) => Promise<void>;
    error?: string | null;
    caption?: string | null;
  }) => (
    <div>
      <button onClick={onClose}>Dismiss</button>
      <button onClick={() => void onSubmit('Held message')}>Submit held</button>
      <span>{error}</span>
      <span>{caption}</span>
    </div>
  ),
}));

beforeEach(() => {
  harness.live = false;
  harness.turn.mockReset();
  harness.setDraft.mockClear();
  harness.draft = '';
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('Companion window lifecycle', () => {
  it('hides on dismissal and can subsequently be revealed when the owner enabled it', async () => {
    // A Companion reveal is now an explicit Settings preference. Simulate an
    // owner-enabled overlay; the default-off preference must not auto-reveal.
    vi.stubGlobal('localStorage', { getItem: (key: string) =>
      key === 'sythion.companionEnabled' ? '1' : null, setItem: vi.fn() });
    const view = create(<CompanionSurface />);
    await act(async () => {
      view.root.findAllByType('button').find((b) => b.children.includes('Dismiss'))!.props.onClick();
    });
    await vi.waitFor(() => expect(shell.invoke).toHaveBeenCalledWith('set_companion_visible', { visible: false }));
    expect(shell.visible).toBe(false);
    expect(shell.close).not.toHaveBeenCalled();

    await act(async () => { await activateCompanion(); });
    expect(shell.invoke).toHaveBeenCalledWith('set_companion_visible', { visible: true });
    expect(shell.visible).toBe(true);
    view.unmount();
  });

  it('does not silently swallow a failed hide and records the failure', async () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const view = create(<CompanionSurface />);
    shell.invoke.mockRejectedValueOnce(new Error('hide failed'));

    await act(async () => {
      view.root.findAllByType('button').find((b) => b.children.includes('Dismiss'))!.props.onClick();
    });

    await vi.waitFor(() => expect(warn).toHaveBeenCalled());
    expect(shell.invoke).toHaveBeenCalledWith('set_companion_visible', { visible: false });
    expect(shell.close).not.toHaveBeenCalled();
    view.unmount();
    warn.mockRestore();
  });
});

describe('Companion offline hold', () => {
  it('holds an unsent draft and auto-sends it once when the runtime returns', async () => {
    harness.live = false;
    harness.draft = 'Held message';
    harness.turn.mockResolvedValueOnce({ assistant_text: '' });

    const view = create(<CompanionSurface />);
    await act(async () => {});
    const companion = view.root.findByType(Companion);

    await act(async () => { await companion.props.onSubmit('Held message'); });
    expect(harness.turn).not.toHaveBeenCalled();
    expect(companion.props.error).toContain('held');
    expect(companion.props.error).toContain('will be sent automatically');
    expect(companion.props.error).not.toContain('Try again');

    harness.live = true;
    await act(async () => { view.update(<CompanionSurface />); });
    await vi.waitFor(() => expect(harness.turn).toHaveBeenCalledTimes(1));
    expect(harness.turn.mock.calls[0]).toEqual(['Held message', expect.any(String)]);
    expect(harness.setDraft).toHaveBeenCalledWith('');
    expect(view.root.findByType(Companion).props.error).toBeNull();
    view.unmount();
  });

  it('never auto-resends a turn whose dispatch outcome is uncertain', async () => {
    harness.live = true;
    harness.draft = 'Uncertain message';
    harness.turn.mockRejectedValueOnce(new Error('PRIVATE BACKEND DETAIL'));

    const view = create(<CompanionSurface />);
    await act(async () => {});
    const companion = view.root.findByType(Companion);

    await act(async () => { await companion.props.onSubmit('Uncertain message'); });
    expect(harness.turn).toHaveBeenCalledTimes(1);
    expect(JSON.stringify(view.toJSON())).toContain('Could not confirm this message');
    expect(JSON.stringify(view.toJSON())).not.toContain('PRIVATE BACKEND DETAIL');

    // A connectivity flap must not resend a request that may already be running.
    harness.live = false;
    await act(async () => { view.update(<CompanionSurface />); });
    harness.live = true;
    await act(async () => { view.update(<CompanionSurface />); });
    expect(harness.turn).toHaveBeenCalledTimes(1);
    view.unmount();
  });

  it('holds multiple different offline drafts and sends them all on reconnect', async () => {
    harness.live = false;
    harness.turn.mockResolvedValue({ assistant_text: '' });

    const view = create(<CompanionSurface />);
    await act(async () => {});
    const companion = view.root.findByType(Companion);
    await act(async () => { await companion.props.onSubmit('First draft'); });
    await act(async () => { await companion.props.onSubmit('Second draft'); });
    expect(harness.turn).not.toHaveBeenCalled();
    expect(companion.props.error).toContain('2 messages are held');

    harness.live = true;
    await act(async () => { view.update(<CompanionSurface />); });
    await vi.waitFor(() => expect(harness.turn).toHaveBeenCalledTimes(2));
    expect(harness.turn.mock.calls.map((c) => c[0])).toEqual(['First draft', 'Second draft']);
    view.unmount();
  });

  it('keeps a single held turn across repeated sends while offline', async () => {
    harness.live = false;
    harness.draft = 'Held message';
    harness.turn.mockResolvedValue({ assistant_text: '' });

    const view = create(<CompanionSurface />);
    await act(async () => {});
    const companion = view.root.findByType(Companion);
    await act(async () => { await companion.props.onSubmit('Held message'); });
    await act(async () => { await companion.props.onSubmit('Held message'); });
    expect(harness.turn).not.toHaveBeenCalled();

    harness.live = true;
    await act(async () => { view.update(<CompanionSurface />); });
    await vi.waitFor(() => expect(harness.turn).toHaveBeenCalledTimes(1));
    expect(harness.turn.mock.calls[0][0]).toBe('Held message');
    view.unmount();
  });
});
