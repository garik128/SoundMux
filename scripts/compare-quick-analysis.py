"""Compare the quick (windowed) analysis with the full analyzer on real film/dub pairs.

Reads sources only. Usage: compare-quick-analysis.py <output.json>
Run from the project root using its Python. Slow (the full analyzer reads whole films).
"""
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.media import Pipeline

PAIRS = [
    ('onslaught-short', 'Onslaught.2026.2160p.iT.WEB-DL.DDP5.1.DV.HDR.H.265.mkv', 'Onslaught.2026.kyberpunk_768kbps (1-32-07).eac3'),
    ('onslaught-long', 'Onslaught.2026.2160p.iT.WEB-DL.DDP5.1.DV.HDR.H.265.mkv', 'Onslaught.2026.kyberpunk_768kbps.eac3'),
    ('code3', 'Code.3.2025.2160p.WEB-DL.DDP5.1.SDR-DVT.mkv', 'Code.3.2025.kyberpunk_640kbps.ac3'),
    ('secret', 'The.Secret.Of.My.Success.1987.mkv', 'The.Secret.Of.My.Success.1987.Kotov.flac'),
    ('wrong-film', 'Code.3.2025.2160p.WEB-DL.DDP5.1.SDR-DVT.mkv', 'The.Secret.Of.My.Success.1987.Kotov.flac'),
]


def main(output):
    config = json.loads((ROOT / '.local/tools.json').read_text(encoding='utf-8'))
    work = Path(output).with_suffix('.work')
    config.update(cache=str(work / 'cache'), work=str(work / 'work'), analyzer=str(ROOT / 'backend/vendor/audio_sync.py'))
    pipeline = Pipeline(config)
    rows = []
    for name, movie_name, audio_name in PAIRS:
        movie = pipeline.probe(ROOT / 'tests' / movie_name, 'movie')
        audio = pipeline.probe(ROOT / 'tests' / audio_name, 'audio')
        ref_stream, target_stream = movie['metadata']['recommendedTrack'], audio['metadata']['recommendedTrack']
        duration = movie['metadata']['duration']
        window, max_offset = max(1, min(60, duration / 7)), min(600, max(5, duration / 3))
        started = time.monotonic()
        quick = pipeline.quick_analyzer(movie['nativePath'], audio['nativePath'], ref_stream, target_stream, window, max_offset)
        quick_seconds = time.monotonic() - started
        started = time.monotonic()
        full = json.loads(pipeline.run('python', ['-I', config['analyzer'], '--ffmpeg', config['ffmpeg'], '--ref', movie['nativePath'], '--target', audio['nativePath'],
                                                  '--ref-stream', f'0:{ref_stream}', '--target-stream', f'0:{target_stream}', '--windows', '7',
                                                  '--window-len', str(window), '--max-offset', str(max_offset)]))
        full_seconds = time.monotonic() - started
        row = {'pair': name, 'quick_seconds': round(quick_seconds, 1), 'full_seconds': round(full_seconds, 1), 'quick': quick, 'full': full}
        if quick and full.get('windows'):
            row['window_offset_diff_ms'] = [q['offset_ms'] - f['offset_ms'] for q, f in zip(quick['windows'], full['windows'])]
        rows.append(row)
        print(json.dumps({k: row[k] for k in ('pair', 'quick_seconds', 'full_seconds')} |
                         {'quick': {k: (quick or {}).get(k) for k in ('verdict', 'ratio', 'offset_ms', 'median_z', 'resid_ms')},
                          'full': {k: full.get(k) for k in ('verdict', 'ratio', 'offset_ms', 'median_z', 'resid_ms')},
                          'window_diff': row.get('window_offset_diff_ms')}, ensure_ascii=False), flush=True)
        Path(output).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')


main(sys.argv[1])
