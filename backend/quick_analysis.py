"""Windowed run of the sync analyzer: read only the film windows that are actually compared.

`vendor/audio_sync.py` decodes both files completely before it compares seven windows of the
film. For a multi-gigabyte MKV that means reading the whole file (about a minute or more) to
look at seven minutes of sound. Its functions only ever touch `ref_env[t0:t0 + L]` for the
window starts it derives from the length, so a film envelope that is filled in only inside
those windows gives the same answer, including the drift and PAL/NTSC search. The vendor file
itself stays untouched; this module imports it and calls the same functions.
"""
from __future__ import annotations

import importlib.util

import numpy as np

# Shorter films are cheap to decode whole, and a window must fit the file with room to spare.
MIN_DURATION = 600
# Seconds decoded around each window and thrown away: the envelope subtracts a 500 ms moving
# average, so its first and last 250 ms depend on the neighbouring sound.
MARGIN = 1.0


def load_vendor(path):
    spec = importlib.util.spec_from_file_location('soundmux_audio_sync', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def window_starts(vendor, ref_len, window_len, n_windows):
    """Window positions in milliseconds, computed exactly as vendor.analyze() computes them."""
    usable = ref_len - window_len
    if usable < window_len:
        return None
    return np.linspace(int(usable * 0.05), int(usable * 0.95), n_windows).astype(int)


def quick_analyze(vendor, decode_ref_window, decode_target, ref_seconds, window_len_s, max_offset_s, n_windows=7, min_z=6.0, sync_tol=40):
    """Same result dict as `audio_sync.py`, or None when the full analysis must be used instead.

    decode_ref_window(begin_s, length_s) -> float32 mono samples at vendor.RATE of the film sound.
    decode_target() -> float32 mono samples of the whole external track.
    """
    env_rate = vendor.ENV_RATE
    window_len = int(window_len_s * env_rate)
    ref_len = int(ref_seconds * env_rate)
    starts = window_starts(vendor, ref_len, window_len, n_windows)
    if starts is None:
        return None
    target = decode_target()
    target_duration = len(target) / vendor.RATE
    target_env = vendor.envelope(target)
    del target

    ref_env = np.zeros(ref_len, np.float32)
    margin = int(MARGIN * env_rate)
    for t0 in starts:
        begin = max(0, int(t0) - margin)
        end = min(ref_len, int(t0) + window_len + margin)
        env = vendor.envelope(decode_ref_window(begin / env_rate, (end - begin) / env_rate))
        inner = int(t0) - begin
        if len(env) < inner + window_len:
            return None  # the sound ended earlier than reported or the decode came back short
        ref_env[t0:t0 + window_len] = env[inner:inner + window_len]

    ratio, windows, fit, tried = vendor.solve(ref_env, target_env, n_windows, window_len_s, max_offset_s, min_z)
    result = {'ref_duration': round(ref_len / env_rate, 3), 'target_duration': round(target_duration, 3), 'tried': tried, 'quick': True}
    if ratio is None:
        result.update({'verdict': 'nomatch', 'ratio': None, 'offset_ms': None, 'windows': []})
        return result
    # Mirrors the verdict logic at the end of audio_sync.main().
    offset = int(round(fit['intercept']))
    total = vendor.snap_ratio(ratio * (1.0 + fit['slope']))
    if abs(total - 1.0) > vendor.DRIFT_MIN:
        verdict, ratio = 'drift', total
    elif abs(offset) > sync_tol:
        verdict = 'offset'
    else:
        verdict = 'sync'
    result.update({'verdict': verdict, 'ratio': ratio, 'offset_ms': offset, 'confident': fit['confident'], 'resid_ms': fit['resid_ms'],
                   'median_z': round(fit['median_z'], 1), 'windows': windows})
    return result
