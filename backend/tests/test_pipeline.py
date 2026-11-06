"""Integration checks use small synthetic files only, never the user's movies."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

import numpy as np
from backend import applog, quick_analysis, tempo
from backend.media import Pipeline, MediaError, duration_shortfall, interpret_analysis, launch_failure_message, resolve_pitch, standard_ratio, CLIP_DB

ROOT = Path(__file__).resolve().parents[2]


def write_wave(path, samples, rate=8000):
    with wave.open(str(path), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes((np.clip(samples, -1, 1) * 32767).astype('<i2').tobytes())


class InterpretationTests(unittest.TestCase):
    def test_false_drift_is_rejected(self):
        result = interpret_analysis({'verdict': 'drift', 'ratio': .993, 'offset_ms': 0, 'ref_duration': 5400, 'target_duration': 5400.032})
        self.assertEqual(result['verdict'], 'uncertain')
        self.assertEqual(result['speedCorrection'], 1)

    def test_drift_with_different_intro_is_not_rejected(self):
        result = interpret_analysis({'verdict': 'drift', 'ratio': 1.001, 'offset_ms': -27800, 'ref_duration': 7000, 'target_duration': 6979.2})
        self.assertEqual(result['verdict'], 'drift')
        self.assertEqual(result['proposedDelayMs'], 27800)

    def test_inconsistent_residual_is_not_auto_corrected(self):
        result = interpret_analysis({'verdict': 'offset', 'ratio': 1, 'offset_ms': 1400, 'resid_ms': 200})
        self.assertEqual(result['verdict'], 'different-edit')
        self.assertEqual(result['proposedDelayMs'], 0)


class DurationCheckTests(unittest.TestCase):
    def test_small_gap_keeps_the_finished_result_with_a_warning(self):
        self.assertIsNone(duration_shortfall(5989, 5990, 5990))
        self.assertEqual(duration_shortfall(5970, 5990, 5990), 'warn')
        self.assertEqual(duration_shortfall(5800, 5990, 5990), 'fail')

    def test_longer_result_is_fine(self):
        self.assertIsNone(duration_shortfall(6100, 5990, 5990))


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory(prefix='синтетика [проверка] ', dir=ROOT / '.local')
        cls.path = Path(cls.folder.name)
        config = json.loads((ROOT / '.local/tools.json').read_text(encoding='utf-8-sig'))
        config.update(cache=str(cls.path / 'cache'), analyzer=str(ROOT / 'backend/vendor/audio_sync.py'))
        if os.environ.get('SOUNDMUX_TEST_MKVMERGE'):  # run the whole suite against a different mkvmerge (for example the older bundled one)
            config['mkvmerge'] = os.environ['SOUNDMUX_TEST_MKVMERGE']
        cls.pipeline = Pipeline(config)
        cls.duration = 96
        rng = np.random.default_rng(422)
        count = cls.duration * 8000
        env = np.zeros(count, np.float32)
        cursor = 0
        while cursor < count:
            length = int(rng.uniform(.06, .7) * 8000)
            env[cursor:cursor+length] = rng.uniform(.1, .8)
            cursor += length + int(rng.uniform(.1, 1) * 8000)
        cls.reference = rng.standard_normal(count).astype(np.float32) * env * .35
        write_wave(cls.path / 'оригинал.wav', cls.reference)
        write_wave(cls.path / 'комментарий.wav', rng.standard_normal(count).astype(np.float32) * .03)
        cls.pipeline.ffmpeg(['-f', 'lavfi', '-i', 'testsrc2=size=192x108:rate=12', '-i', cls.path / 'оригинал.wav', '-i', cls.path / 'комментарий.wav',
                             '-map', '0:v', '-map', '2:a', '-map', '1:a', '-t', str(cls.duration), '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'flac',
                             '-metadata:s:a:0', 'title=Комментарий режиссёра', '-metadata:s:a:0', 'language=eng', '-disposition:a:0', 'default',
                             '-metadata:s:a:1', 'title=Original sound', '-metadata:s:a:1', 'language=eng', '-disposition:a:1', 'original', '-n', cls.path / 'base.mkv'])
        (cls.path / 'subtitle.srt').write_text('1\n00:00:01,000 --> 00:00:03,000\nПроверка субтитров\n', encoding='utf-8')
        (cls.path / 'chapters.txt').write_text('CHAPTER01=00:00:00.000\nCHAPTER01NAME=Начало\nCHAPTER02=00:00:40.000\nCHAPTER02NAME=Середина\n', encoding='utf-8')
        (cls.path / 'attachment.txt').write_text('Attachment must survive', encoding='utf-8')
        cls.movie_path = cls.path / 'Фильм [2026].mkv'
        default_flag = cls.pipeline.default_flag_option()  # the option names differ between MKVToolNix versions
        forced_flag = '--forced-display-flag' if default_flag == '--default-track-flag' else '--forced-track'
        cls.pipeline.run('mkvmerge', ['-o', cls.movie_path, cls.path / 'base.mkv', default_flag, '0:yes', forced_flag, '0:yes', cls.path / 'subtitle.srt',
                                    '--chapters', cls.path / 'chapters.txt', '--attachment-mime-type', 'text/plain', '--attach-file', cls.path / 'attachment.txt'], accepted=(0, 1))
        cls.movie = cls.pipeline.probe(cls.movie_path, 'movie')
        cls.source_hash = hashlib.sha256(cls.movie_path.read_bytes()).hexdigest()

    @classmethod
    def tearDownClass(cls):
        assert hashlib.sha256(cls.movie_path.read_bytes()).hexdigest() == cls.source_hash, 'Movie source changed'
        cls.folder.cleanup()

    def request(self, name, samples, ac3=False):
        target = self.path / f'{name}.wav'
        write_wave(target, samples)
        if ac3:
            converted = target.with_suffix('.ac3')
            self.pipeline.ffmpeg(['-i', target, '-c:a', 'ac3', '-ar', '48000', '-b:a', '192k', '-n', converted])
            target = converted
        audio = self.pipeline.probe(target, 'audio')
        return {'movie': self.movie, 'audio': audio, 'scenario': 'offset',
                'settings': {'referenceTrack': self.movie['metadata']['recommendedTrack'], 'externalTrack': audio['metadata']['recommendedTrack'], 'language': 'Русский', 'title': 'Новая озвучка',
                             'makeDefault': True, 'keepMovieAudio': True, 'keepOriginal': True, 'compatibility': False},
                'adjustment': {'automaticDelayMs': 0, 'manualDelayMs': 0, 'speedCorrection': 1, 'preservePitch': True},
                'preview': {'playbackPosition': 10, 'audioVariant': 'corrected'}, 'outputName': f'{name}.NEW.mkv', 'allowUnverified': False}

    def test_01_probe_selects_original_over_commentary(self):
        recommended = self.movie['metadata']['recommendedTrack']
        track = next(t for t in self.movie['metadata']['tracks'] if t['id'] == recommended)
        self.assertEqual(track['name'], 'Original sound')
        self.assertEqual(self.movie['metadata']['origin'], 'probe')

    def test_preview_variants_report_output_delay_and_reuse_uncorrected_clips(self):
        request = self.request('preview-variants', self.reference)
        request['adjustment']['automaticDelayMs'] = -1000
        for variant in ['film', 'original', 'corrected']:
            with self.subTest(variant=variant):
                request['preview']['audioVariant'] = variant
                fresh = self.pipeline.preview(request)
                self.assertEqual(fresh['effectiveDelayMs'], -1000)
                self.assertEqual(self.pipeline.preview(request), fresh)
                if variant != 'corrected':
                    request['adjustment']['manualDelayMs'] = 50
                    with patch.object(self.pipeline, 'ffmpeg', side_effect=AssertionError('Unchanged clip was encoded again')):
                        cached = self.pipeline.preview(request)
                    self.assertEqual(cached['path'], fresh['path'])
                    self.assertEqual(cached['effectiveDelayMs'], -950)
                    request['adjustment']['manualDelayMs'] = 0

    def test_render_audio_rejects_missing_stream_with_readable_error(self):
        with self.assertRaisesRegex(MediaError, 'дорожка не найдена'):
            self.pipeline.render_audio(self.movie_path, '999', self.path / 'missing.flac', 1, True)

    def test_parallel_stretch_matches_single_pass_without_seam_artifacts(self):
        speed = .998
        track = self.movie['metadata']['recommendedTrack']
        single, chunked = self.path / 'stretch-single.flac', self.path / 'stretch-chunked.flac'
        with patch.object(tempo, 'MIN_PARALLEL_SECONDS', 10 ** 6):
            self.pipeline.render_audio(self.movie_path, track, single, speed, False)
        events = []
        pipeline = Pipeline({**self.pipeline.config}, events.append)
        with patch.object(tempo, 'MIN_PARALLEL_SECONDS', 30), patch.object(tempo, 'CHUNK_SECONDS', 24), patch.object(tempo, 'worker_count', lambda: 2):
            self.assertTrue(tempo.will_parallelize(self.duration, speed))
            pipeline.render_audio(self.movie_path, track, chunked, speed, False)
        self.assertEqual(list(pipeline.work.glob('chunks-*')), [], 'chunk files must be removed')
        info = [pipeline.duration(pipeline.info(path)) for path in (single, chunked)]
        # Compare with the ideal stretched length; a single Rubber Band pass of this short
        # 8 kHz sample ends about 0.1 s early, so it is only a loose reference.
        self.assertAlmostEqual(info[1], self.duration / speed, delta=.05)
        self.assertAlmostEqual(info[0], info[1], delta=.25)
        progress = [e for e in events if e.get('percent') is not None]
        self.assertTrue(any(e.get('etaSeconds') is not None for e in progress), 'remaining time must be reported')
        self.assertEqual([e['percent'] for e in progress if e['label'] == 'Склейка звука'][-1], 100)

        def decode(path):
            raw = self.pipeline.ffmpeg(['-i', path, '-ac', '1', '-ar', '8000', '-f', 'f32le', '-'])
            return np.frombuffer(raw, '<f4').astype(np.float64)
        a, b = decode(chunked), decode(single)
        size = min(len(a), len(b))
        a, b = a[:size], b[:size]
        frame = 80
        energy = lambda x: (x[:size // frame * frame].reshape(-1, frame) ** 2).mean(axis=1)
        ea, eb = energy(a), energy(b)
        self.assertGreater(np.corrcoef(ea, eb)[0, 1], .95)
        self.assertEqual(max(range(-2, 3), key=lambda lag: np.corrcoef(ea[2:-2], np.roll(eb, lag)[2:-2])[0, 1]), 0)
        edges = [self.duration * k / len(tempo.plan(self.duration, speed)) for k in range(1, len(tempo.plan(self.duration, speed)))]
        for edge in edges:
            zone = slice(int((edge / speed - .25) * 8000 / frame), int((edge / speed + .25) * 8000 / frame))
            ratio = 10 * np.log10(ea[zone].sum() / eb[zone].sum())
            self.assertLess(abs(ratio), 1.0, f'level changed at the seam near {edge:.1f}s')

    def test_frame_rate_ratios_are_recognised_and_choose_the_pitch_mode(self):
        pal_to_ntsc = 23.976 / 25
        for speed, label in ((pal_to_ntsc, '25 → 23,976'), (24 / 25, '25 → 24'), (25 / 24, '24 → 25'), (23.976 / 24, '24 → 23,976')):
            exact, name = standard_ratio(speed * 1.0001)
            self.assertAlmostEqual(exact, speed, places=3)
            self.assertEqual(name, label)
        self.assertIsNone(standard_ratio(.998))
        self.assertIsNone(standard_ratio(1))
        self.assertEqual(resolve_pitch('auto', pal_to_ntsc), 'keep', 'the audible 4% PAL conversion needs an explicit choice')
        self.assertEqual(resolve_pitch('auto', 23.976 / 24), 'shift')
        self.assertEqual(resolve_pitch('auto', 24 / 23.976), 'shift')
        self.assertEqual(resolve_pitch('shift', pal_to_ntsc), 'shift')
        self.assertEqual(resolve_pitch('auto', .998), 'keep')
        self.assertEqual(resolve_pitch('shift', .998), 'shift')
        self.assertEqual(resolve_pitch('keep', pal_to_ntsc), 'keep')
        self.assertEqual(resolve_pitch('shift', 1), 'keep')

    def tone(self, name, frequency, seconds=30, amplitude=.5, rate=48000):
        t = np.arange(rate * seconds) / rate
        path = self.path / f'{name}.wav'
        write_wave(path, np.sin(2 * np.pi * frequency * t) * amplitude, rate)
        return self.pipeline.probe(path, 'audio')

    def measured(self, path):
        """(duration, dominant frequency) of a rendered track."""
        raw = self.pipeline.ffmpeg(['-i', path, '-ac', '1', '-ar', '48000', '-f', 'f32le', '-'])
        data = np.frombuffer(raw, '<f4')[48000 * 5:48000 * 15]
        spectrum = np.abs(np.fft.rfft(data * np.hanning(len(data))))
        return self.pipeline.duration(self.pipeline.info(path)), np.argmax(spectrum) * 48000 / len(data)

    def test_resampled_speed_change_moves_pitch_with_the_speed_and_keeps_the_exact_length(self):
        source = self.tone('shift-source', 1000)
        for name, speed in (('pal-to-ntsc', 23.976 / 25), ('ntsc-to-pal', 25 / 23.976), ('free', .9973)):
            with self.subTest(speed=name):
                output = self.path / f'shift-{name}.flac'
                self.pipeline.render_audio(source['nativePath'], '0', output, speed, False, 'shift')
                length, frequency = self.measured(output)
                self.assertAlmostEqual(length, 30 / speed, delta=.01)
                self.assertAlmostEqual(frequency, 1000 * speed, delta=2)

    def test_stretch_with_preserved_pitch_keeps_the_frequency(self):
        source = self.tone('keep-source', 1000)
        output = self.path / 'keep.flac'
        self.pipeline.render_audio(source['nativePath'], '0', output, 23.976 / 25, False, 'keep')
        length, frequency = self.measured(output)
        self.assertAlmostEqual(length, 30 / (23.976 / 25), delta=.3)
        self.assertAlmostEqual(frequency, 1000, delta=2)

    def test_dolby_sound_is_decoded_without_dynamic_range_compression(self):
        source = self.tone('drc-source', 440, seconds=12)
        calls = []
        original = self.pipeline.ffmpeg
        with patch.object(self.pipeline, 'ffmpeg', side_effect=lambda args, **kw: calls.append(list(map(str, args))) or original(args, **kw)):
            self.pipeline.render_audio(source['nativePath'], '0', self.path / 'drc.flac', .99, False, 'shift')
        decode = calls[0]
        self.assertEqual(decode[decode.index('-drc_scale') + 1], '0')
        self.assertLess(decode.index('-drc_scale'), decode.index('-i'))

    def lossy_tone(self, name, seconds=70):
        """A 44.1 kHz AC3 dub: its speed-corrected copy must come out as 48 kHz AC3 whatever the way of stretching."""
        source = self.tone(f'{name}-wav', 1000, seconds=seconds, rate=44100)
        converted = self.path / f'{name}.ac3'
        self.pipeline.ffmpeg(['-i', source['nativePath'], '-c:a', 'ac3', '-b:a', '192k', '-n', converted])
        return self.pipeline.probe(converted, 'audio')

    def test_lossy_dub_keeps_its_format_and_gets_48_khz_for_every_way_of_stretching(self):
        source = self.lossy_tone('lossy44')
        speed = .998
        for name, pitch, parallel in (('single-keep', 'keep', False), ('parallel-keep', 'keep', True), ('shift', 'shift', False)):
            with self.subTest(way=name):
                output = self.path / f'lossy-{name}.ac3'
                with patch.object(tempo, 'MIN_PARALLEL_SECONDS', 30 if parallel else 10 ** 6), patch.object(tempo, 'CHUNK_SECONDS', 24), patch.object(tempo, 'worker_count', lambda: 2):
                    self.pipeline.render_audio(source['nativePath'], '0', output, speed, False, pitch, -3.0 if name == 'shift' else 0.0)
                info = self.pipeline.info(output)
                stream = info['streams'][0]
                self.assertEqual((stream['codec_name'], stream['sample_rate']), ('ac3', '48000'))
                self.assertAlmostEqual(self.pipeline.duration(info), 70 / speed, delta=.15)
                self.assertAlmostEqual(self.measured(output)[1], 1000 * (speed if pitch == 'shift' else 1), delta=2)

    def test_a_plain_sound_with_a_bare_stream_name_is_read_as_it_is_and_tracks_say_what_they_are(self):
        # Only a file that mkvmerge splits into more tracks than FFmpeg finds is unpacked; a lone AC3 stays as it is.
        source = self.tone('bare-wav', 440, seconds=12)
        bare = self.path / 'Фильм.thd+ac3'
        self.pipeline.ffmpeg(['-i', source['nativePath'], '-c:a', 'ac3', '-f', 'ac3', '-n', bare])
        audio = self.pipeline.probe(bare, 'audio')
        self.assertNotIn('readablePath', audio)
        self.assertEqual(audio['metadata']['tracks'][0]['codec'], 'ac3')
        self.assertTrue(audio['metadata']['tracks'][0]['name'].endswith('AC3 1.0'), audio['metadata']['tracks'][0]['name'])
        self.assertEqual(self.pipeline.track_label({'codec_name': 'truehd', 'profile': 'Dolby TrueHD + Dolby Atmos', 'channels': 8}), 'TrueHD Atmos 7.1')
        self.assertEqual(self.pipeline.track_label({'codec_name': 'dts', 'profile': 'DTS-HD MA', 'channels': 6}), 'DTS-HD MA 5.1')

    def test_clipping_guard_lowers_a_track_that_reaches_full_scale(self):
        source = self.tone('loud-source', 1000, seconds=12, amplitude=1)
        audio = {**source, 'id': 'loud'}
        loud = self.pipeline.cached_audio(audio, '0', .99, False, 'shift', False)
        guarded = self.pipeline.cached_audio(audio, '0', .99, False, 'shift', True)
        self.assertNotEqual(loud, guarded)
        self.assertGreaterEqual(self.pipeline.peak_db(loud, '0'), CLIP_DB)
        self.assertLessEqual(self.pipeline.peak_db(guarded, '0'), -2.5)

    def test_hdr_preview_is_tone_mapped_and_sdr_is_left_alone(self):
        for transfer in ('smpte2084', 'arib-std-b67'):
            self.assertIn('tonemap', Pipeline.preview_video_filter(transfer))
            self.assertIn(f'tin={transfer}', Pipeline.preview_video_filter(transfer))
        for transfer in ('bt709', 'unknown', None):
            self.assertNotIn('tonemap', Pipeline.preview_video_filter(transfer))
        # The synthetic film carries no HDR tags.
        self.assertNotIn(self.pipeline.video_transfer(self.movie_path), ('smpte2084', 'arib-std-b67'))

    def test_hdr_filter_chain_handles_odd_scaled_height(self):
        # 1920x802 scales to an odd height at 1280 wide; zscale rejects odd chroma-subsampled sizes.
        source = ['-f', 'lavfi', '-i', 'testsrc2=size=1920x802:rate=12,format=yuv420p10le,setparams=color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc']
        for transfer in ('smpte2084', 'bt709'):
            with self.subTest(transfer=transfer):
                raw = self.pipeline.ffmpeg([*source, '-t', '0.5', '-vf', Pipeline.preview_video_filter(transfer), '-frames:v', '1', '-pix_fmt', 'yuv420p', '-f', 'rawvideo', '-'])
                self.assertEqual(len(raw), 1280 * 720 * 3 // 2)

    def run_full_analyzer(self, request):
        """The reference behaviour: the vendor script decoding both files completely."""
        movie, audio, config = request['movie'], request['audio'], self.pipeline.config
        duration = movie['metadata']['duration']
        return json.loads(self.pipeline.run('python', [
            '-I', config['analyzer'], '--ffmpeg', config['ffmpeg'], '--ref', movie['nativePath'], '--target', audio['nativePath'],
            '--ref-stream', f"0:{request['settings']['referenceTrack']}", '--target-stream', f"0:{request['settings']['externalTrack']}", '--windows', '7',
            '--window-len', str(max(1, min(60, duration / 7))), '--max-offset', str(min(600, max(5, duration / 3)))]))

    def test_quick_analysis_matches_full_analyzer(self):
        drift = lambda factor: np.interp(np.arange(int(len(self.reference) * factor)) / factor, np.arange(len(self.reference)), self.reference)
        cases = [('qsame', self.reference), ('qlate', np.concatenate([np.zeros(8000), self.reference])), ('qearly', self.reference[8000:]),
                 ('qpal', drift(25 / (24000 / 1001))), ('qsmall', drift(1.001)),
                 ('qnoise', np.random.default_rng(7).standard_normal(len(self.reference)).astype(np.float32) * .2)]
        for name, samples in cases:
            with self.subTest(name=name):
                request = self.request(name, samples)
                movie, audio, settings = request['movie'], request['audio'], request['settings']
                duration = movie['metadata']['duration']
                quick = self.pipeline.quick_analyzer(movie['nativePath'], audio['nativePath'], settings['referenceTrack'], settings['externalTrack'],
                                                     max(1, min(60, duration / 7)), min(600, max(5, duration / 3)))
                full = self.run_full_analyzer(request)
                self.assertIsNotNone(quick)
                self.assertTrue(quick['quick'])
                self.assertEqual(quick['verdict'], full['verdict'])
                if full['ratio'] is not None:
                    self.assertAlmostEqual(quick['ratio'], full['ratio'], places=6)
                    self.assertLessEqual(abs(quick['offset_ms'] - full['offset_ms']), 3)
                    self.assertEqual(len(quick['windows']), len(full['windows']))
                    for q, f in zip(quick['windows'], full['windows']):
                        self.assertLessEqual(abs(q['offset_ms'] - f['offset_ms']), 3)

    def test_transport_stream_film_is_read_and_analyzed_like_mkv(self):
        # MPEG-TS streams start late (about 1.4 s) and -ss counts from the file start, which the
        # quick analysis has to account for; it must also agree with the full analyzer here.
        transport = self.path / 'Фильм [ts].ts'
        self.pipeline.ffmpeg(['-i', self.movie_path, '-map', '0:v:0', '-map', '0:a:1', '-c:v', 'copy', '-c:a', 'ac3', '-ar', '48000', '-b:a', '192k', '-n', transport])
        movie = self.pipeline.probe(transport, 'movie')
        self.assertGreater(float(self.pipeline.info(transport)['format'].get('start_time') or 0), .5, 'the sample must really start late')
        late = self.path / 'ts-late.wav'
        write_wave(late, np.concatenate([np.zeros(8000), self.reference]))
        audio = self.pipeline.probe(late, 'audio')
        request = {'movie': movie, 'audio': audio, 'settings': {'referenceTrack': movie['metadata']['recommendedTrack'], 'externalTrack': audio['metadata']['recommendedTrack']}}
        duration = movie['metadata']['duration']
        quick = self.pipeline.quick_analyzer(movie['nativePath'], audio['nativePath'], request['settings']['referenceTrack'], request['settings']['externalTrack'],
                                             max(1, min(60, duration / 7)), min(600, max(5, duration / 3)))
        full = self.run_full_analyzer(request)
        self.assertEqual((quick['verdict'], full['verdict']), ('offset', 'offset'))
        self.assertLessEqual(abs(quick['offset_ms'] - full['offset_ms']), 3)
        self.assertLessEqual(abs(quick['offset_ms'] - 1000), 60)

    def test_analyzer_uses_quick_path_for_long_films_and_falls_back_safely(self):
        request = self.request('qroute', self.reference)
        movie, audio, settings = request['movie'], request['audio'], request['settings']
        args = (movie['nativePath'], audio['nativePath'], settings['referenceTrack'], settings['externalTrack'], movie['metadata']['duration'])
        self.assertNotIn('quick', self.pipeline.analyzer(*args), 'short films use the full analysis')
        with patch.object(quick_analysis, 'MIN_DURATION', 0):
            self.assertTrue(self.pipeline.analyzer(*args).get('quick'))
            with patch.object(self.pipeline, 'quick_analyzer', side_effect=RuntimeError('seek failed')):
                self.assertNotIn('quick', self.pipeline.analyzer(*args), 'any quick-path failure falls back to the full analysis')

    def multi_track_dub(self):
        """External file whose selected track (96 s) is shorter than another track (140 s)."""
        path = self.path / 'двухдорожечная [озвучка].mka'
        if not path.exists():
            self.pipeline.ffmpeg(['-i', self.path / 'оригинал.wav', '-f', 'lavfi', '-i', 'sine=frequency=300:sample_rate=8000:duration=140',
                                  '-map', '0:a', '-map', '1:a', '-c:a', 'flac', '-n', path])
        return path

    def test_track_durations_come_from_the_selected_track_not_the_container(self):
        audio = self.pipeline.probe(self.multi_track_dub(), 'audio')
        durations = {track['id']: track['duration'] for track in audio['metadata']['tracks']}
        self.assertAlmostEqual(durations['0'], self.duration, delta=1)
        self.assertAlmostEqual(durations['1'], 140, delta=1)
        # Rendering the shorter track must not plan chunks past its end.
        output = self.path / 'mt-render.flac'
        with patch.object(tempo, 'MIN_PARALLEL_SECONDS', 30), patch.object(tempo, 'CHUNK_SECONDS', 24), patch.object(tempo, 'worker_count', lambda: 2):
            self.pipeline.render_audio(self.multi_track_dub(), '0', output, .998, False)
        self.assertAlmostEqual(self.pipeline.duration(self.pipeline.info(output)), self.duration / .998, delta=.1)

    def test_build_with_a_shorter_track_in_a_longer_container_passes_the_duration_check(self):
        audio = self.pipeline.probe(self.multi_track_dub(), 'audio')
        request = self.request('mt-build', self.reference)
        request['audio'] = audio
        request['settings']['externalTrack'] = '0'
        request['analysis'] = {'verdict': 'aligned'}
        result = self.pipeline.build(request)
        self.assertTrue(Path(result['path']).exists())

    def test_build_reuses_the_prepared_sound_even_when_the_cache_disk_is_nearly_full(self):
        import collections
        request = self.request('space-check', self.reference)
        request['analysis'] = {'verdict': 'drift'}
        request['adjustment'].update(speedCorrection=.998)
        target = request['settings']['externalTrack']
        self.pipeline.cached_audio(request['audio'], target, .998, False)  # prepared during the analysis
        usage, real = collections.namedtuple('usage', 'total used free'), shutil.disk_usage
        cache = self.pipeline.cache.resolve()

        def nearly_full_cache(path):
            value = real(path)
            return usage(value.total, value.used, 1024 ** 2 if Path(path).resolve() == cache else value.free)
        with patch('backend.media.shutil.disk_usage', nearly_full_cache):
            result = self.pipeline.build(request)
        self.assertTrue(Path(result['path']).exists())

    def test_build_drops_only_the_subtitles_that_were_unchecked(self):
        subtitle = self.movie['metadata']['subtitles'][0]['id']
        for skipped, expected in (([], 1), ([subtitle], 0)):
            with self.subTest(skipped=skipped):
                request = self.request(f'subs-{expected}', self.reference)
                request['analysis'] = {'verdict': 'aligned'}
                request['settings']['skipSubtitles'] = skipped
                result = self.pipeline.build(request)
                tracks = json.loads(self.pipeline.run('mkvmerge', ['-J', result['path']]))['tracks']
                self.assertEqual(len([t for t in tracks if t['type'] == 'subtitles']), expected)
                self.assertEqual(len([t for t in tracks if t['type'] == 'video']), 1)

    def test_build_drops_only_the_movie_sound_that_was_unchecked(self):
        commentary = next(t['id'] for t in self.movie['metadata']['tracks'] if t['name'] == 'Комментарий режиссёра')
        original = self.movie['metadata']['recommendedTrack']
        for skipped, expected in (([], 3), ([commentary], 2), ([commentary, original], 1)):
            with self.subTest(skipped=skipped):
                request = self.request(f'audio-{expected}', self.reference)
                request['analysis'] = {'verdict': 'aligned'}
                request['settings']['skipAudio'] = skipped
                result = self.pipeline.build(request)
                tracks = json.loads(self.pipeline.run('mkvmerge', ['-J', result['path']]))['tracks']
                audio = [t for t in tracks if t['type'] == 'audio']
                self.assertEqual(len(audio), expected)
                self.assertEqual(sum(bool(t['properties'].get('default_track')) for t in audio), 1)
                if skipped:
                    self.assertNotIn('Комментарий режиссёра', [t['properties'].get('track_name') for t in audio])

    def test_a_saved_result_can_take_a_second_dub(self):
        first = self.request('twice-1', self.reference)
        first['analysis'] = {'verdict': 'aligned'}
        first['settings']['title'] = 'Первая озвучка'
        saved = self.pipeline.build(first)['path']
        second = self.request('twice-2', self.reference)
        second['movie'] = self.pipeline.probe(saved, 'movie')
        second['settings'].update(title='Вторая озвучка', referenceTrack=second['movie']['metadata']['recommendedTrack'])
        second['analysis'] = {'verdict': 'aligned'}
        second['outputName'] = 'twice.NEW2.mkv'
        tracks = json.loads(self.pipeline.run('mkvmerge', ['-J', self.pipeline.build(second)['path']]))['tracks']
        names = [t['properties'].get('track_name') for t in tracks if t['type'] == 'audio']
        self.assertEqual(len(names), 4)
        self.assertEqual(names[0], 'Вторая озвучка')
        self.assertIn('Первая озвучка', names)
        self.assertEqual(second['movie']['metadata']['tracks'][[t['id'] for t in second['movie']['metadata']['tracks']].index(second['movie']['metadata']['recommendedTrack'])]['name'], 'Original sound')

    def test_file_duration_ignores_subtitle_and_cover_art_streams(self):
        info = {'format': {'duration': '147.0'}, 'streams': [
            {'codec_type': 'video', 'duration': '100.0'}, {'codec_type': 'audio', 'duration': '99.5'},
            {'codec_type': 'subtitle', 'duration': '364.9'}, {'codec_type': 'video', 'duration': '500', 'disposition': {'attached_pic': 1}}]}
        self.assertEqual(Pipeline.duration(info), 100.0)
        mkv_tags = {'format': {'duration': '63.9'}, 'streams': [{'codec_type': 'video', 'tags': {'DURATION': '00:01:00.060000000'}}, {'codec_type': 'subtitle', 'duration': '63.9'}]}
        self.assertAlmostEqual(Pipeline.duration(mkv_tags), 60.06, places=3)
        self.assertEqual(Pipeline.duration({'format': {'duration': '42.5'}, 'streams': [{'codec_type': 'audio'}]}), 42.5, 'the container figure is the last resort')

    def test_missing_subtitle_flags_mean_the_matroska_defaults(self):
        # MP4 sources report no flags at all, while mkvmerge writes them explicitly into the MKV.
        self.assertEqual(Pipeline.subtitle_flags({'properties': {}}), (True, False))
        self.assertEqual(Pipeline.subtitle_flags({'properties': {'default_track': True, 'forced_track': False}}), (True, False))
        self.assertEqual(Pipeline.subtitle_flags({'properties': {'default_track': False, 'forced_track': True}}), (False, True))

    def test_failure_detail_names_the_command_and_its_exit_code(self):
        detail = Pipeline.failure_detail('mkvmerge', ['-J', 'Фильм [2026].mkv'], 2, 'Error: плохой файл'.encode())
        self.assertIn('mkvmerge -J Фильм [2026].mkv', detail)
        self.assertIn('код выхода: 2', detail)
        self.assertIn('плохой файл', detail)

    def test_a_program_that_cannot_start_is_explained_in_plain_words(self):
        # 0xC0000139: the entry point is missing, e.g. an old Windows without a function the program imports.
        fake = self.path / 'fake_mkvmerge.bat'
        fake.write_text('@echo off\r\nexit /b 3221225785\r\n')
        pipeline = Pipeline({**self.pipeline.config, 'mkvmergeLegacy': None, 'mkvmerge': str(fake)})
        empty = str(self.path / 'no-program-files')  # keep the MKVToolNix installed on this computer out of the picture
        with patch.dict('os.environ', {'ProgramFiles': empty, 'ProgramW6432': empty, 'ProgramFiles(x86)': empty}), patch('backend.media.shutil.which', return_value=None),                 self.assertRaisesRegex(MediaError, 'Windows 10') as raised:
            pipeline.check_tools()
        self.assertIn('mkvmerge --version', raised.exception.detail)
        self.assertIn('3221225785', raised.exception.detail)
        self.assertIsNone(launch_failure_message('mkvmerge', 2), 'ordinary failures keep the ordinary message')
        self.assertIn('библиотеки', launch_failure_message('ffmpeg', 0xC0000135))
        self.assertIsNone(self.pipeline.check_tools(), 'the real mkvmerge starts')

    def test_an_installed_mkvtoolnix_replaces_a_bundled_one_that_cannot_start(self):
        broken = self.path / 'broken_mkvmerge.bat'
        broken.write_text('@echo off' + chr(13) + chr(10) + 'exit /b 3221225785' + chr(13) + chr(10))
        installed = self.path / 'installed_mkvmerge.bat'
        installed.write_text('@echo off' + chr(13) + chr(10) + 'echo mkvmerge v1.0 fake --default-track-flag --identification-format' + chr(13) + chr(10) + 'exit /b 0' + chr(13) + chr(10))
        empty = str(self.path / 'no-program-files')
        nowhere = {'ProgramFiles': empty, 'ProgramW6432': empty, 'ProgramFiles(x86)': empty}
        pipeline = Pipeline({**self.pipeline.config, 'mkvmergeLegacy': None, 'mkvmerge': str(broken)})
        with patch.dict('os.environ', nowhere), patch('backend.media.shutil.which', return_value=str(installed)):
            self.assertEqual(pipeline.tool_path('mkvmerge'), str(installed))
            self.assertIsNone(pipeline.check_tools(), 'the installed program is used from then on')
        stuck = Pipeline({**self.pipeline.config, 'mkvmergeLegacy': None, 'mkvmerge': str(broken)})
        with patch.dict('os.environ', nowhere), patch('backend.media.shutil.which', return_value=None):
            self.assertEqual(stuck.tool_path('mkvmerge'), str(broken), 'with nothing else available the bundled one stays')
            with self.assertRaisesRegex(MediaError, 'Windows 10'):
                stuck.check_tools()
        self.assertEqual(self.pipeline.tool_path('mkvmerge'), str(self.pipeline.config['mkvmerge']), 'a working bundled mkvmerge is always preferred')

    def test_an_installed_mkvtoolnix_that_is_too_old_is_not_used(self):
        # Old versions start fine but know neither JSON identification nor --default-track-flag: they print their usage text.
        broken = self.path / 'broken2_mkvmerge.bat'
        broken.write_text('@echo off' + chr(13) + chr(10) + 'exit /b 3221225785' + chr(13) + chr(10))
        old = self.path / 'old_mkvmerge.bat'
        old.write_text('@echo off' + chr(13) + chr(10) + 'echo mkvmerge v9.0 usage: -i --identify --default-track' + chr(13) + chr(10) + 'exit /b 0' + chr(13) + chr(10))
        empty = str(self.path / 'no-program-files')
        pipeline = Pipeline({**self.pipeline.config, 'mkvmergeLegacy': None, 'mkvmerge': str(broken)})
        with patch.dict('os.environ', {'ProgramFiles': empty, 'ProgramW6432': empty, 'ProgramFiles(x86)': empty}), patch('backend.media.shutil.which', return_value=str(old)):
            self.assertEqual(pipeline.tool_path('mkvmerge'), str(broken))
            with self.assertRaisesRegex(MediaError, 'слишком старый для программы'):
                pipeline.check_tools()

    def test_mkvmerge_choice_order_and_the_default_track_option_name(self):
        def fake(name, help_text, code=0):
            path = self.path / name
            path.write_text('@echo off' + chr(13) + chr(10) + ('echo ' + help_text + chr(13) + chr(10) if help_text else '') + 'exit /b ' + str(code) + chr(13) + chr(10))
            return str(path)
        broken = fake('o_broken.bat', '', 3221225785)
        legacy = fake('o_legacy.bat', 'mkvmerge v60 --identification-format --default-track [TID]')
        installed = fake('o_installed.bat', 'mkvmerge v1 --identification-format --default-track-flag')
        empty = str(self.path / 'no-program-files')
        nowhere = {'ProgramFiles': empty, 'ProgramW6432': empty, 'ProgramFiles(x86)': empty}
        with patch.dict('os.environ', nowhere), patch('backend.media.shutil.which', return_value=installed):
            both = Pipeline({**self.pipeline.config, 'mkvmergeLegacy': None, 'mkvmerge': broken, 'mkvmergeLegacy': legacy})
            self.assertEqual(both.tool_path('mkvmerge'), legacy, 'the bundled older build comes before an installed one')
            self.assertEqual(both.default_flag_option(), '--default-track', 'the older build calls the option by its old name')
            modern = Pipeline({**self.pipeline.config, 'mkvmergeLegacy': None, 'mkvmerge': installed})
            self.assertEqual(modern.tool_path('mkvmerge'), installed)
            self.assertEqual(modern.default_flag_option(), '--default-track-flag')
            only_installed = Pipeline({**self.pipeline.config, 'mkvmergeLegacy': None, 'mkvmerge': broken})
            self.assertEqual(only_installed.tool_path('mkvmerge'), installed)

    def test_cpu_load_modes_limit_parallel_workers(self):
        with patch.object(tempo, 'available_cpus', lambda: 16):
            results = {}
            for mode in ('fast', 'moderate', 'background', 'nonsense'):
                tempo.configure(mode)
                results[mode] = tempo.worker_count()
        tempo.configure('fast')
        self.assertEqual(results, {'fast': 8, 'moderate': 8, 'background': 2, 'nonsense': 8})
        with patch.object(tempo, 'available_cpus', lambda: 4):
            tempo.configure('moderate'); moderate = tempo.worker_count()
            tempo.configure('background'); background = tempo.worker_count()
        tempo.configure('fast')
        self.assertEqual((moderate, background), (2, 1))
        with patch.object(tempo, 'available_cpus', lambda: 2):
            tempo.configure('background')
            self.assertFalse(tempo.will_parallelize(6000, .999), 'one worker must use the plain single pass')
        tempo.configure('fast')

    def test_parallel_stretch_failure_leaves_no_partial_files(self):
        track = self.movie['metadata']['recommendedTrack']
        output = self.path / 'stretch-failed.flac'
        real_plan = tempo.plan

        def broken(duration, speed):
            chunks = real_plan(duration, speed)
            chunks[1] = {**chunks[1], 'start': duration + 100, 'end': duration + 200}
            return chunks
        with patch.object(tempo, 'MIN_PARALLEL_SECONDS', 30), patch.object(tempo, 'CHUNK_SECONDS', 24),                 patch.object(tempo, 'worker_count', lambda: 2), patch.object(tempo, 'plan', broken):
            with self.assertRaises(MediaError):
                self.pipeline.render_audio(self.movie_path, track, output, .998, False)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.pipeline.work.glob('chunks-*')), [])

    def test_02_aligned_and_both_offset_signs(self):
        cases = [('same', self.reference, 0), ('late', np.concatenate([np.zeros(8000), self.reference]), -1000), ('early', self.reference[8000:], 1000)]
        for name, signal, expected in cases:
            with self.subTest(name=name):
                request = self.request(name, signal)
                result = self.pipeline.analyze(request)
                self.assertEqual(result['verdict'], 'aligned' if expected == 0 else 'offset')
                self.assertLessEqual(abs(result['proposedDelayMs'] - expected), 20)

    def test_03_drift_is_rechecked_after_rendering(self):
        # 1.001 would be 24 against 23.976 fps, which is a frame rate conversion and not a plain drift
        for name, factor in [('pal', 25 / (24000 / 1001)), ('film24', 24 / (24000 / 1001)), ('small', 1.0005)]:
            with self.subTest(name=name):
                samples = np.interp(np.arange(int(len(self.reference) * factor)) / factor, np.arange(len(self.reference)), self.reference)
                request = self.request(name, samples)
                result = self.pipeline.analyze(request)
                self.assertEqual(result['verdict'], 'drift', result)
                self.assertLess(abs(result['speedCorrection'] - factor), .0003)
                self.assertTrue(any('Повторная проверка' in line for line in result['evidence']))
                self.assertEqual(result['pitchShift'], name == 'film24', 'only the inaudible 0.1% conversion is resampled by default')
                if name == 'film24':
                    self.assertEqual(result['speedCorrection'], standard_ratio(factor)[0], 'a frame rate conversion uses the exact ratio')
                    self.assertTrue(any('23,976' in line for line in result['evidence']))
                if name != 'small':
                    for mode, shifted in (('shift', True), ('keep', False)):
                        request['settings']['pitchMode'] = mode
                        forced = self.pipeline.analyze(request)
                        self.assertEqual(forced['verdict'], 'drift', forced)
                        self.assertEqual(forced['pitchShift'], shifted)
                        self.assertLess(abs(forced['speedCorrection'] - factor), .0003)
                    request['settings']['pitchMode'] = 'auto'

    def test_04_build_preserves_tracks_and_preview_matches(self):
        request = self.request('сдвиг [AC3]', np.concatenate([np.zeros(8000), self.reference]), ac3=True)
        request['analysis'] = self.pipeline.analyze(request)
        request['adjustment']['automaticDelayMs'] = request['analysis']['proposedDelayMs']
        result = self.pipeline.build(request)
        output = Path(result['path'])
        before = json.loads(self.pipeline.run('mkvmerge', ['-J', self.movie_path]))
        after = json.loads(self.pipeline.run('mkvmerge', ['-J', output]))
        self.assertEqual(len([t for t in after['tracks'] if t['type'] == 'audio']), 4)
        self.assertEqual(sum(t['properties'].get('default_track', False) for t in after['tracks'] if t['type'] == 'audio'), 1)
        self.assertEqual(before['chapters'], after['chapters'])
        self.assertEqual(len(before['attachments']), len(after['attachments']))
        self.assertEqual([t['properties']['forced_track'] for t in before['tracks'] if t['type'] == 'subtitles'], [t['properties']['forced_track'] for t in after['tracks'] if t['type'] == 'subtitles'])
        final_probe = self.pipeline.info(output)
        final_audio = self.pipeline.audio_streams(final_probe)[0]['index']
        for position in [5, 40, 75]:
            request['preview']['playbackPosition'] = position
            clip = self.pipeline.preview(request)
            # Decode five seconds from the same absolute scene in final and preview.
            final = self.pipeline.ffmpeg(['-ss', str(position), '-i', output, '-map', f'0:{final_audio}', '-t', '5', '-ac', '1', '-ar', '8000', '-f', 's16le', '-'])
            preview = self.pipeline.ffmpeg(['-i', clip['path'], '-map', '0:a:0', '-t', '5', '-ac', '1', '-ar', '8000', '-f', 's16le', '-'])
            a = np.frombuffer(final, '<i2').astype(np.float64)
            b = np.frombuffer(preview, '<i2').astype(np.float64)
            correlations = [np.dot(a[800:30000], b[800+shift:30000+shift]) for shift in range(-320, 321)]
            lag = int(np.argmax(correlations)) - 320
            self.assertLessEqual(abs(lag) / 8, 40, (position, lag / 8))
            self.assertEqual(clip['effectiveDelayMs'], result['effectiveDelayMs'])
        with self.assertRaisesRegex(MediaError, 'уже существует'):
            self.pipeline.build(request)

    def test_05_failed_preconditions_never_create_output(self):
        request = self.request('preconditions', self.reference)
        with self.assertRaisesRegex(MediaError, 'не подтверждено'):
            self.pipeline.build(request)
        request['analysis'] = {'verdict': 'aligned'}
        with patch('backend.media.shutil.disk_usage', return_value=shutil._ntuple_diskusage(100, 99, 1)):
            with self.assertRaisesRegex(MediaError, 'Недостаточно места'):
                self.pipeline.build(request)
        self.assertFalse((self.path / request['outputName']).exists())
        broken = copy.deepcopy(request)
        broken['movie']['id'] = 'outdated'
        with self.assertRaisesRegex(MediaError, 'изменился'):
            self.pipeline.build(broken)

    def test_06_preview_and_build_share_drift_timeline(self):
        factor = 25 / (24000 / 1001)
        samples = np.interp(np.arange(int(len(self.reference) * factor)) / factor, np.arange(len(self.reference)), self.reference)
        samples = np.concatenate([np.zeros(5600), samples])
        request = self.request('drift-with-intro', samples)
        request['analysis'] = self.pipeline.analyze(request)
        self.assertEqual(request['analysis']['verdict'], 'drift')
        request['adjustment'].update(automaticDelayMs=request['analysis']['proposedDelayMs'], speedCorrection=request['analysis']['speedCorrection'])
        result = self.pipeline.build(request)
        output = result['path']
        final_audio = self.pipeline.audio_streams(self.pipeline.info(output))[0]['index']
        for position in [5, 40, 75]:
            with self.subTest(position=position):
                request['preview']['playbackPosition'] = position
                clip = self.pipeline.preview(request)
                final = self.pipeline.ffmpeg(['-ss', str(position), '-i', output, '-map', f'0:{final_audio}', '-t', '5', '-ac', '1', '-ar', '8000', '-f', 's16le', '-'])
                preview = self.pipeline.ffmpeg(['-i', clip['path'], '-map', '0:a:0', '-t', '5', '-ac', '1', '-ar', '8000', '-f', 's16le', '-'])
                a = np.frombuffer(final, '<i2').astype(np.float64)
                b = np.frombuffer(preview, '<i2').astype(np.float64)
                correlations = [np.dot(a[800:30000], b[800+shift:30000+shift]) for shift in range(-800, 801)]
                lag = int(np.argmax(correlations)) - 800
                self.assertLessEqual(abs(lag) / 8, 40, (position, lag / 8))

    def test_07_unrelated_sound_needs_explicit_bypass(self):
        unrelated = np.random.default_rng(930).standard_normal(len(self.reference)) * .08
        request = self.request('unrelated', unrelated)
        result = self.pipeline.analyze(request)
        self.assertIn(result['verdict'], ('uncertain', 'different-edit'))
        self.assertEqual(result['speedCorrection'], 1)
        self.assertEqual(result['proposedDelayMs'], 0)
        request['analysis'] = result
        with self.assertRaisesRegex(MediaError, 'не подтверждено'):
            self.pipeline.build(request)
        request['allowUnverified'] = True
        output = self.pipeline.build(request)
        self.assertTrue(Path(output['path']).is_file())

    def test_08_worker_reads_raw_utf8_paths_even_on_legacy_windows_code_page(self):
        request = self.request('Озвучка [日本語] café', self.reference)
        payload = {'action': 'probe', 'config': self.pipeline.config, 'payload': {'path': request['audio']['nativePath'], 'kind': 'audio'}}
        # This is the Rust -> Python wire protocol, not a direct Pipeline call.
        encoded = (json.dumps(payload, ensure_ascii=False) + '\n').encode('utf-8')
        process = subprocess.run([self.pipeline.config['python'], '-I', '-u', str(ROOT / 'backend/worker.py')], input=encoded, capture_output=True)
        events = [json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual(process.returncode, 0, events)
        self.assertEqual(events[-1]['type'], 'result')
        self.assertEqual(events[-1]['value']['name'], 'Озвучка [日本語] café.wav')
        self.assertEqual(Path(events[-1]['value']['nativePath']), Path(request['audio']['nativePath']))

    def test_09_missing_file_returns_actionable_message(self):
        with self.assertRaisesRegex(MediaError, 'Файл не найден.*Выберите его заново'):
            self.pipeline.probe(self.path / 'не существует.wav', 'audio')

    def test_10_overwrite_requires_confirmation_and_preserves_previous_result_on_failure(self):
        request = self.request('overwrite', self.reference)
        request['analysis'] = {'verdict': 'aligned'}
        output = self.path / request['outputName']
        original = b'previous result must remain until verification succeeds'
        output.write_bytes(original)
        with self.assertRaises(MediaError) as conflict:
            self.pipeline.build(request)
        self.assertEqual(conflict.exception.code, 'output-exists')
        self.assertEqual(output.read_bytes(), original)
        request['overwriteToken'] = conflict.exception.context['token']
        with patch.object(self.pipeline, 'run_progress', side_effect=MediaError('Simulated mux failure')):
            with self.assertRaisesRegex(MediaError, 'Simulated mux failure'):
                self.pipeline.build(request)
        self.assertEqual(output.read_bytes(), original)
        # A changed destination must require a fresh confirmation.
        output.write_bytes(b'changed by another program')
        with self.assertRaises(MediaError) as changed:
            self.pipeline.build(request)
        self.assertEqual(changed.exception.code, 'output-exists')
        self.assertNotEqual(changed.exception.context['token'], request['overwriteToken'])
        request['overwriteToken'] = changed.exception.context['token']
        result = self.pipeline.build(request)
        self.assertEqual(Path(result['path']), output)
        self.assertNotEqual(output.read_bytes(), original)
        self.assertTrue(self.pipeline.audio_streams(self.pipeline.info(output)))

    def test_11_overwrite_never_replaces_input_even_when_confirmed(self):
        request = self.request('source-guard', self.reference)
        request['analysis'] = {'verdict': 'aligned'}
        request['outputName'] = self.movie_path.name
        request['overwriteToken'] = self.pipeline.output_token(self.movie_path)
        with self.assertRaisesRegex(MediaError, 'исходный фильм'):
            self.pipeline.build(request)
        self.assertEqual(hashlib.sha256(self.movie_path.read_bytes()).hexdigest(), self.source_hash)


class ServiceLogTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='журнал ', dir=ROOT / '.local')
        self.dir = Path(self.folder.name) / 'logs'
        applog.configure({'logDir': str(self.dir), 'version': '9.9.9'})

    def tearDown(self):
        applog.configure({})
        self.folder.cleanup()

    def test_log_is_capped_at_three_files_and_keeps_the_newest_events(self):
        with patch.object(applog, 'MAX_BYTES', 8 * 1024):
            for index in range(400):
                applog.write('INFO', f'событие {index} ' + 'x' * 300)
        names = sorted(p.name for p in self.dir.iterdir())
        self.assertEqual(names, ['soundmux.1.log', 'soundmux.2.log', 'soundmux.log'])
        for path in self.dir.iterdir():
            self.assertLess(path.stat().st_size, 8 * 1024 + 1500, path.name)
        current = (self.dir / 'soundmux.log').read_text(encoding='utf-8')
        self.assertIn('событие 399', current)
        self.assertIn('SoundMux 9.9.9', current.splitlines()[0], 'every new file starts with what identifies the installation')

    def test_profile_folder_is_replaced_and_long_detail_is_cut(self):
        with patch.dict(os.environ, {'USERPROFILE': r'C:\Users\Секретный Вася'}):
            applog.write('ERROR', r'не открылся C:\Users\Секретный Вася\Видео\фильм.mkv', repr(r'C:\Users\Секретный Вася\x.wav') + '\n' + 'a' * 9000 + '\nконец')
        text = (self.dir / 'soundmux.log').read_text(encoding='utf-8')
        self.assertNotIn('Секретный', text)
        self.assertIn(r'%USERPROFILE%\Видео\фильм.mkv', text)
        self.assertLess(len(text), 6000)
        self.assertIn('    конец', text)

    def test_a_broken_log_folder_never_breaks_the_job(self):
        blocked = Path(self.folder.name) / 'файл'
        blocked.write_text('не папка', encoding='utf-8')
        applog.configure({'logDir': str(blocked / 'logs')})
        applog.write('ERROR', 'ничего не должно произойти')
        applog.configure({})
        applog.write('ERROR', 'без настроек тоже')

    def test_worker_logs_start_end_and_failures_without_the_whole_path(self):
        config = json.loads((ROOT / '.local/tools.json').read_text(encoding='utf-8-sig'))
        config.update(cache=str(Path(self.folder.name) / 'cache'), analyzer=str(ROOT / 'backend/vendor/audio_sync.py'), logDir=str(self.dir), version='1.2.3')
        for payload, expected in ((self.sound_payload(), 'probe: done'), ({'path': str(ROOT / 'нет такого.wav'), 'kind': 'audio'}, 'probe: failed')):
            request = {'action': 'probe', 'config': config, 'payload': payload}
            subprocess.run([config['python'], '-I', '-u', str(ROOT / 'backend/worker.py')], input=(json.dumps(request, ensure_ascii=False) + '\n').encode('utf-8'), capture_output=True)
            self.assertIn(expected, (self.dir / 'soundmux.log').read_text(encoding='utf-8'))
        text = (self.dir / 'soundmux.log').read_text(encoding='utf-8')
        self.assertIn('probe: start. file: ', text)
        self.assertIn('ffmpeg:', text.splitlines()[1], 'the header names the tools')

    def sound_payload(self):
        path = Path(self.folder.name) / 'тон.wav'
        write_wave(path, np.sin(np.arange(16000) / 8) * .3)
        return {'path': str(path), 'kind': 'audio'}


if __name__ == '__main__':
    unittest.main(verbosity=2)
