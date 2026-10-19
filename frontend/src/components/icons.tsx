import type { CSSProperties, ReactElement } from 'react';

/* Inline icon set — 1.5px stroke on a 24px grid, sized via `size`.
 * Kept local so the desktop shell has no icon-font or SVG-sprite dependency. */

export type IconName =
  | 'overview'
  | 'assistant'
  | 'missions'
  | 'life'
  | 'devices'
  | 'comms'
  | 'memory'
  | 'automations'
  | 'security'
  | 'system'
  | 'settings'
  | 'search'
  | 'send'
  | 'microphone'
  | 'bell'
  | 'check'
  | 'alert'
  | 'info'
  | 'close'
  | 'chevronRight'
  | 'chevronDown'
  | 'play'
  | 'pause'
  | 'stop'
  | 'refresh'
  | 'shield'
  | 'lock'
  | 'unlock'
  | 'clock'
  | 'calendar'
  | 'mail'
  | 'phone'
  | 'home'
  | 'laptop'
  | 'server'
  | 'database'
  | 'plug'
  | 'cpu'
  | 'file'
  | 'brain'
  | 'target'
  | 'layers'
  | 'plus'
  | 'trash'
  | 'download'
  | 'edit'
  | 'filter'
  | 'inbox'
  | 'activity'
  | 'power'
  | 'external'
  | 'eye'
  | 'key'
  | 'link';

interface IconProps {
  name: IconName;
  size?: number;
  className?: string;
  style?: CSSProperties;
  strokeWidth?: number;
}

const PATHS: Record<IconName, ReactElement> = {
  overview: (
    <>
      <rect x="3" y="3" width="7.5" height="7.5" rx="1.5" />
      <rect x="13.5" y="3" width="7.5" height="5.5" rx="1.5" />
      <rect x="13.5" y="11.5" width="7.5" height="9.5" rx="1.5" />
      <rect x="3" y="13.5" width="7.5" height="7.5" rx="1.5" />
    </>
  ),
  assistant: (
    <>
      <path d="M20 14.5a2.5 2.5 0 0 1-2.5 2.5H9l-4 3.5V6.5A2.5 2.5 0 0 1 7.5 4h10A2.5 2.5 0 0 1 20 6.5Z" />
      <path d="M9.5 9.5h5M9.5 12.5h3" />
    </>
  ),
  missions: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="12" cy="12" r="0.75" fill="currentColor" stroke="none" />
    </>
  ),
  life: (
    <>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18M8 3v4M16 3v4M8 14h2M14 14h2" />
    </>
  ),
  devices: (
    <>
      <rect x="2.5" y="5" width="13" height="9.5" rx="1.5" />
      <path d="M6 18h6M9 14.5V18" />
      <rect x="17" y="9" width="4.5" height="10" rx="1.2" />
    </>
  ),
  comms: (
    <>
      <path d="M20.5 12a8.5 8.5 0 0 1-11.9 7.8L3.5 20.5l.8-5A8.5 8.5 0 1 1 20.5 12Z" />
      <path d="M8.5 11.5h7M8.5 14.5h4" />
    </>
  ),
  memory: (
    <>
      <path d="M12 4.5a3 3 0 0 0-3 3v.7a2.6 2.6 0 0 0-1.6 4.5A2.6 2.6 0 0 0 9 17.6v.4a3 3 0 0 0 6 0v-.4a2.6 2.6 0 0 0 1.6-4.9A2.6 2.6 0 0 0 15 8.2v-.7a3 3 0 0 0-3-3Z" />
      <path d="M12 4.5v15" />
    </>
  ),
  automations: (
    <>
      <path d="M13 2.5 4.5 13.5H11l-.5 8 8.5-11H12Z" />
    </>
  ),
  security: (
    <>
      <path d="M12 3 5 6v6c0 4.2 2.9 7.9 7 9 4.1-1.1 7-4.8 7-9V6Z" />
      <path d="m9.3 12 1.9 1.9 3.5-3.8" />
    </>
  ),
  system: (
    <>
      <rect x="3" y="4" width="18" height="7" rx="1.5" />
      <rect x="3" y="14" width="18" height="6" rx="1.5" />
      <path d="M7 7.5h.01M7 17h.01M11 7.5h4M11 17h4" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="2.8" />
      <path d="M19.4 14.5a1.6 1.6 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.6 1.6 0 0 0-2.7 1.1v.3a2 2 0 1 1-4 0v-.2a1.6 1.6 0 0 0-2.8-1.1l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.6 1.6 0 0 0-1.1-2.7H3a2 2 0 1 1 0-4h.2A1.6 1.6 0 0 0 4.3 6.2l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.6 1.6 0 0 0 1.8.3H9a1.6 1.6 0 0 0 1-1.5V2a2 2 0 1 1 4 0v.2a1.6 1.6 0 0 0 2.7 1.1l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.6 1.6 0 0 0-.3 1.8V8a1.6 1.6 0 0 0 1.5 1H22a2 2 0 1 1 0 4h-.2a1.6 1.6 0 0 0-1.4 1Z" />
    </>
  ),
  search: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m20 20-4.9-4.9" />
    </>
  ),
  send: <path d="M4.5 12 20 4.5 15.5 20l-4-6.5Z" />,
  microphone: (
    <>
      <rect x="9" y="2.5" width="6" height="11" rx="3" />
      <path d="M5.5 11.5a6.5 6.5 0 0 0 13 0M12 18v3.5" />
    </>
  ),
  bell: (
    <>
      <path d="M18 9a6 6 0 1 0-12 0c0 5-2 6.5-2 6.5h16S18 14 18 9Z" />
      <path d="M13.7 19a2 2 0 0 1-3.4 0" />
    </>
  ),
  check: <path d="m4.5 12.5 5 5 10-11" />,
  alert: (
    <>
      <path d="M10.3 3.9 2.4 17.2A2 2 0 0 0 4.1 20.2h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z" />
      <path d="M12 9v4.5M12 17h.01" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 11v5.5M12 7.8h.01" />
    </>
  ),
  close: <path d="M6 6l12 12M18 6 6 18" />,
  chevronRight: <path d="m9 5 7 7-7 7" />,
  chevronDown: <path d="m5 9 7 7 7-7" />,
  play: <path d="M7 4.5v15l12-7.5Z" />,
  pause: <path d="M8.5 4.5v15M15.5 4.5v15" />,
  stop: <rect x="5.5" y="5.5" width="13" height="13" rx="2" />,
  refresh: (
    <>
      <path d="M20 12a8 8 0 1 1-2.6-5.9" />
      <path d="M20.5 3.5V9H15" />
    </>
  ),
  shield: <path d="M12 3 5 6v6c0 4.2 2.9 7.9 7 9 4.1-1.1 7-4.8 7-9V6Z" />,
  lock: (
    <>
      <rect x="4.5" y="10" width="15" height="10.5" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </>
  ),
  unlock: (
    <>
      <rect x="4.5" y="10" width="15" height="10.5" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 7.6-1.7" />
    </>
  ),
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 6.8V12l3.4 2" />
    </>
  ),
  calendar: (
    <>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M3 10h18M8 3v4M16 3v4" />
    </>
  ),
  mail: (
    <>
      <rect x="2.5" y="5" width="19" height="14" rx="2" />
      <path d="m3 7 9 6 9-6" />
    </>
  ),
  phone: (
    <path d="M6.5 3.5h3l1.5 4-2 1.5a12 12 0 0 0 6 6l1.5-2 4 1.5v3a2 2 0 0 1-2.2 2A17.5 17.5 0 0 1 4.5 5.7 2 2 0 0 1 6.5 3.5Z" />
  ),
  home: (
    <>
      <path d="M3.5 10.5 12 3.5l8.5 7" />
      <path d="M5.5 9.5V20h13V9.5" />
    </>
  ),
  laptop: (
    <>
      <rect x="4" y="5" width="16" height="10" rx="1.5" />
      <path d="M2 18.5h20" />
    </>
  ),
  server: (
    <>
      <rect x="3" y="4" width="18" height="7" rx="1.5" />
      <rect x="3" y="13" width="18" height="7" rx="1.5" />
      <path d="M7 7.5h.01M7 16.5h.01" />
    </>
  ),
  database: (
    <>
      <ellipse cx="12" cy="6" rx="8" ry="3" />
      <path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" />
    </>
  ),
  plug: (
    <>
      <path d="M9 2.5v5M15 2.5v5" />
      <path d="M6.5 7.5h11v3a5.5 5.5 0 0 1-11 0Z" />
      <path d="M12 16v5.5" />
    </>
  ),
  cpu: (
    <>
      <rect x="6" y="6" width="12" height="12" rx="2" />
      <rect x="9.5" y="9.5" width="5" height="5" rx="1" />
      <path d="M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3" />
    </>
  ),
  file: (
    <>
      <path d="M13.5 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8.5Z" />
      <path d="M13.5 3v5.5H19M8.5 13h7M8.5 16.5h4" />
    </>
  ),
  brain: (
    <>
      <path d="M9.5 4.5A3 3 0 0 0 4 6.2 3 3 0 0 0 3 11.4a3 3 0 0 0 1.5 4.3A3 3 0 0 0 9 19.5h1V4.5Z" />
      <path d="M14.5 4.5A3 3 0 0 1 20 6.2a3 3 0 0 1 1 5.2 3 3 0 0 1-1.5 4.3A3 3 0 0 1 15 19.5h-1V4.5Z" />
    </>
  ),
  target: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <circle cx="12" cy="12" r="4.5" />
      <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
    </>
  ),
  layers: (
    <>
      <path d="M12 3 3 7.5l9 4.5 9-4.5Z" />
      <path d="m3 12.5 9 4.5 9-4.5M3 17l9 4.5 9-4.5" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  trash: (
    <>
      <path d="M4 6.5h16M9.5 6.5V4h5v2.5" />
      <path d="M6.5 6.5 7.5 21h9l1-14.5M10 10.5v6.5M14 10.5v6.5" />
    </>
  ),
  download: <path d="M12 3.5v11M7.5 10.5 12 15l4.5-4.5M4 20.5h16" />,
  edit: (
    <>
      <path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17Z" />
      <path d="M14.5 6.5 17.5 9.5" />
    </>
  ),
  filter: <path d="M3.5 5h17l-6.5 8v6l-4 2v-8Z" />,
  inbox: (
    <>
      <path d="M3.5 13.5h4l1.5 3h6l1.5-3h4" />
      <path d="M5.5 4.5h13l2 9v5a2 2 0 0 1-2 2h-13a2 2 0 0 1-2-2v-5Z" />
    </>
  ),
  activity: <path d="M2.5 12.5h4l2.5-7 4 14 2.5-7h6" />,
  power: (
    <>
      <path d="M12 3v9" />
      <path d="M18.4 6.6a9 9 0 1 1-12.8 0" />
    </>
  ),
  external: <path d="M14 4h6v6M20 4l-8.5 8.5M18 14v5a1.5 1.5 0 0 1-1.5 1.5h-12A1.5 1.5 0 0 1 3 19V7a1.5 1.5 0 0 1 1.5-1.5H9" />,
  eye: (
    <>
      <path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12Z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  key: (
    <>
      <circle cx="8" cy="14" r="4.5" />
      <path d="m11.5 11.5 8-8M17 6l2.5 2.5M14.5 8.5 17 11" />
    </>
  ),
  link: (
    <>
      <path d="M10 13.5a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7L11.5 6.3" />
      <path d="M14 10.5a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1.5-1.5" />
    </>
  ),
};

export function Icon({ name, size = 16, className, style, strokeWidth = 1.6 }: IconProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      style={style}
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}

/** Sythion wordmark glyph — a faceted core inside a containment ring. */
export function SythionMark({ size = 22 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 32 32"
      fill="none"
      aria-hidden="true"
      focusable="false"
      style={{ flex: '0 0 auto' }}
    >
      <defs>
        <linearGradient id="sythion-mark" x1="4" y1="2" x2="28" y2="30" gradientUnits="userSpaceOnUse">
          <stop stopColor="#5cf3d8" />
          <stop offset="1" stopColor="#16a892" />
        </linearGradient>
      </defs>
      <rect
        x="1.25"
        y="1.25"
        width="29.5"
        height="29.5"
        rx="8"
        stroke="url(#sythion-mark)"
        strokeWidth="1.5"
        opacity="0.55"
      />
      <path
        d="M16 6.5 25 11v10L16 25.5 7 21V11Z"
        stroke="url(#sythion-mark)"
        strokeWidth="1.5"
        strokeLinejoin="round"
      />
      <circle cx="16" cy="16" r="3.4" fill="url(#sythion-mark)" />
      <path d="M16 6.5V12M25 11l-5.5 2.4M25 21l-5.5-2.4M16 25.5V20M7 21l5.5-2.4M7 11l5.5 2.4" stroke="url(#sythion-mark)" strokeWidth="1" opacity="0.5" />
    </svg>
  );
}