import { act, create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({
  turn: vi.fn(),
  speak: vi.fn(),
}));

vi.mock('../services/api', () => ({
  voiceService: { speak: mock.speak },
  assistantService: { turn: mock.turn },
}));

vi.mock('@tauri-apps/api/core', () => ({ invoke: vi.fn() }));
vi.mock('@tauri-apps/api/window', () => ({ getCurrentWindow: () => ({ close: vi.fn() }) }));
vi.mock('../services/bridge', () => ({ isTauri: () => false }));
vi.mock('../hooks/useVoiceCapture', () => ({ useVoiceCapture: () => ({ toggleListening: vi.fn() }) }));

vi.mock('../hooks/useBackend', () => ({
  useVoiceAssets: () => ({ data: { stt_installed: true } }),
  useActions: () => ({ live: true, assistantTurn: mock.turn }),
}));

const draftSetter = vi.fn();
vi.mock('../store/appStore', () => ({
  useAppStore: (select: (state: unknown) => unknown) =>
    select({ dataMode: 'live', setTranscriptDraft: draftSetter, transcriptDraft: 'Hello Synth' }),
}));

describe('Companion spoken replies', () => {
  it('speaks the assistant reply when a message is sent', async () => {
    mock.turn.mockResolvedValueOnce({ exchange_id: 't1', user_text: 'Hello Synth', assistant_text: 'Hi there', status: 'completed', background_task_ids: [] });
    mock.speak.mockResolvedValueOnce({ success: true });

    const { CompanionSurface } = await import('./CompanionSurface');
    const view = create(<CompanionSurface />);
    await act(async () => {});

    const robot = view.root.findAllByType('button').find((b) => b.props['aria-label'] === 'Show Sythion input');
    await act(async () => { robot?.props.onClick(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    const send = view.root.findAllByType('button').find((b) => b.props['aria-label'] === 'Send to Sythion');
    await act(async () => { send?.props.onClick(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });

    expect(mock.turn).toHaveBeenCalledWith('Hello Synth');
    expect(mock.speak).toHaveBeenCalledWith('Hi there');
    expect(draftSetter).toHaveBeenCalledWith('');
    view.unmount();
  });

  it('does not surface a chat warning when TTS is unavailable', async () => {
    mock.turn.mockResolvedValueOnce({ exchange_id: 't2', user_text: 'Hello Synth', assistant_text: 'Hi there', status: 'completed', background_task_ids: [] });
    mock.speak.mockResolvedValueOnce({ success: false });

    const { CompanionSurface } = await import('./CompanionSurface');
    const view = create(<CompanionSurface />);
    await act(async () => {});

    const robot = view.root.findAllByType('button').find((b) => b.props['aria-label'] === 'Show Sythion input');
    await act(async () => { robot?.props.onClick(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    const send = view.root.findAllByType('button').find((b) => b.props['aria-label'] === 'Send to Sythion');
    await act(async () => { send?.props.onClick(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });

    expect(mock.speak).toHaveBeenCalled();
    expect(JSON.stringify(view.toJSON())).toContain('Hi there');
    expect(JSON.stringify(view.toJSON())).not.toContain('Spoken playback is unavailable');
    view.unmount();
  });

  it('surfaces genuine unexpected playback errors', async () => {
    mock.turn.mockResolvedValueOnce({ exchange_id: 't3', user_text: 'Hello Synth', assistant_text: 'Hi there', status: 'completed', background_task_ids: [] });
    mock.speak.mockRejectedValueOnce(new Error('Speaker device lost'));

    const { CompanionSurface } = await import('./CompanionSurface');
    const view = create(<CompanionSurface />);
    await act(async () => {});

    const robot = view.root.findAllByType('button').find((b) => b.props['aria-label'] === 'Show Sythion input');
    await act(async () => { robot?.props.onClick(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    const send = view.root.findAllByType('button').find((b) => b.props['aria-label'] === 'Send to Sythion');
    await act(async () => { send?.props.onClick(); });
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });

    expect(mock.speak).toHaveBeenCalled();
    expect(JSON.stringify(view.toJSON())).toContain('Speaker device lost');
    view.unmount();
  });
});