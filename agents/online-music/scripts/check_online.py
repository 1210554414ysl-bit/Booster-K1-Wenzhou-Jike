"""Exercise real FFmpeg streaming with a simulated Booster SDK and providers."""

import importlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import ModuleType, SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def wait_for(check, seconds=8):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for playback state")


def ignore(*args, **kwargs):
    pass


class Component:
    def __init__(self, key, name, path, *args, **kwargs):
        self.name = name
        self.path = path
        self.state = args[0] if args and isinstance(args[0], bool) else False

    set_selection_ring_loading = ignore


class Parameters:
    def __init__(self):
        self.values = {}
        self.callbacks = {}

    def add_parameter_callback(self, key, callback):
        self.callbacks[key] = callback

    def set_parameter(self, value):
        key, item = value
        changed = self.values.get(key) != item
        self.values[key] = item
        if changed and key in self.callbacks:
            self.callbacks[key](
                SimpleNamespace(
                    as_int=lambda: item,
                    as_float=lambda: item,
                    as_string=lambda: item,
                    as_bool=lambda: item,
                )
            )
        return SimpleNamespace(successful=True)


def main():
    with tempfile.TemporaryDirectory() as temporary:
        storage = Path(temporary)
        volumes = []

        class AgentBase:
            def __init__(self, features):
                self.parameter_manager = Parameters()
                self.storage_manager = SimpleNamespace(
                    node_config_path=storage,
                    file_exists=lambda path: (storage / path).is_file(),
                    read_text_file=lambda path: (storage / path).read_text(),
                    write_text_file=lambda path, text: (storage / path).write_text(
                        text
                    ),
                )
                self.component_manager = SimpleNamespace(
                    update_component=ignore, publish_toast=ignore
                )
                self.logger = SimpleNamespace(info=ignore, warn=print, error=print)

        framework = ModuleType("booster_agent_framework")
        framework.AgentBase = AgentBase
        framework.AgentFeatures = SimpleNamespace
        framework.LocaleString = lambda en, zh: (en, zh)
        for key in (
            "Component",
            "IconComponent",
            "DefaultStateIconComponent",
            "PlaceholderComponent",
            "OnlineWebviewComponent",
        ):
            setattr(framework, key, Component)
        framework.ComponentOrientation = SimpleNamespace(kLANDSCAPE=0)
        framework.ComponentStatePageProxy = lambda *args: SimpleNamespace(
            register_component_v2=ignore,
            register_components=ignore,
            register_component=ignore,
        )
        framework.ToastPosition = SimpleNamespace(kBOTTOM=0)
        framework.ToastIcon = SimpleNamespace(kNONE=0)
        sdk = ModuleType("boosteros.robots.booster")
        sdk.BoosterRobot = lambda: SimpleNamespace(
            audio_manager=SimpleNamespace(set_system_volume=volumes.append)
        )
        sys.modules[framework.__name__] = framework
        sys.modules[sdk.__name__] = sdk
        package = ModuleType("online_music")
        package.__path__ = [str(ROOT / "src")]
        sys.modules["online_music"] = package
        code = importlib.import_module("online_music.main")
        core = importlib.import_module("online_music.local_player")
        core.PARAM_SETTLE_SEC = 0.01

        class Player(code.OnlineMusicAgent):
            def _output_arguments(self):
                return ["-f", "null", "-"]

            @staticmethod
            def _ffmpeg_command(*args):
                arguments = list(args)
                arguments.insert(arguments.index("-i"), "-re")
                return core.MusicPlayerAgent._ffmpeg_command(*arguments)

        os.chdir(ROOT)
        player = Player()
        for provider in player.providers.values():
            provider.close()
        player.on_agent_activated()
        wait_for(lambda: player._params_ready)
        subprocess.run(
            [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=30",
                str(storage / "sample.mp3"),
            ],
            check=True,
        )

        class AudioHandler(SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass

        audio = ThreadingHTTPServer(
            ("127.0.0.1", 0), partial(AudioHandler, directory=str(storage))
        )
        threading.Thread(target=audio.serve_forever, daemon=True).start()
        slow_search = threading.Event()
        slow_resolve = threading.Event()
        entered = threading.Event()
        url = f"http://127.0.0.1:{audio.server_port}/sample.mp3"

        class Provider:
            name = "测试音源"

            def __init__(self, can_login=True):
                self.can_login = can_login

            def status(self):
                return {
                    "logged_in": False,
                    "name": "未登录",
                    "can_login": self.can_login,
                    "can_playlists": self.can_login,
                }

            def search(self, query, page):
                if query == "slow":
                    entered.set()
                    slow_search.wait(12)
                return {
                    "items": [
                        {
                            "provider": "netease",
                            "id": str(i),
                            "title": f"测试歌曲 {i}",
                            "artist": "测试歌手",
                            "duration": 30,
                        }
                        for i in range(1, 5)
                    ],
                    "more": False,
                }

            def resolve(self, item):
                if item["id"] == "slow":
                    slow_resolve.wait(12)
                if item["id"] == "error":
                    raise code.ProviderError("当前账号无权播放这首歌。")
                return url

            def close(self):
                pass

            def playlists(self, page):
                return {
                    "items": [{"id": "1", "title": "测试歌单", "count": 4}],
                    "more": False,
                }

            def playlist_tracks(self, identifier, page):
                return self.search("", page)

            def login_start(self):
                return {
                    "session": "test",
                    "image": "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jZ1sAAAAASUVORK5CYII=",
                    "hint": "测试扫码",
                    "expires_in": 120,
                }

            def login_poll(self, session):
                return {"status": "waiting"}

        player.providers = {"netease": Provider(), "qq": Provider(can_login=False)}
        panel = player.panel_url.replace("http://127.0.0.1", "http://127.0.0.1")
        # The simulator binds all interfaces. Tests use its loopback address.
        from urllib.parse import urlsplit, urlunsplit

        parts = urlsplit(panel)
        panel = urlunsplit(("http", f"127.0.0.1:{parts.port}", parts.path, "", ""))

        def api(command, **extra):
            request = urllib.request.Request(
                panel + "api",
                json.dumps({"command": command, **extra}).encode(),
                {"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                return json.load(response)

        def state():
            return api("status")["state"]

        try:
            for operation in (
                "login_start",
                "login_poll",
                "logout",
                "playlists",
                "playlist_tracks",
            ):
                assert not api(operation, provider="qq")["success"]
            assert api("search", provider="qq", query="测试")["success"]
            print(
                "PASS QQ login and personal playlists are unavailable while public search works",
                flush=True,
            )
            result = api("search", provider="netease", query="测试")
            keys = [item["key"] for item in result["items"]]
            assert api("select", track=keys[0], queue=keys)["success"]
            wait_for(lambda: state()["position"] > 0.7)
            assert api("play_pause")["success"]
            paused = state()
            time.sleep(0.5)
            assert paused["paused"] and state()["position"] == paused["position"]
            api("play_pause")
            wait_for(lambda: state()["position"] > paused["position"] + 0.5)
            api("seek", track=keys[0], percent=40)
            wait_for(lambda: state()["position"] >= 12 and state()["playing"])
            api("setting", name="volume", value=35)
            assert state()["volume"] == 35 and volumes[-1] == 0.35
            for _ in range(15):
                api("next")
                api("previous")
            wait_for(lambda: state()["playing"])
            print(
                "PASS streaming, decoded progress, pause/resume, seek, volume and 30 rapid skips",
                flush=True,
            )

            with ThreadPoolExecutor(max_workers=1) as executor:
                pending = executor.submit(
                    api, "search", provider="netease", query="slow"
                )
                assert entered.wait(2)
                start = time.monotonic()
                api("play_pause")
                assert state()["paused"]
                assert time.monotonic() - start < 1
                slow_search.set()
                pending.result()
            print("PASS slow search cannot block pause or status", flush=True)

            special = player._register_tracks(
                [
                    {
                        "provider": "netease",
                        "id": name,
                        "title": name,
                        "artist": "",
                        "duration": 30,
                    }
                    for name in ("slow", "error")
                ]
            )
            api("select", track=special[0]["key"], queue=[special[0]["key"], keys[0]])
            time.sleep(0.2)
            api("next")
            wait_for(lambda: state()["playing"] and state()["track"] == keys[0])
            slow_resolve.set()
            api("select", track=special[1]["key"])
            wait_for(lambda: bool(state()["error"]))
            api("select", track=keys[0], queue=keys)
            wait_for(lambda: state()["playing"])
            assert not state()["error"]
            print(
                "PASS cancelled slow resolve and recovery from permission failure",
                flush=True,
            )

            api("setting", name="play_mode", value="shuffle")
            played = []
            for _ in range(4):
                api("next")
                played.append(state()["track"])
            assert len(set(played)) == 4
            api("previous")
            assert state()["track"] == played[-2]
            print(
                "PASS shuffle visits each track and previous follows playback history",
                flush=True,
            )
            api("play_pause")
            if len(sys.argv) > 1:
                subprocess.run(
                    ["node", str(ROOT / "scripts/check_panel.mjs"), panel, sys.argv[1]],
                    check=True,
                )
            print("All checks passed", flush=True)
        finally:
            slow_search.set()
            slow_resolve.set()
            player.on_agent_close()
            audio.shutdown()
            audio.server_close()


if __name__ == "__main__":
    main()
