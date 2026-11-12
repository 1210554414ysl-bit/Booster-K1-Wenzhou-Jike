"""Extend an existing signed online bundle with this independent Agent's sources."""

import ast
import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
OLD = 'com_wenzhoujike_k1_onlinemusic'
NEW = 'com_wenzhoujike_k1_defaultmusic'


def write(bundle, name, data, mode=0o644):
    for parent in reversed(PurePosixPath(name).parents):
        if parent == PurePosixPath('.'):
            continue
        path = parent.as_posix() + '/'
        if path not in bundle.NameToInfo:
            entry = zipfile.ZipInfo(path)
            entry.create_system = 3
            entry.external_attr = ((stat.S_IFDIR | 0o755) << 16) | 0x10
            bundle.writestr(entry, b'')
    entry = zipfile.ZipInfo(name)
    entry.create_system = 3
    entry.external_attr = (stat.S_IFREG | mode) << 16
    entry.compress_type = zipfile.ZIP_DEFLATED
    bundle.writestr(entry, data)


def main():
    template = ROOT.parent / 'online-music/com.wenzhoujike.k1.onlinemusic-0.1.2.agent'
    if hashlib.sha256(template.read_bytes()).hexdigest() != '4d423d21a36e3324efb0285e3ec18a389f7e4aa057d636e37cae27700a663eb3':
        raise ValueError('Unexpected online runtime template')
    manifest = json.loads((ROOT / 'agent.json').read_text(encoding='utf8'))
    assert manifest['id'] != 'com.boosterobotics.default'
    for path in (ROOT / 'src').glob('*.py'):
        ast.parse(path.read_text(encoding='utf8'))
    panel = ROOT / 'res/music_panel.zip'
    with zipfile.ZipFile(panel, 'w', zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted((ROOT / 'web').rglob('*')):
            if path.is_file():
                write(bundle, 'dist/' + path.relative_to(ROOT / 'web').as_posix(), path.read_bytes())
    output = ROOT / f"{manifest['id']}-{manifest['version']}.unsigned.agent"
    module_root = f'agent/{NEW}/lib/python3.10/site-packages/{NEW}/'
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as bundle:
        with zipfile.ZipFile(template) as source:
            for item in source.infolist():
                if item.is_dir() or item.filename.startswith('res/') or item.filename in ('agent.json', 'shortcut_list.json', 'LICENSE', 'resources/music_panel.zip'):
                    continue
                name = item.filename.replace(OLD, NEW)
                if name.startswith(module_root):
                    continue
                data = source.read(item)
                try:
                    data = data.decode('utf8').replace(OLD, NEW).encode('utf8')
                except UnicodeDecodeError:
                    pass
                write(bundle, name, data, (item.external_attr >> 16) & 0o777 or 0o644)
        for path in (ROOT / 'src').glob('*.py'):
            write(bundle, module_root + path.name, path.read_bytes())
        for path in (ROOT / 'res').rglob('*'):
            if path.is_file():
                write(bundle, path.relative_to(ROOT).as_posix(), path.read_bytes())
        for name in ('agent.json', 'shortcut_list.json', 'LICENSE'):
            write(bundle, name, (ROOT / name).read_bytes())
    with zipfile.ZipFile(output) as bundle:
        assert bundle.testzip() is None
        assert json.loads(bundle.read('agent.json')) == manifest
        with zipfile.ZipFile(io.BytesIO(bundle.read('res/music_panel.zip'))) as web:
            assert web.read('dist/index.html') == (ROOT / 'web/index.html').read_bytes()
    print(f'Created {output.name}: {output.stat().st_size} bytes')


if __name__ == '__main__':
    main()
