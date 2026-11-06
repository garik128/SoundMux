"""Real media pipeline. All subprocess arguments are structured; sources are read-only."""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

try:
    from . import applog, quick_analysis, tempo
except ImportError:  # worker.py puts this folder on sys.path and imports `media` directly
    import applog
    import quick_analysis
    import tempo

CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0
# Large enough to judge lip sync; the clip is 20 seconds, so size stays modest. Part of the cache key.
PREVIEW_WIDTH, PREVIEW_HEIGHT = 1280, 720
HDR_TRANSFERS = ('smpte2084', 'arib-std-b67')  # PQ (HDR10, Dolby Vision base layer) and HLG
TONEMAP_VERSION = 'hable-1'  # part of the preview cache key
# Part of the corrected-sound cache key: bump it whenever the way the sound is decoded or resampled changes.
# audio-2: AC3/E-AC3 are decoded without dynamic range compression, sample rate conversion uses SoX.
AUDIO_RENDER_VERSION = 'audio-2'

# Classic film frame rates. Moving sound between them (PAL 25 <-> NTSC 23.976 <-> film 24) is done by
# resampling, which changes speed and pitch together, is exactly reversible and leaves no artifacts.
# Time stretching with preserved pitch is a one-way trip: its artifacts stay in the track.
FILM_RATES = ((24000 / 1001, '23,976'), (24, '24'), (25, '25'))
RATE_TOLERANCE = 3e-4  # the analyzer measures speed to about 1e-4; the closest standard ratios differ by 1e-3
# 'Automatic' resamples only where the pitch change is inaudible (24 against 23.976 fps, 0.1%). The 4% PAL conversions
# audibly move the voices, so they keep the pitch unless the user asks for the resampling.
AUTO_SHIFT_LIMIT = .005
CLIP_DB = -0.1  # a rendered track whose loudest sample is at or above this has probably been clipped
GUARD_GAIN_DB = -3.0  # headroom recommended for tracks that clip after the speed change
PITCH_MODES = ('auto', 'shift', 'keep')
# Bare Blu-ray audio streams. A TrueHD stream usually carries a Dolby Digital copy of itself in the same file
# (.thd+ac3); FFmpeg reads that as one broken AC3 track, mkvmerge sees both tracks.
RAW_AUDIO_SUFFIXES = ('.thd+ac3', '.thd', '.mlp', '.truehd')
CODEC_LABELS = {'truehd': 'TrueHD', 'ac3': 'AC3', 'eac3': 'E-AC3', 'dts': 'DTS', 'flac': 'FLAC', 'aac': 'AAC', 'mp3': 'MP3', 'opus': 'Opus', 'vorbis': 'Vorbis'}
CHANNEL_LABELS = {1: '1.0', 2: '2.0', 3: '2.1', 4: '4.0', 5: '5.0', 6: '5.1', 7: '6.1', 8: '7.1'}


def standard_ratio(speed):
    """(exact ratio, "25 -> 23,976") when `speed` is a frame rate conversion, otherwise None."""
    for target, target_name in FILM_RATES:
        for source, source_name in FILM_RATES:
            if source != target and abs(speed / (target / source) - 1) <= RATE_TOLERANCE:
                return target / source, f'{source_name} → {target_name}'
    return None


def resolve_pitch(mode, speed):
    """'shift' (resample, pitch follows speed) or 'keep' (Rubber Band, pitch preserved) for a speed factor."""
    if speed == 1 or mode == 'keep':
        return 'keep'
    if mode == 'shift':
        return 'shift'
    standard = standard_ratio(speed)
    return 'shift' if standard and abs(standard[0] - 1) < AUTO_SHIFT_LIMIT else 'keep'


# A program that cannot even start is reported by Windows as an NTSTATUS exit code, not as a message.
LAUNCH_FAILURES = {
    0xC0000139: 'в системе нет нужной функции',
    0xC0000135: 'в системе нет нужной библиотеки',
    0xC000007B: 'программа не подходит для этой системы',
    0xC0000142: 'не удалось подготовить библиотеку',
}


def launch_failure_message(tool, code):
    """Plain explanation when `tool` died at start-up on this Windows, else None."""
    reason = LAUNCH_FAILURES.get(code & 0xFFFFFFFF)
    if reason is None:
        return None
    return (f'Компонент «{tool}» не запускается на этом компьютере: {reason}. Скорее всего, версия Windows слишком старая. '
            'Нужна более новая Windows 10 (надёжнее всего версия 22H2) либо Windows 11; версию показывает команда winver (Win+R, затем winver). '
            'Обновите Windows (Параметры, Обновление и безопасность) и повторите.')


class MediaError(Exception):
    def __init__(self, message, detail='', code=None, context=None):
        super().__init__(message)
        self.detail = detail
        self.code = code
        self.context = context


class Pipeline:
    def __init__(self, config, emit=lambda event: None):
        self.config = config
        self.emit = emit
        self._mkvmerge = None
        self._default_flag = '--default-track-flag'
        self.mkvmerge_note = ''
        tempo.configure(config.get('load'))
        self.cache = Path(config['cache'])
        self.cache.mkdir(parents=True, exist_ok=True)
        self.work = Path(config.get('work', self.cache))
        self.work.mkdir(parents=True, exist_ok=True)

    def progress(self, label, percent=None, eta=None):
        event = {'type': 'progress', 'label': label, 'percent': percent}
        if eta is not None:
            event['etaSeconds'] = eta
        self.emit(event)

    def tool_path(self, tool):
        """The program to run. For mkvmerge: the bundled one, or an installed MKVToolNix when the bundled one cannot start here
        (the bundled build needs Windows functions that older Windows 10 versions do not have)."""
        if tool != 'mkvmerge':
            return str(self.config[tool])
        if self._mkvmerge is None:
            self._mkvmerge = self.working_mkvmerge()
        return self._mkvmerge

    def working_mkvmerge(self):
        """First mkvmerge that starts here and knows what this application needs: the bundled one, then the bundled
        older build for old Windows (config key mkvmergeLegacy), then an installed MKVToolNix."""
        bundled = str(self.config['mkvmerge'])

        def capability(path):
            """The name of the 'default track' option this mkvmerge understands, or None when it cannot be used.
            It must start and offer JSON identification; old versions start fine but only print their usage text.
            The option was called --default-track before it became --default-track-flag."""
            try:
                if subprocess.run([path, '--version'], stdin=subprocess.DEVNULL, capture_output=True, creationflags=CREATE_NO_WINDOW).returncode != 0:
                    return None
                help_text = subprocess.run([path, '--ui-language', 'en', '--help'], stdin=subprocess.DEVNULL, capture_output=True, creationflags=CREATE_NO_WINDOW).stdout.decode('utf-8', 'replace')
            except OSError:
                return None
            if '--identification-format' not in help_text:
                return None
            if '--default-track-flag' in help_text:
                return '--default-track-flag'
            return '--default-track' if '--default-track ' in help_text else None

        def accept(path):
            option = capability(path)
            if option is None:
                return None
            self._default_flag = option
            return path

        legacy = self.config.get('mkvmergeLegacy')
        for candidate in [bundled, str(legacy) if legacy and Path(legacy).is_file() else None]:
            if candidate and accept(candidate):
                return candidate
        found = [shutil.which('mkvmerge')]
        for variable in ('ProgramFiles', 'ProgramW6432', 'ProgramFiles(x86)'):
            if os.environ.get(variable):
                found.append(str(Path(os.environ[variable]) / 'MKVToolNix' / 'mkvmerge.exe'))
        known = {str(Path(p).resolve()) for p in (bundled, legacy) if p and Path(p).exists()}
        installed = list(dict.fromkeys(p for p in found if p and Path(p).is_file() and str(Path(p).resolve()) not in known))
        for candidate in installed:
            if accept(candidate):
                return candidate
        if installed:
            self.mkvmerge_note = ' Найденный на компьютере MKVToolNix слишком старый для программы, поэтому использовать его нельзя.'
        self._default_flag = '--default-track-flag'
        return bundled  # nothing better: the error then names the real cause

    def default_flag_option(self):
        """Name of the 'default track' option of the mkvmerge in use (it differs between MKVToolNix versions)."""
        self.tool_path('mkvmerge')
        return self._default_flag

    def launch_failure(self, tool, code):
        message = launch_failure_message(tool, code)
        return message + self.mkvmerge_note if message and tool == 'mkvmerge' else message

    def run(self, tool, args, *, accepted=(0,)):
        process = subprocess.run([self.tool_path(tool), *map(str, args)], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
        if process.returncode not in accepted:
            detail = self.failure_detail(tool, args, process.returncode, process.stderr + process.stdout)
            raise MediaError(self.launch_failure(tool, process.returncode) or 'Не удалось обработать файл. Проверьте, что он полностью скачан и открывается в плеере.', detail)
        return process.stdout

    @staticmethod
    def failure_detail(tool, args, code, output):
        """What the user can send along with a bug report: which command failed, its exit code and its last output."""
        command = ' '.join(str(a) for a in args)
        return f'{tool} {command[:700]}\nкод выхода: {code}\n' + output.decode('utf-8', 'replace')[-5000:]

    def run_progress(self, tool, args, label, duration=None, accepted=(0,)):
        """Report measured encoder/muxer output, never synthetic percentages."""
        with tempfile.TemporaryFile(dir=self.work) as errors:
            process = subprocess.Popen([self.tool_path(tool), *map(str, args)], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=errors, creationflags=CREATE_NO_WINDOW)
            output = []
            previous = -1
            for raw_line in process.stdout:
                line = raw_line.decode('utf-8', 'replace').strip()
                output.append(raw_line)
                percent = None
                match = re.search(r'progress(?::|\s)\s*(\d+)%', line, re.I)
                if match:
                    percent = int(match.group(1))
                elif duration and line.startswith('out_time_us='):
                    try:
                        percent = min(99, max(0, int(float(line.split('=', 1)[1]) / 1_000_000 / duration * 100)))
                    except ValueError:
                        pass
                if percent is not None and percent != previous:
                    self.progress(label, percent)
                    previous = percent
            code = process.wait()
            process.stdout.close()
            if code not in accepted:
                errors.seek(0)
                detail = self.failure_detail(tool, args, code, errors.read() + b''.join(output))
                raise MediaError(self.launch_failure(tool, code) or 'Не удалось обработать файл. Проверьте свободное место и попробуйте ещё раз.', detail)
            self.progress(label, 100)

    def ffmpeg(self, args, *, label=None, duration=None):
        common = ['-hide_banner', '-loglevel', 'error', '-nostdin']
        if label:
            return self.run_progress('ffmpeg', [*common, '-progress', 'pipe:1', '-nostats', *args], label, duration)
        return self.run('ffmpeg', [*common, *args])

    @staticmethod
    def source_path(path):
        try:
            return Path(path).resolve(strict=True)
        except FileNotFoundError as error:
            raise MediaError('Файл не найден. Возможно, он был перемещён. Выберите его заново.', str(error)) from error
        except PermissionError as error:
            raise MediaError('Нет доступа к файлу. Проверьте доступ к папке и выберите файл заново.', str(error)) from error
        except OSError as error:
            raise MediaError('Не удалось открыть путь к файлу. Проверьте подключение диска и выберите файл заново.', str(error)) from error

    def info(self, path):
        path = self.source_path(path)
        if not path.is_file() or path.stat().st_size == 0:
            raise MediaError('Файл пуст или недоступен. Выберите другой файл.')
        with path.open('rb') as stream:
            if not any(stream.read(4096)):
                raise MediaError('Файл ещё не скачан полностью. Дождитесь загрузки и выберите его снова.')
        data = json.loads(self.run('ffprobe', ['-v', 'error', '-show_format', '-show_streams', '-show_chapters', '-of', 'json', path]))
        if not data.get('streams'):
            raise MediaError('В файле не найдено видео или звука. Выберите другой файл.')
        return data

    def video_transfer(self, path):
        """Transfer characteristic of the main picture, or None when unknown."""
        info = self.info(path)
        for stream in info['streams']:
            if stream.get('codec_type') == 'video' and not stream.get('disposition', {}).get('attached_pic'):
                return stream.get('color_transfer')
        return None

    @staticmethod
    def preview_video_filter(transfer):
        """Fit into the preview frame; HDR is tone-mapped to SDR after shrinking, so the heavy
        float conversion works on 720p instead of 4K."""
        fit = f'scale={PREVIEW_WIDTH}:{PREVIEW_HEIGHT}:force_original_aspect_ratio=decrease:force_divisible_by=2'
        pad = f'pad={PREVIEW_WIDTH}:{PREVIEW_HEIGHT}:(ow-iw)/2:(oh-ih)/2,setsar=1'
        if transfer not in HDR_TRANSFERS:
            return f'{fit},{pad}'
        tonemap = (f'zscale=tin={transfer}:min=bt2020nc:pin=bt2020:t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,'
                   'tonemap=tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p')
        return f'{fit},{tonemap},{pad}'

    @staticmethod
    def own_seconds(stream):
        """A stream's own length (its duration field, else the MKV DURATION tag), or None when it does not say."""
        value = stream.get('duration')
        if value and value != 'N/A':
            return float(value)
        tags = stream.get('tags') or {}
        tag = tags.get('DURATION') or tags.get('duration')
        if tag:
            try:
                hours, minutes, seconds = tag.split(':')
                return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
            except ValueError:
                return None
        return None

    @staticmethod
    def duration(info):
        """Length of the picture and sound. Subtitle, data and cover-art streams are ignored: their durations are
        arbitrary (a one-minute MP4 can report a subtitle track of six minutes) and would inflate the file's length.
        The container's own figure is used only when no picture or sound stream states its length."""
        media = [s for s in info['streams'] if s.get('codec_type') in ('video', 'audio') and not s.get('disposition', {}).get('attached_pic')]
        values = [v for v in (Pipeline.own_seconds(s) for s in media) if v is not None]
        if not values:
            container = info.get('format', {}).get('duration')
            values = [float(container)] if container and container != 'N/A' else []
        return max(values or [0])

    @staticmethod
    def audio_streams(info):
        return [s for s in info['streams'] if s.get('codec_type') == 'audio']

    @staticmethod
    def reference(streams):
        def score(stream):
            tags = stream.get('tags', {})
            title = tags.get('title', '').lower()
            commentary = any(word in title for word in ['comment', 'коммент', 'descript', 'описание']) or stream.get('disposition', {}).get('comment', 0)
            original = stream.get('disposition', {}).get('original', 0) or 'original' in title or 'оригинал' in title
            return (not commentary, bool(original), tags.get('language') in ('eng', 'en'), bool(stream.get('disposition', {}).get('default')))
        return max(streams, key=score)

    @staticmethod
    def track_label(stream):
        """'TrueHD Atmos 7.1': what tells two tracks of one file apart when neither has a title."""
        codec = stream.get('codec_name', '')
        profile = stream.get('profile', '')
        label = CODEC_LABELS.get(codec, codec.upper())
        if codec == 'dts' and 'MA' in profile:
            label = 'DTS-HD MA'
        if 'Atmos' in profile:
            label += ' Atmos'
        channels = int(stream.get('channels') or 0)
        return f"{label} {CHANNEL_LABELS.get(channels, f'{channels} кан.')}".strip()

    def unpacked_audio(self, path, audio):
        """A readable copy of a sound file that FFmpeg cannot split into its tracks, or None when it needs none.

        Only files that look like bare TrueHD/MLP streams, or whose sample rate FFmpeg cannot read, are asked about,
        and the copy is made only when mkvmerge finds tracks FFmpeg did not. mkvmerge copies the tracks as they are
        (about half a minute for a film), the copy lives in the cache and is made again if the cache was cleared.
        """
        unreadable = any(not int(s.get('sample_rate') or 0) for s in audio)
        if not unreadable and not path.name.lower().endswith(RAW_AUDIO_SUFFIXES):
            return None
        listing = json.loads(self.run('mkvmerge', ['-J', path], accepted=(0, 1)))
        found = [t for t in listing.get('tracks', []) if t.get('type') == 'audio']
        if not found or (len(found) <= len(audio) and not unreadable):
            return None
        stat = path.stat()
        output = self.cache / f"unpacked-{hashlib.sha256(f'{path}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest()}.mka"
        if output.exists():
            return output
        if shutil.disk_usage(self.cache).free < stat.st_size * 1.05 + 64 * 1024**2:
            raise MediaError(f'Недостаточно места для распаковки звука. Освободите примерно {math.ceil(stat.st_size * 1.05 / 1024**3)} ГБ на диске программы.')
        self.progress('Распаковка звука')
        applog.note(f'unpack: {path.name} ({stat.st_size / 1024**2:.0f} МБ) into the cache, mkvmerge found {len(found)} audio track(s), FFmpeg {len(audio)}')
        with tempfile.TemporaryDirectory(prefix='unpack-', dir=self.work) as folder:
            temporary = Path(folder) / output.name
            self.run_progress('mkvmerge', ['--ui-language', 'en', '--gui-mode', '-o', temporary, path], 'Распаковка звука', accepted=(0, 1))
            temporary.replace(output)
        return output

    def probe(self, path, kind):
        path = self.source_path(path)
        info = self.info(path)
        audio = self.audio_streams(info)
        readable = self.unpacked_audio(path, audio) if kind == 'audio' else None
        if readable:
            info = self.info(readable)
            audio = self.audio_streams(info)
        if not audio:
            raise MediaError('В этом файле нет звука. Выберите файл со звуком.')
        if kind == 'movie' and not any(s.get('codec_type') == 'video' for s in info['streams']):
            raise MediaError('В этом файле нет картинки. Выберите фильм.')
        tracks = []
        for stream in audio:
            tags = stream.get('tags', {})
            name = tags.get('title') or f"Звук {len(tracks) + 1} · {tags.get('language', 'язык не указан')}"
            if kind == 'audio' and not tags.get('title'):
                name += f' · {self.track_label(stream)}'
            tracks.append({'id': str(stream['index']), 'name': name,
                           'codec': stream.get('codec_name', ''), 'channels': stream.get('channels', 0), 'sampleRate': int(stream.get('sample_rate', 48000)), 'duration': self.stream_seconds(stream, info), 'default': bool(stream.get('disposition', {}).get('default'))})
        subtitles = []
        for stream in (s for s in info['streams'] if s.get('codec_type') == 'subtitle'):
            tags = stream.get('tags', {})
            subtitles.append({'id': str(stream['index']), 'name': tags.get('title') or f"Субтитры {len(subtitles) + 1} · {tags.get('language', 'язык не указан')}",
                              'codec': stream.get('codec_name', '')})
        stat = path.stat()
        return {'id': hashlib.sha256(f'{path}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest(),
                'name': path.name, 'size': stat.st_size, 'nativePath': str(path), **({'readablePath': str(readable)} if readable else {}),
                'metadata': {'origin': 'probe', 'duration': self.duration(info), 'tracks': tracks, 'subtitles': subtitles,
                             'recommendedTrack': str(self.reference(audio)['index'])}}

    @staticmethod
    def subtitle_flags(track):
        """(default, forced) of a subtitle track. MP4 and some other sources leave the flags unreported while
        mkvmerge writes them explicitly into the MKV, so an absent flag counts as the Matroska default."""
        properties = track['properties']
        default, forced = properties.get('default_track'), properties.get('forced_track')
        return (True if default is None else bool(default), False if forced is None else bool(forced))

    def validate_input(self, request):
        movie = self.probe(request['movie']['nativePath'], 'movie')
        audio = self.probe(request['audio']['nativePath'], 'audio')
        for current, provided in [(movie, request['movie']), (audio, request['audio'])]:
            if current['id'] != provided['id']:
                raise MediaError('Файл изменился после выбора. Выберите его заново и повторите проверку.')
        # From here on the sound is read from its unpacked copy, when it has one; the user's own file stays untouched.
        audio = {**audio, 'nativePath': audio.get('readablePath', audio['nativePath'])}
        settings = request['settings']
        reference = settings.get('referenceTrack') or movie['metadata']['recommendedTrack']
        target = settings.get('externalTrack') or audio['metadata']['recommendedTrack']
        if reference not in [s['id'] for s in movie['metadata']['tracks']] or target not in [s['id'] for s in audio['metadata']['tracks']]:
            raise MediaError('Выбранный звук больше недоступен. Выберите файлы заново.')
        return movie, audio, reference, target

    def analyzer(self, ref, target, ref_stream, target_stream, duration):
        window = max(1, min(60, duration / 7))
        max_offset = min(600, max(5, duration / 3))
        if duration >= quick_analysis.MIN_DURATION:
            # Reads only the compared film windows instead of the whole file. Any trouble means
            # the full analysis below runs, which is the reference behaviour.
            try:
                quick = self.quick_analyzer(ref, target, ref_stream, target_stream, window, max_offset)
            except Exception:
                quick = None
            if quick is not None:
                return quick
        data = self.run('python', ['-I', self.config['analyzer'], '--ffmpeg', self.config['ffmpeg'], '--ref', ref, '--target', target,
                                  '--ref-stream', f'0:{ref_stream}', '--target-stream', f'0:{target_stream}',
                                  '--windows', '7', '--window-len', str(window), '--max-offset', str(max_offset)])
        return json.loads(data)

    def quick_analyzer(self, ref, target, ref_stream, target_stream, window, max_offset):
        import numpy as np
        vendor = quick_analysis.load_vendor(self.config['analyzer'])
        info = self.info(ref)
        stream = next(s for s in info['streams'] if str(s['index']) == str(ref_stream))
        seconds = self.stream_seconds(stream, info)
        # The full decode starts at the first sample of this stream, while FFmpeg's -ss counts from the
        # start of the file. They differ in transport streams (and any file whose streams start late).
        first_sample = float(stream.get('start_time') or 0) - float(info.get('format', {}).get('start_time') or 0)

        def decode(path, mapping, begin=None, length=None):
            seek = [] if begin is None else ['-ss', f'{begin + first_sample:.6f}', '-t', f'{length:.6f}']
            raw = self.run('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-nostdin', *seek, '-i', path, '-map', mapping, '-vn', '-sn', '-dn',
                                      '-ac', '1', '-ar', str(vendor.RATE), '-f', 's16le', '-'])
            if len(raw) < vendor.RATE * 2:
                raise MediaError('Не удалось прочитать звук файла.')
            return np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768.0

        return quick_analysis.quick_analyze(
            vendor, lambda begin, length: decode(ref, f'0:{ref_stream}', begin, length), lambda: decode(target, f'0:{target_stream}', None),
            seconds, window, max_offset)

    @staticmethod
    def track_duration(source, track_id):
        """Length of one track of a probed file; the container's length only when the track has none."""
        track = next((t for t in source['metadata']['tracks'] if t['id'] == str(track_id)), None)
        return float((track or {}).get('duration') or source['metadata']['duration'])

    @staticmethod
    def stream_seconds(stream, info):
        """Length of one stream: its own figure, else that of the picture and sound of the whole file."""
        seconds = Pipeline.own_seconds(stream)
        return seconds if seconds is not None else Pipeline.duration(info)

    def check_tools(self):
        """mkvmerge is first needed at the very last step; find out at once whether it starts on this computer."""
        self.run('mkvmerge', ['--version'])

    def analyze(self, request):
        self.progress('Чтение файлов')
        self.check_tools()
        movie, audio, reference, target = self.validate_input(request)
        if movie['metadata']['duration'] < 8 or self.track_duration(audio, target) < 8:
            raise MediaError('Для сравнения нужен фрагмент длиннее 8 секунд. Выберите более длинный файл.')
        self.progress('Сравнение звука')
        raw = self.analyzer(movie['nativePath'], audio['nativePath'], reference, target, movie['metadata']['duration'])
        result = interpret_analysis(raw)
        # Always verify a speed change on the rendered audio. A weak second pass must
        # never silently apply an automatic correction.
        result['pitchShift'] = False
        if result['verdict'] == 'drift':
            mode = request['settings'].get('pitchMode') or 'auto'
            measured = result['speedCorrection']
            standard = standard_ratio(measured) if mode != 'keep' else None
            # A frame rate conversion has an exact ratio and the measured one is only close to it, so the exact one is
            # tried first. Whatever else is measured is never worse than before: the measured speed stays the fallback.
            candidates = [measured]
            if standard and resolve_pitch(mode, standard[0]) == 'shift':
                candidates.insert(0, standard[0])
            self.progress('Проверка исправленной скорости')
            verified = None
            for speed in candidates:
                pitch, guard = self.render_options(request['settings'], speed)
                fixed = self.cached_audio(audio, target, speed, False, pitch, guard)
                check = self.analyzer(movie['nativePath'], fixed, reference, '0', movie['metadata']['duration'])
                if check.get('verdict') in ('sync', 'offset') and abs((check.get('ratio') or 1) - 1) <= .0002 and (check.get('resid_ms') or 0) <= 80:
                    verified = speed, pitch, check
                    break
                applog.note(f"speed check failed at {speed:.7f} ({pitch}): verdict={check.get('verdict')}, ratio={check.get('ratio')}, residual={check.get('resid_ms')} мс")
            if verified is None:
                result = {**unverified('Не удалось подтвердить совпадение после изменения скорости. Проверьте звук вручную.'), 'pitchShift': False}
            else:
                speed, pitch, check = verified
                result['speedCorrection'] = speed
                result['pitchShift'] = pitch == 'shift'
                result['proposedDelayMs'] = -int(check.get('offset_ms') or 0)
                if pitch == 'shift' and standard:
                    result['evidence'].append(f'Скорость соответствует смене частоты кадров {standard[1]} кадр/с: звук пересчитывается вместе с тоном, без искажений.')
                result['evidence'].append(f"Повторная проверка: остаточное расхождение {check.get('resid_ms', 0):.1f} мс")
        self.progress('Подготовка результата')
        return result

    def resampler(self):
        """FFmpeg's own resampler is fine for small jobs; SoX (soxr) is far more accurate when the build has it."""
        if not hasattr(self, '_resampler'):
            banner = self.run('ffmpeg', ['-hide_banner', '-version']).decode('utf-8', 'replace')
            self._resampler = ':resampler=soxr:precision=28' if '--enable-libsoxr' in banner else ''
        return self._resampler

    def speed_filters(self, speed, pitch, rate, out_rate):
        """Filters that change the speed of a sound and bring it to `out_rate` (None keeps its own rate).

        'shift' relabels the samples with a faster (or slower) rate and resamples back: speed and pitch change
        together, like a tape. 'keep' stretches time with Rubber Band and leaves the pitch alone.
        """
        final = out_rate or rate
        if speed != 1 and pitch == 'shift':
            steps, base = [], rate
            if abs(round(rate * speed) / (rate * speed) - 1) > 1e-6:
                # An integer sample rate cannot express this speed exactly (the error adds up to seconds over a film);
                # a finer grid makes the rounding negligible.
                base = rate * 8
                steps.append(f'aresample={base}{self.resampler()}')
            return [*steps, f'asetrate={round(base * speed)}', f'aresample={final}{self.resampler()}']
        steps = [f'rubberband=tempo={speed:.10f}'] if speed != 1 else []
        if final != rate:
            steps.append(f'aresample={final}{self.resampler()}')
        return steps

    def render_audio(self, path, stream, output, speed, compatibility, pitch='keep', gain=0.0):
        if not math.isfinite(speed) or not .5 <= speed <= 2:
            raise MediaError('Поправка скорости недопустима. Повторите проверку.')
        info = self.info(path)
        selected = next((s for s in self.audio_streams(info) if str(s['index']) == str(stream)), None)
        if selected is None:
            raise MediaError('Выбранная звуковая дорожка не найдена. Выберите файл и дорожку заново.')
        if speed != 1 and pitch == 'keep':
            filters = self.run('ffmpeg', ['-hide_banner', '-filters']).decode('utf-8', 'replace')
            if 'rubberband' not in filters:
                raise MediaError('В этой сборке недоступно исправление скорости. Подключите FFmpeg с Rubber Band.', 'Required audio filter: rubberband')
        channels = int(selected.get('channels', 2))
        if compatibility and channels > 6:
            raise MediaError('В озвучке больше шести каналов. Для сохранения всех каналов выберите «Как в исходной озвучке».')
        encoder, _, cap = self.speed_output(selected.get('codec_name', ''), channels, compatibility)
        rate = int(selected.get('sample_rate') or 48000)
        out_rate = 48000 if encoder in ('ac3', 'eac3') and cap is not None else None
        if cap is None:
            encode = ['-c:a', 'flac']
        else:
            # A lossy source stays lossy at its own bitrate: FLAC of a 5.1 AC3 is about ten times larger.
            bitrate = cap if compatibility else min(int(selected.get('bit_rate') or cap), cap)
            encode = ['-c:a', encoder, '-b:a', str(bitrate)]
        duration = self.stream_seconds(selected, info)
        gain_filter = [f'volume={gain}dB'] if gain else []
        if pitch == 'keep' and tempo.will_parallelize(duration, speed):
            # The chunks are stretched in parallel; rate conversion and gain are applied once to the joined sound.
            tail = ','.join([*self.speed_filters(1, pitch, rate, out_rate), *gain_filter])
            tempo.render(self.config['ffmpeg'], path, stream, speed, duration, output, encode, self.work, self.progress, MediaError, tail)
            return
        chain = [*self.speed_filters(speed, pitch, rate, out_rate), *gain_filter]
        args = [*tempo.DECODE, '-i', path, '-map', f'0:{stream}', '-vn', '-sn', '-dn', *(['-af', ','.join(chain)] if chain else [])]
        self.ffmpeg([*args, *encode, '-n', output], label='Подготовка звука', duration=duration / speed)

    def render_options(self, settings, speed):
        """(pitch, guard) of a corrected sound: how its speed is changed and whether it is protected from clipping."""
        mode = settings.get('pitchMode') or 'auto'
        return resolve_pitch(mode if mode in PITCH_MODES else 'auto', speed), bool(settings.get('limitPeak'))

    def peak_db(self, path, stream):
        """Loudest sample of a track in dB (0 is full scale), or None when it cannot be measured."""
        process = subprocess.run([self.tool_path('ffmpeg'), '-hide_banner', '-nostdin', '-i', str(path), '-map', f'0:{stream}', '-vn', '-sn', '-dn',
                                  '-af', 'volumedetect', '-f', 'null', '-'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
        match = re.search(rb'max_volume:\s*(-?[\d.]+|-inf)\s*dB', process.stderr)
        return float(match.group(1)) if process.returncode == 0 and match else None

    # Source codec -> (encoder, cache extension, bitrate cap, max channels) for a corrected sound that stays lossy.
    LOSSY_SPEED = {'ac3': ('ac3', 'ac3', 640000, 6), 'eac3': ('eac3', 'eac3', 1024000, 6),
                   'dts': ('eac3', 'eac3', 1024000, 6), 'aac': ('aac', 'mka', 512000, 8)}

    @classmethod
    def speed_output(cls, codec, channels, compatibility):
        """(encoder, extension, bitrate cap) of a recalculated sound; the cap is None for lossless FLAC."""
        if compatibility:
            return 'eac3', 'eac3', 448000 if channels <= 2 else 1024000
        entry = cls.LOSSY_SPEED.get(codec)
        if entry and 0 < channels <= entry[3]:
            return entry[:3]
        return 'flac', 'flac', None

    @classmethod
    def track_output(cls, audio, stream, compatibility):
        selected = next(t for t in audio['metadata']['tracks'] if t['id'] == stream)
        return cls.speed_output(selected.get('codec', ''), int(selected.get('channels') or 2), compatibility)

    @classmethod
    def audio_estimate(cls, audio, stream, speed, compatibility, pitch='keep'):
        selected = next(t for t in audio['metadata']['tracks'] if t['id'] == stream)
        track_seconds = float(selected.get('duration') or audio['metadata']['duration'])
        seconds = track_seconds / min(speed, 1)
        lossless = int(seconds * max(1, selected['channels']) * selected.get('sampleRate', 48000) * 4 * 1.1)
        cap = cls.track_output(audio, stream, compatibility)[2]
        if cap is not None:
            encoded = int(seconds * cap / 8 * 1.1)
            # Parallel rendering keeps lossless chunks next to the final file.
            return encoded + lossless if pitch == 'keep' and tempo.will_parallelize(track_seconds, speed) else encoded
        return lossless

    def cached_audio_path(self, audio, stream, speed, compatibility, pitch='keep', guard=False):
        extension = self.track_output(audio, stream, compatibility)[1]
        key = hashlib.sha256(json.dumps([audio['id'], stream, speed, compatibility, extension, AUDIO_RENDER_VERSION, pitch, guard]).encode()).hexdigest()
        return self.cache / f'audio-{key}.{extension}'

    def cached_audio(self, audio, stream, speed, compatibility, pitch='keep', guard=False):
        """Reuse the full corrected timeline across scenes and the final build.

        With `guard` a track that reaches full scale is rendered again 3 dB quieter instead of being clipped.
        """
        output = self.cached_audio_path(audio, stream, speed, compatibility, pitch, guard)
        if output.exists():
            return output
        required = self.audio_estimate(audio, stream, speed, compatibility, pitch) + 64 * 1024**2
        if shutil.disk_usage(self.cache).free < required:
            raise MediaError('Недостаточно места для временного звука. Освободите место на диске программы.')
        with tempfile.TemporaryDirectory(prefix='audio-', dir=self.work) as work:
            temporary = Path(work) / output.name
            self.render_audio(audio['nativePath'], stream, temporary, speed, compatibility, pitch)
            if guard:
                self.progress('Проверка громкости')
                peak = self.peak_db(temporary, '0')
                if peak is not None and peak >= CLIP_DB:
                    applog.note(f'clipping guard: the corrected sound peaks at {peak} dB, rendered again {-GUARD_GAIN_DB:g} dB quieter')
                    temporary.unlink()
                    self.render_audio(audio['nativePath'], stream, temporary, speed, compatibility, pitch, GUARD_GAIN_DB)
            try:
                temporary.replace(output)
            except PermissionError:
                # Another job may have published and opened the same cache entry.
                if not output.exists():
                    raise
        return output

    def preview(self, request):
        movie, audio, reference, target = self.validate_input(request)
        duration = movie['metadata']['duration']
        position = float(request['preview']['playbackPosition'])
        if not math.isfinite(position):
            raise MediaError('Выберите другую позицию предпросмотра.')
        position = max(0, min(position, max(0, duration - 1)))
        length = min(20, duration - position)
        adjustment = request['adjustment']
        variant = request['preview']['audioVariant']
        effective_delay = self.effective_delay(request, audio, target)
        # Only the corrected variant depends on correction and compatibility settings.
        transfer = self.video_transfer(movie['nativePath'])
        tonemapped = transfer in HDR_TRANSFERS
        identity = [movie['id'], reference, variant, position, PREVIEW_WIDTH, PREVIEW_HEIGHT, TONEMAP_VERSION if tonemapped else 'sdr']
        if variant != 'film':
            identity += [audio['id'], target]
        if variant == 'corrected':
            identity += [adjustment, request['settings']['compatibility'], AUDIO_RENDER_VERSION, *self.render_options(request['settings'], adjustment['speedCorrection'])]
        key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
        output = self.cache / f'{key}.mp4'
        if output.exists():
            return {'path': str(output), 'start': position, 'duration': length, 'effectiveDelayMs': effective_delay}
        self.progress('Подготовка фрагмента')
        with tempfile.TemporaryDirectory(prefix='preview-', dir=self.work) as folder:
            intermediate = Path(folder) / 'clip.mp4'
            # Full audio timeline is rendered before selecting the scene. This preserves
            # absolute-time mapping, silence padding and cuts after speed correction.
            if variant == 'film':
                # The film's own sound needs no shift, speed change or padding. Take it from the same
                # seeked input as the picture; a second unseeked input would make FFmpeg read the whole
                # multi-gigabyte file up to the scene (tens of seconds).
                inputs = ['-ss', str(position), *tempo.DECODE, '-i', movie['nativePath']]
                audio_filter = f'[0:{reference}]asetpts=PTS-STARTPTS,apad,atrim=duration={length:.6f},asetpts=PTS-STARTPTS[a]'
            else:
                path, stream = audio['nativePath'], target
                speed = adjustment['speedCorrection'] if variant == 'corrected' else 1
                delay = effective_delay if variant == 'corrected' else 0
                if speed != 1 or (variant == 'corrected' and request['settings']['compatibility']):
                    path = str(self.cached_audio(audio, target, speed, request['settings']['compatibility'], *self.render_options(request['settings'], speed)))
                    stream = '0'
                correction = f'adelay={delay}:all=1' if delay >= 0 else f'atrim=start={-delay / 1000:.6f},asetpts=PTS-STARTPTS'
                inputs = ['-ss', str(position), '-i', movie['nativePath'], *tempo.DECODE, '-i', path]
                audio_filter = f'[1:{stream}]asetpts=PTS-STARTPTS,{correction},apad,atrim=start={position:.6f}:duration={length:.6f},asetpts=PTS-STARTPTS[a]'
            self.ffmpeg([*inputs,
                         '-filter_complex', audio_filter, '-map', '0:v:0', '-map', '[a]', '-t', str(length),
                         '-vf', self.preview_video_filter(transfer),
                         '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '23', '-pix_fmt', 'yuv420p',
                         '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-movflags', '+faststart', '-n', intermediate], label='Подготовка фрагмента', duration=length)
            intermediate.replace(output)
        return {'path': str(output), 'start': position, 'duration': length, 'effectiveDelayMs': effective_delay}

    def effective_delay(self, request, audio, target):
        a = request['adjustment']
        delay = float(a['automaticDelayMs']) + float(a['manualDelayMs'])
        if not math.isfinite(delay) or abs(delay) > 600000:
            raise MediaError('Сдвиг должен быть в пределах 10 минут.')
        codec = next(s['codec'] for s in audio['metadata']['tracks'] if s['id'] == target)
        recalculated = request['settings']['compatibility'] or a['speedCorrection'] != 1
        if recalculated:
            codec = self.track_output(audio, target, request['settings']['compatibility'])[0]
        # mkvmerge trims negative AC3/E-AC3 shifts in complete 1536-sample frames.
        # Use the same effective shift for preview and mux instead of a finer preview.
        if delay < 0 and codec in ('ac3', 'eac3'):
            info = self.info(audio['nativePath'])
            selected = next(s for s in info['streams'] if str(s['index']) == target)
            rate = 48000 if recalculated else int(selected.get('sample_rate', 48000))
            frame = 1536 / rate * 1000
            delay = -math.floor(abs(delay) / frame) * frame
        return int(round(delay))

    def build(self, request):
        self.progress('Проверка файлов и свободного места')
        movie, audio, reference, target = self.validate_input(request)
        result = request.get('analysis') or {}
        if result.get('verdict') not in ('aligned', 'offset', 'drift') and not request.get('allowUnverified'):
            raise MediaError('Совпадение звука не подтверждено. Проверьте фрагменты и подтвердите сохранение.')
        settings, adjustment = request['settings'], request['adjustment']
        folder = Path(request.get('outputDirectory') or Path(movie['nativePath']).parent).resolve(strict=True)
        name = request['outputName']
        if not name.lower().endswith('.mkv') or Path(name).name != name or any(c in name for c in '<>:"/\\|?*') or name.endswith((' ', '.')):
            raise MediaError('Укажите имя результата с окончанием .mkv, без символов пути.')
        output = folder / name
        self.check_output(output, movie, audio, request.get('overwriteToken'))
        speed = float(adjustment['speedCorrection'])
        changed = speed != 1 or settings['compatibility']
        pitch, guard = self.render_options(settings, speed)
        delay = self.effective_delay(request, audio, target)
        duration = movie['metadata']['duration']
        # FLAC encoding may approach raw 32-bit PCM; reserve a conservative amount.
        # A sound prepared during the analysis is reused as it is: it needs no new cache space and its real
        # size is known. Asking for space to render it again would block a save that can in fact proceed.
        prepared = self.cached_audio_path(audio, target, speed, settings['compatibility'], pitch, guard) if changed else None
        reuse = prepared is not None and prepared.exists()
        temp_audio = prepared.stat().st_size if reuse else self.audio_estimate(audio, target, speed, settings['compatibility'], pitch) if changed else 0
        estimate = movie['size'] + audio['size'] * 2 + temp_audio + 64 * 1024**2
        if shutil.disk_usage(folder).free < estimate:
            raise MediaError(f'Недостаточно места. Освободите примерно {math.ceil(estimate / 1024**3)} ГБ или выберите другую папку.')
        if changed and not reuse and shutil.disk_usage(self.cache).free < temp_audio + 64 * 1024**2:
            raise MediaError('Недостаточно места для временного звука. Освободите место на диске программы.')
        warnings = []
        with tempfile.TemporaryDirectory(prefix='.soundmux-', dir=self.config.get('buildWork', folder)) as work:
            work = Path(work)
            fixed, fixed_stream = audio['nativePath'], target
            if changed:
                self.progress('Подготовка звука')
                fixed = self.cached_audio(audio, target, speed, settings['compatibility'], pitch, guard)
                fixed_stream = '0'
                if not guard:
                    self.progress('Проверка громкости')
                    peak = self.peak_db(fixed, '0')
                    if peak is not None and peak >= CLIP_DB:
                        warnings.append('Звук упирается в максимальную громкость, возможны щелчки (клиппинг). '
                                        'В «Дополнительно» можно включить защиту: громкость будет снижена на 3 дБ.')
                        applog.note(f'clipping: the corrected sound peaks at {peak} dB')
            movie_json = json.loads(self.run('mkvmerge', ['-J', movie['nativePath']], accepted=(0, 1)))
            audio_json = json.loads(self.run('mkvmerge', ['-J', audio['nativePath']], accepted=(0, 1)))
            movie_tracks = [t for t in movie_json['tracks'] if t['type'] == 'audio']
            skipped_audio = {str(i) for i in settings.get('skipAudio') or []}
            if skipped_audio and settings['keepMovieAudio']:
                probed_audio = [s['id'] for s in movie['metadata']['tracks']]
                if len(movie_tracks) != len(probed_audio):
                    raise MediaError('Не удалось сопоставить звук фильма. Оставьте все дорожки или выберите файл заново.')
                movie_tracks = [t for t, probed in zip(movie_tracks, probed_audio) if probed not in skipped_audio]
            # ffprobe indices and mkvmerge IDs are different namespaces; map by audio ordinal.
            audio_ids = [s['id'] for s in audio['metadata']['tracks']]
            external_tracks = [t for t in audio_json['tracks'] if t['type'] == 'audio']
            native_target = external_tracks[audio_ids.index(target)]['id']
            fixed_id = 0 if changed else native_target
            temporary = work / 'result.mkv'
            flag = self.default_flag_option()
            args = ['--ui-language', 'en', '--gui-mode', '-o', str(temporary)]
            if settings['makeDefault']:
                for t in movie_tracks:
                    args += [flag, f"{t['id']}:no"]
            if not settings['keepMovieAudio'] or not movie_tracks:
                args += ['--no-audio']
            elif skipped_audio:
                args += ['--audio-tracks', ','.join(str(t['id']) for t in movie_tracks)]
            # ffprobe indices and mkvmerge IDs are different namespaces; map subtitles by ordinal as well.
            movie_subtitles = [t for t in movie_json['tracks'] if t['type'] == 'subtitles']
            probed_subtitles = [s['id'] for s in movie['metadata']['subtitles']]
            skipped = {str(i) for i in settings.get('skipSubtitles') or []}
            if skipped:
                if len(movie_subtitles) != len(probed_subtitles):
                    raise MediaError('Не удалось сопоставить субтитры фильма. Оставьте все субтитры или выберите файл заново.')
                movie_subtitles = [t for t, probed in zip(movie_subtitles, probed_subtitles) if probed not in skipped]
                args += ['--subtitle-tracks', ','.join(str(t['id']) for t in movie_subtitles)] if movie_subtitles else ['--no-subtitles']
            args += [movie['nativePath']]
            language = {'Русский': 'rus', 'Английский': 'eng'}.get(settings['language'], 'und')
            args += ['--no-video', '--no-subtitles', '--no-chapters', '--no-attachments', '--no-global-tags', '--no-track-tags',
                     '--audio-tracks', str(fixed_id), '--language', f'{fixed_id}:{language}', '--track-name', f"{fixed_id}:{settings['title']}",
                     flag, f"{fixed_id}:{'yes' if settings['makeDefault'] else 'no'}",
                     '--sync', f'{fixed_id}:{delay}', str(fixed)]
            add_original = settings['keepOriginal'] and (changed or delay != 0)
            if add_original:
                args += ['--no-video', '--no-subtitles', '--no-chapters', '--no-attachments', '--no-global-tags', '--no-track-tags',
                         '--audio-tracks', str(native_target), '--language', f'{native_target}:{language}',
                         '--track-name', f"{native_target}:{settings['title']} (без исправлений)",
                         flag, f'{native_target}:no', audio['nativePath']]
            order = [f"0:{t['id']}" for t in movie_json['tracks'] if t['type'] == 'video'] + [f'1:{fixed_id}']
            if add_original:
                order += [f'2:{native_target}']
            kept_subtitles = {t['id'] for t in movie_subtitles}
            kept_audio = {t['id'] for t in movie_tracks}
            order += [f"0:{t['id']}" for t in movie_json['tracks'] if t['type'] != 'video' and (t['type'] != 'audio' or (settings['keepMovieAudio'] and t['id'] in kept_audio)) and (t['type'] != 'subtitles' or t['id'] in kept_subtitles)]
            args += ['--track-order', ','.join(order)]
            self.progress('Сборка файла')
            self.run_progress('mkvmerge', args, 'Сборка файла', accepted=(0, 1))
            self.progress('Проверка результата')
            check = json.loads(self.run('mkvmerge', ['-J', temporary], accepted=(0, 1)))
            expected_audio = (len(movie_tracks) if settings['keepMovieAudio'] else 0) + 1 + int(add_original)
            actual_audio = [t for t in check['tracks'] if t['type'] == 'audio']
            if len(actual_audio) != expected_audio or (settings['makeDefault'] and sum(bool(t['properties'].get('default_track')) for t in actual_audio) != 1):
                raise MediaError('Результат не прошёл проверку звука. Исходные файлы сохранены.')
            unchanged = ['video', 'subtitles', 'buttons']
            for kind in unchanged:
                before = [t for t in movie_json['tracks'] if t['type'] == kind and (kind != 'subtitles' or t['id'] in kept_subtitles)]
                after = [t for t in check['tracks'] if t['type'] == kind]
                if len(before) != len(after):
                    raise MediaError('Результат не прошёл проверку состава. Исходные файлы сохранены.')
                if kind == 'subtitles' and [self.subtitle_flags(t) for t in before] != [self.subtitle_flags(t) for t in after]:
                    raise MediaError('Не удалось сохранить настройки субтитров. Исходные файлы сохранены.')
            if len(movie_json.get('attachments', [])) != len(check.get('attachments', [])) or movie_json.get('chapters', []) != check.get('chapters', []):
                raise MediaError('Не удалось сохранить главы или вложения. Исходные файлы сохранены.')
            measured = self.duration(self.info(temporary))
            track_seconds = self.track_duration(audio, target)
            expected_duration = max(duration, track_seconds / speed + delay / 1000, track_seconds if add_original else 0)
            # Only a result that is too short means something was cut off. A longer one is normal: subtitle tracks
            # (often timed for another cut) can stretch the container beyond the picture and sound.
            applog.note(f'duration check: result {measured:.1f} s, expected {expected_duration:.1f} s (film {duration:.1f} s)')
            shortfall = duration_shortfall(measured, expected_duration, duration)
            if shortfall == 'fail':
                raise MediaError(f'Длительность результата не прошла проверку: получилось {measured:.0f} с, ожидалось около {expected_duration:.0f} с. Исходные файлы сохранены.')
            if shortfall == 'warn':
                warnings.append(f'Результат короче ожидаемого на {expected_duration - measured:.0f} с. Скорее всего, в исходном файле неточные длительности дорожек; проверьте конец фильма.')
            self.ffmpeg(['-sseof', '-15', '-i', temporary, '-map', '0', '-c', 'copy', '-f', 'null', '-'])
            self.check_output(output, movie, audio, request.get('overwriteToken'))
            if request.get('overwriteToken') and output.exists():
                # Replace only after successful verification; the previous result stays
                # intact throughout preparation, cancellation and failed verification.
                os.replace(temporary, output)
            else:
                try:
                    if os.name == 'nt':
                        os.rename(temporary, output)
                    else:
                        os.link(temporary, output)
                        temporary.unlink()
                except FileExistsError:
                    self.check_output(output, movie, audio, None)
        return {'path': str(output), 'effectiveDelayMs': delay, 'estimatedBytes': estimate, 'warnings': warnings}

    @staticmethod
    def output_token(path):
        stat = path.stat()
        return hashlib.sha256(f'{path.resolve()}:{stat.st_dev}:{stat.st_ino}:{stat.st_size}:{stat.st_mtime_ns}'.encode()).hexdigest()

    def check_output(self, output, movie, audio, token):
        sources = [Path(movie['nativePath']), Path(audio['nativePath'])]
        if output.resolve() in sources or (output.exists() and any(os.path.samefile(output, source) for source in sources)):
            raise MediaError('Нельзя перезаписать исходный фильм или озвучку. Укажите другое имя результата.')
        if output.exists():
            if not output.is_file() or output.is_symlink():
                raise MediaError('Это имя занято папкой или ссылкой. Укажите другое имя результата.')
            current = self.output_token(output)
            if token != current:
                raise MediaError('Файл с таким именем уже существует. Можно изменить имя или подтвердить перезапись.',
                                 code='output-exists', context={'path': str(output), 'token': current})


def duration_shortfall(measured, expected, film):
    """None when the result is long enough, 'warn' for a small gap, 'fail' when the result is cut off.

    Track lengths written into a source file are often a few seconds off, and a finished build is not thrown
    away for that: only a gap beyond 2% of the film means the result is really incomplete.
    """
    gap = expected - measured
    if gap <= max(3, film * .001):
        return None
    return 'fail' if gap > max(30, film * .02) else 'warn'


def unverified(detail):
    return {'verdict': 'uncertain', 'proposedDelayMs': 0, 'speedCorrection': 1, 'explanation': 'Не удалось надёжно сопоставить звук', 'evidence': [detail], 'demo': False}


def interpret_analysis(raw):
    verdict = raw.get('verdict')
    if verdict not in ('sync', 'offset', 'drift', 'inconsistent'):
        return unverified('Недостаточно общих звуков для автоматического исправления.')
    ratio = float(raw.get('ratio') or 1)
    offset = float(raw.get('offset_ms') or 0)
    if not math.isfinite(ratio) or not math.isfinite(offset) or not .5 <= ratio <= 2:
        return unverified('Анализ вернул недопустимые параметры.')
    if verdict == 'drift':
        reference = float(raw.get('ref_duration') or 0)
        target = float(raw.get('target_duration') or 0)
        if reference <= 0 or abs(ratio - target / reference) > .002 + abs(offset) / 1000 / reference:
            return unverified('Предложенная скорость не согласуется с длительностями. Автоматическое исправление отклонено.')
    if (raw.get('resid_ms') or 0) > 80:
        verdict = 'inconsistent'
    mapped = {'sync': 'aligned', 'offset': 'offset', 'drift': 'drift', 'inconsistent': 'different-edit'}[verdict]
    seconds = f'{abs(offset) / 1000:.2f}'.rstrip('0').rstrip('.').replace('.', ',')
    texts = {'aligned': 'Сдвиг не обнаружен', 'offset': f"Озвучка звучит {'позже' if offset > 0 else 'раньше'} на {seconds} с", 'drift': 'Скорость озвучки отличается. К концу фильма звук расходится', 'different-edit': 'В разных частях фильма требуется разный сдвиг'}
    evidence = [f"Коэффициент скорости: {ratio:.7f}", f"Остаточное расхождение: {raw.get('resid_ms') or 0:.1f} мс"]
    return {'verdict': mapped, 'proposedDelayMs': -round(offset) if mapped in ('offset', 'drift') else 0,
            'speedCorrection': ratio if mapped == 'drift' else 1, 'explanation': texts[mapped], 'evidence': evidence, 'demo': False}
