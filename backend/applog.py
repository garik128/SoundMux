"""Service log: what a person needs to read to understand what went wrong on someone else's computer.

One short line per event (the start and the end of a job, errors with the command that failed, a few notable
decisions), never the media data. Files are capped: `soundmux.log` is closed at MAX_BYTES and renamed to
`soundmux.1.log`, then `soundmux.2.log`; the oldest is deleted, so the folder never holds more than about 750 KB.
Writing must never break a job: every failure here is swallowed. Rust writes into the same files (src-tauri/src/applog.rs),
both sides use UTC time and the same rotation.
"""
from __future__ import annotations

import os
import platform
import subprocess
import time
from pathlib import Path

MAX_BYTES = 256 * 1024
KEEP = 3  # soundmux.log, soundmux.1.log, soundmux.2.log
DETAIL_LIMIT = 4000
NAME = 'soundmux'
CREATE_NO_WINDOW = 0x08000000 if os.name == 'nt' else 0

_config: dict = {}


def configure(config):
    """Remember where the log lives and which program version writes it (both come from the Rust side)."""
    global _config
    _config = config or {}


def redact(text):
    """The user's profile folder is replaced, so a log can be shared without the account name in it."""
    for home in {os.environ.get('USERPROFILE') or '', str(Path.home())}:
        if len(home) > 3:
            # the plain path, the forward-slash form and the doubled-backslash form that repr() prints
            for variant in (home, home.replace('\\', '/'), home.replace('\\', '\\\\')):
                text = text.replace(variant, '%USERPROFILE%').replace(variant.lower(), '%USERPROFILE%').replace(variant.upper(), '%USERPROFILE%')
    return text


def log_file(directory):
    return Path(directory) / f'{NAME}.log'


def rotate(path, max_bytes=MAX_BYTES, keep=KEEP):
    """Shift the numbered files by one when the current file is full."""
    if not path.exists() or path.stat().st_size < max_bytes:
        return False
    stem = path.with_suffix('')
    last = stem.with_name(f'{stem.name}.{keep - 1}.log')
    last.unlink(missing_ok=True)
    for number in range(keep - 2, 0, -1):
        older = stem.with_name(f'{stem.name}.{number}.log')
        if older.exists():
            older.replace(stem.with_name(f'{stem.name}.{number + 1}.log'))
    path.replace(stem.with_name(f'{stem.name}.1.log'))
    return True


def _first_line(command):
    try:
        done = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=15, creationflags=CREATE_NO_WINDOW)
        return (done.stdout.decode('utf-8', 'replace').splitlines() or [''])[0][:160]
    except Exception:
        return 'не удалось определить'


def header():
    """What identifies the installation; written at the top of every new log file."""
    lines = [f"SoundMux {_config.get('version', '?')} · {platform.platform()} · Python {platform.python_version()}"]
    for key in ('ffmpeg', 'mkvmerge'):
        if _config.get(key):
            lines.append(f'{key}: {_first_line([str(_config[key]), "-version" if key == "ffmpeg" else "--version"])}')
    return lines


def write(level, text, detail=''):
    """Append one event. `detail` (a command line, its last output) is cut to DETAIL_LIMIT characters and indented."""
    try:
        directory = _config.get('logDir')
        if not directory:
            return
        Path(directory).mkdir(parents=True, exist_ok=True)
        path = log_file(directory)
        rotate(path, MAX_BYTES)
        stamp = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        lines = []
        if not path.exists() or path.stat().st_size == 0:
            lines += [f'{stamp} [INFO] {line}' for line in header()]
        lines.append(f'{stamp} [{level}] {text}')
        if detail:
            shown = detail if len(detail) <= DETAIL_LIMIT else '...' + detail[-DETAIL_LIMIT:]
            lines += ['    ' + line for line in shown.splitlines()]
        with path.open('a', encoding='utf-8') as stream:
            stream.write(redact('\n'.join(lines)) + '\n')
    except Exception:
        pass


def note(text):
    write('INFO', text)


def describe(payload):
    """The files and settings of a request in a few words (names only, never full paths)."""
    parts = []
    for key in ('movie', 'audio'):
        item = payload.get(key)
        if isinstance(item, dict):
            meta = item.get('metadata') or {}
            tracks = ', '.join(f"{t.get('codec')} {t.get('channels')}ch" for t in meta.get('tracks', [])[:6])
            parts.append(f"{key}: {item.get('name')} ({item.get('size', 0) / 1024**2:.0f} МБ, {meta.get('duration', 0):.0f} с; {tracks})")
    if 'path' in payload:
        parts.append(f"file: {Path(str(payload['path'])).name} ({payload.get('kind')})")
    settings = payload.get('settings') or {}
    keys = ('referenceTrack', 'externalTrack', 'pitchMode', 'limitPeak', 'compatibility', 'keepMovieAudio', 'keepOriginal')
    shown = {k: settings[k] for k in keys if k in settings}
    if shown:
        parts.append('settings: ' + ', '.join(f'{k}={v}' for k, v in shown.items()))
    adjustment = payload.get('adjustment')
    if adjustment:
        parts.append(f"adjustment: delay={adjustment.get('automaticDelayMs', 0)}+{adjustment.get('manualDelayMs', 0)} мс, speed={adjustment.get('speedCorrection', 1)}")
    if payload.get('outputName'):
        parts.append(f"output: {payload['outputName']}")
    return '; '.join(parts)


def summarize(action, value):
    """The result of a job in a few words."""
    if not isinstance(value, dict):
        return ''
    if action == 'analyze':
        return f"verdict={value.get('verdict')}, delay={value.get('proposedDelayMs')} мс, speed={value.get('speedCorrection')}, pitchShift={value.get('pitchShift')}; " + ' | '.join(value.get('evidence', []))
    if action == 'build':
        warnings = ' | '.join(value.get('warnings') or [])
        return f"{Path(str(value.get('path', ''))).name}, delay={value.get('effectiveDelayMs')} мс" + (f'; warnings: {warnings}' if warnings else '')
    if action == 'probe':
        meta = value.get('metadata') or {}
        return f"{value.get('name')}: {meta.get('duration', 0):.0f} с, " + ', '.join(f"{t.get('codec')} {t.get('channels')}ch" for t in meta.get('tracks', []))
    return ''
