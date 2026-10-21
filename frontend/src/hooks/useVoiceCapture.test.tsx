import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useVoiceCapture } from './useVoiceCapture';
import { useAppStore } from '../store/appStore';
const mock = vi.hoisted(() => ({ startSession:vi.fn(), endSession:vi.fn(), sendAudioFrame:vi.fn() }));
vi.mock('../services/api', () => ({voiceService:mock}));
let capture: ReturnType<typeof useVoiceCapture>;
let view: ReactTestRenderer;
const getUserMedia = vi.fn();
const stopTrack = vi.fn();
const Harness = () => { capture=useVoiceCapture();return null; };

beforeEach(async () => {
  vi.clearAllMocks();
  useAppStore.setState({isVoiceEnabled:false,isListening:false,isVoiceSpeaking:false,transcriptDraft:'',transcriptSegments:[]});
  const track = {stop:stopTrack,addEventListener:vi.fn()};
  getUserMedia.mockResolvedValue({getTracks:()=>[track]});
  mock.startSession.mockResolvedValue({session_id:'session',stt_available:true});
  mock.endSession.mockResolvedValue({segments:[{segment_id:'last',text:'Hello Synth',stop_detected:false}]});
  vi.stubGlobal('document',{visibilityState:'visible',addEventListener:vi.fn(),removeEventListener:vi.fn()});
  vi.stubGlobal('navigator',{mediaDevices:{getUserMedia}});
  vi.stubGlobal('window',{
    addEventListener:vi.fn(),removeEventListener:vi.fn(),
    AudioContext: class {
      state='running';
      destination={};
      createMediaStreamSource(){return {connect:vi.fn()};}
      createScriptProcessor(){return {connect:vi.fn(),disconnect:vi.fn(),onaudioprocess:null};}
      close(){this.state='closed';return Promise.resolve();}
    },
  });
  await act(async()=>{view=create(<Harness/>);});
});
afterEach(async()=>{await act(async()=>{view.unmount();});vi.unstubAllGlobals();});

describe('microphone first use',()=>{
  it('starts on the first click using current consent and preserves final flushed words',async()=>{
    // Reproduce the original same-handler enable/start closure, before React rerenders.
    const start=capture.startCapture;
    await act(async()=>{useAppStore.getState().setVoiceEnabled(true);await start();});
    expect(getUserMedia).toHaveBeenCalledTimes(1);
    expect(useAppStore.getState().isListening).toBe(true);
    await act(async()=>{await capture.stopCapture();});
    expect(stopTrack).toHaveBeenCalled();
    expect(useAppStore.getState().transcriptDraft).toBe('Hello Synth');
    expect(useAppStore.getState().isListening).toBe(false);
  });
  it('surfaces permission denial to the caller and closes the backend session',async()=>{
    getUserMedia.mockRejectedValue(new Error('Microphone permission denied'));
    await act(async()=>{
      useAppStore.getState().setVoiceEnabled(true);
      await expect(capture.toggleListening()).rejects.toThrow('Microphone permission denied');
    });
    expect(mock.endSession).toHaveBeenCalledWith('session');
    expect(useAppStore.getState().isListening).toBe(false);
  });
  it('does not request a microphone without explicit consent',async()=>{
    await act(async()=>{await capture.startCapture();});
    expect(getUserMedia).not.toHaveBeenCalled();
    expect(mock.startSession).not.toHaveBeenCalled();
  });
});
