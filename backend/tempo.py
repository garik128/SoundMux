"""Parallel speed correction of long audio.

Rubber Band is single-threaded (about 8x real time), so a feature-length track takes
minutes. The track is cut into independent chunks that are stretched concurrently.
Every chunk is rendered with extra warm-up audio on both sides, trimmed back, and
neighbouring chunks overlap by CROSSFADE seconds so that the seams are cross-faded
instead of cut. All times below are on the stretched (output) timeline unless noted.
"""
from __future__ import annotations

import ctypes
import math
import os
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MIN_PARALLEL_SECONDS = 600
CHUNK_SECONDS = 240
MARGIN = 3.0  # input seconds rendered before/after a chunk and discarded afterwards
CROSSFADE = 0.5
MAX_WORKERS = 8
LENGTH_TOLERANCE = 0.25  # seconds a rendered chunk or the joined file may differ from the plan
BELOW_NORMAL_PRIORITY =0x00004000 if os.name == 'nt' else 0
CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0
# Decoder option placed before every `-i`. Dolby Digital would otherwise apply the dynamic range compression
# that the stream asks for, and re-encoding would bake it into the new track. Other codecs ignore it.
DECODE = ('-drc_scale', '0')


def available_cpus():
    """CPUs this process may use (honours the Windows affinity mask)."""
    if os.name == 'nt':
        kernel = ctypes.windll.kernel32
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        process, system = ctypes.c_size_t(), ctypes.c_size_t()
        if kernel.GetProcessAffinityMask(ctypes.c_void_p(kernel.GetCurrentProcess()), ctypes.byref(process), ctypes.byref(system)) and process.value:
            return bin(process.value).count('1')
    return os.cpu_count() or 1


_load = 'fast'


def configure(load):
    """Set the CPU load mode chosen in the settings: fast, moderate or background."""
    global _load
    _load = load if load in ('fast', 'moderate', 'background') else 'fast'


def worker_count():
    cpus = available_cpus()
    if _load == 'background':
        limit = 1 if cpus <= 4 else 2
    elif _load == 'moderate':
        limit = cpus // 2
    else:
        limit = cpus
    return max(1, min(limit, MAX_WORKERS))


def will_parallelize(duration, speed):
    return speed != 1 and duration >= MIN_PARALLEL_SECONDS and worker_count() >= 2


def plan(duration, speed):
    """Chunks as dicts: input range to decode and the kept range of its output."""
    count = max(2, round(duration / CHUNK_SECONDS))
    edges = [duration * k / count for k in range(count + 1)]
    chunks = []
    for k in range(count):
        start = max(0.0, edges[k] - MARGIN)
        end = min(duration, edges[k + 1] + MARGIN)
        origin = start / speed
        keep_from = 0.0 if k == 0 else edges[k] / speed - CROSSFADE / 2 - origin
        keep_to = None if k == count - 1 else edges[k + 1] / speed + CROSSFADE / 2 - origin
        chunks.append({'index': k, 'start': start, 'end': end, 'keep_from': keep_from, 'keep_to': keep_to,
                       'kept': (keep_to if keep_to is not None else (end - start) / speed) - keep_from})
    return chunks


class Progress:
    """Combines chunk progress into one percentage plus a remaining-time estimate."""

    def __init__(self, chunks, emit, label, estimate):
        self.expected = [c['kept'] for c in chunks]
        self.done = [0.0] * len(chunks)
        self.emit, self.label, self.estimate = emit, label, estimate
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.last = -1

    def update(self, index, seconds):
        with self.lock:
            self.done[index] = min(seconds, self.expected[index])
            fraction = sum(self.done) / sum(self.expected)
            percent = min(99, int(fraction * 100))
            if percent == self.last:
                return
            self.last = percent
            elapsed = time.monotonic() - self.started
            if fraction >= .03 and elapsed >= 5:
                eta = elapsed * (1 - fraction) / fraction
            else:
                eta = self.estimate * (1 - fraction)
            self.emit(self.label, percent, round(eta))


def _estimate(ffmpeg, source, stream, speed, duration, workers, count, error):
    """Time a short sample on this machine to predict the whole parallel render."""
    sample = min(10.0, duration / 4)
    started = time.monotonic()
    process = subprocess.run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-ss', f'{duration / 2:.3f}', '-t', f'{sample:.3f}',
                              *DECODE, '-i', str(source), '-map', f'0:{stream}', '-vn', '-sn', '-dn', '-af', f'rubberband=tempo={speed:.10f}', '-f', 'null', '-'],
                             stdin=subprocess.DEVNULL, capture_output=True, creationflags=CREATE_NO_WINDOW)
    if process.returncode != 0:
        raise error('Не удалось обработать файл. Проверьте, что он полностью скачан и открывается в плеере.', process.stderr.decode('utf-8', 'replace')[-6000:])
    per_second = (time.monotonic() - started) / sample
    waves = math.ceil(count / workers)
    return per_second * (duration / count + 2 * MARGIN) * waves * 1.1 + 5


def render(ffmpeg, source, stream, speed, duration, output, encode_args, work, emit, error, tail=''):
    """Stretch `stream` of `source` by `speed` into `output` using all useful CPUs.

    `encode_args` are the FFmpeg output codec arguments of the final file; chunks are
    kept lossless (FLAC) until the final cross-fading pass. `tail` is an optional filter chain (sample rate
    conversion, gain) that is applied to the joined sound right before it is encoded.
    """
    workers = worker_count()
    chunks = plan(duration, speed)
    label = 'Подготовка звука'
    estimate = _estimate(ffmpeg, source, stream, speed, duration, workers, len(chunks), error)
    emit(label, 0, round(estimate))
    progress = Progress(chunks, emit, label, estimate)
    common = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-progress', 'pipe:1', '-nostats']
    running, stop = set(), threading.Event()
    guard = threading.Lock()
    folder = Path(tempfile.mkdtemp(prefix='chunks-', dir=work))

    def run_chunk(chunk):
        path = folder / f"{chunk['index']:04d}.flac"
        trim = f"atrim=start={chunk['keep_from']:.6f}" + (f":end={chunk['keep_to']:.6f}" if chunk['keep_to'] is not None else '')
        filters = f'rubberband=tempo={speed:.10f},{trim},asetpts=PTS-STARTPTS'
        args = [*common, '-ss', f"{chunk['start']:.6f}", '-t', f"{chunk['end'] - chunk['start']:.6f}", *DECODE, '-i', str(source),
                '-map', f'0:{stream}', '-vn', '-sn', '-dn', '-af', filters, '-c:a', 'flac', '-n', str(path)]
        errors = tempfile.TemporaryFile(dir=work)
        with guard:
            if stop.is_set():
                errors.close()
                return None
            process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors,
                                       creationflags=CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY)
            running.add(process)
        try:
            rendered = 0.0
            for raw in process.stdout:
                line = raw.decode('utf-8', 'replace').strip()
                if line.startswith('out_time_us='):
                    try:
                        rendered = int(line.split('=', 1)[1]) / 1_000_000
                        progress.update(chunk['index'], rendered)
                    except ValueError:
                        pass
            code = process.wait()
            if code != 0 and not stop.is_set():
                errors.seek(0)
                raise error('Не удалось обработать файл. Проверьте свободное место и попробуйте ещё раз.', errors.read().decode('utf-8', 'replace')[-6000:])
            if not stop.is_set() and abs(rendered - chunk['kept']) > LENGTH_TOLERANCE:
                raise error('Не удалось подготовить звук: часть дорожки получилась неполной. Исходные файлы сохранены.',
                            f"chunk {chunk['index']}: rendered {rendered:.3f}s, expected {chunk['kept']:.3f}s")
            progress.update(chunk['index'], chunk['kept'])
            return path
        finally:
            process.stdout.close()
            errors.close()
            with guard:
                running.discard(process)

    def cancel_all():
        stop.set()
        with guard:
            for process in list(running):
                process.kill()

    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(run_chunk, chunk) for chunk in chunks]
            try:
                paths = [future.result() for future in futures]
            except BaseException:
                cancel_all()
                raise
        _join(ffmpeg, paths, output, encode_args, duration / speed, emit, error, tail)
    except BaseException:
        Path(output).unlink(missing_ok=True)
        raise
    finally:
        cancel_all()
        for leftover in folder.glob('*'):
            leftover.unlink(missing_ok=True)
        folder.rmdir()


def _join(ffmpeg, paths, output, encode_args, expected, emit, error, tail=''):
    label = 'Склейка звука'
    args = [str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-nostdin', '-progress', 'pipe:1', '-nostats']
    for path in paths:
        args += ['-i', str(path)]
    graph, previous = [], '0:a'
    for index in range(1, len(paths)):
        name = f'x{index}'
        graph.append(f'[{previous}][{index}:a]acrossfade=d={CROSSFADE}:c1=tri:c2=tri[{name}]')
        previous = name
    if tail:
        graph.append(f'[{previous}]{tail}[tail]')
        previous = 'tail'
    args += ['-filter_complex', ';'.join(graph), '-map', f'[{previous}]', *encode_args, '-n', str(output)]
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=errors,
                                   creationflags=CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY)
        last, joined = -1, 0.0
        for raw in process.stdout:
            line = raw.decode('utf-8', 'replace').strip()
            if line.startswith('out_time_us='):
                try:
                    joined = int(line.split('=', 1)[1]) / 1_000_000
                except ValueError:
                    continue
                percent = min(99, int(joined / expected * 100))
                if percent != last:
                    emit(label, percent, None)
                    last = percent
        code = process.wait()
        process.stdout.close()
        if code != 0:
            errors.seek(0)
            raise error('Не удалось обработать файл. Проверьте свободное место и попробуйте ещё раз.', errors.read().decode('utf-8', 'replace')[-6000:])
    if abs(joined - expected) > LENGTH_TOLERANCE:
        raise error('Не удалось подготовить звук: итоговая длина не совпала с ожидаемой. Исходные файлы сохранены.',
                    f'joined {joined:.3f}s, expected {expected:.3f}s')
    emit(label, 100, None)
