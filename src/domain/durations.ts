// Length of the film against length of the dub, checked as soon as both files are chosen.
// A large difference usually means the dub belongs to another cut (theatrical, director's, extended)
// or is heavily trimmed; the sound then matches only in pieces and a single shift cannot fix it.

// Frame-rate conversions legitimately change the length (PAL speed-up 25 vs 23.976 or 24 fps). They are expected, not suspicious.
const frameRateRatios = [25 / (24000 / 1001), 25 / 24].flatMap(ratio => [ratio, 1 / ratio]);
const ratioTolerance = 0.012;
const minimumDifferenceSeconds = 120;
const minimumDifferenceShare = 0.03;

export interface DurationMismatch { movie: number; dub: number; difference: number }

export function durationMismatch(movieSeconds?: number, dubSeconds?: number): DurationMismatch | null {
  if (!movieSeconds || !dubSeconds || !(movieSeconds > 0) || !(dubSeconds > 0)) return null;
  const ratio = dubSeconds / movieSeconds;
  if (frameRateRatios.some(known => Math.abs(ratio - known) <= ratioTolerance * known)) return null;
  const difference = Math.abs(dubSeconds - movieSeconds);
  return difference >= Math.max(minimumDifferenceSeconds, movieSeconds * minimumDifferenceShare) ? { movie: movieSeconds, dub: dubSeconds, difference } : null;
}

/** Hours:minutes:seconds, the way tracks are listed on the site (01:39:35). */
export function formatClock(seconds: number) {
  const total = Math.max(0, Math.round(seconds));
  const pad = (value: number) => String(value).padStart(2, '0');
  return `${pad(Math.floor(total / 3600))}:${pad(Math.floor((total % 3600) / 60))}:${pad(total % 60)}`;
}
