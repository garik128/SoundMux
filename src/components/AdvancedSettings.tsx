import type { AnalysisResult, OutputSettings, PitchMode, SourceFile } from '../domain/types';
import { InfoHint } from './InfoHint';
interface Props { settings: OutputSettings; update: (settings: OutputSettings) => void; movie?: SourceFile; audio?: SourceFile; result?: AnalysisResult; demo: boolean; manualDelay: number; adjust: (ms: number) => void; disabled: boolean }
const pitchSummary: Record<PitchMode, string> = {
  auto: 'Способ выбирается сам; для PAL тон сохраняется.',
  shift: 'Скорость и тон меняются вместе, без артефактов.',
  keep: 'Тон не меняется, возможны слабые артефакты.',
};
export function AdvancedSettings({ settings, update, movie, audio, result, demo, manualDelay, adjust, disabled }: Props) {
  const set = <K extends keyof OutputSettings>(key: K, value: OutputSettings[K]) => update({ ...settings, [key]: value });
  return <details className="advanced"><summary>Дополнительно</summary>
    <p className="small muted">{demo ? 'Параметры будущей обработки. В демо файлы не меняются.' : 'Исходные файлы всегда сохраняются. Настройки применяются к новому файлу.'}</p>
    <fieldset disabled={disabled}><div className="settings-grid">
      <label>Язык новой дорожки<select value={settings.language} onChange={event => set('language', event.target.value)}><option>Русский</option><option>Английский</option><option>Другой</option></select></label>
      <label>С каким звуком сравнивать<select value={settings.referenceTrack} onChange={event => set('referenceTrack', event.target.value)}>{movie?.metadata ? movie.metadata.tracks.map(track => <option key={track.id} value={track.id}>{track.name}</option>) : <option value="original">Автоматически · после выбора фильма</option>}</select></label>
      <label>Ручная поправка, мс<input type="number" step="50" min="-600000" max="600000" value={manualDelay} onChange={event => adjust(Number.isFinite(event.target.valueAsNumber) ? event.target.valueAsNumber : 0)}/></label>
      {audio?.metadata && audio.metadata.tracks.length > 1 && <label>Какую озвучку взять из файла<select value={settings.externalTrack} onChange={event => set('externalTrack', event.target.value)}>{audio.metadata.tracks.map(track => <option key={track.id} value={track.id}>{track.name}</option>)}</select></label>}
    </div>
    {([['makeDefault', 'Новая озвучка включится сама'], ['keepMovieAudio', 'Сохранить исходный звук фильма'], ['keepOriginal', 'При исправлении сохранить также исходную озвучку']] as const).map(([key, label]) => <label className="checkbox" key={key}><input type="checkbox" checked={settings[key]} onChange={event => set(key, event.target.checked)}/>{label}</label>)}
    {settings.keepMovieAudio && movie?.metadata && <Checklist title="Звук фильма" tracks={movie.metadata.tracks} skipped={settings.skipAudio} change={value => set('skipAudio', value)}/>}
    {movie?.metadata?.subtitles && <Checklist title="Субтитры фильма" tracks={movie.metadata.subtitles} skipped={settings.skipSubtitles} change={value => set('skipSubtitles', value)}/>}
    <label>Обработка звука<InfoHint title="Обработка звука">
      <dl>
        <dt>Как в исходной озвучке</dt><dd>Без изменения скорости звук копируется. При исправлении скорости он кодируется тем же форматом и с тем же битрейтом (AC3 остаётся AC3), поэтому размер почти не растёт. Для остальных форматов используется FLAC.</dd>
        <dt>Для старых телевизоров и приставок</dt><dd>Будет использован E-AC3 — распространённый формат для телевизоров. Звук потребуется пересчитать. Для более шести каналов этот режим недоступен.</dd>
      </dl>
      <p>Дополнительные данные Atmos / DTS:X при пересчёте не сохраняются.</p>
    </InfoHint><select value={settings.compatibility ? 'tv' : 'source'} onChange={event => set('compatibility', event.target.value === 'tv')}><option value="source">Как в исходной озвучке</option><option value="tv">Для старых телевизоров и приставок</option></select></label>
    <p className="small muted">{settings.compatibility ? 'E-AC3: подходит старым телевизорам и приставкам.' : 'Озвучка остаётся в своём формате.'}</p>
    {!demo && <>
      <label>Скорость и тон<InfoHint title="Скорость и тон">
        <p>Нужно, когда озвучка идёт чуть быстрее или медленнее фильма (например, сделана для видео с другой частотой кадров).</p>
        <dl>
          <dt>Автоматически</dt><dd>Для озвучки, которая отличается от фильма как 24 и 23,976 кадра/с (разница 0,1%), звук перетягивается вместе с тоном: на слух тон не меняется, зато нет искажений растяжки. Во всех остальных случаях, в том числе для PAL (25 кадров/с), тон сохраняется.</dd>
          <dt>Менять скорость вместе с тоном</dt><dd>Как у магнитофона: способ точный и обратимый, артефактов нет. Подходит, когда озвучка сделана с PAL-видео (25 кадров/с): голоса на ней звучат выше нормы, и перетяжка вернёт их на место.</dd>
          <dt>Сохранять тон</dt><dd>Скорость меняется, тон остаётся прежним. Подходит, когда голоса звучат естественно, но дорожка подогнана под другой хронометраж. Растяжка оставляет едва слышные артефакты, и исправить их позже не получится.</dd>
        </dl>
      </InfoHint><select value={settings.pitchMode ?? 'auto'} onChange={event => set('pitchMode', event.target.value as PitchMode)}><option value="auto">Автоматически</option><option value="shift">Менять скорость вместе с тоном</option><option value="keep">Сохранять тон</option></select></label>
      <p className="small muted">{pitchSummary[settings.pitchMode ?? 'auto']}{result && result.speedCorrection !== 1 && ` Для этой озвучки: ${result.pitchShift ? 'скорость и тон меняются вместе' : 'тон сохраняется'}.`}</p>
      <label className="checkbox"><input type="checkbox" checked={!!settings.limitPeak} onChange={event => set('limitPeak', event.target.checked)}/><span>Защита от перегрузки<InfoHint title="Защита от перегрузки">
        <p>После пересчёта звук иногда упирается в максимальную громкость, и тогда слышны щелчки (клиппинг). Без защиты программа только предупредит об этом после сохранения.</p>
        <p>С защитой в таком случае дорожка пересчитывается на 3 дБ тише. Звук без пересчёта не затрагивается.</p>
      </InfoHint></span></label>
    </>}
    </fieldset>
  </details>;
}

// Unchecked tracks are stored as a skip list, so an untouched selection (and a new film) keeps everything.
function Checklist({ title, tracks, skipped = [], change }: { title: string; tracks: { id: string; name: string }[]; skipped?: string[]; change: (skipped: string[]) => void }) {
  if (tracks.length < 2) return null;
  const left = tracks.filter(track => !skipped.includes(track.id)).length;
  return <details className="advanced"><summary>{title}: оставить {left} из {tracks.length}</summary>
    {tracks.map(track => <label className="checkbox" key={track.id}><input type="checkbox" checked={!skipped.includes(track.id)} onChange={event => change(event.target.checked ? skipped.filter(id => id !== track.id) : [...skipped, track.id])}/>{track.name}</label>)}
  </details>;
}
