import { useEffect, useRef } from 'react';
import type { SourceFile, SourceKind } from '../domain/types';
import { formatClock } from '../domain/durations';
import type { MediaService } from '../services/mediaService';

interface Props { kind: SourceKind; source?: SourceFile; service: MediaService; onChange: (source?: SourceFile) => void; onPick: () => void; onError?: (error: Error) => void }
export function SourcePicker({ kind, source, service, onChange, onPick, onError }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => service.bindDrop(ref.current!, kind, onChange, onError), [kind, service, onChange, onError]);
  return <div ref={ref} className={`source ${source ? 'has-file' : ''}`}>
    <div className="source-label"><span className="file-icon" aria-hidden="true">{kind === 'movie' ? '▤' : '♫'}</span><h3>{kind === 'movie' ? 'Фильм' : 'Внешняя озвучка'}</h3>{source?.metadata?.origin === 'demo' && <span className="tag">Пример</span>}</div>
    {source ? <>
      <p className="filename" title={source.nativePath ?? source.name}>{source.name}</p>
      <p className="muted small">{new Intl.NumberFormat('ru', { maximumFractionDigits: 2 }).format(source.size / 1024 ** 3)} ГБ{source.metadata?.origin === 'demo' ? ' · данные примера' : source.metadata ? ` · ${formatClock(source.metadata.tracks.find(track => track.id === source.metadata?.recommendedTrack)?.duration ?? source.metadata.duration)}` : ' · сведения о звуке ещё не определены'}</p>
      <div className="source-actions"><button className="text-button" onClick={onPick}>Заменить</button><button className="text-button muted" onClick={() => onChange(undefined)} aria-label={`Удалить ${kind === 'movie' ? 'фильм' : 'озвучку'}`}>Убрать</button></div>
    </> : <><button className="choose" onClick={onPick}>Выбрать {kind === 'movie' ? 'фильм' : 'озвучку'} <span aria-hidden="true">＋</span></button><p className="muted small">или перетащите файл сюда</p></>}
  </div>;
}
