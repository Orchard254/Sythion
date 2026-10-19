import { useCallback, useEffect, useRef, useState } from 'react';
import { Icon } from './icons';
import { CompanionRobot, type CompanionMood } from './CompanionRobot';
import { useVoiceCapture } from '../hooks/useVoiceCapture';
import { useAppStore } from '../store/appStore';

/** Pointer travel, in px, that turns a press on the robot into a window drag. */
const DRAG_SLOP_PX = 4;

/* Minimal floating Companion.
 * ---------------------------------------------------------------------------
 * Composition rules honoured here:
 *  - the robot and the capsule are separate elements with a clear gap;
 *  - there is NO shared background or container wrapping both;
 *  - no Sythion name, logo, label, history, task list or dashboard appears
 *    inside the Companion.
 *
 * HOSTING
 * -------
 * This component is rendered on two surfaces:
 *
 *  - the real desktop overlay, mounted by `CompanionSurface` when the window was
 *    opened with `?surface=companion` (src-tauri/src/main.rs ->
 *    `create_companion_window`: transparent, undecorated, always-on-top,
 *    skip-taskbar, 360x300, bottom-right);
 *  - the development-only preview route (`#/companion`) over a transparent page.
 *
 * The accessible name on the input is visually hidden. It is a label for screen
 * readers, required for the control to be operable by keyboard, not visible
 * branding. Idle state renders no visible text at all.
 *
 * CLICK-THROUGH
 * -------------
 * Whole-window click-through (`set_ignore_cursor_events`) is deliberately NOT
 * used: Tauri v2 exposes it, but only at whole-window granularity, so enabling
 * it would make this capsule's own microphone, keyboard and send controls
 * unusable. Per-region click-through needs per-pixel hit testing (WM_NCHITTEST)
 * driven by the webview alpha mask - a Windows-specific native change that is
 * out of scope. See `CompanionSurface.tsx` for the same note at the call site.
 */

export interface CompanionProps {
  /** Delivers the composed text. A confirmation, not an authorization. */
  onSubmit: (text: string) => Promise<void> | void;
  /** True only while a submission is genuinely in flight. */
  submitting?: boolean;
  /** The most recent assistant response, when one exists. */
  caption?: string | null;
  /** True only while audio playback is genuinely active. */
  speaking?: boolean;
  /** Real STT availability from GET /voice/assets. */
  sttAvailable: boolean;
  /** A real error from a real attempt. */
  error?: string | null;
  /** Forces the capsule open, e.g. to review a state that needs attention. */
  open?: boolean;
  reducedMotion?: boolean;
  /**
   * Fired once a press on the robot has clearly become a drag rather than a
   * click. The overlay uses this to start a native window drag.
   *
   * Deliberately NOT fired on pointer-down: on Windows, `startDragging()` puts
   * the mouse into a modal move loop, which captures the release, so the browser
   * never synthesises a `click` and the robot would become impossible to click.
   * Waiting for real movement keeps click and drag distinct.
   *
   * Attached to the robot only, so the text field and capsule buttons can never
   * start a drag.
   */
  onRobotDragStart?: () => void;
  /**
   * Dismiss affordance. Rendered inside the capsule only, so the idle state
   * stays "the minimal floating robot visible" and nothing else.
   */
  onClose?: () => void;
}

export function Companion({
  onSubmit,
  submitting = false,
  caption = null,
  speaking = false,
  sttAvailable,
  error = null,
  open,
  reducedMotion = false,
  onRobotDragStart,
  onClose,
}: CompanionProps) {
  const transcriptDraft = useAppStore((s) => s.transcriptDraft);
  const setTranscriptDraft = useAppStore((s) => s.setTranscriptDraft);
  const isVoiceEnabled = useAppStore((s) => s.isVoiceEnabled);
  const setVoiceEnabled = useAppStore((s) => s.setVoiceEnabled);
  const isListening = useAppStore((s) => s.isListening);
  const isSpeakingNow = useAppStore((s) => s.isVoiceSpeaking);

  const { toggleListening } = useVoiceCapture();

  const [expanded, setExpanded] = useState(false);
  const [micError, setMicError] = useState<string | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  // Set when a press on the robot became a window drag rather than a click, so
  // finishing a drag does not also toggle the capsule.
  const draggedRef = useRef(false);
  const pressedRef = useRef(false);
  const pressOriginRef = useRef<{ x: number; y: number } | null>(null);

  // The capsule opens when interaction is required, and closes when it is not.
  const visible = open ?? expanded;
  useEffect(() => {
    if (visible) inputRef.current?.focus();
  }, [visible]);

  const onToggleVoice = useCallback(async () => {
    setMicError(null);
    if (isVoiceEnabled) {
      setVoiceEnabled(false);
      return;
    }
    setVoiceEnabled(true);
    try {
      await toggleListening();
    } catch (err) {
      setMicError(err instanceof Error ? err.message : 'Microphone unavailable');
    }
  }, [isVoiceEnabled, setVoiceEnabled, toggleListening]);

  const text = transcriptDraft.trim();
  // A previous failure must not disable sending: keeping the input visible after
  // an error is only useful if the message can be corrected and retried.
  const canSend = text.length > 0 && !submitting;

  const send = useCallback(() => {
    const value = transcriptDraft.trim();
    if (!value || submitting) return;
    void onSubmit(value);
  }, [onSubmit, submitting, transcriptDraft]);

  // Mood is derived, never timed. `speaking` only when a playback signal is
  // real; `unavailable` only when speech assets genuinely are not.
  const mood: CompanionMood = error || micError
    ? 'unavailable'
    : speaking
      ? 'speaking'
      : submitting
        ? 'thinking'
        : isListening
          ? isSpeakingNow
            ? 'listening'
            : 'listening'
          : sttAvailable
            ? 'idle'
            : 'unavailable';

  const statusLine = error ?? micError ?? null;

  return (
    // No shared container: the robot and the capsule are siblings in flow, each
    // carrying its own surface, with the gap expressed as margin.
    <div className="companion">
      <div className="companion__robot-slot">
        <button
          type="button"
          className="companion__robot-button"
          aria-expanded={visible}
          aria-label={visible ? 'Hide Sythion input' : 'Show Sythion input'}
          title="Show Sythion input"
          onPointerDown={(e) => {
            pressedRef.current = true;
            draggedRef.current = false;
            pressOriginRef.current = { x: e.clientX, y: e.clientY };
          }}
          onPointerMove={(e) => {
            if (!pressedRef.current || draggedRef.current) return;
            const origin = pressOriginRef.current;
            if (!origin) return;
            // A few pixels of slop, so a slightly shaky click still counts as a
            // click while a deliberate move becomes a drag.
            if (Math.abs(e.clientX - origin.x) < DRAG_SLOP_PX) return;
            if (Math.abs(e.clientY - origin.y) < DRAG_SLOP_PX) return;
            // Claim the gesture BEFORE the native drag starts. Once
            // startDragging() begins its modal move loop the webview stops
            // receiving pointer events, so nothing after this point can run.
            draggedRef.current = true;
            onRobotDragStart?.();
          }}
          onPointerUp={() => {
            pressedRef.current = false;
            pressOriginRef.current = null;
          }}
          onPointerCancel={() => {
            pressedRef.current = false;
            pressOriginRef.current = null;
          }}
          onClick={() => {
            // A native drag swallows the click on Windows; this guards the case
            // where it does not.
            if (draggedRef.current) {
              draggedRef.current = false;
              return;
            }
            setExpanded((v) => !v);
          }}
        >
          <CompanionRobot mood={mood} reducedMotion={reducedMotion} />
        </button>
      </div>

      {visible && (
        <div className="companion__capsule">
          {/* Dismiss affordance. Icon-only with an accessible name: the overlay
              has no title bar and no taskbar button, so without this there
              would be no way to close it. */}
          {onClose && (
            <button
              type="button"
              className="companion__close"
              aria-label="Close the Sythion Companion"
              title="Close the Companion"
              onClick={onClose}
            >
              <Icon name="close" size={12} />
            </button>
          )}

          {/* Status or caption. Only rendered when there is something real. */}
          {(statusLine || caption) && (
            <p className={`companion__line${statusLine ? ' companion__line--error' : ''}`}>
              {statusLine ?? caption}
            </p>
          )}

          {/* When speech is genuinely unavailable the keyboard path must still
              work, so the input and send are always present. Only the
              microphone is withheld. Hiding the input on an error would also
              make a failed send impossible to retry. */}
          {!sttAvailable && !micError && (
            <div className="companion__unavailable">
              <Icon name="alert" size={13} />
              <span>Speech recognition is not available on this machine. Type instead.</span>
            </div>
          )}

          <>
            <label className="sr-only" htmlFor="companion-input">
              Message Sythion
            </label>
            <textarea
              id="companion-input"
              ref={inputRef}
              className="companion__input"
              rows={1}
              value={transcriptDraft}
              placeholder={isListening ? 'Listening…' : 'Say or type…'}
              disabled={submitting}
              onChange={(e) => setTranscriptDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
                if (e.key === 'Escape') setExpanded(false);
              }}
            />

            <div className="companion__controls">
              <button
                type="button"
                className={`companion__btn${isListening ? ' companion__btn--listening' : ''}`}
                aria-pressed={isListening}
                aria-label={isListening ? 'Stop listening' : 'Start voice input'}
                title={
                  sttAvailable
                    ? isListening
                      ? 'Stop listening'
                      : 'Start voice input'
                    : 'Speech recognition is not available on this machine'
                }
                disabled={!sttAvailable || submitting}
                onClick={() => void onToggleVoice()}
              >
                <Icon name="microphone" size={15} />
              </button>

              <button
                type="button"
                className="companion__btn companion__btn--send"
                aria-label="Send to Sythion"
                title="Send to Sythion"
                disabled={!canSend}
                onClick={send}
              >
                <Icon name="send" size={15} />
              </button>
            </div>
          </>
        </div>
      )}
    </div>
  );
}