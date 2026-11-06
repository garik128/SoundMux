"""Line-delimited JSON protocol. Rust owns this process and every descendant."""
import json
import sys
import time
import traceback
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from media import Pipeline, MediaError
import applog

# Rust writes UTF-8 bytes. Windows otherwise decodes piped stdin using the active
# ANSI code page (e.g. cp1251), corrupting Cyrillic paths before probe sees them.
sys.stdin.reconfigure(encoding='utf-8')
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')


def emit(event):
    print(json.dumps(event, ensure_ascii=True), flush=True)


def main():
    # Wait for Rust to assign the Windows Job Object before spawning descendants.
    request = json.loads(sys.stdin.readline())
    action = request.get('action', '?')
    started = time.monotonic()
    try:
        applog.configure(request['config'])
        pipeline = Pipeline(request['config'], emit)
        payload = request['payload']
        # A preview is made every time the scene or a setting changes: only its failures are worth a line.
        if action != 'preview':
            applog.note(f'{action}: start. {applog.describe(payload)}')
        if action == 'probe':
            value = pipeline.probe(payload['path'], payload['kind'])
        elif action == 'analyze':
            value = pipeline.analyze(payload)
        elif action == 'preview':
            value = pipeline.preview(payload)
        elif action == 'build':
            value = pipeline.build(payload)
        else:
            raise MediaError('Неизвестное действие. Перезапустите программу.')
        if action != 'preview':
            applog.note(f'{action}: done in {time.monotonic() - started:.1f} s. {applog.summarize(action, value)}')
        emit({'type': 'result', 'value': value})
    except Exception as error:
        applog.write('ERROR', f'{action}: failed after {time.monotonic() - started:.1f} s. {error if isinstance(error, MediaError) else type(error).__name__}',
                     error.detail if isinstance(error, MediaError) else traceback.format_exc())
        emit({'type': 'error', 'message': str(error) if isinstance(error, MediaError) else 'Не удалось выполнить операцию. Проверьте файлы и повторите попытку.',
              'detail': error.detail if isinstance(error, MediaError) else traceback.format_exc(),
              'code': error.code if isinstance(error, MediaError) else None,
              'context': error.context if isinstance(error, MediaError) else None})
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
