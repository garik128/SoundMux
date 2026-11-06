import type { Adjustment, AnalysisResult, OutputSettings, PreviewState, Progress, Scenario, SourceFile, SourceKind } from '../domain/types';
export interface JobInput { movie: SourceFile; audio: SourceFile; scenario: Scenario; settings: OutputSettings }
export interface PreparedPreview { url: string; start: number; duration: number; effectiveDelayMs: number }
export interface BuildResult { path: string; effectiveDelayMs: number; warnings?: string[] }
export interface BuildInput extends JobInput { adjustment: Adjustment; outputName: string; outputDirectory?: string; allowUnverified: boolean; analysis?: AnalysisResult; overwriteToken?: string }
export interface MediaService {
  pickSource(kind: SourceKind, signal: AbortSignal): Promise<SourceFile | undefined>;
  bindDrop(element: HTMLElement, kind: SourceKind, onSource: (source: SourceFile) => void, onError?: (error: Error) => void): () => void;
  analyze(input: JobInput, signal: AbortSignal, progress: (event: Progress) => void): Promise<AnalysisResult>;
  preparePreview(input: JobInput & { adjustment: Adjustment; preview: PreviewState }, signal: AbortSignal): Promise<PreparedPreview | void>;
  build(input: BuildInput, signal: AbortSignal, progress: (event: Progress) => void): Promise<BuildResult | void>;
}
