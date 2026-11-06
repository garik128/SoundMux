import { useEffect, useState } from 'react';

export type ThemePreference = 'system' | 'light' | 'dark';
export type Accent = 'amber' | 'red' | 'blue' | 'green';
const storageKey = 'soundmux.theme';

function readPreference(): ThemePreference {
  try {
    const value = localStorage.getItem(storageKey) ?? localStorage.getItem('audiomost.theme');
    if (value === 'light' || value === 'dark') return value;
  } catch { /* The system default still works when storage is unavailable. */ }
  return 'system';
}

export function useTheme() {
  const [theme, setTheme] = useState<ThemePreference>(readPreference);
  const [accent, setAccent] = useState<Accent>(() => {
    try { const value = localStorage.getItem('soundmux.accent'); if (value === 'red' || value === 'blue' || value === 'green') return value; } catch { /* Storage is optional. */ }
    return 'amber';
  });
  const [volume, setVolume] = useState(() => {
    try { const raw = localStorage.getItem('soundmux.volume'); const value = Number(raw); if (raw !== null && Number.isFinite(value)) return Math.max(0, Math.min(100, value)); } catch { /* Storage is optional. */ }
    return 75;
  });
  useEffect(() => {
    document.documentElement.dataset.accent = accent;
    try { localStorage.setItem('soundmux.accent', accent); } catch { /* Storage is optional. */ }
  }, [accent]);
  useEffect(() => { try { localStorage.setItem('soundmux.volume', String(volume)); } catch { /* Storage is optional. */ } }, [volume]);
  useEffect(() => {
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const apply = () => {
      const resolved = theme === 'system' ? (media.matches ? 'dark' : 'light') : theme;
      document.documentElement.dataset.theme = resolved;
      document.querySelector('meta[name="theme-color"]')?.setAttribute('content', resolved === 'dark' ? '#14171d' : '#f6f3ed');
    };
    apply();
    try { localStorage.setItem(storageKey, theme); } catch { /* Storage is optional. */ }
    media.addEventListener('change', apply);
    return () => media.removeEventListener('change', apply);
  }, [theme]);
  return { theme, setTheme, accent, setAccent, volume, setVolume };
}
