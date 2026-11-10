import { useCallback, useEffect, useRef, useState } from 'react';
import { Companion } from './Companion';
import { useActions, useVoiceAssets } from '../hooks/useBackend';
import { useAppStore } from '../store/appStore';
import { voiceService } from '../services/api';
import { isTauri } from '../services/bridge';

/* Companion overlay surface.
 *
 * Rendered instead of the whole Control Center when the window was opened with
 * `?surface=companion`. Same bundle, same React entry, same zustand stores as
 * the Control Center — no second entry point and no second state system.
 *
 * Two deliberate differences from the browser preview:
 *
 *  1. It does NOT wait on the app bootstrap. `useAppInit` renders a full-screen
 *     "Connecting to Sythion" / "backend unreachable" shell, which would paint
 *     an opaque rectangle over the desktop. The overlay therefore renders
 *     immediately and reports an honest error only if a submission actually
 *     fails.
 *
 *  2. It never assumes speech is available. `stt_installed` comes from the real
 *     GET /voice/assets response, and when it is false the capsule reports that
 *     honestly rather than presenting an idle microphone.
 *
 * CLICK-THROUGH — documented limitation
 * -----------------------------------
 * Tauri v2 exposes `set_ignore_cursor_events`, and it is implemented for Windows
 * through `tao`, so no Win32 code or extra dependency is needed. However it is
 * WHOLE-WINDOW only: Win32's `WS_EX_TRANSPARENT` is a window style, not a
 * per-pixel hit test. Because the capsule's microphone, keyboard and send
 * controls live inside the same window rect, enabling it would make the
 * overlay's own controls unusable — which the brief explicitly forbids.
 *
 * So click-through is deliberately NOT enabled, and `set-ignore-cursor-events`
 * is deliberately NOT granted in capabilities/companion.json. Genuine
 * per-region click-through needs per-pixel hit-testing (WM_NCHITTEST) driven by
 * the webview's alpha mask — a Windows-specific native change with real
 * fragility risk, and out of scope here.
 *
 * KNOWN COST of that choice (mitigated below): the overlay's window rect is
 * 360x300 so the capsule has room to grow, but only the top-left ~110px is
 * opaque content. An interactive window swallows clicks meant for whatever is
 * behind it, so shrinking the window is not a free fix - it would clip the
 * capsule.
 *
 * CLICK-THROUGH (per-region)
 * --------------------------
 * `set_ignore_cursor_events` is whole-window, so it is driven by where the
 * cursor actually is. This component publishes the interactive rectangles; the
 * Rust poller (spawn_hit_test_poller in src-tauri/src/main.rs) reads the native
 * cursor position and toggles the window. The decision cannot live here: while
 * the window ignores cursor events the WebView receives no pointer input, so it
 * is structurally unable to notice the pointer returning.
 *
 * Rectangles are CSS pixels relative to the viewport, which is exactly what
 * `getBoundingClientRect` returns. `devicePixelRatio` is sent alongside so the
 * native side converts to physical pixels without guessing the DPI.
 *
 * Movement is provided by dragging the robot, which is the only element that
 * starts a drag. The drag is fired from pointermove after a few pixels of
 * travel, not from pointerdown - see the note in Companion.tsx. While a drag is
 * in progress the poller is told to keep the window interactive. */

export function CompanionSurface() {
  const assets = useVoiceAssets();
  const actions = useActions();
  const setTranscriptDraft = useAppStore((s) => s.setTranscriptDraft);

  const [submitting, setSubmitting] = useState(false);
  const [surfaceError, setSurfaceError] = useState<string | null>(null);
  const [caption, setCaption] = useState<string | null>(null);
  const [speaking, setSpeaking] = useState(false);
  const reducedMotionRef = useRef(false);
  const surfaceRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef(false);
  // Reuse the same backend request identity for an identical retry after an
  // unknown transport outcome. Do not automatically retry or mint a second
  // exchange on an exception. A different draft starts a new request.
  const pendingTurnRef = useRef<{ text: string; id: string } | null>(null);

  // Honour the OS reduced-motion setting. Read once here rather than adding a
  // media-query listener, because the robot's only motion is decorative and a
  // static snapshot is sufficient.
  useEffect(() => {
    if (typeof window !== 'undefined' && window.matchMedia) {
      reducedMotionRef.current = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    }
  }, []);

  // STT availability is a real reported value. `null` means the assets query has
  // not answered yet, which is not the same as "unavailable", so the Companion
  // is told the truth once known rather than guessing early.
  const sttAvailable = assets.data?.stt_installed ?? false;

  const onSubmit = useCallback(
    async (text: string) => {
      if (!actions.live) {
        // `live` is false when the backend is unreachable; the message must
        // say exactly that, not imply some other mode the user never chose.
        setSurfaceError(
          'Sythion is not reachable right now, so nothing was sent. Try again when the local runtime is back.'
        );
        return;
      }
      setSubmitting(true);
      setSurfaceError(null);
      try {
        // The backend REQUIRES client_request_id and uses it to deduplicate
        // possible replays. A conversational send must never silently fall
        // back to executable POST /tasks.
        if (typeof actions.assistantTurn !== 'function') {
          throw new Error('Conversational transport is unavailable.');
        }
        const reserved = pendingTurnRef.current?.text === text
          ? pendingTurnRef.current
          : {
              text,
              id: typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
                ? crypto.randomUUID()
                : `companion-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`,
            };
        pendingTurnRef.current = reserved;
        const turn = await actions.assistantTurn(text, reserved.id);
        pendingTurnRef.current = null;
        const reply = turn?.assistant_text?.trim() ?? '';
        setCaption(reply || null);
        setTranscriptDraft('');
        setSubmitting(false);

        // Spoken reply by default, mirroring the Assistant screen: unavailable
        // TTS is a known, expected condition on machines without a usable voice,
        // so a missing speaker never becomes an alarming status line. Genuine,
        // unrelated playback failures still surface honestly.
        if (reply) {
          setSpeaking(true);
          try {
            const result = await voiceService.speak(reply.slice(0, 1000));
            if (!result?.success) return;
          } catch (err) {
            const message = err instanceof Error ? err.message : 'Playback failed. Read the reply below.';
            if (/playback is unavailable|TTS (is )?unavailable|no (usable )?voice/i.test(message)) return;
            setSurfaceError(message);
          } finally {
            setSpeaking(false);
          }
        }
      } catch {
        // A rejected request is not always proof of non-dispatch. Keep the
        // draft and request identity for an explicit user retry, never make
        // an optimistic "not sent" claim or display raw backend exception text.
        setSurfaceError('Could not confirm this message. It was not retried automatically. Check the conversation before trying again.');
      } finally {
        setSubmitting(false);
      }
    },
    [actions, setTranscriptDraft]
  );

  /* Publish the interactive rectangles to the native hit-test poller.
   *
   * `PUBLISH_INTERVAL_MS` is deliberately non-zero: a text caret or a spinner can
   * repaint the capsule many times a second, and one invoke per repaint would be
   * pure overhead. Layout only really changes at discrete moments, so a short
   * coalescing window keeps the hit regions accurate without flooding IPC. */
const PUBLISH_INTERVAL_MS = 60;
/** Extra padding around each region, in CSS px. The painted edge of a neumorphic
    surface fades out, and a pointer 1-2px outside the visible element should
    still feel like it is on the control. */
const REGION_PADDING_PX = 4;

const publishRegions = useCallback(async () => {
    if (!isTauri()) return;
    const selector = '.companion__robot-slot, .companion__capsule';
    const regions: { x: number; y: number; w: number; h: number }[] = [];
    document.querySelectorAll(selector).forEach((el) => {
      const r = el.getBoundingClientRect();
      if (r.width <= 0 || r.height <= 0) return;
      regions.push({
        x: r.left - REGION_PADDING_PX,
        y: r.top - REGION_PADDING_PX,
        w: r.width + REGION_PADDING_PX * 2,
        h: r.height + REGION_PADDING_PX * 2,
      });
    });
    try {
      const { invoke } = await import('@tauri-apps/api/core');
      await invoke('set_companion_hit_regions', {
        regions,
        scale: window.devicePixelRatio,
        dragging: draggingRef.current,
      });
    } catch {
      // Not running under the Tauri shell, or the window is gone. The overlay is
      // simply not click-through driven; every control still works.
    }
  }, []);

const onRobotDragStart = useCallback(() => {
    if (!isTauri()) return;
    // A native drag moves the window with the cursor and `inner_position()` can
    // lag a frame, so the poller is told to stay interactive for its duration.
    draggingRef.current = true;
    void publishRegions();
    void (async () => {
      try {
        const { getCurrentWindow } = await import('@tauri-apps/api/window');
        await getCurrentWindow().startDragging();
      } catch {
        // Dragging is a convenience. If the platform refuses, the robot still
        // toggles the capsule; nothing here is load-bearing.
      } finally {
        draggingRef.current = false;
        void publishRegions();
      }
    })();
  }, [publishRegions]);

/* Republish whenever the rendered Companion layout changes.
   *
   * `Companion` owns its own expanded state, so rather than widening its public
   * API just to be told about capsule visibility, observe the DOM. The capsule
   * mounting/unmounting, an error line appearing, or the caret resizing the text
   * field all show up as subtree mutations, and the resize listener covers a
   * window-level or DPI change. */
useEffect(() => {
    const node = surfaceRef.current;
    if (!node || !isTauri()) return;

    let timer: number | undefined;
    const schedule = () => {
      if (timer !== undefined) window.clearTimeout(timer);
      timer = window.setTimeout(() => void publishRegions(), PUBLISH_INTERVAL_MS);
    };

    const observer = new MutationObserver(schedule);
    observer.observe(node, {
      childList: true,
      subtree: true,
      attributes: true,
      characterData: true,
    });
    window.addEventListener('resize', schedule);
    void publishRegions();

    return () => {
      observer.disconnect();
      window.removeEventListener('resize', schedule);
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [publishRegions]);

  const onClose = useCallback(() => {
    if (!isTauri()) return;
    void (async () => {
      try {
        const { invoke } = await import('@tauri-apps/api/core');
        await invoke('set_companion_visible', { visible: false });
      } catch (err) {
        // Hiding is best-effort, but a swallowed failure would leave the user
        // with no explanation of why the overlay stayed visible. Surface it both
        // as a visible error on the capsule and in the dev/Rust log stream, and
        // keep the window alive so the user can retry or exit from the Control
        // Center.
        console.warn('[sythion] could not hide the Companion overlay:', err);
        setSurfaceError(
          err instanceof Error ? err.message : 'Could not hide the Companion overlay.',
        );
      }
    })();
  }, []);

  return (
    <div className="surface-companion" ref={surfaceRef}>
      <Companion
        onSubmit={onSubmit}
        onRobotDragStart={onRobotDragStart}
        onClose={onClose}
        submitting={submitting}
        sttAvailable={sttAvailable}
        error={surfaceError}
        caption={caption}
        speaking={speaking}
        reducedMotion={reducedMotionRef.current}
      />
    </div>
  );
}