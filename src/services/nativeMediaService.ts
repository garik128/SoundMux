import { convertFileSrc, invoke, isTauri } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import { getCurrentWebviewWindow } from '@tauri-apps/api/webviewWindow';
import { open } from '@tauri-apps/plugin-dialog';
import type { AppSettings, LoadMode, Progress, SourceFile, SourceKind } from '../domain/types';
import type { BuildResult, MediaService } from './mediaService';

export const nativeAvailable = isTauri();
export class NativeMediaError extends Error {
  detail: string;
  code?: string;
  context?: { path: string; token: string };
  constructor(value: unknown) {
    let message = String(value), detail = message, code: string | undefined, context: { path: string; token: string } | undefined;
    try { const parsed = JSON.parse(message); message = parsed.message || message; detail = parsed.detail || message; code = parsed.code; context = parsed.context; } catch { /* Rust startup errors are plain strings. */ }
    super(message); this.detail = detail;
    this.code = code; this.context = context;
  }
}

async function run<T>(action: string, payload: unknown, signal: AbortSignal, progress?: (event: Progress) => void): Promise<T> {
  signal.throwIfAborted();
  const taskId = crypto.randomUUID();
  const unlisten = await listen<Progress & { taskId: string }>('media-progress', event => {
    if (event.payload.taskId === taskId && !signal.aborted) progress?.(event.payload);
  });
  const cancel = () => { void invoke('media_cancel', { taskId }); };
  try {
    signal.throwIfAborted();
    const promise = invoke<T>('media_run', { taskId, action, payload });
    signal.addEventListener('abort', cancel, { once: true });
    if (signal.aborted) cancel();
    const result = await promise; signal.throwIfAborted(); return result;
  } catch (error) {
    if (signal.aborted) throw new DOMException('Cancelled', 'AbortError');
    throw new NativeMediaError(error);
  } finally { signal.removeEventListener('abort', cancel); unlisten(); }
}

const probe = (path: string, kind: SourceKind, signal: AbortSignal) => run<SourceFile>('probe', { path, kind }, signal);
export const probeSource = (path: string, kind: SourceKind) => probe(path, kind, new AbortController().signal);
const lastDirectory = (kind: SourceKind | 'output') => invoke<string | null>('get_dialog_directory', { kind }).catch(error => { console.warn('Dialog history unavailable', error); return null; });
const rememberDirectory = (kind: SourceKind | 'output', path: string, directory = false) => invoke('remember_dialog_directory', { kind, path, directory }).catch(error => { console.warn('Cannot save dialog history', error); });
export const clearMediaCache = () => invoke<number>('clear_media_cache');
export const getAppSettings = () => invoke<AppSettings>('get_app_settings');
export const setLoadMode = (load: LoadMode) => invoke<void>('set_load', { load });
export const setCacheDirectory = (path: string | null) => invoke<void>('set_cache_dir', { path });
export const openLogFolder = () => invoke<void>('open_log_dir');
export const chooseCacheDirectory = async () => {
  const path = await open({ directory: true, multiple: false, title: 'Папка для временных файлов' });
  return typeof path === 'string' ? path : undefined;
};
export const chooseOutputDirectory = async () => {
  const defaultPath = await lastDirectory('output');
  const path = await open({ directory: true, multiple: false, title: 'Куда сохранить фильм', defaultPath: defaultPath ?? undefined });
  if (typeof path === 'string') await rememberDirectory('output', path, true);
  return typeof path === 'string' ? path : undefined;
};
export const openResult = (path: string, folder: boolean) => invoke('open_result', { path, folder });
// The app only opens the project's own site; the Rust side refuses any other address.
export const openSite = (url: string) => invoke<void>('open_link', { url });

export const nativeMediaService: MediaService = {
  async pickSource(kind, signal) {
    const defaultPath = await lastDirectory(kind);
    signal.throwIfAborted();
    const selected = await open({ defaultPath: defaultPath ?? undefined, multiple: false, title: kind === 'movie' ? 'Выберите фильм' : 'Выберите озвучку', filters: [{ name: kind === 'movie' ? 'Фильмы' : 'Звук', extensions: kind === 'movie' ? ['mkv', 'mp4', 'avi', 'mov', 'webm', 'm4v', 'ts', 'm2ts', 'mts'] : ['ac3', 'eac3', 'dts', 'thd', 'mlp', 'thd+ac3', 'flac', 'wav', 'mp3', 'm4a', 'aac', 'ogg', 'mka', 'mkv', 'mp4'] }] });
    signal.throwIfAborted();
    if (typeof selected === 'string') await rememberDirectory(kind, selected);
    return typeof selected === 'string' ? probe(selected, kind, signal) : undefined;
  },
  bindDrop(element, kind, onSource, onError) {
    let disposed = false; let controller: AbortController | undefined;
    const promise = getCurrentWebviewWindow().onDragDropEvent(event => {
      const payload = event.payload;
      if (payload.type !== 'drop') return;
      const rect = element.getBoundingClientRect(), scale = window.devicePixelRatio;
      const { x, y } = payload.position;
      if (x / scale < rect.left || x / scale > rect.right || y / scale < rect.top || y / scale > rect.bottom) return;
      const path = payload.paths[0]; if (!path) return;
      controller?.abort(); controller = new AbortController();
      const signal = controller.signal;
      void rememberDirectory(kind, path).then(() => probe(path, kind, signal)).then(source => { if (!disposed) onSource(source); }).catch(error => { if (!disposed && error.name !== 'AbortError') onError?.(error); });
    });
    return () => { disposed = true; controller?.abort(); void promise.then(unlisten => unlisten()); };
  },
  analyze: (input, signal, progress) => run('analyze', input, signal, progress),
  async preparePreview(input, signal) {
    const result = await run<{ path: string; start: number; duration: number; effectiveDelayMs: number }>('preview', input, signal);
    return { ...result, url: convertFileSrc(result.path) };
  },
  build: (input, signal, progress) => run<BuildResult>('build', input, signal, progress),
};
