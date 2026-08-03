import axios from 'axios';
import type { Task, HealthStatus, ToolInfo, TaskEvent, VoiceAssets } from '../types';

const API_BASE_URL = '/api/v1';

// Get control token from sessionStorage (set by Tauri on launch)
const getControlToken = () => {
  const desktopToken = sessionStorage.getItem('jarvis_control_token');
  if (desktopToken) {
    return desktopToken;
  }

  if (import.meta.env.DEV) {
    return import.meta.env.VITE_JARVIS_CONTROL_TOKEN || '';
  }

  return '';
};

// Create axios instance with authentication
const apiClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
  },
});

// Add control token to all requests
apiClient.interceptors.request.use((config) => {
  const token = getControlToken();
  if (token) {
    config.headers['X-Control-Token'] = token;
  }
  return config;
});

export const healthService = {
  check: async (): Promise<HealthStatus> => {
    const response = await apiClient.get('/health');
    return response.data;
  },
};

export const taskService = {
  list: async (): Promise<Task[]> => {
    const response = await apiClient.get('/tasks');
    return response.data.tasks || [];
  },

  get: async (taskId: string): Promise<Task> => {
    const response = await apiClient.get(`/tasks/${taskId}`);
    return response.data;
  },

  create: async (originalRequest: string): Promise<Task> => {
    const response = await apiClient.post('/tasks', { original_request: originalRequest });
    return response.data;
  },

  cancel: async (taskId: string): Promise<void> => {
    await apiClient.post(`/tasks/${taskId}/cancel`);
  },

  resume: async (taskId: string): Promise<Task> => {
    const response = await apiClient.post(`/tasks/${taskId}/resume`);
    return response.data;
  },

  reply: async (taskId: string, answer: string): Promise<Task> => {
    const response = await apiClient.post(`/tasks/${taskId}/reply`, { answer });
    return response.data;
  },

  events: async (taskId: string, afterSequence = 0): Promise<TaskEvent[]> => {
    const response = await apiClient.get(`/tasks/${taskId}/events`, {
      params: { after_sequence: afterSequence },
    });
    return response.data.events || [];
  },
};

export interface VoiceSegment {
  segment_id: string;
  text: string;
  stop_detected: boolean;
  audio_deleted: boolean;
  stt_available: boolean;
}

export interface VoiceStartResult {
  session_id: string;
  status: string;
  stt_available: boolean;
  recording_enabled: boolean;
}

export interface VoiceAudioResult {
  session_id: string;
  segments: VoiceSegment[];
  is_speaking: boolean;
  stt_available: boolean;
}

export const voiceService = {
  startSession: async (): Promise<VoiceStartResult> => {
    const response = await apiClient.post('/voice/start');
    return response.data;
  },

  sendAudioFrame: async (sessionId: string, pcm16Mono: Uint8Array): Promise<VoiceAudioResult> => {
    const audioBase64 = arrayBufferToBase64(pcm16Mono);
    const response = await apiClient.post(`/voice/${sessionId}/audio`, {
      session_id: sessionId,
      audio_data: audioBase64,
      sample_rate: 16000,
      channels: 1,
    });
    return response.data;
  },

  endSession: async (sessionId: string): Promise<void> => {
    await apiClient.post(`/voice/${sessionId}/end`);
  },

  speak: async (text: string): Promise<{ success: boolean }> => {
    const response = await apiClient.post('/voice/speak', { text });
    return response.data;
  },

  assets: async (): Promise<VoiceAssets> => {
    const response = await apiClient.get('/voice/assets');
    return response.data;
  },
};

export interface AppSettings {
  voice_enabled: boolean;
  voice_recording_enabled: boolean;
  voice_tts_voice: string;
  retention_days: number;
  file_roots: string[];
}

export const settingsService = {
  get: async (): Promise<AppSettings> => {
    const response = await apiClient.get('/settings');
    return response.data;
  },

  update: async (updates: Record<string, string | boolean | number>): Promise<AppSettings> => {
    const response = await apiClient.put('/settings', updates);
    return response.data.settings || {};
  },
};

export const toolService = {
  list: async (): Promise<ToolInfo[]> => {
    const response = await apiClient.get('/tools');
    return response.data.tools || [];
  },
};

function arrayBufferToBase64(buffer: Uint8Array): string {
  let binary = '';
  const chunkSize = 0x8000;
  for (let i = 0; i < buffer.length; i += chunkSize) {
    binary += String.fromCharCode(...buffer.subarray(i, i + chunkSize));
  }
  return btoa(binary);
}

export default apiClient;
