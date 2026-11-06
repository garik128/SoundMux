import { useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { initialJob, jobReducer } from '../domain/jobReducer';
import { formatRemaining, initialAdjustment } from '../domain/types';
import { durationMismatch, formatClock } from '../domain/durations';
import type { Adjustment, OutputSettings, PreviewState, Progress, Scenario, SourceFile, SourceKind, SystemSettingsApi } from '../domain/types';
import { mediaService as mockMediaService, mockSystemSettings } from '../services/mockMediaService';
import { nativeAvailable, nativeMediaService, NativeMediaError, chooseOutputDirectory, openResult, openSite, probeSource, clearMediaCache, getAppSettings, setLoadMode, chooseCacheDirectory, setCacheDirectory, openLogFolder } from '../services/nativeMediaService';
import { demoSources, scenarios } from '../fixtures/scenarios';
import { SourcePicker } from '../components/SourcePicker';
import { PreviewPanel, delayText, scenePosition } from '../components/PreviewPanel';
import { AdvancedSettings } from '../components/AdvancedSettings';
import { useTheme } from './useTheme';
import { SettingsDialog } from '../components/SettingsDialog';
import { defaultTrackTitle, settingsFromDefaults, useOutputDefaults } from './useOutputDefaults';
import { Modal } from '../components/Modal';

const siteUrl = 'https://perevodman.com/';
const emptyPreview: PreviewState = { selectedPoint: 'start', audioVariant: 'corrected', playbackPosition: 120, status: 'empty', playing: false, volume: 75 };
const systemApi: SystemSettingsApi = nativeAvailable ? { get: getAppSettings, setLoad: setLoadMode, chooseCacheDirectory, setCacheDirectory, openLogFolder } : mockSystemSettings;
type Sources = { movie?: SourceFile; audio?: SourceFile };

export function App() {
  const { theme, setTheme, accent, setAccent, volume, setVolume } = useTheme();
  const [outputDefaults, setOutputDefaults] = useOutputDefaults();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [overwrite, setOverwrite] = useState<{ path: string; token: string }>();
  const nameInput = useRef<HTMLInputElement>(null);
  const [sources, setSources] = useState<Sources>({});
  const [demo, setDemo] = useState(!nativeAvailable);
  const service = demo ? mockMediaService : nativeMediaService;
  const pickerService = nativeAvailable ? nativeMediaService : mockMediaService;
  const [job, dispatch] = useReducer(jobReducer, initialJob);
  const [scenario, setScenario] = useState<Scenario>('offset');
  const [adjustment, setAdjustment] = useState<Adjustment>(initialAdjustment);
  const [settings, setSettings] = useState<OutputSettings>(() => settingsFromDefaults(outputDefaults));
  const [titleTouched, setTitleTouched] = useState(false);
  const [preview, setPreview] = useState({ ...emptyPreview, volume });
  const [previewPosition, setPreviewPosition] = useState(120);
  const [previewError, setPreviewError] = useState<string>();
  const [outputName, setOutputName] = useState('');
  const [directory, setDirectory] = useState('');
  const [resultPath, setResultPath] = useState<string>();
  const [resultWarnings, setResultWarnings] = useState<string[]>([]);
  const [allowUnverified, setAllowUnverified] = useState(false);
  const [sourceBusy, setSourceBusy] = useState(false);
  const [notice, setNotice] = useState<{ message: string; detail?: string }>();
  const task = useRef(0), controller = useRef<AbortController | null>(null), previewController = useRef<AbortController | null>(null), pickerController = useRef<AbortController | null>(null), previewTask = useRef(0);
  const invalidate = useCallback(() => { controller.current?.abort(); previewController.current?.abort(); previewTask.current++; task.current++; }, []);
  useEffect(() => () => { invalidate(); pickerController.current?.abort(); }, [invalidate]);
  const showError = useCallback((error: unknown) => {
    const normalized = error instanceof Error ? error : new NativeMediaError(error);
    setNotice({ message: normalized.message, detail: normalized instanceof NativeMediaError ? normalized.detail : undefined });
  }, []);
  const reset = useCallback((next: Sources, preserveEdits = false) => {
    invalidate(); pickerController.current?.abort(); setSourceBusy(false); setSources(next); setAdjustment(value => preserveEdits ? { ...initialAdjustment, manualDelayMs: value.manualDelayMs } : initialAdjustment); setAllowUnverified(false); setResultPath(undefined); setResultWarnings([]); setNotice(undefined); setPreviewError(undefined); setOverwrite(undefined);
    const position = scenePosition('start', next.movie?.metadata?.duration ?? 6840);
    setPreview({ ...emptyPreview, volume, playbackPosition: position }); setPreviewPosition(position);
    setSettings(value => ({ ...value, referenceTrack: next.movie?.metadata?.recommendedTrack ?? 'original', externalTrack: next.audio?.metadata?.recommendedTrack, skipSubtitles: preserveEdits ? value.skipSubtitles : [], skipAudio: preserveEdits ? value.skipAudio : [] }));
    if (!preserveEdits) setOutputName(next.movie ? next.movie.name.replace(/\.[^.]+$/, '') + '.NEW.mkv' : '');
    dispatch({ type: 'reset', taskId: task.current, ready: !!(next.movie && next.audio) });
  }, [invalidate, volume]);
  const changeSource = useCallback((kind: SourceKind, source?: SourceFile) => {
    const next = { ...sources, [kind]: source };
    if (source?.metadata?.origin === 'probe') {
      setDemo(false);
      if (kind === 'movie' && next.audio?.metadata?.origin === 'demo') next.audio = undefined;
      if (kind === 'audio' && next.movie?.metadata?.origin === 'demo') next.movie = undefined;
    }
    if (kind === 'audio') setTitleTouched(false);
    reset(next);
  }, [sources, reset]);
  const changeMovie = useCallback((source?: SourceFile) => changeSource('movie', source), [changeSource]);
  const changeAudio = useCallback((source?: SourceFile) => changeSource('audio', source), [changeSource]);
  const pick = async (kind: SourceKind) => {
    pickerController.current?.abort(); const c = new AbortController(); pickerController.current = c; setSourceBusy(true); setNotice(undefined);
    try { const source = await pickerService.pickSource(kind, c.signal); if (source && !c.signal.aborted) changeSource(kind, source); }
    catch (error) { if (!c.signal.aborted) showError(error as Error); }
    finally { if (pickerController.current === c) setSourceBusy(false); }
  };
  const busy = job.status === 'analyzing' || job.status === 'building';
  // Known right after the files are chosen, before any comparison is started.
  const dubTrack = sources.audio?.metadata?.tracks.find(track => track.id === settings.externalTrack);
  const mismatch = sources.movie?.metadata?.origin === 'probe' && sources.audio?.metadata?.origin === 'probe' ? durationMismatch(sources.movie.metadata.duration, dubTrack?.duration ?? sources.audio.metadata.duration) : null;
  const unverified = !!job.result && ['uncertain', 'different-edit'].includes(job.result.verdict);
  const review = !!job.result && ['review', 'cancelled'].includes(job.status);
  const trackTitle = defaultTrackTitle(outputDefaults, sources.audio?.name);
  // Until the user edits the field, the track name follows the dub file and the saved defaults.
  useEffect(() => { if (!titleTouched) setSettings(value => value.title === trackTitle ? value : { ...value, title: trackTitle }); }, [titleTouched, trackTitle]);
  const input = sources.movie && sources.audio ? { movie: sources.movie, audio: sources.audio, scenario, settings: { ...settings, title: settings.title.trim() || trackTitle } } : undefined;
  const duration = sources.movie?.metadata?.duration ?? 6840;
  const preparePreview = useCallback(async () => {
    if (!sources.movie || !sources.audio) return;
    previewController.current?.abort(); const c = new AbortController(); previewController.current = c; const id = ++previewTask.current;
    setPreview(value => ({ ...value, status: 'preparing', playing: false })); setPreviewError(undefined);
    try {
      const prepared = await service.preparePreview({ movie: sources.movie, audio: sources.audio, scenario, settings, adjustment, preview: { ...emptyPreview, audioVariant: preview.audioVariant, selectedPoint: preview.selectedPoint, playbackPosition: previewPosition } }, c.signal);
      if (!c.signal.aborted && id === previewTask.current) setPreview(value => ({ ...value, status: 'ready', url: prepared?.url, clipStart: prepared?.start, clipDuration: prepared?.duration, effectiveDelayMs: prepared?.effectiveDelayMs, playbackPosition: previewPosition }));
    } catch (error) {
      if (!c.signal.aborted && id === previewTask.current) { setPreview(value => ({ ...value, status: 'error', playing: false })); setPreviewError((error as Error).message); }
    }
  }, [sources, service, scenario, settings, adjustment, preview.selectedPoint, preview.audioVariant, previewPosition]);
  useEffect(() => {
    if (!review) return;
    const timer = setTimeout(() => void preparePreview(), 350);
    return () => { clearTimeout(timer); previewController.current?.abort(); };
  }, [review, preparePreview]);
  useEffect(() => {
    if (!demo || !preview.playing) return;
    const timer = setInterval(() => setPreview(value => ({ ...value, playbackPosition: Math.min(duration, value.playbackPosition + 1), playing: value.playbackPosition < duration - 1 })), 1000);
    return () => clearInterval(timer);
  }, [demo, duration, preview.playing]);
  const stale = () => {
    previewController.current?.abort(); previewTask.current++;
    setPreview(value => ({ ...value, status: job.result ? 'stale' : 'empty', playing: false, effectiveDelayMs: undefined }));
  };
  const seek = (position: number) => { stale(); setPreviewPosition(position); setPreview(value => ({ ...value, playbackPosition: position })); };
  const changePreview = (update: Partial<PreviewState>) => {
    if (update.selectedPoint || update.audioVariant) { stale(); setPreviewPosition(update.playbackPosition ?? preview.playbackPosition); }
    setPreview(value => ({ ...value, ...update }));
  };
  const adjust = (ms: number) => { stale(); setPreviewPosition(preview.playbackPosition); setAdjustment(value => ({ ...value, manualDelayMs: ms })); };
  const run = async (build: boolean, overwriteToken?: string) => {
    if (!input || busy || sourceBusy) return;
    controller.current?.abort(); previewController.current?.abort(); previewTask.current++;
    const c = new AbortController(); controller.current = c; const id = ++task.current;
    setPreview(value => ({ ...value, playing: false })); setNotice(undefined); setOverwrite(undefined);
    dispatch({ type: 'start', taskId: id, status: build ? 'building' : 'analyzing' });
    const progress = (event: Progress) => { if (!c.signal.aborted) dispatch({ type: 'progress', taskId: id, progress: event }); };
    try {
      if (build) {
        const result = await service.build({ ...input, adjustment, outputName, outputDirectory: directory, allowUnverified, analysis: job.result, overwriteToken }, c.signal, progress);
        if (!c.signal.aborted && task.current === id) { setResultPath(result?.path); setResultWarnings(result?.warnings ?? []); dispatch({ type: 'finish', taskId: id, status: 'completed' }); }
      } else {
        const result = await service.analyze(input, c.signal, progress);
        if (c.signal.aborted || task.current !== id) return;
        setAdjustment(value => ({ ...initialAdjustment, manualDelayMs: value.manualDelayMs, automaticDelayMs: result.proposedDelayMs, speedCorrection: result.speedCorrection }));
        setAllowUnverified(false); setPreview(value => ({ ...value, status: 'stale', playing: false }));
        dispatch({ type: 'finish', taskId: id, status: 'review', result });
      }
    } catch (error) {
      if (!c.signal.aborted && task.current === id) {
        const e = error instanceof Error ? error : new NativeMediaError(error);
        dispatch({ type: 'failure', taskId: id, error: e.message });
        if (build && e instanceof NativeMediaError && e.code === 'output-exists' && e.context) setOverwrite(e.context);
        else showError(e);
      }
    }
  };
  const cancel = () => {
    controller.current?.abort(); previewController.current?.abort(); previewTask.current++;
    setPreview(value => ({ ...value, playing: false })); dispatch({ type: 'finish', taskId: task.current, status: 'cancelled' });
  };
  // The saved film is an ordinary MKV, so a second dub is added by running it through the same steps again.
  const addAnotherDub = async () => {
    if (!resultPath) return;
    try {
      const movie = await probeSource(resultPath, 'movie');
      const name = sources.movie?.metadata?.tracks.find(track => track.id === settings.referenceTrack)?.name;
      reset({ movie });
      setSettings(value => ({ ...value, referenceTrack: movie.metadata?.tracks.find(track => track.name === name)?.id ?? value.referenceTrack }));
      // "Film.NEW.mkv" -> "Film.NEW2.mkv" -> "Film.NEW3.mkv", so the earlier result is not overwritten.
      const base = movie.name.replace(/\.[^.]+$/, '');
      setOutputName((/\.NEW\d*$/.test(base) ? base.replace(/\.NEW(\d*)$/, (_, n) => `.NEW${(Number(n) || 1) + 1}`) : `${base}.NEW`) + '.mkv');
    } catch (error) { showError(error as Error); }
  };
  const loadDemo = (value = scenario) => { setDemo(true); setScenario(value); reset(demoSources()); };
  const leaveDemo = () => { setDemo(false); reset({}); };
  const pickDirectory = async () => { try { const selected = await chooseOutputDirectory(); if (selected) setDirectory(selected); } catch (error) { showError(error as Error); } };
  const updateSettings = (next: OutputSettings) => {
    // The tracks and the way the speed is changed decide what the check measured, so any change repeats it.
    if (next.referenceTrack !== settings.referenceTrack || next.externalTrack !== settings.externalTrack || next.pitchMode !== settings.pitchMode) reset(sources, true);
    else { stale(); setPreviewPosition(preview.playbackPosition); }
    setSettings(next);
  };
  // A plain link would navigate the app window itself; in the app the address is handed to the system browser.
  const openSiteLink = (event: { preventDefault: () => void }) => { event.preventDefault(); if (nativeAvailable) void openSite(siteUrl).catch(showError); else window.open(siteUrl, '_blank', 'noopener,noreferrer'); };
  return <div className="page">
    <div className="app">
    <header>
      <div className="brand"><span className="brand-mark" aria-hidden="true"><svg viewBox="0 0 36 48" fill="none"><rect x="10" y="2" width="16" height="29" rx="8" fill="currentColor"/><path d="M13 8h10M12 13h12M12 18h12M13 23h10" stroke="#68180f" strokeWidth="2"/><path d="M5 23v2a13 13 0 0 0 26 0v-2M18 38v7M10 46h16" stroke="currentColor" strokeWidth="3"/></svg></span><div><h1>SoundMux</h1><p>Добавить озвучку к фильму</p></div></div>
      <div className="header-tools">{demo && <span className="prototype"><span aria-hidden="true"/>Демо · обработка имитируется</span>}<button className="settings-trigger" onClick={() => setSettingsOpen(true)} aria-haspopup="dialog"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m9 3-.7 2.3-2 .9-2.2-.5-1.5 2.6 1.6 1.8-.2 2.2-1.6 1.8 1.5 2.6 2.4-.4 1.8 1.1L9 20h3l.8-2.3 2-1 2.3.4 1.5-2.6-1.7-1.8.1-2.2 1.6-1.8-1.5-2.6-2.4.4-1.8-1L12 3H9Z" stroke="currentColor" strokeWidth="1.4"/><circle cx="10.5" cy="11.5" r="3" stroke="currentColor" strokeWidth="1.4"/></svg>Настройки</button></div>
    </header>
    <main>
      {notice && <div className="notice" role="alert"><span>{notice.message}</span>{notice.detail && <details><summary>Подробности</summary><pre>{notice.detail}</pre></details>}<button className="text-button" onClick={() => setNotice(undefined)}>Закрыть сообщение</button></div>}
      <div className="workspace">
        <section className="sources-panel">
          <div className="section-heading"><h2>Фильм и озвучка</h2><span className="step-label">01 / Источники</span></div>
          <p className="intro">Выберите фильм и отдельный файл с нужным переводом.</p>
          <SourcePicker kind="movie" source={sources.movie} service={pickerService} onChange={changeMovie} onPick={() => void pick('movie')} onError={showError}/>
          <SourcePicker kind="audio" source={sources.audio} service={pickerService} onChange={changeAudio} onPick={() => void pick('audio')} onError={showError}/>
          {sourceBusy && <p className="small muted" role="status">Чтение выбранного файла…</p>}
          <div className="analysis-area" aria-live="polite">
            {mismatch && !job.result && job.status !== 'analyzing' && <div className="result warning">
              <span className="eyebrow">ВНИМАНИЕ</span><h3>Длительности сильно отличаются</h3>
              <p>Фильм: {formatClock(mismatch.movie)}, озвучка: {formatClock(mismatch.dub)} (разница {formatClock(mismatch.difference)}). Так бывает, когда озвучка сделана для другой версии фильма (режиссёрской, расширенной) или сильно обрезана. Автоматически подогнать такую озвучку, скорее всего, не получится, а проверка может занять несколько минут.</p>
            </div>}
            {job.result && job.status !== 'analyzing' && <div className={`result ${unverified ? 'warning' : ''}`}>
              <span className="eyebrow">{job.result.demo ? 'ДЕМОНСТРАЦИОННЫЙ РЕЗУЛЬТАТ' : 'РЕЗУЛЬТАТ ПРОВЕРКИ'}</span><h3>{job.result.explanation}</h3>
              <p>{unverified ? job.result.verdict === 'different-edit' ? 'Такой монтаж нельзя исправить автоматически. Проверьте фрагменты и при необходимости настройте сдвиг вручную.' : 'Автоматическая поправка не применена. Проверьте фрагменты и при необходимости настройте сдвиг вручную.' : job.result.verdict === 'drift' ? 'Предложено исправить скорость с сохранением тона. Проверьте фрагменты перед сохранением.' : `${delayText(adjustment.automaticDelayMs)}. Проверьте несколько фрагментов перед сохранением.`}</p>
              <details><summary>Подробнее</summary>{job.result.demo && <p className="small">Примерные данные, а не анализ выбранных файлов.</p>}{job.result.evidence.map(line => <p className="small" key={line}>{line}</p>)}</details>
            </div>}
            {job.status === 'error' && <p className="cancel-note">Операция не завершена. Проверьте сообщение выше, замените файл или повторите проверку.</p>}
            {job.status === 'cancelled' && <p className="cancel-note">{demo ? 'Демонстрация' : 'Обработка'} отменена. Файлы остались выбраны — можно попробовать ещё раз.</p>}
          </div>
          <AdvancedSettings settings={settings} update={updateSettings} movie={sources.movie} audio={sources.audio} result={job.result} demo={demo} manualDelay={adjustment.manualDelayMs} adjust={adjust} disabled={busy || job.status === 'completed'}/>
      <section className="output-panel">
        <div className="output-fields">
          <label>Название результата<input ref={nameInput} value={outputName} placeholder="Сначала выберите фильм" disabled={!sources.movie || busy || job.status === 'completed'} onChange={event => { setOutputName(event.target.value); setNotice(undefined); }}/></label>
          <label>Название дорожки<input value={settings.title} maxLength={120} placeholder={trackTitle} disabled={busy || job.status === 'completed'} onChange={event => { setTitleTouched(true); setSettings(value => ({ ...value, title: event.target.value })); setNotice(undefined); }}/></label>
          <label className="wide-field">Куда сохранить{demo ? ' · пример' : ''}<div className="directory-field"><input value={directory || 'Рядом с фильмом'} readOnly disabled={busy || job.status === 'completed'}/>{!demo && <button disabled={busy || job.status === 'completed'} onClick={() => void pickDirectory()}>Выбрать</button>}</div></label>
        </div>
        <p className="small muted">{demo ? 'Место назначения демонстрационное. Файлы не создаются.' : 'Исходные файлы сохраняются. Замена существующего результата — только с вашего подтверждения.'}</p>
        {unverified && review && <label className="unverified checkbox"><input type="checkbox" checked={allowUnverified} onChange={event => setAllowUnverified(event.target.checked)}/>Программа не смогла проверить, совпадает ли звук. Посмотрите фрагменты: если на слух всё в порядке, можно сохранять.</label>}
        <div className="action-row">
          <div className="summary"><p>Картинка не меняется · {settings.makeDefault ? 'новая озвучка включится сама' : 'озвучку нужно будет выбрать в плеере'}</p><p className="small muted">Старые файлы остаются на месте{adjustment.speedCorrection !== 1 || settings.compatibility ? '. Звук будет пересчитан. Дополнительные данные объёмного звука могут не сохраниться.' : ''}</p></div>
          <div className="primary-area">
            {busy ? <button onClick={cancel}>Отменить</button> : job.status === 'completed' ? <button className="primary" onClick={() => reset({})}>Добавить другой фильм</button> : <button className="primary" disabled={!input || sourceBusy || (review && (!outputName.trim() || (unverified && !allowUnverified)))} onClick={() => void run(review)}>{review ? unverified ? 'Всё равно сохранить' : 'Сохранить фильм' : job.status === 'error' ? 'Повторить проверку' : mismatch ? 'Всё равно проверить синхронизацию' : 'Проверить синхронизацию'}<span aria-hidden="true"> →</span></button>}
            {!input && <span className="small muted">Выберите фильм и озвучку</span>}
          </div>
        </div>
        <div aria-live="polite">
          {busy && <div className="progress"><div><span>{demo ? 'Демонстрация · ' : ''}{job.progress?.label ?? 'Подготовка'}</span><span>{job.progress?.percent != null ? `${job.progress.percent}%` : 'Выполняется…'}{formatRemaining(job.progress?.etaSeconds) && ` · ${formatRemaining(job.progress?.etaSeconds)}`}</span></div><progress max="100" value={job.progress?.percent ?? undefined}/></div>}
          {job.status === 'completed' && <div className="completed"><strong>{demo ? 'Демонстрация завершена. Файл не создавался.' : 'Готово. Фильм с новой озвучкой сохранён.'}</strong><div>{!demo && <button disabled={!resultPath} onClick={() => void addAnotherDub()}>Добавить ещё озвучку</button>}<button disabled={!resultPath} onClick={() => resultPath && void openResult(resultPath, true).catch(error => showError(new NativeMediaError(error)))}>Открыть папку</button><button disabled={!resultPath} onClick={() => resultPath && void openResult(resultPath, false).catch(error => showError(new NativeMediaError(error)))}>Воспроизвести результат</button></div><span className="small muted">{resultPath ?? 'Эти действия доступны после реального сохранения.'}</span>{resultWarnings.map(line => <span className="small" role="status" key={line}>{line}</span>)}</div>}
        </div>
      </section>
        </section>
        <PreviewPanel preview={preview} setPreview={changePreview} seek={seek} adjustment={adjustment} adjust={adjust} enabled={review} prepare={() => void preparePreview()} busy={busy} demo={demo} duration={duration} error={previewError} pitchShift={job.result?.pitchShift}/>
      </div>
    </main>
    </div>
    <footer className="site-footer"><div className="app footer-inner"><span>{demo ? 'Демонстрация интерфейса' : 'Обработка на вашем компьютере'} · файлы не отправляются в сеть · Создано для <a className="site-link" href={siteUrl} target="_blank" rel="noopener noreferrer" onClick={openSiteLink}>perevodman.com</a></span><div className="demo-controls">
      {demo ? <><label htmlFor="scenario">Демо-сценарий</label><select id="scenario" value={scenario} onChange={event => loadDemo(event.target.value as Scenario)}>{scenarios.map(value => <option value={value.id} key={value.id}>{value.label}</option>)}</select>{nativeAvailable && <button className="text-button" onClick={leaveDemo}>Вернуться к файлам</button>}</> : <button className="text-button" onClick={() => loadDemo()}>Открыть демонстрацию</button>}
    </div></div></footer>
    {settingsOpen && <SettingsDialog theme={theme} setTheme={setTheme} accent={accent} setAccent={setAccent} outputDefaults={outputDefaults} setOutputDefaults={setOutputDefaults} system={systemApi} volume={volume} setVolume={value => { setVolume(value); setPreview(previous => ({ ...previous, volume: value })); }} onClose={() => setSettingsOpen(false)} cacheDisabled={busy || sourceBusy || preview.status === 'preparing'} onClearCache={nativeAvailable ? async () => { setPreview(value => ({ ...value, status: 'stale', playing: false, url: undefined })); return clearMediaCache(); } : undefined}/>}
    {overwrite && <Modal title="Файл уже существует" onClose={() => setOverwrite(undefined)}>
      <p>Заменить этот файл новым результатом?</p><p className="overwrite-path">{overwrite.path}</p><p className="small muted">Предыдущий результат будет заменён после успешной проверки нового. Исходный фильм и озвучка останутся на месте.</p>
      <div className="modal-actions"><button autoFocus onClick={() => { setOverwrite(undefined); requestAnimationFrame(() => { nameInput.current?.focus(); nameInput.current?.select(); }); }}>Изменить имя</button><button onClick={() => setOverwrite(undefined)}>Отмена</button><button className="primary" onClick={() => void run(true, overwrite.token)}>Перезаписать</button></div>
    </Modal>}
  </div>;
}
