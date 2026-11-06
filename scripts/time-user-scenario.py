"""Time a typical user flow (analyze, one preview, build) per stage on real films.

Reads sources only. Each run uses a fresh cache so nothing is reused. The built
MKV is removed after timing. Optional --cores N limits CPU affinity of this
process and its FFmpeg/mkvmerge children (emulates a weaker machine; it does not
slow the disk). Run from the project root using its Python.
"""
import argparse
import ctypes
import json
from pathlib import Path
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.media import Pipeline

SCENARIOS = {
    'fast': ('Onslaught.2026.2160p.iT.WEB-DL.DDP5.1.DV.HDR.H.265.mkv', 'Onslaught.2026.kyberpunk_768kbps (1-32-07).eac3'),
    'offset': ('Onslaught.2026.2160p.iT.WEB-DL.DDP5.1.DV.HDR.H.265.mkv', 'Onslaught.2026.kyberpunk_768kbps.eac3'),
    'drift': ('Code.3.2025.2160p.WEB-DL.DDP5.1.SDR-DVT.mkv', 'Code.3.2025.kyberpunk_640kbps.ac3'),
}


def limit_cores(count):
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    kernel.SetProcessAffinityMask.argtypes = [ctypes.c_void_p, ctypes.c_size_t]
    if not kernel.SetProcessAffinityMask(kernel.GetCurrentProcess(), (1 << count) - 1):
        raise OSError('SetProcessAffinityMask failed')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('scenario', choices=SCENARIOS)
    parser.add_argument('--cores', type=int, default=0)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if args.cores:
        limit_cores(args.cores)
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    config = json.loads((ROOT / '.local/tools.json').read_text(encoding='utf-8'))
    config.update(cache=str(output / 'cache'), work=str(output / 'work'), analyzer=str(ROOT / 'backend/vendor/audio_sync.py'))

    stages, clock = [], {'label': 'start', 'at': time.monotonic()}

    def event(item):
        label = item.get('label') or clock['label']
        if label != clock['label']:
            now = time.monotonic()
            stages.append({'label': clock['label'], 'seconds': round(now - clock['at'], 1)})
            clock.update(label=label, at=now)

    pipeline = Pipeline(config, event)
    media = ROOT / 'tests'
    movie_name, audio_name = SCENARIOS[args.scenario]
    movie = pipeline.probe(media / movie_name, 'movie')
    audio = pipeline.probe(media / audio_name, 'audio')
    request = {'movie': movie, 'audio': audio,
               'settings': {'referenceTrack': movie['metadata']['recommendedTrack'], 'externalTrack': audio['metadata']['recommendedTrack'],
                            'language': 'Русский', 'title': 'SoundMux timing', 'makeDefault': True, 'keepMovieAudio': True,
                            'keepOriginal': True, 'compatibility': False},
               'adjustment': {'automaticDelayMs': 0, 'manualDelayMs': 0, 'speedCorrection': 1, 'preservePitch': True},
               'preview': {'audioVariant': 'corrected', 'playbackPosition': round(movie['metadata']['duration'] * .5, 3)},
               'outputDirectory': str(output), 'outputName': 'timing.mkv', 'allowUnverified': False}
    totals = {}

    def timed(name, call):
        started = time.monotonic()
        clock.update(label=name, at=started)
        result = call()
        now = time.monotonic()
        stages.append({'label': clock['label'], 'seconds': round(now - clock['at'], 1)})
        totals[name] = round(now - started, 1)
        stages.append({'label': f'== {name} total', 'seconds': totals[name]})
        return result

    analysis = timed('analyze', lambda: pipeline.analyze(request))
    request['analysis'] = analysis
    request['adjustment'].update(automaticDelayMs=analysis['proposedDelayMs'], speedCorrection=analysis['speedCorrection'])
    timed('preview', lambda: pipeline.preview(request))
    built = timed('build', lambda: pipeline.build(request))
    result = {'scenario': args.scenario, 'cores': args.cores or 'all', 'verdict': analysis['verdict'],
              'proposedDelayMs': analysis['proposedDelayMs'], 'speedCorrection': analysis['speedCorrection'],
              'totals': totals, 'total_seconds': round(sum(totals.values()), 1), 'stages': stages,
              'output_size': Path(built['path']).stat().st_size}
    Path(built['path']).unlink()
    shutil.rmtree(output / 'cache', ignore_errors=True)
    (output / 'timing.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))


main()
