import { useEffect, useState } from 'react';
import type { OutputSettings } from '../domain/types';

// Options of the new track that are remembered between launches. Per-film choices
// (reference track, external track) are never stored.
export type OutputDefaults = Pick<OutputSettings, 'language' | 'title' | 'makeDefault' | 'keepMovieAudio' | 'keepOriginal' | 'compatibility'> & { titleFromFile: boolean };
export const factoryOutputDefaults: OutputDefaults = { language: 'Русский', title: 'Русская озвучка', titleFromFile: true, makeDefault: true, keepMovieAudio: true, keepOriginal: true, compatibility: false };
export const languages = ['Русский', 'Английский', 'Другой'] as const;
const storageKey = 'soundmux.outputDefaults';
// Stored data carries a format version. Anything older (for example values written automatically by earlier builds) is ignored,
// so it cannot pin a setting the user never chose. Bump it only when the meaning of stored values changes.
export const outputDefaultsVersion = 2;

/** Accept only well-formed values; anything else falls back to the factory value for that field. */
export function parseOutputDefaults(raw: string | null): OutputDefaults {
  let value: Record<string, unknown> = {};
  try { const parsed = raw ? JSON.parse(raw) : {}; if (parsed && typeof parsed === 'object' && parsed.v === outputDefaultsVersion) value = parsed; } catch { /* Corrupt storage means factory settings. */ }
  const flag = (key: 'makeDefault' | 'keepMovieAudio' | 'keepOriginal' | 'compatibility') => typeof value[key] === 'boolean' ? value[key] as boolean : factoryOutputDefaults[key];
  const title = typeof value.title === 'string' && value.title.trim() ? value.title.slice(0, 80) : factoryOutputDefaults.title;
  const language = languages.find(item => item === value.language) ?? factoryOutputDefaults.language;
  const titleFromFile = typeof value.titleFromFile === 'boolean' ? value.titleFromFile : factoryOutputDefaults.titleFromFile;
  return { language, title, titleFromFile, makeDefault: flag('makeDefault'), keepMovieAudio: flag('keepMovieAudio'), keepOriginal: flag('keepOriginal'), compatibility: flag('compatibility') };
}

/** Only what differs from the factory values is stored (null when nothing does), so a later change of a factory default still reaches everyone who never touched it. */
export function serializeOutputDefaults(defaults: OutputDefaults): string | null {
  const changed = Object.fromEntries(Object.entries(defaults).filter(([key, value]) => value !== factoryOutputDefaults[key as keyof OutputDefaults]));
  return Object.keys(changed).length ? JSON.stringify({ v: outputDefaultsVersion, ...changed }) : null;
}

/** Name of the new track: the dub file's name (without extension) or the fixed default title. */
export function defaultTrackTitle(defaults: Pick<OutputDefaults, 'title' | 'titleFromFile'>, audioFileName?: string) {
  const stem = audioFileName?.replace(/\.[^.]+$/, '').trim();
  return defaults.titleFromFile && stem ? stem.slice(0, 120) : defaults.title;
}

/** Per-film settings start from the remembered defaults; the title is resolved separately. */
export function settingsFromDefaults(defaults: OutputDefaults): OutputSettings {
  const { titleFromFile: _titleFromFile, ...rest } = defaults;
  return { ...rest, referenceTrack: 'original' };
}

export function useOutputDefaults() {
  const [defaults, setDefaults] = useState<OutputDefaults>(() => {
    try { return parseOutputDefaults(localStorage.getItem(storageKey)); } catch { return factoryOutputDefaults; }
  });
  useEffect(() => {
    try {
      const stored = serializeOutputDefaults(defaults);
      if (stored === null) localStorage.removeItem(storageKey);
      else localStorage.setItem(storageKey, stored);
    } catch { /* Storage is optional. */ }
  }, [defaults]);
  return [defaults, setDefaults] as const;
}
