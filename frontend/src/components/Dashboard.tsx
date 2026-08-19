import { useState, useRef, useCallback, useEffect } from 'react';
import { taskService, voiceService, settingsService } from '../services/api';
import { useAppStore } from '../store/appStore';
import { useVoiceCapture } from '../hooks/useVoiceCapture';

export function Dashboard() {
  const [newTaskRequest, setNewTaskRequest] = useState('');
  const [voiceStatus, setVoiceStatus] = useState('Idle');
  const [expandedTaskId, setExpandedTaskId] = useState<string | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const [voiceAssets, setVoiceAssets] = useState<{
    stt_installed: boolean;
    stt_sha256_verified: boolean;
    tts_voice: string;
  } | null>(null);
  const [settingsError, setSettingsError] = useState('');

  const {
    tasks,
    activeTaskId,
    setActiveTask,
    setTasks,
    isVoiceEnabled,
    setVoiceEnabled,
    transcriptDraft,
    setTranscriptDraft,
    voiceSttAvailable,
    retentionDays,
    setRetentionDays,
    fileRoots,
    setFileRoots,
    activeTaskEvents,
    setActiveTaskEvents,
  } = useAppStore();

  const stopScheduledRef = useRef(false);

  const {
    isListening,
    stopDetected,
    toggleListening,
    clearStopDetected,
  } = useVoiceCapture();

  const pollTasks = useCallback(async () => {
    try {
      const list = await taskService.list();
      setTasks(list);
    } catch (error) {
      console.error('Failed to poll tasks:', error);
    }
  }, [setTasks]);

  const hasActiveTasks = tasks.some((t) =>
    t.state === 'running' || t.state === 'waiting_for_user' || t.state === 'interrupted'
  );

  const activeTask = tasks.find((t) => t.id === activeTaskId) || null;

  // Load tasks + voice assets + settings on mount
  useEffect(() => {
    pollTasks();
    voiceService.assets().then(setVoiceAssets).catch(() => {});
    settingsService.get().then((s) => {
      setRetentionDays(s.retention_days);
      setFileRoots(s.file_roots || []);
    }).catch(() => {});
  }, [pollTasks, setRetentionDays, setFileRoots]);

  // Adaptive polling: fast while any task is in flight, slow when idle.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    let cancelled = false;

    const schedule = () => {
      timer = setTimeout(async () => {
        await pollTasks();
        if (!cancelled) schedule();
      }, hasActiveTasks ? 2000 : 15000);
    };
    schedule();

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [hasActiveTasks, pollTasks]);

  // Fetch event timeline for the expanded/active task.
  useEffect(() => {
    if (!activeTask) {
      setActiveTaskEvents([]);
      return;
    }
    let cancelled = false;
    const load = async () => {
      try {
        const events = await taskService.events(activeTask.id);
        if (!cancelled) setActiveTaskEvents(events);
      } catch {
        if (!cancelled) setActiveTaskEvents([]);
      }
    };
    load();
    const timer = setInterval(load, 5000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [activeTask, setActiveTaskEvents]);

  const handleCreateTask = async (e?: React.FormEvent) => {
    e?.preventDefault();
    const request = transcriptDraft.trim() || newTaskRequest.trim();
    if (!request) return;

    // A spoken stop was detected: never submit it as a task.
    if (stopDetected) {
      clearStopDetected();
      setTranscriptDraft('');
      setNewTaskRequest('');
      setVoiceStatus('Stop phrase detected — not submitted');
      return;
    }

    try {
      const task = await taskService.create(request);
      setTasks([...tasks, task]);
      setNewTaskRequest('');
      setTranscriptDraft('');
      setActiveTask(task.id);
      pollTasks();
    } catch (error) {
      console.error('Failed to create task:', error);
    }
  };

  const handleCancelTask = async (taskId: string) => {
    try {
      await taskService.cancel(taskId);
      setTasks(tasks.map((t) =>
        t.id === taskId ? { ...t, state: 'cancelled' as const } : t
      ));
      if (activeTaskId === taskId) {
        setActiveTask(null);
      }
      pollTasks();
    } catch (error) {
      console.error('Failed to cancel task:', error);
    }
  };

  const handleResumeTask = async (taskId: string) => {
    try {
      const task = await taskService.resume(taskId);
      setTasks(tasks.map((t) =>
        t.id === taskId ? { ...t, state: task.state } : t
      ));
      setActiveTask(task.id);
      pollTasks();
    } catch (error) {
      console.error('Failed to resume task:', error);
    }
  };

  const handleToggleVoice = async () => {
    if (isVoiceEnabled) {
      setVoiceEnabled(false);
    } else {
      setVoiceEnabled(true);
      try {
        await toggleListening();
      } catch (error) {
        setVoiceStatus('Microphone unavailable: ' + (error as Error).message);
      }
    }
  };

  const handleSpeakResult = async () => {
    if (!activeTask?.final_result) return;
    // Local TTS only for the concise evidence-based final result.
    await voiceService.speak(activeTask.final_result.slice(0, 500));
  };

  const handleSaveSettings = async () => {
    setSettingsError('');
    try {
      const updated = await settingsService.update({
        retention_days: retentionDays,
        file_roots: fileRoots.join(','),
        voice_recording_enabled: false,
      });
      setRetentionDays(Number(updated.retention_days) || retentionDays);
      setFileRoots(updated.file_roots ?? fileRoots);
      setShowSettings(false);
    } catch (error) {
      setSettingsError((error as Error).message || 'Failed to save settings');
    }
  };

  // Drafted stop: if the user typed a stop phrase, still allow submit (they
  // confirmed). Only a spoken stop is treated as an interrupt. We cancel the
  // running task when a spoken stop arrives while a task is active.
  useEffect(() => {
    if (stopDetected && hasActiveTasks && activeTaskId && !stopScheduledRef.current) {
      stopScheduledRef.current = true;
      handleCancelTask(activeTaskId).finally(() => {
        stopScheduledRef.current = false;
        clearStopDetected();
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [stopDetected, hasActiveTasks, activeTaskId]);

  return (
    <div style={styles.container}>
      <header style={styles.header}>
        <h1 style={styles.title}>Sythion (Synth)</h1>
        <div style={styles.controls}>
          <button
            onClick={() => setShowSettings(!showSettings)}
            style={styles.button}
            title="Settings"
          >
            ⚙ Settings
          </button>
          <button
            onClick={handleToggleVoice}
            style={{
              ...styles.button,
              backgroundColor: isVoiceEnabled ? '#4CAF50' : '#666',
            }}
            title="Enable/disable voice processing"
          >
            🎤 Voice {isVoiceEnabled ? 'On' : 'Off'}
          </button>
          <button
            onClick={toggleListening}
            disabled={!isVoiceEnabled}
            style={{
              ...styles.button,
              backgroundColor: isListening ? '#f44336' : '#2196F3',
              opacity: isVoiceEnabled ? 1 : 0.5,
            }}
            title={isListening ? 'Stop listening' : 'Start listening'}
          >
            {isListening ? '⏹ Stop' : '🎙️ Listen'}
          </button>
          <span style={styles.voiceStatus}>{voiceStatus}</span>
        </div>
      </header>

      {showSettings && (
        <div style={styles.settingsPanel}>
          <h3 style={styles.sectionTitle}>Settings</h3>
          <label style={styles.settingRow}>
            Retention (days)
            <input
              type="number"
              min={1}
              value={retentionDays}
              onChange={(e) => setRetentionDays(Math.max(1, Number(e.target.value) || 90))}
              style={styles.input}
            />
          </label>
          <label style={styles.settingRow}>
            Approved file roots (comma separated)
            <input
              type="text"
              value={fileRoots.join(', ')}
              onChange={(e) =>
                setFileRoots(
                  e.target.value.split(',').map((r) => r.trim()).filter(Boolean)
                )
              }
              style={styles.input}
            />
          </label>
          <div style={styles.assetRow}>
            <strong>Voice assets:</strong>{' '}
            {voiceAssets
              ? `STT ${voiceAssets.stt_installed ? 'installed' : 'not installed'}${
                  voiceAssets.stt_sha256_verified ? ' (checksum verified)' : ''
                } · TTS ${voiceAssets.tts_voice || 'unavailable'}`
              : 'loading…'}
            {' '}
            <span style={styles.assetNote}>No downloads on startup.</span>
          </div>
          {settingsError && <div style={styles.errorText}>{settingsError}</div>}
          <div style={styles.settingsActions}>
            <button onClick={handleSaveSettings} style={styles.button}>
              Save
            </button>
            <button
              onClick={() => setShowSettings(false)}
              style={{ ...styles.button, backgroundColor: '#666' }}
            >
              Close
            </button>
          </div>
        </div>
      )}

      <main style={styles.main}>
        {/* New Task Form */}
        <form onSubmit={handleCreateTask} style={styles.form}>
          <input
            type="text"
            value={transcriptDraft || newTaskRequest}
            onChange={(e) => {
              setTranscriptDraft('');
              setNewTaskRequest(e.target.value);
            }}
            placeholder="What would you like me to do? (or use voice)"
            style={styles.input}
          />
          <button type="submit" style={styles.submitButton}>
            {stopDetected ? 'Stop detected' : 'Submit'}
          </button>
        </form>

        {/* Privacy / voice status strip */}
        {(isListening || stopDetected || voiceSttAvailable !== null) && (
          <div style={styles.privacyStrip}>
            {isListening && (
              <span style={{ color: '#f44336' }}>● Listening — capture stops when the dashboard hides</span>
            )}
            {stopDetected && (
              <span style={{ color: '#FF9800' }}> Spoken stop detected — cancelling active task</span>
            )}
            {voiceSttAvailable === false && (
              <span style={{ color: '#888' }}>
                {' '}Local STT not installed — use Settings to install pinned model.
              </span>
            )}
          </div>
        )}

        {/* Active Task Detail Card */}
        {activeTask && (
          <div style={styles.taskDetailCard}>
            <div style={styles.taskDetailHeader}>
              <span style={styles.taskDetailTitle}>Active task</span>
              <span style={{ ...styles.taskState, color: getStateColor(activeTask.state) }}>
                {activeTask.state}
              </span>
            </div>
            <p style={styles.taskRequest}>
              <strong>Goal:</strong> {activeTask.normalized_goal || activeTask.original_request}
            </p>
            <div style={styles.taskDetailGrid}>
              <span><strong>State:</strong> {activeTask.state}</span>
              <span><strong>Current action:</strong> {activeTask.active_action_id || '—'}</span>
              <span><strong>Actions run:</strong> {countActions(activeTaskEvents)}</span>
              <span><strong>Elapsed:</strong> {elapsedSince(activeTask.created_at)}</span>
            </div>
            {activeTask.pending_question && (
              <p style={styles.pendingQuestion}>
                <strong>Clarification:</strong> {activeTask.pending_question}
              </p>
            )}
            {activeTask.final_result && (
              <p style={styles.taskResult}>{activeTask.final_result}</p>
            )}
            {activeTask.state === 'failed' && activeTask.terminal_reason && (
              <p style={{ ...styles.taskResult, color: '#f44336' }}>
                <strong>Failed:</strong> {activeTask.terminal_reason}
              </p>
            )}
            {(activeTask.state === 'running' || activeTask.state === 'waiting_for_user') && (
              <button onClick={() => handleCancelTask(activeTask.id)} style={styles.stopButton}>
                ■ Stop
              </button>
            )}
            {activeTask.state === 'interrupted' && (
              <button onClick={() => handleResumeTask(activeTask.id)} style={styles.resumeButton}>
                Resume
              </button>
            )}
            {activeTask.final_result && (
              <button onClick={handleSpeakResult} style={styles.button}>
                🔊 Speak result
              </button>
            )}
          </div>
        )}

        {/* Tasks List */}
        <div style={styles.tasksSection}>
          <h2 style={styles.sectionTitle}>Tasks</h2>
          {tasks.length === 0 ? (
            <p style={styles.emptyMessage}>No tasks yet. Create one above!</p>
          ) : (
            <div style={styles.taskList}>
              {tasks.map((task) => (
                <div key={task.id}>
                  <div
                    onClick={() => setActiveTask(task.id)}
                    style={{
                      ...styles.taskCard,
                      borderLeft: `4px solid ${getStateColor(task.state)}`,
                      backgroundColor: activeTaskId === task.id ? '#333' : '#2a2a2a',
                    }}
                  >
                    <div style={styles.taskHeader}>
                      <span style={styles.taskState}>{task.state}</span>
                      <span style={styles.taskDate}>
                        {new Date(task.created_at).toLocaleString()}
                      </span>
                    </div>
                    <p style={styles.taskRequest}>{task.original_request}</p>
                    {task.final_result && (
                      <p style={styles.taskResult}>{task.final_result}</p>
                    )}
                    {task.state === 'failed' && task.terminal_reason && (
                      <p style={{ ...styles.taskResult, color: '#f44336' }}>
                        <strong>Failed:</strong> {task.terminal_reason}
                      </p>
                    )}
                    <div style={styles.taskActions}>
                      {(task.state === 'running' || task.state === 'waiting_for_user') && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleCancelTask(task.id);
                          }}
                          style={styles.cancelButton}
                        >
                          Cancel
                        </button>
                      )}
                      {task.state === 'interrupted' && (
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleResumeTask(task.id);
                          }}
                          style={styles.resumeButton}
                        >
                          Resume
                        </button>
                      )}
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          setExpandedTaskId(expandedTaskId === task.id ? null : task.id);
                        }}
                        style={styles.timelineButton}
                      >
                        {expandedTaskId === task.id ? 'Hide timeline' : 'Timeline'}
                      </button>
                    </div>
                  </div>
                  {expandedTaskId === task.id && activeTaskId === task.id && (
                    <div style={styles.timeline}>
                      <h4 style={styles.timelineTitle}>Event timeline</h4>
                      {activeTaskEvents.length === 0 ? (
                        <p style={styles.emptyMessage}>No events yet.</p>
                      ) : (
                        <ul style={styles.timelineList}>
                          {activeTaskEvents.map((ev) => (
                            <li key={ev.sequence_num} style={styles.timelineItem}>
                              <span style={styles.timelineSeq}>#{ev.sequence_num}</span>{' '}
                              <span style={styles.timelineType}>{ev.event_type}</span>
                              <span style={styles.timelineDate}>
                                {new Date(ev.created_at).toLocaleTimeString()}
                              </span>
                              <div style={styles.timelineData}>
                                {formatEventData(ev.event_data)}
                              </div>
                            </li>
                          ))}
                        </ul>
                      )}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

function countActions(events: Array<{ event_type: string }>): number {
  return events.filter((e) => e.event_type === 'action.started').length;
}

function elapsedSince(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 0) return '0s';
  const seconds = Math.floor(ms / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}

function formatEventData(data: Record<string, unknown>): string {
  try {
    const json = JSON.stringify(data);
    return json.length > 200 ? json.slice(0, 200) + '…' : json;
  } catch {
    return '{}';
  }
}

function getStateColor(state: string): string {
  switch (state) {
    case 'completed':
      return '#4CAF50';
    case 'failed':
      return '#f44336';
    case 'cancelled':
      return '#9e9e9e';
    case 'running':
      return '#2196F3';
    case 'waiting_for_user':
      return '#FF9800';
    default:
      return '#666';
  }
}

const styles: { [key: string]: React.CSSProperties } = {
  container: {
    display: 'flex',
    flexDirection: 'column',
    height: '100vh',
    backgroundColor: '#1a1a1a',
    color: '#fff',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: '16px 24px',
    borderBottom: '1px solid #333',
  },
  title: {
    fontSize: '24px',
    fontWeight: 'bold',
    margin: 0,
  },
  controls: {
    display: 'flex',
    gap: '12px',
    alignItems: 'center',
  },
  button: {
    padding: '8px 16px',
    border: 'none',
    borderRadius: '4px',
    color: '#fff',
    cursor: 'pointer',
    fontSize: '14px',
    backgroundColor: '#2196F3',
  },
  settingsPanel: {
    padding: '16px 24px',
    borderBottom: '1px solid #333',
    backgroundColor: '#222',
  },
  settingRow: {
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
    marginBottom: '12px',
    fontSize: '14px',
  },
  assetRow: {
    marginBottom: '12px',
    fontSize: '14px',
  },
  assetNote: {
    color: '#888',
  },
  settingsActions: {
    display: 'flex',
    gap: '12px',
  },
  main: {
    flex: 1,
    padding: '24px',
    overflowY: 'auto',
  },
  form: {
    display: 'flex',
    gap: '12px',
    marginBottom: '16px',
  },
  input: {
    flex: 1,
    padding: '12px 16px',
    border: '1px solid #333',
    borderRadius: '4px',
    backgroundColor: '#2a2a2a',
    color: '#fff',
    fontSize: '16px',
  },
  submitButton: {
    padding: '12px 24px',
    border: 'none',
    borderRadius: '4px',
    backgroundColor: '#2196F3',
    color: '#fff',
    cursor: 'pointer',
    fontSize: '16px',
  },
  privacyStrip: {
    display: 'flex',
    gap: '16px',
    marginBottom: '16px',
    padding: '8px 12px',
    backgroundColor: '#241a1a',
    borderRadius: '4px',
    fontSize: '13px',
  },
  taskDetailCard: {
    padding: '16px',
    borderRadius: '4px',
    backgroundColor: '#222',
    border: '1px solid #333',
    marginBottom: '24px',
  },
  taskDetailHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: '8px',
  },
  taskDetailTitle: {
    fontSize: '14px',
    textTransform: 'uppercase',
    color: '#888',
  },
  taskDetailGrid: {
    display: 'flex',
    gap: '24px',
    flexWrap: 'wrap',
    marginBottom: '8px',
    fontSize: '14px',
  },
  pendingQuestion: {
    marginBottom: '8px',
    color: '#FF9800',
    fontSize: '14px',
  },
  stopButton: {
    marginTop: '8px',
    marginRight: '8px',
    padding: '10px 20px',
    border: 'none',
    borderRadius: '4px',
    backgroundColor: '#f44336',
    color: '#fff',
    cursor: 'pointer',
    fontWeight: 'bold',
    fontSize: '14px',
  },
  tasksSection: {
    marginTop: '8px',
  },
  sectionTitle: {
    fontSize: '18px',
    marginBottom: '16px',
  },
  emptyMessage: {
    color: '#666',
    textAlign: 'center',
    padding: '40px',
  },
  taskList: {
    display: 'flex',
    flexDirection: 'column',
    gap: '12px',
  },
  taskCard: {
    padding: '16px',
    borderRadius: '4px',
    cursor: 'pointer',
    transition: 'background-color 0.2s',
  },
  taskHeader: {
    display: 'flex',
    justifyContent: 'space-between',
    marginBottom: '8px',
  },
  taskState: {
    fontSize: '12px',
    textTransform: 'uppercase',
    color: '#888',
  },
  taskDate: {
    fontSize: '12px',
    color: '#666',
  },
  taskRequest: {
    fontSize: '14px',
    marginBottom: '8px',
  },
  taskResult: {
    fontSize: '14px',
    color: '#4CAF50',
    marginTop: '8px',
  },
  taskActions: {
    display: 'flex',
    gap: '8px',
  },
  cancelButton: {
    marginTop: '8px',
    padding: '6px 12px',
    border: 'none',
    borderRadius: '4px',
    backgroundColor: '#f44336',
    color: '#fff',
    cursor: 'pointer',
    fontSize: '12px',
  },
  resumeButton: {
    marginTop: '8px',
    padding: '6px 12px',
    border: 'none',
    borderRadius: '4px',
    backgroundColor: '#FF9800',
    color: '#fff',
    cursor: 'pointer',
    fontSize: '12px',
  },
  timelineButton: {
    marginTop: '8px',
    padding: '6px 12px',
    border: 'none',
    borderRadius: '4px',
    backgroundColor: '#444',
    color: '#fff',
    cursor: 'pointer',
    fontSize: '12px',
  },
  timeline: {
    padding: '12px 16px',
    backgroundColor: '#1f1f1f',
    borderRadius: '0 0 4px 4px',
    borderTop: '1px solid #333',
  },
  timelineTitle: {
    margin: '0 0 8px',
    fontSize: '13px',
    color: '#aaa',
  },
  timelineList: {
    listStyle: 'none',
    padding: 0,
    margin: 0,
    maxHeight: '300px',
    overflowY: 'auto',
  },
  timelineItem: {
    padding: '6px 0',
    borderBottom: '1px solid #2a2a2a',
    fontSize: '13px',
  },
  timelineSeq: {
    color: '#666',
  },
  timelineType: {
    color: '#2196F3',
    marginRight: '8px',
  },
  timelineDate: {
    color: '#666',
    fontSize: '12px',
  },
  timelineData: {
    color: '#999',
    fontSize: '12px',
    marginTop: '2px',
    wordBreak: 'break-all',
  },
  voiceStatus: {
    fontSize: '12px',
    color: '#aaa',
    marginLeft: '12px',
    alignSelf: 'center',
  },
  errorText: {
    color: '#f44336',
    marginBottom: '8px',
  },
};
