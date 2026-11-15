import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { SettingsScreen } from './Settings';
import { useAppStore } from '../store/appStore';

const mock = vi.hoisted(() => ({ data: {} as Record<string, unknown>, update: vi.fn(), refetch: vi.fn() }));
vi.mock('../hooks/useBackend', () => ({
  useAppSettings: () => ({ data: mock.data, refetch: mock.refetch }),
  useHealth: () => ({ data: {} }), useSecrets: () => ({ data: { configured: [] } }),
  useVoiceAssets: () => ({ data: { stt_installed: true } }),
  useActions: () => ({ updateSettings: mock.update }),
}));
vi.mock('../services/api', () => ({ healthService: { version: async () => ({ version: 'test' }) }, voiceService: {} }));
vi.mock('../components/AIModelsSettings', () => ({ AIModelsSettings: () => 'AI Models' }));

let view: ReactTestRenderer;
const text = (n: { children: unknown[] }): string => n.children.map(c => typeof c === 'string' ? c : c && typeof c === 'object' && 'children' in c ? text(c as { children: unknown[] }) : '').join('');
const button = (label: string) => view.root.findAllByType('button').find(b => text(b) === label)!;
const click = async (label: string) => { await act(async () => { button(label).props.onClick(); }); };

beforeEach(async () => {
  vi.clearAllMocks();
  vi.stubGlobal('localStorage', { getItem: () => null, setItem: vi.fn() });
  vi.stubGlobal('document', { documentElement: { style: { setProperty: vi.fn() } } });
  mock.data = { retention_days: 90, file_roots: ['C:\\env'], persisted_file_roots: ['C:\\one,two'], environment_file_roots: [], effective_file_roots: ['C:\\one,two', 'C:\\backend'], fallback_file_roots: ['C:\\home', 'C:\\cwd'] };
  mock.update.mockResolvedValue({});
  useAppStore.setState({ isConnected: true });
  await act(async () => { view = create(<SettingsScreen />); });
});
afterEach(() => { act(() => view.unmount()); vi.unstubAllGlobals(); });

describe('file root settings', () => {
  it('saves retention alone without copying roots', async () => {
    act(() => view.root.findByProps({ id: 'retention' }).props.onChange({ target: { value: '45' } }));
    await click('Save settings');
    expect(mock.update).toHaveBeenCalledWith({ retention_days: 45 });
  });
  it('edits and sends each path as an array, preserving commas', async () => {
    expect(view.root.findByProps({ 'aria-label': 'File grant 1' }).props.value).toBe('C:\\one,two');
    await click('Add file grant');
    act(() => view.root.findByProps({ 'aria-label': 'File grant 2' }).props.onChange({ target: { value: 'D:\\three,four' } }));
    await click('Save settings');
    expect(mock.update).toHaveBeenCalledWith({ file_roots: ['C:\\one,two', 'D:\\three,four'] });
  });
  it('shows prospective fallback and requires explicit confirmation', async () => {
    await click('Remove grant 1');
    await click('Save settings');
    expect(mock.update).not.toHaveBeenCalled();
    expect(JSON.stringify(view.toJSON())).toContain('C:\\\\home');
    expect(JSON.stringify(view.toJSON())).toContain('C:\\\\cwd');
    await click('Cancel');
    expect(mock.update).not.toHaveBeenCalled();
    await click('Save settings');
    await click('Confirm fallback access');
    expect(mock.update).toHaveBeenCalledWith({ file_roots: [], file_roots_fallback_acknowledgement: { persisted_file_roots: ['C:\\one,two'], fallback_file_roots: ['C:\\home', 'C:\\cwd'] } });
  });
  it('invalidates confirmation when refreshed root state changes', async () => {
    await click('Remove grant 1');
    await click('Save settings');
    mock.data = { ...mock.data, fallback_file_roots: ['C:\\new-home', 'C:\\cwd'] };
    await act(async () => { view.update(<SettingsScreen />); });
    expect(view.root.findAllByType('button').some(b => text(b) === 'Confirm fallback access')).toBe(false);
    expect(mock.update).not.toHaveBeenCalled();
  });
  it('invalidates confirmation and refreshes after a rejected acknowledgement', async () => {
    mock.update.mockRejectedValueOnce(new Error('state changed'));
    await click('Remove grant 1');
    await click('Save settings');
    await click('Confirm fallback access');
    expect(mock.refetch).toHaveBeenCalled();
    expect(view.root.findAllByType('button').some(b => text(b) === 'Confirm fallback access')).toBe(false);
  });
  it('does not expose editing on old backends or save merged roots', async () => {
    mock.data = { retention_days: 90, file_roots: ['C:\\environment'] };
    await act(async () => { view.update(<SettingsScreen />); });
    expect(button('Add file grant').props.disabled).toBe(true);
    act(() => view.root.findByProps({ id: 'retention' }).props.onChange({ target: { value: '30' } }));
    await click('Save settings');
    expect(mock.update).toHaveBeenCalledWith({ retention_days: 30 });
  });
  it('clears persisted grants without fallback when environment roots remain', async () => {
    mock.data = { ...mock.data, environment_file_roots: ['C:\\environment'] };
    await act(async () => { view.update(<SettingsScreen />); });
    await click('Remove grant 1');
    await click('Save settings');
    expect(mock.update).toHaveBeenCalledWith({ file_roots: [] });
    expect(view.root.findAllByProps({ role: 'alert' })).toHaveLength(0);
  });
  it('blocks clearing if a transitional backend cannot report prospective roots', async () => {
    mock.data = { ...mock.data, fallback_file_roots: undefined };
    await act(async () => { view.update(<SettingsScreen />); });
    await click('Remove grant 1');
    expect(button('Save settings').props.disabled).toBe(true);
    expect(mock.update).not.toHaveBeenCalled();
  });
});
