import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { AssistantScreen } from './Assistant';
import { useAppStore } from '../store/appStore';
import type { Task } from '../types';
const mock=vi.hoisted(()=>({createTask:vi.fn(),replyToTask:vi.fn(),resumeTask:vi.fn(),stopCapture:vi.fn(),speak:vi.fn(),conversations:vi.fn(),taskList:[] as Task[]}));
vi.mock('../services/api',()=>({voiceService:{speak:mock.speak},conversationService:{list:mock.conversations}}));
vi.mock('../hooks/useVoiceCapture',()=>({useVoiceCapture:()=>({startCapture:vi.fn(),stopCapture:mock.stopCapture})}));
vi.mock('../hooks/useBackend',async()=>{
  const {useQuery}=await import('@tanstack/react-query');
  return {
    useTasks:()=>useQuery({queryKey:['tasks'],queryFn:async()=>mock.taskList,initialData:[],enabled:false}),
    useTools:()=>({data:[]}),useAgent:()=>({data:null}),useVoiceAssets:()=>({data:{stt_installed:false}}),
    useActions:()=>({live:true,createTask:mock.createTask,replyToTask:mock.replyToTask,resumeTask:mock.resumeTask}),useTaskEvents:()=>({data:[]}),
  };
});

describe('task and microphone controls',()=>{
  it('resumes an interrupted task instead of offering a reply',async()=>{
    mock.resumeTask.mockResolvedValueOnce(undefined);
    await settle(()=>client.setQueryData(['tasks'],[{...task,state:'interrupted',terminal_reason:'Paused safely'}]));
    expect(text()).toContain('Paused safely');
    expect(view.root.findAllByType('input')).toHaveLength(0);
    await settle(()=>view.root.findAllByType('button').find(b=>b.children.includes('Resume task'))!.props.onClick());
    expect(mock.resumeTask).toHaveBeenCalledWith(task.id);
    expect(mock.replyToTask).not.toHaveBeenCalled();
  });
  it('still sends answers for waiting tasks',async()=>{
    mock.replyToTask.mockResolvedValueOnce(undefined);
    await settle(()=>client.setQueryData(['tasks'],[{...task,state:'waiting_for_user',pending_question:'Which one?'}]));
    const input=view.root.findAllByType('input')[0];
    await settle(()=>input.props.onChange({target:{value:'This one'}}));
    await settle(()=>view.root.findAllByType('button').find(b=>b.children.includes('Send'))!.props.onClick());
    expect(mock.replyToTask).toHaveBeenCalledWith(task.id,'This one');
    expect(mock.resumeTask).not.toHaveBeenCalled();
  });
  it('shows active listening and disables send even when a task needs attention',async()=>{
    await draft();
    await settle(()=>{client.setQueryData(['tasks'],[{...task,state:'interrupted'}]);useAppStore.setState({isListening:true});});
    const mic=view.root.findByProps({'aria-label':'Stop listening'});
    expect(mic.props['aria-pressed']).toBe(true);
    expect(mic.props.disabled).toBe(false);
    expect(view.root.findByProps({'aria-label':'Send message'}).props.disabled).toBe(true);
    await settle(()=>mic.props.onClick());
    expect(mock.stopCapture).toHaveBeenCalled();
    expect(mock.createTask).not.toHaveBeenCalled();
  });
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
  mock.taskList=[];mock.conversations.mockResolvedValue({conversations:[],next_cursor:null});mock.createTask.mockImplementation(async()=>{mock.taskList=[task];return task;});mock.speak.mockResolvedValue({success:true});
  client=new QueryClient({defaultOptions:{queries:{retry:false}}});
  await settle(()=>{view=create(<QueryClientProvider client={client}><AssistantScreen/></QueryClientProvider>);});
});
afterEach(()=>{act(()=>view.unmount());client.clear();vi.unstubAllGlobals();});
const draft=async()=>settle(()=>view.root.findByProps({'aria-label':'Message Sythion'}).props.onChange({target:{value:'Hello Synth'}}));
const send=async()=>settle(()=>view.root.findByProps({'aria-label':'Send message'}).props.onClick());

describe('first message delivery',()=>{
  it('restores full stored conversation when opening the screen',async()=>{
    mock.conversations.mockResolvedValue({conversations:[{id:'conv-saved',session_id:'s',user_text:'Saved message',assistant_text:'Saved complete reply',created_at:'2026-10-22T00:00:00Z'}],next_cursor:null});
    await settle(()=>{void client.invalidateQueries({queryKey:['conversation-history']});});
    expect(text()).toContain('Saved message');
    expect(text()).toContain('Saved complete reply');
  });
  it('loads older conversations on request',async()=>{
    mock.conversations.mockImplementation(async(cursor?:string)=>cursor
      ? {conversations:[{id:'old',session_id:'s',user_text:'Older question',assistant_text:'Older answer',created_at:'2026-10-21T00:00:00Z'}],next_cursor:null}
      : {conversations:[],next_cursor:'older-page'});
    await settle(()=>{void client.invalidateQueries({queryKey:['conversation-history']});});
    await settle(()=>view.root.findAllByType('button').find(b=>b.children.includes('Load older messages'))!.props.onClick());
    expect(mock.conversations).toHaveBeenCalledWith('older-page');
    expect(text()).toContain('Older answer');
  });
  it('does not retain a cached reply after its persisted record is removed',async()=>{
    const row={id:'conv-1',session_id:'s',user_text:'Hello Synth',assistant_text:'Saved reply',created_at:'2026-10-22T00:00:00Z'};
    mock.createTask.mockResolvedValueOnce({...task,id:row.id,interaction_type:'CONVERSATION',state:'completed',final_result:row.assistant_text});
    mock.conversations.mockResolvedValue({conversations:[row],next_cursor:null});
    await draft();await send();
    expect(text()).toContain('Saved reply');
    mock.conversations.mockResolvedValue({conversations:[],next_cursor:null});
    await settle(()=>{void client.invalidateQueries({queryKey:['conversation-history']});});
    expect(text()).not.toContain('Saved reply');
  });
  it('keeps a conversational reply visible when the task list refreshes',async()=>{
    mock.createTask.mockResolvedValueOnce({...task,id:'conv-1',interaction_type:'CONVERSATION',state:'completed',final_result:'Hello from Synth'});
    await draft();await send();
    await settle(()=>client.setQueryData(['tasks'],[]));
    expect(text()).toContain('Hello Synth');
    expect(text()).toContain('Hello from Synth');
    expect(text()).not.toContain('exec-line__toggle');
  });
  it('does not show live conversation in preview mode',async()=>{
    mock.createTask.mockResolvedValueOnce({...task,id:'conv-1',interaction_type:'CONVERSATION',state:'completed',final_result:'Private response'});
    await draft();await send();
    expect(text()).toContain('Private response');
    await settle(()=>useAppStore.setState({dataMode:'preview'}));
    expect(text()).not.toContain('Private response');
    await settle(()=>useAppStore.setState({dataMode:'live'}));
    expect(text()).toContain('Private response');
  });
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
