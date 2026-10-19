import { useState } from 'react';
import { Companion } from '../components/Companion';
import { Button, DemoTag, Notice, Panel, Segmented, ScreenHead } from '../components/ui';
import { useVoiceAssets } from '../hooks/useBackend';
import { useAppStore } from '../store/appStore';

/* Development-only visual preview for the Companion.
 *
 * Reachable at `#/companion`. It is NOT in the navigation, and it exists
 * because the project has no transparent always-on-top window — see the scope
 * note in components/Companion.tsx.
 *
 * The stage can also be set with `?stage=idle|input|processing|listening|unavailable`,
 * which makes each state deep-linkable for review instead of requiring a click.
 * Only speech availability and transcription are real; the rest are staged. */

type Stage = 'idle' | 'input' | 'processing' | 'unavailable' | 'listening';

const STAGES: Array<{ value: Stage; label: string; blurb: string }> = [
  { value: 'idle', label: 'Idle', blurb: 'Robot only. No capsule, no name, no history.' },
  {
    value: 'input',
    label: 'Input',
    blurb: 'Capsule open, editable transcription, microphone and send.',
  },
  {
    value: 'processing',
    label: 'Processing',
    blurb: 'Shown only while a submission is genuinely in flight.',
  },
  {
    value: 'listening',
    label: 'Listening',
    blurb: 'Shown only while a real microphone session is open.',
  },
  {
    value: 'unavailable',
    label: 'Unavailable',
    blurb: 'Honest failure or missing speech asset. Never a fake idle.',
  },
];

function initialStage(): Stage {
  if (typeof window === 'undefined') return 'input';
  const raw = new URLSearchParams(window.location.search).get('stage');
  return STAGES.some((s) => s.value === raw) ? (raw as Stage) : 'input';
}

export function CompanionPreviewScreen() {
  const assets = useVoiceAssets();
  const transcriptDraft = useAppStore((s) => s.transcriptDraft);
  const setTranscriptDraft = useAppStore((s) => s.setTranscriptDraft);

  const [stage, setStage] = useState<Stage>(initialStage);
  const [sent, setSent] = useState<string | null>(null);

  // Real signal, not a staged value.
  const sttAvailable = assets.data?.stt_installed ?? false;
  const dataMode = useAppStore((s) => s.dataMode);

  const stageInfo = STAGES.find((s) => s.value === stage)!;
  const unavailable = stage === 'unavailable';
  const processing = stage === 'processing';

  return (
    <div className="screen">
      <div className="screen__inner">
        <ScreenHead
          eyebrow="Development preview"
          title="Companion"
          description="Visual review surface for the floating Companion. It is not a desktop overlay: the Tauri shell has one opaque window and no transparency or always-on-top mechanism, so shipping this properly needs a second window — a separate bounded task."
          actions={
            <>
              <Badge_Preview />
              <Button
                size="sm"
                variant="ghost"
                icon="external"
                onClick={() => window.open('#/companion', '_blank')}
              >
                Open alone
              </Button>
            </>
          }
        />

        <Notice tone="warn" icon="alert">
          <span>
            <strong>Staged preview.</strong> The staged states below are switched by hand for review.
            Speech availability is real, read from <span className="t-mono">GET /voice/assets</span>
            {dataMode === 'preview' ? ', and preview mode is active so there is no backend.' : '.'}
          </span>
        </Notice>

        <div className="grid grid--aside">
          {/* The Companion, over a transparent page so the absence of any
              shared container is visible during review. */}
          <Panel title="Live surface" icon="eye" tight>
            <div className="companion-stage">
              <Companion
                open={stage !== 'idle'}
                onSubmit={(value) => {
                  setTranscriptDraft('');
                  setSent(value);
                }}
                submitting={processing}
                caption={stage === 'input' && !sent ? 'Ready when you are.' : sent}
                speaking={false}
                sttAvailable={stage === 'listening' ? true : sttAvailable}
                error={
                  unavailable
                    ? sttAvailable
                      ? 'Speech recognition is not available on this machine.'
                      : 'The microphone could not be opened.'
                    : null
                }
              />
            </div>
          </Panel>

          <div className="stack">
            <Panel title="Stage" icon="layers" tight>
              <div className="stack stack--tight">
                <Segmented
                  value={stage}
                  onChange={setStage}
                  ariaLabel="Companion stage"
                  options={STAGES.map((s) => ({ value: s.value, label: s.label }))}
                />
                <p className="t-muted">{stageInfo.blurb}</p>
              </div>
            </Panel>

            <Panel title="Review checklist" icon="check" tight>
              <ul className="check-list">
                <li>Robot sits above a separate capsule with a clear gap.</li>
                <li>No shared background wraps the robot and the capsule.</li>
                <li>No Sythion name, logo, label, history or task list inside.</li>
                <li>Idle shows only the robot.</li>
                <li>Transcription is editable before sending.</li>
                <li>Processing appears only while a request is in flight.</li>
                <li>Speaking never appears without a real playback signal.</li>
                <li>Missing speech assets produce an honest unavailable state.</li>
                <li>Keyboard reachable, focus visible, Escape closes the capsule.</li>
                <li>Reduced motion disables the halo animation.</li>
              </ul>
            </Panel>

            <Panel title="Actual signals" icon="activity" tight>
              <div className="stack stack--tight">
                <SignalRow label="Speech-to-text installed" value={String(assets.data?.stt_installed ?? 'unknown')} />
                <SignalRow label="STT model" value={assets.data?.stt_model || '—'} />
                <SignalRow label="TTS provider" value={assets.data?.tts_provider || '—'} />
                <SignalRow label="Transcript draft" value={transcriptDraft || '(empty)'} />
                <SignalRow label="Last sent" value={sent || '(none)'} />
                <p className="t-faint">
                  Sending records your text and nothing more. It does not authorize a consequential
                  action; authorization is enforced by the backend executor policy for every tool call.
                </p>
              </div>
            </Panel>
          </div>
        </div>
      </div>
    </div>
  );
}

function SignalRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="kv">
      <span className="kv__k">{label}</span>
      <span className="kv__v t-mono">{value}</span>
    </div>
  );
}

function Badge_Preview() {
  const dataMode = useAppStore((s) => s.dataMode);
  return dataMode === 'preview' ? <DemoTag /> : null;
}