import { act, create } from 'react-test-renderer';
import { beforeAll, describe, expect, it, vi } from 'vitest';
import { DeviceAccessControls } from './DeviceAccessControls';
import type { DeviceCapability } from '../types';

beforeAll(() => {
  if (typeof globalThis.window === 'undefined') {
    (globalThis as { window: unknown }).window = {
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
    };
  }
});

const cap = (granted: boolean): DeviceCapability => ({
  capability: 'conversation.write',
  status: granted ? 'granted' : 'available',
  granted,
  min_trust: 'trusted',
  global_capability: 'reasoning_model',
  global_status: 'AVAILABLE',
  allowed: granted,
  reason: granted ? 'ok' : 'no_grant',
});

const api = () => ({
  issueCredential: vi.fn().mockResolvedValue({ device_id: 'dev_1', token: 'secret-token' }),
  revokeCredential: vi.fn().mockResolvedValue({ device_id: 'dev_1', revoked: true }),
  addGrant: vi.fn().mockResolvedValue({}),
  removeGrant: vi.fn().mockResolvedValue({}),
});

describe('DeviceAccessControls', () => {
  it('issues a credential and shows it once with a warning', async () => {
    const service = api();
    const onChanged = vi.fn();
    let view!: ReturnType<typeof create>;
    await act(async () => {
      view = create(<DeviceAccessControls deviceId="dev_1" capabilities={[]} api={service} onChanged={onChanged} />);
    });

    const issue = view.root.findAllByType('button')
      .find((b) => b.children.join('') === 'Issue credential');
    await act(async () => { issue!.props.onClick(); });
    await act(async () => { await Promise.resolve(); });

    expect(service.issueCredential).toHaveBeenCalledWith('dev_1');
    const rendered = JSON.stringify(view.toJSON());
    expect(rendered).toContain('Shown once');
    expect(rendered).toContain('secret-token');
    expect(onChanged).toHaveBeenCalled();
  });

  it('adds and removes grants as explicit owner actions', async () => {
    const service = api();
    let view!: ReturnType<typeof create>;
    await act(async () => {
      view = create(<DeviceAccessControls deviceId="dev_1" capabilities={[cap(false)]} api={service} />);
    });

    let add = view.root.findAllByType('button')
      .find((b) => b.children.join('') === 'Add grant');
    expect(add).toBeDefined();
    await act(async () => { add!.props.onClick(); });
    await act(async () => { await Promise.resolve(); });
    expect(service.addGrant).toHaveBeenCalledWith('dev_1', 'conversation.write');

    let removeView!: ReturnType<typeof create>;
    await act(async () => {
      removeView = create(<DeviceAccessControls deviceId="dev_1" capabilities={[cap(true)]} api={service} />);
    });
    const remove = removeView.root.findAllByType('button')
      .find((b) => b.children.join('') === 'Remove grant');
    expect(remove).toBeDefined();
    await act(async () => { remove!.props.onClick(); });
    await act(async () => { await Promise.resolve(); });
    expect(service.removeGrant).toHaveBeenCalledWith('dev_1', 'conversation.write');
  });

  it('requires confirmation before revoking', async () => {
    const service = api();
    let view!: ReturnType<typeof create>;
    await act(async () => {
      view = create(<DeviceAccessControls deviceId="dev_1" capabilities={[]} api={service} />);
    });

    const revoke = view.root.findAllByType('button')
      .find((b) => b.children.join('') === 'Revoke credential');
    await act(async () => { revoke!.props.onClick(); });
    expect(service.revokeCredential).not.toHaveBeenCalled();
    expect(JSON.stringify(view.toJSON())).toContain('Revoke credential for this device?');
  });

  it('surfaces access errors', async () => {
    const service = api();
    service.issueCredential.mockRejectedValue(new Error('unknown device'));
    let view!: ReturnType<typeof create>;
    await act(async () => {
      view = create(<DeviceAccessControls deviceId="dev_1" capabilities={[]} api={service} />);
    });
    const issue = view.root.findAllByType('button')
      .find((b) => b.children.join('') === 'Issue credential');
    await act(async () => { issue!.props.onClick(); });
    await act(async () => { await Promise.resolve(); });
    expect(JSON.stringify(view.toJSON())).toContain('unknown device');
  });
});
