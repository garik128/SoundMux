import type { AnalysisResult, Job, JobState, Progress } from './types';
export type JobAction = { type: 'reset'; taskId: number; ready: boolean } | { type: 'start'; taskId: number; status: 'analyzing' | 'building' } | { type: 'progress'; taskId: number; progress: Progress } | { type: 'failure'; taskId: number; error: string } | { type: 'finish'; taskId: number; status: JobState; result?: AnalysisResult; error?: string };
export const initialJob: Job = { status: 'empty', taskId: 0 };
export function jobReducer(state: Job, action: JobAction): Job {
  if (action.type === 'reset') return { status: action.ready ? 'ready' : 'empty', taskId: action.taskId };
  if (action.type === 'start') return { ...state, status: action.status, taskId: action.taskId, result: action.status === 'analyzing' ? undefined : state.result, progress: undefined, error: undefined };
  if (action.taskId !== state.taskId || !['analyzing', 'building'].includes(state.status)) return state;
  if (action.type === 'progress') return { ...state, progress: action.progress };
  if (action.type === 'failure') return { ...state, status: state.status === 'building' && state.result ? 'review' : 'error', progress: undefined, error: action.error };
  return { ...state, status: action.status, result: action.result ?? state.result, progress: undefined, error: action.error };
}
