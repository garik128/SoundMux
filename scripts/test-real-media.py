"""Exercise the real pipeline on supplied films; never write to the media folder.

Large generated MKVs are removed after verification. Reports, raw analysis and
small previews remain under --output. Run from the project root using its Python.
"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np
from backend.media import Pipeline, MediaError


def signature(path):
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for offset in [0, max(0, stat.st_size // 2 - 32768), max(0, stat.st_size - 65536)]:
            stream.seek(offset)
            digest.update(stream.read(65536))
    return {'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'sample_sha256': digest.hexdigest()}


class Suite:
    def __init__(self, media, output, build, resume=False):
        self.media, self.output, self.build = media, output, build
        output.mkdir(parents=True, exist_ok=resume)
        config = json.loads((ROOT / '.local/tools.json').read_text(encoding='utf-8'))
        config.update(cache=str(output / 'cache'), work=str(output / 'work'), analyzer=str(ROOT / 'backend/vendor/audio_sync.py'))
        self.case = 'inventory'
        self.last_stage = None
        self.pipeline = Pipeline(config, self.event)
        self.report = json.loads((output / 'report.json').read_text(encoding='utf-8')) if resume else {'started': time.strftime('%Y-%m-%d %H:%M:%S'), 'output': str(output), 'inventory': [], 'cases': []}
        self.files = {}
        self.raw_analyzer = self.pipeline.analyzer
        self.pipeline.analyzer = self.analyzer

    def save(self):
        (self.output / 'report.json').write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding='utf-8')

    def event(self, event):
        with (self.output / 'events.jsonl').open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'case': self.case, **event}, ensure_ascii=False) + '\n')
        stage = (event.get('label'), int(event.get('percent') or 0) // 10)
        if stage != self.last_stage:
            print(json.dumps({'case': self.case, **event}, ensure_ascii=True), flush=True)
            self.last_stage = stage

    def analyzer(self, *args):
        raw = self.raw_analyzer(*args)
        self.current.setdefault('raw_analysis', []).append(raw)
        self.save()
        return raw

    def inventory(self):
        if self.report['inventory']:
            for record in self.report['inventory']:
                path = Path(record['source']['nativePath'])
                if signature(path) != record['signature_before']:
                    raise RuntimeError(f'Source changed since previous run: {path}')
                self.files[path.name] = record
            return
        for path in sorted(self.media.iterdir()):
            if path.suffix.lower() not in ('.mkv', '.ac3', '.eac3', '.flac'):
                continue
            kind = 'movie' if path.suffix.lower() == '.mkv' else 'audio'
            source = self.pipeline.probe(path, kind)
            info = self.pipeline.info(path)
            record = {'source': source, 'signature_before': signature(path), 'video': [s for s in info['streams'] if s['codec_type'] == 'video']}
            self.files[path.name] = record
            self.report['inventory'].append(record)
        self.save()

    def request(self, movie, audio):
        movie = copy.deepcopy(self.files[movie]['source'])
        audio = copy.deepcopy(self.files[audio]['source'])
        return {'movie': movie, 'audio': audio,
                'settings': {'referenceTrack': movie['metadata']['recommendedTrack'], 'externalTrack': audio['metadata']['recommendedTrack'],
                             'language': 'Русский', 'title': 'SoundMux real-media test', 'makeDefault': True, 'keepMovieAudio': True,
                             'keepOriginal': True, 'compatibility': False},
                'adjustment': {'automaticDelayMs': 0, 'manualDelayMs': 0, 'speedCorrection': 1, 'preservePitch': True},
                'preview': {'audioVariant': 'corrected', 'playbackPosition': 120}, 'outputDirectory': str(self.output),
                'outputName': self.case + '.mkv', 'allowUnverified': False}

    def compare_audio(self, final, clip, position, final_stream):
        def decode(args):
            raw = self.pipeline.ffmpeg([*args, '-t', '5', '-ac', '1', '-ar', '8000', '-f', 's16le', '-'])
            return np.frombuffer(raw, '<i2').astype(np.float64)
        a = decode(['-ss', str(position), '-i', final, '-map', f'0:{final_stream}'])
        b = decode(['-i', clip, '-map', '0:a:0'])
        size = min(len(a), len(b))
        if size < 12000:
            raise AssertionError('Decoded preview is too short')
        # AAC can add small coding differences; compare a three-second interior.
        end = min(size - 800, 32000)
        x = a[800:end]
        scores = []
        for shift in range(-400, 401):
            y = b[800 + shift:end + shift]
            norm = np.linalg.norm(x) * np.linalg.norm(y)
            scores.append(float(np.dot(x, y) / norm) if norm else 0)
        lag = int(np.argmax(scores)) - 400
        result = {'lag_ms': lag / 8, 'correlation': max(scores), 'position': position}
        if result['correlation'] > .3 and abs(result['lag_ms']) > 40:
            raise AssertionError(f'Preview and saved audio differ in timing: {result}')
        result['timing_confirmed'] = result['correlation'] > .3
        return result

    def run_case(self, name, movie, audio, negative=False, compatibility=False):
        self.case = name
        started = time.monotonic()
        self.current = {'name': name, 'negative_control': negative, 'compatibility': compatibility}
        self.report['cases'].append(self.current)
        self.save()
        output = self.output / (name + '.mkv')
        try:
            request = self.request(movie, audio)
            request['settings']['compatibility'] = compatibility
            self.current['request'] = request
            result = self.pipeline.analyze(request)
            self.current['analysis'] = result
            self.save()
            print(json.dumps({'case': name, 'analysis': result}, ensure_ascii=True), flush=True)
            if negative:
                if result['verdict'] in ('aligned', 'offset', 'drift'):
                    raise AssertionError('Unrelated film/audio incorrectly accepted')
                try:
                    self.pipeline.build(request)
                except MediaError as error:
                    self.current['build_blocked'] = str(error)
                else:
                    raise AssertionError('Unverified negative control was saved')
                self.current['status'] = 'passed'
                return
            request['analysis'] = result
            request['adjustment'].update(automaticDelayMs=result['proposedDelayMs'], speedCorrection=result['speedCorrection'])
            if result['verdict'] not in ('aligned', 'offset', 'drift'):
                self.current['status'] = 'needs-listening'
                return
            duration = request['movie']['metadata']['duration']
            clips = []
            for fraction in [.1, .5, .9]:
                request['preview'].update(audioVariant='corrected', playbackPosition=round(duration * fraction, 3))
                clip = self.pipeline.preview(request)
                if self.pipeline.preview(request) != clip:
                    raise AssertionError('Cached preview metadata differs')
                clips.append(clip)
            self.current['corrected_previews'] = clips
            request['preview']['playbackPosition'] = round(duration * .5, 3)
            self.current['comparison_previews'] = []
            for variant in ['film', 'original']:
                request['preview']['audioVariant'] = variant
                self.current['comparison_previews'].append(self.pipeline.preview(request))
            self.save()
            if self.build:
                built = self.pipeline.build(request)
                self.current['build'] = built
                info = self.pipeline.info(output)
                self.current['output_audio_tracks'] = self.pipeline.probe(output, 'movie')['metadata']['tracks']
                original_video = self.files[movie]['video']
                actual_video = [s for s in info['streams'] if s['codec_type'] == 'video']
                keys = ['codec_name', 'width', 'height', 'pix_fmt', 'color_space', 'color_transfer', 'color_primaries']
                if [[s.get(k) for k in keys] for s in original_video] != [[s.get(k) for k in keys] for s in actual_video]:
                    raise AssertionError('Video properties changed during mux')
                self.current['video_properties_preserved'] = True
                stream = self.pipeline.audio_streams(info)[0]['index']
                self.current['audio_comparisons'] = [self.compare_audio(str(output), c['path'], c['start'], stream) for c in clips]
                self.current['output_size'] = output.stat().st_size
            self.current['status'] = 'passed'
        except Exception as error:
            self.current.update(status='failed', error=str(error), traceback=traceback.format_exc())
            print(json.dumps({'case': name, 'error': str(error)}, ensure_ascii=True), flush=True)
        finally:
            if output.exists():
                output.unlink()
                self.current['generated_mkv_removed'] = True
            self.current['seconds'] = round(time.monotonic() - started, 2)
            self.save()

    def run(self):
        self.inventory()
        code = 'Code.3.2025.2160p.WEB-DL.DDP5.1.SDR-DVT.mkv'
        onslaught = 'Onslaught.2026.2160p.iT.WEB-DL.DDP5.1.DV.HDR.H.265.mkv'
        secret = 'The.Secret.Of.My.Success.1987.mkv'
        cases = [
            ('code3', code, 'Code.3.2025.kyberpunk_640kbps.ac3', {}),
            ('onslaught-short', onslaught, 'Onslaught.2026.kyberpunk_768kbps (1-32-07).eac3', {}),
            ('onslaught-long', onslaught, 'Onslaught.2026.kyberpunk_768kbps.eac3', {}),
            ('secret-flac', secret, 'The.Secret.Of.My.Success.1987.Kotov.flac', {}),
            ('secret-compatibility', secret, 'The.Secret.Of.My.Success.1987.Kotov.flac', {'compatibility': True}),
            ('wrong-film', code, 'The.Secret.Of.My.Success.1987.Kotov.flac', {'negative': True}),
        ]
        for name, movie, audio, options in cases:
            previous = next((c for c in self.report['cases'] if c['name'] == name), None)
            if previous and previous.get('status') in ('passed', 'needs-listening'):
                continue
            if previous:
                self.report.setdefault('previous_attempts', []).append(previous)
                self.report['cases'].remove(previous)
            self.run_case(name, movie, audio, **options)
        for record in self.report['inventory']:
            path = Path(record['source']['nativePath'])
            record['signature_after'] = signature(path)
            record['unchanged'] = record['signature_before'] == record['signature_after']
        self.report['finished'] = time.strftime('%Y-%m-%d %H:%M:%S')
        self.save()
        print(json.dumps({'report': str(self.output / 'report.json'), 'cases': [{k: c.get(k) for k in ['name', 'status', 'seconds', 'error']} for c in self.report['cases']]}, ensure_ascii=True), flush=True)
        return 1 if any(c['status'] == 'failed' for c in self.report['cases']) or not all(r['unchanged'] for r in self.report['inventory']) else 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--media-dir', type=Path, default=ROOT / 'tests')
    parser.add_argument('--output', type=Path, default=ROOT / '.local' / 'real-media' / time.strftime('%Y%m%d-%H%M%S'))
    parser.add_argument('--analysis-only', action='store_true', help='Skip full MKV builds; still prepare preview clips')
    parser.add_argument('--resume', action='store_true', help='Reuse an existing --output after ensuring its previous process has exited')
    args = parser.parse_args()
    sys.exit(Suite(args.media_dir.resolve(), args.output.resolve(), not args.analysis_only, args.resume).run())
