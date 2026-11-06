import { useEffect, useRef, useState } from 'react';
import { isTauri } from '@tauri-apps/api/core';
import { getCurrentWindow } from '@tauri-apps/api/window';
import type { Adjustment, PreviewState } from '../domain/types';
import { totalDelay } from '../domain/types';

export const delayText = (delay: number) => delay === 0 ? 'Озвучка без сдвига' : `Озвучка сдвинута ${delay > 0 ? 'позже' : 'раньше'} на ${new Intl.NumberFormat('ru', { maximumFractionDigits: 3 }).format(Math.abs(delay) / 1000)} с`;
const time = (s: number) => `${Math.floor(s / 60).toString().padStart(2, '0')}:${Math.floor(s % 60).toString().padStart(2, '0')}`;
export function scenePosition(point: PreviewState['selectedPoint'], duration: number) {
  return point === 'start' ? Math.min(120, duration * .1) : point === 'middle' ? duration * .5 : duration * .9;
}

interface Props {
  preview: PreviewState;
  setPreview: (update: Partial<PreviewState>) => void;
  seek: (seconds: number) => void;
  adjustment: Adjustment;
  adjust: (ms: number) => void;
  enabled: boolean;
  prepare: () => void;
  busy: boolean;
  demo: boolean;
  duration: number;
  error?: string;
  pitchShift?: boolean;
}

export function PreviewPanel({ preview, setPreview, seek, adjustment, adjust, enabled, prepare, busy, demo, duration, error, pitchShift }: Props) {
  const video = useRef<HTMLVideoElement>(null);
  const active = enabled && !busy && preview.status === 'ready';
  // Expanded view covers the whole window; in the app the window itself also goes full screen.
  const [expanded, setExpanded] = useState(false);
  const windowFullscreen = useRef(false);
  useEffect(() => {
    // Touch the window only when the state really changes, never at startup.
    if (isTauri() && windowFullscreen.current !== expanded) { windowFullscreen.current = expanded; void getCurrentWindow().setFullscreen(expanded).catch(() => undefined); }
    if (!expanded) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setExpanded(false);
      else if (event.key === ' ' && active && !['BUTTON', 'SELECT', 'INPUT'].includes((event.target as HTMLElement).tagName)) { event.preventDefault(); setPreview({ playing: !preview.playing }); }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [expanded, active, preview.playing]);
  useEffect(() => () => { if (windowFullscreen.current) void getCurrentWindow().setFullscreen(false).catch(() => undefined); }, []);
  useEffect(() => {
    const element = video.current; if (!element) return;
    element.volume = preview.volume / 100;
    if (preview.playing && active) void element.play().catch(() => setPreview({ playing: false }));
    else element.pause();
  }, [preview.playing, preview.volume, active]);
  const move = (position: number) => {
    const start = preview.clipStart ?? 0, length = preview.clipDuration ?? 0;
    if (video.current && preview.status === 'ready' && position >= start && position < start + length) {
      video.current.currentTime = position - start; setPreview({ playbackPosition: position });
    } else seek(position);
  };
  return <section className="preview-panel">
    <div className="section-heading"><h2>Проверка синхронизации</h2><span className="muted small">Предпросмотр</span></div>
    <div className={`player${expanded ? ' expanded' : ''}`}>
    <div className="stage">
    {!demo && preview.url && active ? <video className="native-video" ref={video} src={preview.url} playsInline
      onClick={() => setPreview({ playing: !preview.playing })} onDoubleClick={() => setExpanded(value => !value)}
      onTimeUpdate={event => setPreview({ playbackPosition: (preview.clipStart ?? 0) + event.currentTarget.currentTime })}
      onEnded={() => setPreview({ playing: false })} onError={() => setPreview({ status: 'error', playing: false })}
      aria-label="Фрагмент фильма с выбранным звуком"/> : <div className="video-placeholder">
      <svg width="46" height="38" viewBox="0 0 46 38" fill="none" aria-hidden="true"><rect x="2" y="2" width="42" height="30" rx="5" stroke="currentColor" strokeWidth="1.5"/><path d="m20 10 10 7-10 7V10ZM16 37h14" stroke="currentColor" strokeWidth="1.5"/></svg>
      <strong>{preview.status === 'preparing' ? 'Подготовка фрагмента…' : 'Здесь можно проверить звук'}</strong>
      <span>{demo ? 'Демонстрация состояний — без видео и звука' : 'Выберите фильм и озвучку, затем проверьте звук'}</span>
      <span className="small">{demo ? 'Для реальной обработки откройте приложение Windows' : 'Оригинальный фильм не меняется'}</span>
    </div>}
    </div>
    <div className="player-controls">
      <button aria-label={preview.playing ? 'Приостановить' : demo ? 'Запустить демонстрацию позиции' : 'Воспроизвести фрагмент'} disabled={!active} onClick={() => setPreview({ playing: !preview.playing })}>{preview.playing ? 'Ⅱ' : '▶'}</button>
      <span className="small time">{time(preview.playbackPosition)}</span>
      <input aria-label="Позиция в фильме" type="range" min="0" max={duration} step="0.1" disabled={!enabled || busy} value={preview.playbackPosition} onChange={event => move(+event.target.value)}/>
      <label className="volume">Громкость<input aria-label="Громкость" type="range" min="0" max="100" disabled={!enabled || busy} value={preview.volume} onChange={event => setPreview({ volume: +event.target.value })}/></label>
      <button className="expand-button" aria-label={expanded ? 'Свернуть' : 'На весь экран'} title={expanded ? 'Свернуть (Esc)' : 'На весь экран'} disabled={!active && !expanded} onClick={() => setExpanded(value => !value)}>{expanded ? '✕' : '⛶'}</button>
    </div>
    {expanded && <div className="fullscreen-bar">
      <label className="variant">Слушать<select disabled={!enabled || busy} value={preview.audioVariant} onChange={event => setPreview({ audioVariant: event.target.value as PreviewState['audioVariant'] })}>
        <option value="film">Звук фильма</option><option value="original">Новая озвучка · исходная</option><option value="corrected">Новая озвучка · с исправлением</option>
      </select></label>
      <button disabled={!enabled || busy} onClick={() => adjust(adjustment.manualDelayMs - 50)}>− Звук раньше</button>
      <button disabled={!enabled || busy} onClick={() => adjust(adjustment.manualDelayMs + 50)}>＋ Звук позже</button>
      <span className="delay">{delayText(totalDelay(adjustment))}</span>
      <span className="small muted">{preview.status === 'preparing' ? 'Готовим новый фрагмент…' : preview.status === 'stale' ? 'Параметры изменены, обновляем фрагмент…' : 'Esc: свернуть · пробел: пауза'}</span>
    </div>}
    </div>
    <div className="points">{(['start', 'middle', 'end'] as const).map((point, index) => <button key={point} className={preview.selectedPoint === point ? 'selected' : ''} disabled={!enabled || busy} onClick={() => setPreview({ selectedPoint: point, playbackPosition: scenePosition(point, duration) })}>{['Начало', 'Середина', 'Конец'][index]}</button>)}</div>
    <label className="variant">Слушать<select disabled={!enabled || busy} value={preview.audioVariant} onChange={event => setPreview({ audioVariant: event.target.value as PreviewState['audioVariant'] })}>
      <option value="film">Звук фильма</option><option value="original">Новая озвучка · исходная</option><option value="corrected">Новая озвучка · с исправлением</option>
    </select></label>
    <div className="preview-state" role="status">
      {preview.status === 'preparing' ? `${demo ? 'Демонстрация: ' : ''}подготовка фрагмента…` : preview.status === 'stale' ? 'Параметры изменены. Подготавливаем новый фрагмент.' : preview.status === 'ready' ? demo ? 'Демо-фрагмент готов · звука нет' : 'Фрагмент готов. Можно сравнить варианты звука.' : preview.status === 'error' ? error ?? 'Не удалось открыть видео. Попробуйте подготовить фрагмент ещё раз.' : 'Сначала выберите файлы и проверьте звук.'}
      {enabled && <button className="text-button" disabled={busy || preview.status === 'preparing'} onClick={prepare}>{demo ? 'Обновить демо' : 'Обновить фрагмент'}</button>}
    </div>
    <div className="adjustment">
      <div><h3>Подстроить на слух</h3><p className="muted small">Один шаг — 0,05 секунды</p></div>
      <div className="adjust-buttons"><button disabled={!enabled || busy} onClick={() => adjust(adjustment.manualDelayMs - 50)}>− Звук раньше</button><button disabled={!enabled || busy} onClick={() => adjust(adjustment.manualDelayMs + 50)}>＋ Звук позже</button><button className="text-button" disabled={!enabled || busy} onClick={() => adjust(0)}>Сбросить</button></div>
      <p className="delay">{delayText(totalDelay(adjustment))}</p>
      {!demo && preview.effectiveDelayMs !== undefined && preview.effectiveDelayMs !== totalDelay(adjustment) && <p className="small muted">В результате: {delayText(preview.effectiveDelayMs).toLowerCase()} — с учётом точности исходного звука.</p>}
      {adjustment.speedCorrection !== 1 && <p className="small muted">Сначала исправляется скорость {pitchShift ? 'вместе с тоном' : 'с сохранением тона'}, затем применяется сдвиг.</p>}
    </div>
  </section>;
}
