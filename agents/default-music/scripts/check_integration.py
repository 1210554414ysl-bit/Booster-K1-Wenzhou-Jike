"""Exercise the default host, one audio engine, source switches and real browser UI."""

import importlib
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
ONLINE = ROOT.parent / 'online-music'
helpers = runpy.run_path(str(ONLINE / 'scripts/check_online.py'))
wait = helpers['wait_for']
ignore = helpers['ignore']


def main():
    with tempfile.TemporaryDirectory() as temporary:
        storage = Path(temporary)
        volumes = []
        components = {}

        class Component(helpers['Component']):
            def __init__(self, key, *args, **kwargs):
                super().__init__(key, *args, **kwargs)
                self.id = key

        class DefaultBase:
            def __init__(self):
                self._walk_components = []
                self._current_component_ids = set()
                self.storage_manager = SimpleNamespace(
                    node_config_path=storage,
                    file_exists=lambda path: (storage / path).is_file(),
                    read_text_file=lambda path: (storage / path).read_text(encoding='utf8'),
                    write_text_file=lambda path, text: (storage / path).write_text(text, encoding='utf8'))
                self.logger = SimpleNamespace(info=ignore, warn=print, error=print)
                self.component_manager = SimpleNamespace(update_component=ignore, publish_toast=ignore)

            def _replace_components(self, items):
                components.clear()
                components.update({item.id: item for item in items})
                self._current_component_ids = set(components)

            def on_agent_close(self):
                pass

        framework = ModuleType('booster_agent_framework')
        framework.AgentBase = DefaultBase
        framework.AgentFeatures = SimpleNamespace
        framework.LocaleString = lambda en, zh: (en, zh)
        for name in ('Component', 'IconComponent', 'DefaultStateIconComponent',
                     'PlaceholderComponent', 'OnlineWebviewComponent'):
            setattr(framework, name, Component)
        framework.ComponentOrientation = SimpleNamespace(kLANDSCAPE=0)
        framework.ComponentStatePageProxy = lambda *args: None
        framework.ToastIcon = SimpleNamespace(kNONE=0)
        framework.ToastPosition = SimpleNamespace(kBOTTOM=0)
        sdk = ModuleType('boosteros.robots.booster')
        sdk.BoosterRobot = lambda: SimpleNamespace(audio_manager=SimpleNamespace(set_system_volume=volumes.append))
        sys.modules[framework.__name__] = framework
        sys.modules[sdk.__name__] = sdk
        package = ModuleType('default_music_test')
        package.__path__ = [str(ROOT / 'src')]
        sys.modules[package.__name__] = package
        controller = importlib.import_module(package.__name__ + '.controller')
        local = importlib.import_module(package.__name__ + '.local_player')
        local.PARAM_SETTLE_SEC = 0.01
        loader = importlib.import_module(package.__name__ + '.default_loader')
        loader.load_default = lambda: DefaultBase
        host_code = importlib.import_module(package.__name__ + '.main')

        class Player(controller.MusicController):
            def _output_arguments(self):
                return ['-f', 'null', '-']

            @staticmethod
            def _ffmpeg_command(*args):
                args = list(args)
                if '-i' in args and '-f' in args:
                    args.insert(args.index('-i'), '-re')
                return local.MusicPlayerAgent._ffmpeg_command(*args)

        host_code.MusicController = Player
        os.chdir(ROOT)
        (storage / 'music').mkdir()
        sample = storage / 'music/local-one.mp3'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                        'sine=frequency=440:duration=30', str(sample)], check=True)
        shutil.copyfile(sample, storage / 'music/local-two.mp3')
        audio = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=str(sample.parent)))
        threading.Thread(target=audio.serve_forever, daemon=True).start()
        host = host_code.DefaultMusicAgent()
        player = host.music
        for provider in player.providers.values():
            provider.close()
        url = f'http://127.0.0.1:{audio.server_port}/local-one.mp3'
        slow_entered = threading.Event()
        release = threading.Event()

        class Provider:
            name = 'Test provider'

            def __init__(self, login=True):
                self.login = login

            def status(self):
                return {'logged_in': False, 'name': 'Guest', 'can_login': self.login, 'can_playlists': self.login}

            def search(self, query, page):
                if query == 'slow':
                    slow_entered.set()
                    release.wait(12)
                return {'items': [{'id': str(n), 'provider': 'netease', 'title': f'Online {n}',
                                   'artist': 'Test', 'duration': 30} for n in range(4)], 'more': False}

            def resolve(self, item):
                return url

            def playlists(self, page):
                return {'items': [{'id': '1', 'title': '测试歌单', 'count': 4}], 'more': False}

            def playlist_tracks(self, identifier, page):
                return self.search('', page)

            def login_start(self):
                return {'session': 'test', 'image': 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZ1sAAAAASUVORK5CYII=', 'hint': 'Test', 'expires_in': 120}

            def login_poll(self, session):
                return {'status': 'waiting'}

            def close(self):
                pass

        player.providers = {'netease': Provider(), 'qq': Provider(False)}
        host.on_agent_activated()
        wait(lambda: player._params_ready)
        from urllib.parse import urlsplit
        parts = urlsplit(player.panel_url)
        panel = f'http://127.0.0.1:{parts.port}{parts.path}'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

        def api(source, command, **extra):
            request = urllib.request.Request(panel + source + '/api',
                json.dumps({'command': command, **extra}).encode(), {'Content-Type': 'application/json'})
            with opener.open(request, timeout=15) as response:
                return json.load(response)

        state = lambda: api('local', 'status')['state']
        try:
            for fake_mode in ('prepare', 'walking', 'damping', 'walking'):
                host._replace_components([])
                assert 'default_music_panel' in components
            print('PASS original host inheritance and music entry survives component rebuilds', flush=True)
            assert len(api('local', 'status')['catalog']) == 2
            api('local', 'play_pause')
            wait(lambda: state()['playing'] and state()['duration'] > 29)
            time.sleep(0.8)
            assert state()['position'] > 0.6
            api('local', 'play_pause')
            paused = state()['position']
            time.sleep(0.4)
            assert state()['paused'] and abs(state()['position'] - paused) < 0.05
            api('local', 'play_pause')
            wait(lambda: state()['playing'])
            api('local', 'seek', percent=50, track=state()['track'])
            wait(lambda: state()['playing'] and state()['position'] >= 15)
            print('PASS local playback, actual FFmpeg, pause/resume and seek', flush=True)
            result = api('online', 'search', provider='netease', query='test')
            keys = [item['key'] for item in result['items']]
            api('online', 'select', track=keys[0], queue=keys)
            wait(lambda: state()['playing'] and state()['position'] > 0.5 and state()['track'] == keys[0])
            assert len(api('local', 'status')['catalog']) == 2
            for _ in range(10):
                api('online', 'next')
                api('online', 'previous')
            local_key = api('local', 'status')['catalog'][0][0]
            api('local', 'select', track=local_key)
            wait(lambda: state()['playing'] and state()['track'] == local_key)
            assert len(api('online', 'status')['catalog']) == 2
            api('local', 'setting', name='volume', value=31)
            assert state()['volume'] == 31 and volumes[-1] == 0.31
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor() as pool:
                pending = pool.submit(api, 'online', 'search', provider='netease', query='slow')
                assert slow_entered.wait(2)
                started = time.monotonic()
                api('local', 'play_pause')
                assert state()['paused'] and time.monotonic() - started < 1
                release.set()
                pending.result()
            print('PASS local/online switching, queue isolation, rapid skips, volume and slow-search responsiveness', flush=True)
            if len(sys.argv) > 1:
                subprocess.run(['node', str(ONLINE / 'scripts/check_panel.mjs'), panel + 'online/', sys.argv[1]], check=True)
                if state()['playing'] or state()['loading']:
                    api('local', 'play_pause')
                upload = storage / '手机上传验收.mp3'
                shutil.copyfile(sample, upload)
                subprocess.run(['node', str(ROOT / 'scripts/check_upload.mjs'), panel + 'local/', sys.argv[1], str(upload)], check=True)
                subprocess.run(['node', str(ROOT / 'scripts/check_hub.mjs'), panel, sys.argv[1]], check=True)
            if state()['playing'] or state()['loading']:
                api('local', 'play_pause')
            api('local', 'play_pause')
            wait(lambda: state()['playing'])
            host.on_agent_deactivated()
            assert state()['paused']
            print('PASS deactivation pauses the common audio engine', flush=True)
        finally:
            release.set()
            host.on_agent_close()
            audio.shutdown()
            audio.server_close()
        print('All default-music integration checks passed', flush=True)


if __name__ == '__main__':
    main()
