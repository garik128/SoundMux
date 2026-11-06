export type SourceKind = 'movie' | 'audio';
export type Scenario = 'aligned' | 'offset' | 'drift' | 'uncertain' | 'different-edit' | 'error';
export interface SourceFile { id: string; name: string; size: number; nativePath?: string; browserFile?: File; metadata?: { origin: 'demo' | 'probe'; duration: number; recommendedTrack?: string; tracks: { id: string; name: string; codec?: string; channels?: number; default?: boolean; duration?: number }[]; subtitles?: { id: string; name: string; codec?: string }[] } }
// 'auto' resamples (speed and pitch change together) for film frame rate conversions and keeps the pitch otherwise.
export type PitchMode = 'auto' | 'shift' | 'keep';
export interface AnalysisResult { verdict: Exclude<Scenario, 'error'>; proposedDelayMs: number; speedCorrection: number; explanation: string; evidence: string[]; demo: boolean; pitchShift?: boolean }
// A factor > 1 speeds up the external audio: target duration / reference duration.
// Apply speed correction first, then delay on the corrected timeline. Positive delay plays later.
export interface Adjustment { automaticDelayMs: number; manualDelayMs: number; speedCorrection: number; preservePitch: boolean }
export type JobState = 'empty' | 'ready' | 'analyzing' | 'review' | 'building' | 'completed' | 'cancelled' | 'error';
export type LoadMode = 'fast' | 'moderate' | 'background';
export interface AppSettings { cacheDir: string; cacheCustom: boolean; cacheSkipped: boolean; cacheDefault: string; load: LoadMode; logDir: string }
export interface SystemSettingsApi {
  get: () => Promise<AppSettings>;
  setLoad: (load: LoadMode) => Promise<void>;
  chooseCacheDirectory: () => Promise<string | undefined>;
  setCacheDirectory: (path: string | null) => Promise<void>;
  openLogFolder: () => Promise<void>;
}
export interface Progress { label: string; percent?: number | null; etaSeconds?: number | null }
// Short estimates are noise; show remaining time only when it is worth planning around.
export const formatRemaining = (seconds?: number | null) => seconds == null || seconds < 30 ? '' : seconds < 90 ? 'осталось около минуты' : `осталось около ${Math.round(seconds / 60)} мин`;
export interface Job { status: JobState; taskId: number; result?: AnalysisResult; progress?: Progress; error?: string }
export interface PreviewState { selectedPoint: 'start' | 'middle' | 'end'; audioVariant: 'film' | 'original' | 'corrected'; playbackPosition: number; status: 'empty' | 'preparing' | 'ready' | 'stale' | 'error'; playing: boolean; volume: number; url?: string; clipStart?: number; clipDuration?: number; effectiveDelayMs?: number }
export interface OutputSettings { language: string; title: string; referenceTrack: string; externalTrack?: string; makeDefault: boolean; keepMovieAudio: boolean; keepOriginal: boolean; compatibility: boolean; skipSubtitles?: string[]; skipAudio?: string[]; pitchMode?: PitchMode; limitPeak?: boolean }
export const initialAdjustment: Adjustment = { automaticDelayMs: 0, manualDelayMs: 0, speedCorrection: 1, preservePitch: true };
export const totalDelay = (a: Adjustment) => a.automaticDelayMs + a.manualDelayMs;
