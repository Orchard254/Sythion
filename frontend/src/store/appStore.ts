import { create } from 'zustand';
import type { Task, HealthStatus, TaskEvent } from '../types';

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
  voiceSttAvailable: boolean | null;
  transcriptSegments: Array<{ id: string; text: string }>;
  transcriptDraft: string;
  recordingEnabled: boolean;

  // Settings
  retentionDays: number;
  fileRoots: string[];

  // Events (timeline for active task)
  activeTaskEvents: TaskEvent[];

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
  setVoiceSttAvailable: (available: boolean | null) => void;
  setTranscriptSegments: (segments: Array<{ id: string; text: string }>) => void;
  appendTranscriptSegment: (id: string, text: string) => void;
  setTranscriptDraft: (draft: string) => void;
  setRecordingEnabled: (enabled: boolean) => void;
  setRetentionDays: (days: number) => void;
  setFileRoots: (roots: string[]) => void;
  setActiveTaskEvents: (events: TaskEvent[]) => void;
  resetVoice: () => void;
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
  voiceSttAvailable: null,
  transcriptSegments: [],
  transcriptDraft: '',
  recordingEnabled: false,
  retentionDays: 90,
  fileRoots: [],
  activeTaskEvents: [],

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
      transcriptSegments: [],
      transcriptDraft: '',
    }),
}));
