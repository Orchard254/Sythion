import type { CSSProperties } from 'react';

/* The Sythion Companion robot.
 *
 * There were no robot assets in the repository, so the identity is authored
 * here as inline SVG rather than introduced as a dependency or an external
 * image. It is a recognisable robot — head, antenna, visor, chassis, arms —
 * not an orb or a waveform, which the brief explicitly rules out.
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
  size = 104,
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
      height={size}
      viewBox="0 0 96 96"
      role="img"
      aria-label={`Sythion companion, ${mood}`}
      data-mood={mood}
      style={style}
    >
      {/* Soft ground shadow, so the robot sits rather than floats. */}
      <ellipse cx="48" cy="86" rx="24" ry="4.5" fill="var(--companion-shadow)" />

      {/* Antenna */}
      <g className="companion-robot__antenna">
        <line x1="48" y1="16" x2="48" y2="8" stroke="var(--companion-line)" strokeWidth="2.5" strokeLinecap="round" />
        <circle cx="48" cy="6" r="3.4" fill="var(--companion-face)" />
      </g>

      {/* Head / visor housing */}
      <rect
        x="16"
        y="17"
        width="64"
        height="44"
        rx="17"
        fill="var(--companion-body)"
        stroke="var(--companion-line)"
        strokeWidth="1.5"
      />
      {/* Top highlight: the soft-UI light source, kept consistent with the app. */}
      <path
        d="M28 21.5h30a7 7 0 0 1 7 7v1.5H21v-1.5a7 7 0 0 1 7-7z"
        fill="var(--companion-highlight)"
      />

      {/* Visor */}
      <rect x="24" y="27" width="48" height="20" rx="10" fill="var(--companion-visor)" />

      {/* Eyes. Their shape changes with mood so state is not carried by colour
          alone — the idle eyes narrow, the unavailable eyes flatten. */}
      {mood === 'unavailable' ? (
        <>
          <rect x="33" y="35.4" width="11" height="3" rx="1.5" fill="var(--companion-face)" />
          <rect x="52" y="35.4" width="11" height="3" rx="1.5" fill="var(--companion-face)" />
        </>
      ) : mood === 'idle' ? (
        <>
          <rect x="34" y="32.5" width="9" height="9" rx="4.5" fill="var(--companion-face)" />
          <rect x="53" y="32.5" width="9" height="9" rx="4.5" fill="var(--companion-face)" />
        </>
      ) : (
        <>
          <circle cx="38.5" cy="37" r="5" fill="var(--companion-face)" />
          <circle cx="57.5" cy="37" r="5" fill="var(--companion-face)" />
        </>
      )}

      {/* Mouth: a small status slot. Present so the face reads as a face. */}
      <rect
        x="41"
        y="50"
        width="14"
        height="3.4"
        rx="1.7"
        fill="var(--companion-face)"
        opacity={mood === 'unavailable' ? 0.35 : 0.75}
      />

      {/* Chassis */}
      <rect
        x="26"
        y="61"
        width="44"
        height="20"
        rx="10"
        fill="var(--companion-body-2)"
        stroke="var(--companion-line)"
        strokeWidth="1.5"
      />
      <circle cx="48" cy="71" r="4.6" fill="var(--companion-chest)" />

      {/* Arms */}
      <rect x="16" y="64" width="8" height="15" rx="4" fill="var(--companion-body-2)" />
      <rect x="72" y="64" width="8" height="15" rx="4" fill="var(--companion-body-2)" />

      {/* Active halo. Only rendered for a real active state, and only animated
          when motion is allowed. */}
      {active && !reducedMotion && <circle className="companion-robot__halo" cx="48" cy="45" r="40" fill="none" stroke="var(--companion-halo)" strokeWidth="2" />}
    </svg>
  );
}