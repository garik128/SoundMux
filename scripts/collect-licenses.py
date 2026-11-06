"""Collect third-party license notices for the portable package.

Usage: collect-licenses.py <cargo-metadata.json> <project-root> <output.txt>

Covers the Rust crates compiled into SoundMux.exe and the JavaScript packages bundled into the
interface. FFmpeg, MKVToolNix and Python ship their own license files separately.
"""
import hashlib
import json
from pathlib import Path
import re
import sys

LICENSE_FILE = re.compile(r'^(licen[cs]e|copying|unlicense|notice)', re.I)
FORBIDDEN = re.compile(r'GPL', re.I)  # a GPL dependency would change how the app itself may be distributed


def license_files(folder):
    return [p for p in sorted(folder.iterdir()) if p.is_file() and LICENSE_FILE.match(p.name)] if folder.is_dir() else []


def main(metadata_path, root, output):
    root = Path(root)
    metadata = json.loads(Path(metadata_path).read_text(encoding='utf-8-sig'))
    components = []  # (kind, name, version, license, url, [files])
    own = {package['id'] for package in metadata['packages'] if Path(package['manifest_path']).parent == root / 'src-tauri'}
    for package in sorted(metadata['packages'], key=lambda p: (p['name'], p['version'])):
        if package['id'] in own:
            continue
        components.append(('Rust', package['name'], package['version'], package.get('license') or 'see files', package.get('repository') or package.get('homepage') or '',
                           license_files(Path(package['manifest_path']).parent)))
    lock = json.loads((root / 'package-lock.json').read_text(encoding='utf-8'))
    for path, entry in sorted(lock['packages'].items()):
        if not path or entry.get('dev') or not path.startswith('node_modules/'):
            continue
        folder = root / path
        info = json.loads((folder / 'package.json').read_text(encoding='utf-8'))
        repository = info.get('repository')
        url = repository.get('url') if isinstance(repository, dict) else repository or info.get('homepage', '')
        components.append(('JavaScript', info['name'], info['version'], info.get('license') or entry.get('license') or 'see files', url or '', license_files(folder)))
    problems = [c for c in components if FORBIDDEN.search(str(c[3]))]
    if problems:
        raise SystemExit('GPL-licensed dependency found: ' + ', '.join(f'{c[1]} ({c[3]})' for c in problems))
    missing = [c for c in components if not c[5]]

    texts = {}
    for kind, name, version, _license, _url, files in components:
        for file in files:
            data = file.read_bytes().decode('utf-8', 'replace').strip()
            digest = hashlib.sha256(re.sub(r'\s+', ' ', data).encode()).hexdigest()
            texts.setdefault(digest, [data, []])[1].append(f'{name} {version}')

    lines = ['THIRD-PARTY NOTICES FOR SOUNDMUX', '',
             'SoundMux includes the open-source components below. Each remains under its own license.',
             'Components under MPL-2.0 are used unmodified; their source is available from the repository listed for them.',
             'FFmpeg, MKVToolNix (GPL), Python and NumPy come with their own license files in this folder or inside runtime/.', '',
             f'COMPONENTS ({len(components)})', '']
    for kind, name, version, license_name, url, files in components:
        lines.append(f'{kind}: {name} {version} | {license_name}' + (f' | {url}' if url else ''))
    if missing:
        lines += ['', 'No separate license file shipped in the package source (license named above): ' + ', '.join(f'{c[1]} {c[2]}' for c in missing)]
    lines += ['', f'LICENSE TEXTS ({len(texts)} distinct)', '']
    for data, users in sorted(texts.values(), key=lambda item: item[1][0]):
        lines += ['=' * 78, 'Used by: ' + ', '.join(sorted(set(users))), '=' * 78, data, '']
    Path(output).write_text('\n'.join(lines), encoding='utf-8')
    print(f'{len(components)} components, {len(texts)} distinct license texts, {len(missing)} without a file -> {output}')


main(*sys.argv[1:4])
