import { Modal } from './Modal';
import { useEffect, useState } from 'react';
import type { Accent, ThemePreference } from '../app/useTheme';
import { languages } from '../app/useOutputDefaults';
import type { OutputDefaults } from '../app/useOutputDefaults';
import { factoryOutputDefaults } from '../app/useOutputDefaults';
import type { AppSettings, LoadMode, SystemSettingsApi } from '../domain/types';

interface Props {
  theme: ThemePreference; setTheme: (value: ThemePreference) => void;
  accent: Accent; setAccent: (value: Accent) => void;
  volume: number; setVolume: (value: number) => void;
  outputDefaults: OutputDefaults; setOutputDefaults: (value: OutputDefaults) => void;
  onClose: () => void;
  onClearCache?: () => Promise<number>;
  cacheDisabled?: boolean;
  system?: SystemSettingsApi;
}

const loadChoices: [LoadMode, string][] = [
  ['fast', 'Быстро: почти все ядра процессора'],
  ['moderate', 'Умеренно: половина ядер'],
  ['background', 'Фоном: минимум ядер, компьютер почти не тормозит'],
];
const message = (error: unknown) => error instanceof Error ? error.message : String(error);

export function SettingsDialog({ theme, setTheme, accent, setAccent, volume, setVolume, outputDefaults, setOutputDefaults, onClose, onClearCache, cacheDisabled, system }: Props) {
  const [clearing, setClearing] = useState(false);
  const [cacheMessage, setCacheMessage] = useState('');
  const [app, setApp] = useState<AppSettings>();
  const [systemMessage, setSystemMessage] = useState('');
  useEffect(() => {
    let current = true;
    void system?.get().then(value => { if (current) setApp(value); }).catch(error => { if (current) setSystemMessage(message(error)); });
    return () => { current = false; };
  }, [system]);
  const clearCache = async () => {
    if (!onClearCache || clearing) return;
    setClearing(true); setCacheMessage('');
    try { const bytes = await onClearCache(); setCacheMessage(`Кэш очищен. Освобождено ${(bytes / 1024 ** 2).toLocaleString('ru', { maximumFractionDigits: 1 })} МБ.`); }
    catch (error) { setCacheMessage(message(error)); }
    finally { setClearing(false); }
  };
  const changeLoad = async (load: LoadMode) => {
    if (!system || !app) return;
    setSystemMessage('');
    try { await system.setLoad(load); setApp({ ...app, load }); } catch (error) { setSystemMessage(message(error)); }
  };
  const changeCache = async (picked: string | null) => {
    if (!system || !app) return;
    setSystemMessage('');
    try {
      await system.setCacheDirectory(picked);
      const updated = await system.get();
      setApp(updated);
      setSystemMessage(updated.cacheDir === app.cacheDir ? '' : `Новые временные файлы будут в этой папке. Прежние остались в «${app.cacheDir}», их можно удалить вручную.`);
    } catch (error) { setSystemMessage(message(error)); }
  };
  const chooseCache = async () => {
    try { const picked = await system?.chooseCacheDirectory(); if (picked) await changeCache(picked); } catch (error) { setSystemMessage(message(error)); }
  };
  const setDefault = <K extends keyof OutputDefaults>(key: K, value: OutputDefaults[K]) => setOutputDefaults({ ...outputDefaults, [key]: value });
  return <Modal title="Настройки" onClose={onClose}>
    <label className="settings-field">Оформление<select value={theme} onChange={event => setTheme(event.target.value as ThemePreference)}><option value="system">Как в системе</option><option value="light">Светлое</option><option value="dark">Тёмное</option></select></label>
    <fieldset className="accent-options"><legend>Акцентный цвет</legend>{([['amber', 'Янтарный'], ['red', 'Красный'], ['blue', 'Синий'], ['green', 'Зелёный']] as const).map(([value, label]) => <label key={value} className={`accent-choice ${accent === value ? 'active' : ''}`}><input type="radio" name="accent" value={value} checked={accent === value} onChange={() => setAccent(value)}/><span className={`swatch ${value}`} aria-hidden="true"/>{label}</label>)}</fieldset>
    <label className="settings-field">Громкость при открытии фильма <span className="muted">{volume}%</span><input aria-label="Громкость при открытии фильма" type="range" min="0" max="100" value={volume} onChange={event => setVolume(+event.target.value)}/></label>
    <fieldset className="settings-section"><legend>Новая озвучка по умолчанию</legend>
      <p className="small muted">Подставляется при каждом запуске. Для конкретного фильма всё можно изменить в разделе «Дополнительно».</p>
      <label className="settings-field">Язык дорожки<select value={outputDefaults.language} onChange={event => setDefault('language', event.target.value)}>{languages.map(language => <option key={language}>{language}</option>)}</select></label>
      <label className="checkbox"><input type="checkbox" checked={outputDefaults.titleFromFile} onChange={event => setDefault('titleFromFile', event.target.checked)}/>Брать название дорожки из имени файла озвучки</label>
      <label className="settings-field">Название дорожки{outputDefaults.titleFromFile ? ' · если имя файла недоступно' : ''}<input value={outputDefaults.title} maxLength={80} onChange={event => setDefault('title', event.target.value)}/></label>
      <p className="small muted">Название можно изменить прямо на главном экране для конкретного фильма. Эти значения хранятся в профиле приложения и не зависят от папки, из которой оно запущено.</p>
      <button className="text-button" onClick={() => setOutputDefaults(factoryOutputDefaults)}>Вернуть стандартные значения</button>
      {([['makeDefault', 'Новая озвучка включится сама'], ['keepMovieAudio', 'Сохранить исходный звук фильма'], ['keepOriginal', 'При исправлении сохранить также исходную озвучку']] as const).map(([key, label]) => <label className="checkbox" key={key}><input type="checkbox" checked={outputDefaults[key]} onChange={event => setDefault(key, event.target.checked)}/>{label}</label>)}
      <label className="settings-field">Обработка звука<select value={outputDefaults.compatibility ? 'tv' : 'source'} onChange={event => setDefault('compatibility', event.target.value === 'tv')}><option value="source">Как в исходной озвучке</option><option value="tv">Для старых телевизоров и приставок</option></select></label>
    </fieldset>
    {system && <fieldset className="settings-section"><legend>Производительность</legend>
      <label className="settings-field">Нагрузка на процессор<select disabled={!app} value={app?.load ?? 'fast'} onChange={event => void changeLoad(event.target.value as LoadMode)}>{loadChoices.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <p className="small muted">Влияет на пересчёт звука при исправлении скорости: на «Быстро» он идёт в несколько потоков, на «Фоном» дольше, но не мешает другой работе. Применяется со следующей обработки.</p>
      <div className="cache-folder"><span className="small">Временные файлы</span><code className="overwrite-path">{app?.cacheDir ?? '…'}</code>
        <p className="small muted">{app?.cacheSkipped ? 'Выбранная папка недоступна, используется стандартная. ' : app?.cacheCustom ? 'Папка выбрана вами. ' : 'Стандартная папка рядом с программой. '}Здесь лежат фрагменты предпросмотра и подготовленный звук (при исправлении скорости несколько гигабайт).</p>
        <div className="modal-actions"><button disabled={!app || cacheDisabled} onClick={() => void chooseCache()}>Выбрать папку…</button><button disabled={!app || cacheDisabled || !(app.cacheCustom || app.cacheSkipped)} onClick={() => void changeCache(null)}>Вернуть стандартную</button></div>
      </div>
      <div className="cache-folder"><span className="small">Журнал работы</span><code className="overwrite-path">{app?.logDir ?? '…'}</code>
        <p className="small muted">Короткая запись о том, что делала программа, и об ошибках. Хранится не больше трёх небольших файлов. Если что-то не работает, пришлите файл soundmux.log из этой папки: личных данных в нём нет, а имя пользователя Windows заменено.</p>
        <div className="modal-actions"><button disabled={!app} onClick={() => void system.openLogFolder().catch(error => setSystemMessage(message(error)))}>Открыть папку журнала</button></div>
      </div>
      {systemMessage && <p className="small" role="status">{systemMessage}</p>}
    </fieldset>}
    <p className="small muted">Настройки сохраняются автоматически. Размер окна и последние папки также запоминаются.</p>
    {onClearCache && <div className="cache-settings"><button disabled={cacheDisabled || clearing} onClick={() => void clearCache()}>{clearing ? 'Очистка…' : 'Очистить кэш'}</button><p className="small muted">Удаляет временные фрагменты и подготовленный звук. Они будут созданы заново при необходимости.</p><p className="small" role="status">{cacheMessage || (cacheDisabled ? 'Очистка доступна после завершения обработки.' : '')}</p></div>}
    <div className="modal-actions"><button className="primary" onClick={onClose}>Готово</button></div>
  </Modal>;
}
