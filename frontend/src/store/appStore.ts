import { create } from 'zustand';
import type { Task, HealthStatus, TaskEvent } from '../types';

export type DataMode = 'live' | 'preview';

export interface AppNotification {
  id: string;
  tone: 'ok' | 'warn' | 'danger' | 'info' | 'accent';
  title: string;
  body?: string;
  createdAt: number;
  route?: string;
  /** A preview-mode notice is marked so it is never read as a real alert. */
  demo?: boolean;
}

interface AppState {
  // Tasks
  tasks: Task[];
  activeTaskId: string | null;
  isLoading: boolean;
  error: string | null;

  // Health
  healthStatus: HealthStatus | null;
  isConnected: boolean;

  // Voice
  isVoiceEnabled: boolean;
  isRecording: boolean;
  isListening: boolean;
  /** True when the sidecar VAD reported speech on the most recent audio frame. */
  isVoiceSpeaking: boolean;
  voiceSttAvailable: boolean | null;
  transcriptSegments: Array<{ id: string; text: string }>;
  transcriptDraft: string;
  recordingEnabled: boolean;

  // Settings
  retentionDays: number;
  fileRoots: string[];

  // Events (timeline for active task)
  activeTaskEvents: TaskEvent[];

  // Where screen data comes from. 'preview' is an explicit, user-chosen
  // demonstration mode entered when no backend is reachable.
  dataMode: DataMode;
  notifications: AppNotification[];
  unreadNotifications: number;

  // Actions
  setTasks: (tasks: Task[]) => void;
  setActiveTask: (taskId: string | null) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  setHealthStatus: (status: HealthStatus | null) => void;
  setConnected: (connected: boolean) => void;
  setVoiceEnabled: (enabled: boolean) => void;
  setRecording: (recording: boolean) => void;
  setListening: (listening: boolean) => void;
  setVoiceSpeaking: (speaking: boolean) => void;
  setVoiceSttAvailable: (available: boolean | null) => void;
  setTranscriptSegments: (segments: Array<{ id: string; text: string }>) => void;
  appendTranscriptSegment: (id: string, text: string) => void;
  setTranscriptDraft: (draft: string) => void;
  setRecordingEnabled: (enabled: boolean) => void;
  setRetentionDays: (days: number) => void;
  setFileRoots: (roots: string[]) => void;
  setActiveTaskEvents: (events: TaskEvent[]) => void;
  resetVoice: () => void;
  setDataMode: (mode: DataMode) => void;
  notify: (n: Omit<AppNotification, 'id' | 'createdAt'> & { id?: string; createdAt?: number }) => void;
  dismissNotification: (id: string) => void;
  clearNotifications: () => void;
}

/**
 * Preview mode can be entered from the URL (`?preview=1`) so a demonstration
 * view is shareable and survives a reload without a backend running.
 */
function initialDataMode(): DataMode {
  if (typeof window === 'undefined') return 'live';
  const params = new URLSearchParams(window.location.search);
  return params.get('preview') === '1' ? 'preview' : 'live';
}

export const useAppStore = create<AppState>((set) => ({
  // Initial state
  tasks: [],
  activeTaskId: null,
  isLoading: false,
  error: null,
  healthStatus: null,
  isConnected: false,
  isVoiceEnabled: false,
  isRecording: false,
  isListening: false,
  isVoiceSpeaking: false,
  voiceSttAvailable: null,
  transcriptSegments: [],
  transcriptDraft: '',
  recordingEnabled: false,
  retentionDays: 90,
  fileRoots: [],
  activeTaskEvents: [],
  dataMode: initialDataMode(),
  notifications: [],
  unreadNotifications: 0,

  // Actions
  setTasks: (tasks) => set({ tasks }),
  setActiveTask: (taskId) => set({ activeTaskId: taskId, activeTaskEvents: [] }),
  setLoading: (loading) => set({ isLoading: loading }),
  setError: (error) => set({ error }),
  setHealthStatus: (status) => set({ healthStatus: status }),
  setConnected: (connected) => set({ isConnected: connected }),
  setVoiceEnabled: (enabled) => set({ isVoiceEnabled: enabled }),
  setRecording: (recording) => set({ isRecording: recording }),
  setListening: (listening) => set({ isListening: listening }),
  setVoiceSpeaking: (speaking) => set({ isVoiceSpeaking: speaking }),
  setVoiceSttAvailable: (available) => set({ voiceSttAvailable: available }),
  setTranscriptSegments: (segments) => set({ transcriptSegments: segments }),
  appendTranscriptSegment: (id, text) =>
    set((state) => ({
      transcriptSegments: [...state.transcriptSegments, { id, text }],
      transcriptDraft: `${state.transcriptDraft}${state.transcriptDraft ? ' ' : ''}${text}`.trim(),
    })),
  setTranscriptDraft: (draft) => set({ transcriptDraft: draft }),
  setRecordingEnabled: (enabled) => set({ recordingEnabled: enabled }),
  setRetentionDays: (days) => set({ retentionDays: days }),
  setFileRoots: (roots) => set({ fileRoots: roots }),
  setActiveTaskEvents: (events) => set({ activeTaskEvents: events }),
  resetVoice: () =>
    set({
      isRecording: false,
      isListening: false,
      isVoiceSpeaking: false,
      transcriptSegments: [],
      transcriptDraft: '',
    }),

  setDataMode: (dataMode) => set({ dataMode }),

  notify: (n) =>
    set((state) => {
      const id = n.id ?? `n${Date.now()}${state.notifications.length}`;
      const notification: AppNotification = {
        ...n,
        id,
        createdAt: n.createdAt ?? Date.now(),
      };
      // Keep the tray bounded; newest first.
      const notifications = [notification, ...state.notifications].slice(0, 40);
      return { notifications, unreadNotifications: state.unreadNotifications + 1 };
    }),

  dismissNotification: (id) =>
    set((state) => {
      const target = state.notifications.find((n) => n.id === id);
      return {
        notifications: state.notifications.filter((n) => n.id !== id),
        unreadNotifications: target
          ? Math.max(0, state.unreadNotifications - 1)
          : state.unreadNotifications,
      };
    }),

  clearNotifications: () => set({ notifications: [], unreadNotifications: 0 }),
}));
