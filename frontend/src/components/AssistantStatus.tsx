import type { CSSProperties } from 'react';
import { Icon, type IconName } from './icons';
import { isVoiceState, type AssistantStatus, type AssistantState } from '../lib/assistantStatus';

/**
 * Assistant status indicator.
 *
 * This is the seam intended for future motion. It takes a fully-derived
 * `AssistantStatus` and renders it; it never derives state itself and never
 * invents activity. Two extension points exist for later work:
 *
 *  - `audioLevel` (0..1). Today it is always null, because the sidecar
 *    returns no amplitude. When a real analyser is attached, components must
 *    render identically for null and must scale only for a real number.
 *  - `data-assistant-state` on the root. Any future animation, test or
 *    external style hook can target a specific state without this component
 *    having to know about it.
 *
 * Deliberately no animation library and no timers: the only movement is a
 * scale driven by `audioLevel`, and it is disabled under reduced motion.
 */

const STATE_ICON: Record<AssistantState, IconName> = {
  idle: 'assistant',
  listening: 'microphone',
  speaking: 'microphone',
  thinking: 'clock',
  executing: 'activity',
  awaiting: 'alert',
  offline: 'info',
  error: 'alert',
};

export function AssistantStatusIndicator({
  status,
  audioLevel,
  showLabel = true,
  className,
}: {
  status: AssistantStatus;
  audioLevel?: number | null;
  showLabel?: boolean;
  className?: string;
}) {
  const level =
    typeof audioLevel === 'number' && !Number.isNaN(audioLevel)
      ? Math.max(0, Math.min(1, audioLevel))
      : null;

  return (
    <div
      className={['as-status', `as-status--${status.state}`, className].filter(Boolean).join(' ')}
      data-assistant-state={status.state}
      data-audio-level={level === null ? undefined : level.toFixed(3)}
      // Consumed by .as-status__orb. Absent when there is no real level, so
      // the default scale of 1 applies.
      style={level === null ? undefined : ({ '--a-level': level } as CSSProperties)}
      role="status"
      aria-live="polite"
    >
      <span className="as-status__orb" aria-hidden="true">
        <Icon name={STATE_ICON[status.state]} size={14} />
        {isVoiceState(status.state) && <span className="as-status__ripple" />}
      </span>
      {showLabel && (
        <span className="as-status__text">
          <span className="as-status__label">{status.label}</span>
          {status.detail && <span className="as-status__detail">{status.detail}</span>}
        </span>
      )}
    </div>
  );
}