import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AssistantScreen } from './Assistant';
import { useAppStore } from '../store/appStore';
import type { Task } from '../types';
const mock=vi.hoisted(()=>({createTask:vi.fn(),speak:vi.fn(),taskList:[] as Task[]}));
vi.mock('../services/api',()=>({voiceService:{speak:mock.speak}}));
vi.mock('../hooks/useVoiceCapture',()=>({useVoiceCapture:()=>({startCapture:vi.fn(),stopCapture:vi.fn()})}));
vi.mock('../hooks/useBackend',async()=>{
  const {useQuery}=await import('@tanstack/react-query');
  return {
    useTasks:()=>useQuery({queryKey:['tasks'],queryFn:async()=>mock.taskList,initialData:[],enabled:false}),
    useTools:()=>({data:[]}),useAgent:()=>({data:null}),useVoiceAssets:()=>({data:{stt_installed:false}}),
    useActions:()=>({live:true,createTask:mock.createTask}),useTaskEvents:()=>({data:[]}),
  };
});
let view:ReactTestRenderer;
let client:QueryClient;
let mode='text';
const task:Task={id:'first',original_request:'Hello Synth',state:'created',final_result:null,terminal_reason:null,normalized_goal:null,pending_question:null,active_action_id:null,version:1,created_at:'2026-10-22T00:00:00Z',updated_at:'2026-10-22T00:00:00Z'};
const settle=async(action:()=>void)=>{await act(async()=>{action();await new Promise(r=>setTimeout(r,20));});};
const text=()=>JSON.stringify(view.toJSON());
beforeEach(async()=>{
  vi.clearAllMocks();mode='text';
  vi.stubGlobal('localStorage',{getItem:()=>mode,setItem:(_key:string,value:string)=>{mode=value;}});
  vi.stubGlobal('document',{visibilityState:'visible'});
  useAppStore.setState({isConnected:true,dataMode:'live',isListening:false,isVoiceSpeaking:false,transcriptDraft:''});
  mock.taskList=[];mock.createTask.mockImplementation(async()=>{mock.taskList=[task];return task;});mock.speak.mockResolvedValue({success:true});
  client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  await settle(()=>{view=create(<QueryClientProvider client={client}><AssistantScreen/></QueryClientProvider>);});
});
afterEach(()=>{act(()=>view.unmount());client.clear();vi.unstubAllGlobals();});
const draft=async()=>settle(()=>view.root.findByProps({'aria-label':'Message Sythion'}).props.onChange({target:{value:'Hello Synth'}}));
const send=async()=>settle(()=>view.root.findByProps({'aria-label':'Send message'}).props.onClick());

describe('first message delivery',()=>{
  it('shows a sent request before completion, then renders the reply in text',async()=>{
    await draft();await send();
    expect(text()).toContain('Hello Synth');
    expect(text()).toContain('Synth is working on your message');
    expect(useAppStore.getState().transcriptDraft).toBe('');
    await settle(()=>client.setQueryData(['tasks'],[{...task,state:'completed',final_result:'Hello from Synth'}]));
    expect(text()).toContain('Hello from Synth');
    expect(mock.speak).not.toHaveBeenCalled();
  });
  it('keeps a failed draft available for retry',async()=>{
    mock.createTask.mockRejectedValueOnce(new Error('Connection lost'));
    await draft();await send();
    expect(useAppStore.getState().transcriptDraft).toBe('Hello Synth');
    expect(text()).toContain('Connection lost');
  });
  it('reads a new reply once and preserves text after playback failure',async()=>{
    await settle(()=>view.root.findByProps({'aria-label':'Synth reply mode'}).props.onChange({target:{value:'voice'}}));
    mock.speak.mockResolvedValue({success:false});
    await draft();await send();
    const done={...task,state:'completed',final_result:'Hello from Synth'};
    await settle(()=>client.setQueryData(['tasks'],[done]));
    expect(mock.speak).toHaveBeenCalledTimes(1);
    expect(text()).toContain('Spoken playback is unavailable');
    expect(text()).toContain('Hello from Synth');
    await settle(()=>client.setQueryData(['tasks'],[{...done}]));
    expect(mock.speak).toHaveBeenCalledTimes(1);
  });
});
