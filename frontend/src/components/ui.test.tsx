import { create } from 'react-test-renderer';
import { describe, expect, it, vi } from 'vitest';
import { Chips } from './ui';

describe('Chips', () => {
  it('omits the all control when its label is empty', () => {
    const onChange = vi.fn();
    const view = create(<Chips value="today" options={[{ value: 'today', label: 'Today' }, { value: 'reminders', label: 'Reminders' }]} onChange={onChange} allLabel="" />);
    const buttons = view.root.findAllByType('button');
    expect(buttons.map((button) => button.children[0])).toEqual(['Today', 'Reminders']);
    buttons[1].props.onClick();
    expect(onChange).toHaveBeenCalledWith('reminders');
  });

  it('keeps the default All filter when requested', () => {
    const onChange = vi.fn();
    const view = create(<Chips value="today" options={[{ value: 'today', label: 'Today' }]} onChange={onChange} />);
    const buttons = view.root.findAllByType('button');
    expect(buttons[0].children[0]).toBe('All');
    buttons[0].props.onClick();
    expect(onChange).toHaveBeenCalledWith('__all__');
  });
});
