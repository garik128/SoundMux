#!/usr/bin/env python3
"""
audio_sync.py - measure offset and speed drift between two audio tracks.

Reference = audio track inside the video (its own original dub).
Target    = external audio track that will be muxed in.

Both are decoded to 8 kHz mono, turned into a 1 kHz onset envelope and
cross-correlated in several windows spread across the file. A linear fit of
offset-vs-time gives:
    offset  - constant delay (ms) at t=0 of the reference
    ratio   - speed ratio (target is `ratio` times longer than reference;
              1.0427 = PAL 25 fps track over 23.976 fps video)

Prints one JSON object to stdout. Exit code 0 even when no match is found
(see "verdict" field); non-zero only on decode / usage errors.

Usage:
    audio_sync.py --ffmpeg ffmpeg.exe --ref video.mkv --target dub.ac3
                  [--ref-stream 0:a:0] [--target-stream 0:a:0]
                  [--windows 7] [--window-len 60] [--max-offset 600]
"""

import argparse
import json
import subprocess
import sys

import numpy as np

RATE = 8000        # decode sample rate (Hz)
ENV_RATE = 1000    # envelope rate (Hz) -> 1 ms resolution
DECIM = RATE // ENV_RATE

# Known speed ratios (target / reference duration). Measured ratio is snapped to the
# closest one if within SNAP_TOL, otherwise the measured value is used as-is.
KNOWN_RATIOS = [
    25.0 / (24000.0 / 1001.0),   # 1.042709  PAL track on NTSC-film video
    25.0 / 24.0,                 # 1.041667
    24.0 / (24000.0 / 1001.0),   # 1.001001
]
KNOWN_RATIOS += [1.0 / r for r in list(KNOWN_RATIOS)]
SNAP_TOL = 5e-4

DRIFT_MIN = 2e-4          # |ratio-1| below this = no drift
RESID_MAX_MS = 80.0       # max residual of linear fit before we call it inconsistent


def decode(ffmpeg, path, stream):
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
           "-i", path, "-map", stream, "-vn", "-sn", "-dn",
           "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"]
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0 or len(p.stdout) < RATE * 2:
        err = p.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError("ffmpeg decode failed for %s (%s): %s" % (path, stream, err or "no data"))
    return np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def moving_average(x, k):
    c = np.cumsum(np.insert(x, 0, 0.0, axis=0), dtype=np.float64)
    ma = (c[k:] - c[:-k]) / k
    pad_l = k // 2
    pad_r = len(x) - len(ma) - pad_l
    return np.concatenate([np.full(pad_l, ma[0]), ma, np.full(pad_r, ma[-1])])


def envelope(x):
    n = (len(x) // DECIM) * DECIM
    e = np.abs(x[:n]).reshape(-1, DECIM).mean(axis=1).astype(np.float64)
    e = np.log1p(e * 50.0)                  # compress dynamics
    e = e - moving_average(e, 500)          # keep onsets, drop slow level changes
    return e.astype(np.float32)


def correlate_window(ref_seg, tgt_seg):
    """Return (lag, zscore, ncc). lag k: ref_seg[i] ~ tgt_seg[k+i]."""
    r = ref_seg - ref_seg.mean()
    rn = np.linalg.norm(r)
    if rn < 1e-6:
        return None
    r = r / rn
    t = tgt_seg - tgt_seg.mean()
    nlag = len(t) - len(r) + 1
    if nlag < 1:
        return None
    nfft = 1 << (len(t) + len(r) - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(t, nfft) * np.conj(np.fft.rfft(r, nfft)), nfft)[:nlag]
    # sliding norm of t over len(r) samples -> normalized cross-correlation
    c2 = np.cumsum(np.insert(t.astype(np.float64) ** 2, 0, 0.0))
    norms = np.sqrt(np.maximum(c2[len(r):len(r) + nlag] - c2[:nlag], 1e-9))
    ncc = corr / norms
    k = int(np.argmax(ncc))
    peak = float(ncc[k])
    std = float(ncc.std()) + 1e-9
    z = (peak - float(ncc.mean())) / std
    return k, z, peak


def analyze(ref_env, tgt_env, n_windows, window_len_s, max_offset_s):
    """Correlate n windows of ref against tgt (both in the same time scale)."""
    L = int(window_len_s * ENV_RATE)
    M = int(max_offset_s * ENV_RATE)
    ref_len = len(ref_env)
    tgt_len = len(tgt_env)
    usable = ref_len - L
    if usable < L:
        # very short file: single window covering as much as possible
        L = max(ref_len // 2, ENV_RATE * 5)
        usable = ref_len - L
        n_windows = 1
    starts = np.linspace(int(usable * 0.05), int(usable * 0.95), n_windows).astype(int)
    windows = []
    for t0 in starts:
        ref_seg = ref_env[t0:t0 + L]
        s = max(0, t0 - M)
        e = min(tgt_len, t0 + L + M)
        tgt_seg = tgt_env[s:e]
        res = correlate_window(ref_seg, tgt_seg)
        if res is None:
            continue
        k, z, peak = res
        offset = (s + k) - t0     # ms: target position minus reference position
        windows.append({"t": int(t0), "offset_ms": int(offset), "z": round(z, 1), "ncc": round(peak, 3)})
    return windows


def resample_env(env, r):
    """Bring a target envelope that is r times longer than the reference into reference time."""
    if r == 1.0:
        return env
    m = int(len(env) / r)
    idx = np.arange(m, dtype=np.float64) * r
    return np.interp(idx, np.arange(len(env), dtype=np.float64), env).astype(np.float32)


def snap_ratio(r):
    for kr in KNOWN_RATIOS:
        if abs(r - kr) < SNAP_TOL:
            return kr
    return r


def fit(windows, min_z):
    """Linear fit offset(t) over confident windows. Returns dict with slope/intercept/resid."""
    good = [w for w in windows if w["z"] >= min_z]
    out = {"confident": len(good), "slope": 0.0, "intercept": None, "resid_ms": None,
           "median_z": float(np.median([w["z"] for w in good])) if good else 0.0}
    if not good:
        return out
    t = np.array([w["t"] for w in good], dtype=np.float64)
    o = np.array([w["offset_ms"] for w in good], dtype=np.float64)
    if len(good) >= 3:
        slope, intercept = np.polyfit(t, o, 1)
        if abs(slope) < DRIFT_MIN:
            slope, intercept = 0.0, float(np.median(o))
    else:
        slope, intercept = 0.0, float(np.median(o))
    resid = float(np.max(np.abs(o - (slope * t + intercept))))
    out.update({"slope": float(slope), "intercept": float(intercept), "resid_ms": round(resid, 1)})
    return out


def is_good(f, need):
    return f["confident"] >= need and f["resid_ms"] is not None and f["resid_ms"] <= RESID_MAX_MS


def solve(ref_env, tgt_env, n_windows, window_len_s, max_offset_s, min_z):
    """Try ratio candidates; return (ratio, windows, fit, tried)."""
    need = min(3, n_windows)
    tried = []

    def attempt(r):
        w = analyze(ref_env, resample_env(tgt_env, r), n_windows, window_len_s, max_offset_s)
        f = fit(w, min_z)
        tried.append({"ratio": round(r, 6), "confident": f["confident"], "median_z": round(f["median_z"], 1)})
        return w, f

    def refine(r, w, f):
        # residual slope after resampling by r -> total ratio = r * (1 + slope)
        if f["slope"] != 0.0:
            r2 = snap_ratio(r * (1.0 + f["slope"]))
            if r2 != r:
                w2, f2 = attempt(r2)
                if is_good(f2, need) and f2["median_z"] >= f["median_z"] * 0.8:
                    return r2, w2, f2
        return r, w, f

    # 1. plain
    w, f = attempt(1.0)
    if is_good(f, need):
        return refine(1.0, w, f) + (tried,)

    # 2. known PAL/NTSC ratios
    best = None
    for r in KNOWN_RATIOS:
        w, f = attempt(r)
        if is_good(f, need) and (best is None or f["median_z"] > best[2]["median_z"]):
            best = (r, w, f)
    if best is not None:
        return refine(*best) + (tried,)

    # 3. coarse grid for unusual ratios
    for r in np.arange(0.96, 1.0401, 0.001):
        r = float(round(r, 4))
        w, f = attempt(r)
        if is_good(f, need) and (best is None or f["median_z"] > best[2]["median_z"]):
            best = (r, w, f)
    if best is not None:
        return refine(*best) + (tried,)

    return None, None, None, tried


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ffmpeg", required=True)
    ap.add_argument("--ref", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--ref-stream", default="0:a:0")
    ap.add_argument("--target-stream", default="0:a:0")
    ap.add_argument("--windows", type=int, default=7)
    ap.add_argument("--window-len", type=float, default=60.0)
    ap.add_argument("--max-offset", type=float, default=600.0)
    ap.add_argument("--min-z", type=float, default=6.0)
    ap.add_argument("--sync-tol", type=int, default=40)
    args = ap.parse_args()

    try:
        ref = decode(args.ffmpeg, args.ref, args.ref_stream)
        tgt = decode(args.ffmpeg, args.target, args.target_stream)
    except Exception as ex:
        print(json.dumps({"error": str(ex)}))
        return 2

    ref_dur = len(ref) / RATE
    tgt_dur = len(tgt) / RATE
    ref_env = envelope(ref)
    tgt_env = envelope(tgt)
    del ref, tgt

    ratio, windows, f, tried = solve(ref_env, tgt_env, args.windows, args.window_len,
                                     args.max_offset, args.min_z)
    result = {"ref_duration": round(ref_dur, 3), "target_duration": round(tgt_dur, 3),
              "tried": tried}
    if ratio is None:
        result.update({"verdict": "nomatch", "ratio": None, "offset_ms": None, "windows": []})
    else:
        offset = int(round(f["intercept"]))
        # The residual slope inside the chosen candidate is drift too. refine() only
        # accepts a corrected ratio when it passes is_good(), and a single junk window
        # with z >= min_z wrecks resid_ms, so the correction gets dropped. The verdict
        # used to look at `ratio` alone and reported "constant offset" for a track whose
        # measured offset walked from 0.4 s to 6.3 s across the film - caught on Double
        # Impact, a 23.976 fps track over 24.000 fps video.
        total = snap_ratio(ratio * (1.0 + f["slope"]))
        if abs(total - 1.0) > DRIFT_MIN:
            verdict = "drift"
            ratio = total
        elif abs(offset) > args.sync_tol:
            verdict = "offset"
        else:
            verdict = "sync"
        # offset_ms is measured in reference time after stretching the target by `ratio`,
        # i.e. it is the delay still present once the target has been sped up by `ratio`.
        result.update({"verdict": verdict, "ratio": ratio, "offset_ms": offset,
                       "confident": f["confident"], "resid_ms": f["resid_ms"],
                       "median_z": round(f["median_z"], 1), "windows": windows})
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
