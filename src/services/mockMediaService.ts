import type { AppSettings, LoadMode, SourceFile, SourceKind, Progress, SystemSettingsApi } from '../domain/types';
import type { MediaService } from './mediaService';
import { demoResult } from '../fixtures/scenarios';
export function wait(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); reject(new DOMException('Cancelled', 'AbortError')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, ms);
    if (signal.aborted) abort(); else signal.addEventListener('abort', abort, { once: true });
  });
}
const fromFile = (file: File): SourceFile => ({ id: crypto.randomUUID(), name: file.name, size: file.size, browserFile: file });
async function sequence(labels: string[], signal: AbortSignal, progress: (event: Progress) => void) {
  for (let i = 0; i < labels.length; i++) for (let step = 0; step < 5; step++) { signal.throwIfAborted(); progress({ label: labels[i], percent: Math.round((i * 5 + step) / (labels.length * 5) * 100) }); await wait(110, signal); }
  signal.throwIfAborted();
}
export const mediaService: MediaService = {
  pickSource(kind: SourceKind, signal: AbortSignal) {
    return new Promise((resolve) => {
      const input = document.createElement('input'); input.type = 'file'; input.accept = kind === 'movie' ? '.mkv,.mp4,.avi,.mov,.webm,.m4v' : '.ac3,.eac3,.dts,.flac,.wav,.mp3,.m4a,.aac,.ogg,.mka,.mp4,.mkv';
      const finish = (file?: File) => { signal.removeEventListener('abort', cancel); input.remove(); resolve(file && !signal.aborted ? fromFile(file) : undefined); };
      const cancel = () => finish(); input.onchange = () => finish(input.files?.[0]); input.oncancel = cancel;
      if (signal.aborted) finish(); else { signal.addEventListener('abort', cancel, { once: true }); input.click(); }
    });
  },
  bindDrop(element, _kind, onSource) {
    const over = (event: DragEvent) => { event.preventDefault(); element.dataset.dragging = 'true'; };
    const leave = () => { delete element.dataset.dragging; };
    const drop = (event: DragEvent) => { event.preventDefault(); leave(); const file = event.dataTransfer?.files[0]; if (file) onSource(fromFile(file)); };
    element.addEventListener('dragover', over); element.addEventListener('dragleave', leave); element.addEventListener('drop', drop);
    return () => { element.removeEventListener('dragover', over); element.removeEventListener('dragleave', leave); element.removeEventListener('drop', drop); };
  },
  async analyze(input, signal, progress) {
    await sequence(['Чтение файлов', 'Сравнение звука', 'Подготовка результата'], signal, progress);
    if (input.scenario === 'error') throw new Error('Не удалось прочитать аудиофайл');
    return demoResult(input.scenario);
  },
  async preparePreview(_input, signal) { await wait(550, signal); },
  async build(input, signal, progress) {
    if (['uncertain', 'different-edit'].includes(input.scenario) && !input.allowUnverified) throw new Error('Подтвердите сохранение без проверки совпадения звука');
    await sequence(['Подготовка звука', 'Сборка файла', 'Проверка результата'], signal, progress);
  },
};

// Demo of the system settings (folder and CPU load): in memory, nothing is written anywhere.
const demoDefaultCache = 'C:\\Users\\Пользователь\\AppData\\Local\\local.audiomost.desktop\\SoundMux cache';
let demoSettings: AppSettings = { cacheDir: demoDefaultCache, cacheCustom: false, cacheSkipped: false, cacheDefault: demoDefaultCache, load: 'fast', logDir: 'C:\\Users\\Пользователь\\SoundMux\\logs' };
export const mockSystemSettings: SystemSettingsApi = {
  get: async () => ({ ...demoSettings }),
  setLoad: async (load: LoadMode) => { demoSettings = { ...demoSettings, load }; },
  openLogFolder: async () => {},
  chooseCacheDirectory: async () => 'D:\\Видео\\Временные файлы',
  setCacheDirectory: async (path: string | null) => {
    demoSettings = path ? { ...demoSettings, cacheDir: `${path}\\SoundMux cache`, cacheCustom: true } : { ...demoSettings, cacheDir: demoDefaultCache, cacheCustom: false };
  },
};
