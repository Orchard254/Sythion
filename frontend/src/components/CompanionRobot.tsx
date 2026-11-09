import type { CSSProperties } from 'react';

/* The Sythion Companion robot.
 *
 * There were no robot assets in the repository, so the identity is authored
 * here as inline SVG rather than introduced as a dependency or an external
 * image. It is recognisable as a compact, friendly robot — large display head,
 * antennae, torso, articulated arms and legs — not an orb or a waveform.
 *
 * `mood` is a *presentation* input driven by real interaction state. It never
 * advances on a timer: `thinking` only appears while a request is genuinely in
 * flight, and `speaking` only while a playback signal is real. */

export type CompanionMood = 'idle' | 'listening' | 'thinking' | 'speaking' | 'unavailable';

const MOOD_TONE: Record<CompanionMood, { face: string; glow: string }> = {
  idle: { face: 'var(--companion-face)', glow: 'var(--companion-glow-idle)' },
  listening: { face: 'var(--companion-face-active)', glow: 'var(--companion-glow-active)' },
  thinking: { face: 'var(--companion-face-active)', glow: 'var(--companion-glow-active)' },
  speaking: { face: 'var(--companion-face-active)', glow: 'var(--companion-glow-active)' },
  unavailable: { face: 'var(--companion-face-dim)', glow: 'var(--companion-glow-idle)' },
};

export function CompanionRobot({
  mood,
  size = 168,
  reducedMotion = false,
}: {
  mood: CompanionMood;
  size?: number;
  reducedMotion?: boolean;
}) {
  const tone = MOOD_TONE[mood];
  const active = mood === 'listening' || mood === 'thinking' || mood === 'speaking';
  const style = { '--companion-face': tone.face, '--companion-glow': tone.glow } as CSSProperties;

  return (
    <svg
      className={`companion-robot${active ? ' companion-robot--active' : ''}`}
      width={size}
      height={size * 1.25}
      viewBox="0 0 120 150"
      role="img"
      aria-label={`Sythion companion, ${mood}`}
      data-mood={mood}
      style={style}
    >
      <ellipse cx="60" cy="145" rx="44" ry="5" fill="var(--companion-body-2)" />

      <path d="M40 24l-8-14" stroke="var(--companion-visor)" strokeWidth="2.4" strokeLinecap="round" />
      <path d="M80 24l8-14" stroke="var(--companion-visor)" strokeWidth="2.4" strokeLinecap="round" />
      <circle cx="31" cy="9" r="2.8" fill="#58dcff" />
      <circle cx="89" cy="9" r="2.8" fill="#58dcff" />

      <rect x="21" y="13" width="78" height="58" rx="27" fill="var(--companion-body)" stroke="var(--companion-line)" strokeWidth="1.2" />
      <rect x="15" y="35" width="7" height="22" rx="3.5" fill="var(--companion-body-2)" />
      <rect x="98" y="35" width="7" height="22" rx="3.5" fill="var(--companion-body-2)" />
      <rect x="30" y="27" width="60" height="39" rx="18" fill="var(--companion-visor)" />
      <circle cx="49" cy="46" r="6.5" fill="#58dcff" />
      <circle cx="71" cy="46" r="6.5" fill="#58dcff" />
      <path d="M56 60h8" stroke="#58dcff" strokeWidth="2" strokeLinecap="round" opacity={mood === 'unavailable' ? 0.3 : 0.7} />

      <rect x="55" y="71" width="10" height="6" fill="var(--companion-visor)" />
      <rect x="36" y="78" width="48" height="30" rx="12" fill="var(--companion-body)" stroke="var(--companion-line)" strokeWidth="1.2" />
      <rect x="42" y="82" width="36" height="4" rx="2" fill="var(--companion-visor)" />
      <rect x="42" y="90" width="36" height="4" rx="2" fill="var(--companion-chest)" />

      <g>
        <circle cx="33" cy="80" r="7" fill="var(--companion-body-2)" />
        <rect x="24" y="85" width="10" height="18" rx="4" fill="var(--companion-body)" />
        <rect x="24" y="103" width="12" height="17" rx="4" fill="var(--companion-body-2)" />
        <circle cx="87" cy="80" r="7" fill="var(--companion-body-2)" />
        <rect x="86" y="85" width="10" height="18" rx="4" fill="var(--companion-body)" />
        <rect x="84" y="103" width="12" height="17" rx="4" fill="var(--companion-body-2)" />
        <path d="M28 122c2 1 4 2 7 2M92 122c-2 1-4 2-7 2" stroke="var(--companion-visor)" strokeWidth="2" strokeLinecap="round" />
        <path d="M28 126l-4 4M92 126l4 4" stroke="var(--companion-visor)" strokeWidth="2" strokeLinecap="round" />
      </g>

      <rect x="49" y="108" width="22" height="7" rx="3" fill="var(--companion-visor)" />
      <g fill="var(--companion-body)">
        <rect x="39" y="114" width="16" height="19" rx="6" />
        <rect x="65" y="114" width="16" height="19" rx="6" />
        <rect x="37" y="131" width="20" height="9" rx="4" />
        <rect x="63" y="131" width="20" height="9" rx="4" />
      </g>
      <ellipse cx="47" cy="140" rx="13" ry="4" fill="var(--companion-body-2)" />
      <ellipse cx="73" cy="140" rx="13" ry="4" fill="var(--companion-body-2)" />
    </svg>
  );
}
