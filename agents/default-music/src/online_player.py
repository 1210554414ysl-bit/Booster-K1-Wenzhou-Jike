"""Online music Agent. Reuses the local player's transport, queue and volume logic."""

from __future__ import annotations

import concurrent.futures
import math
import subprocess
import threading
import time
from collections import OrderedDict
from pathlib import Path

from booster_agent_framework import LocaleString

from .local_player import MusicPlayerAgent, Track
from .providers import Accounts, NeteaseProvider, ProviderError, QQProvider


class OnlineMusicAgent(MusicPlayerAgent):
    def __init__(self, owner):
        self.providers = {}
        self._known_tracks = OrderedDict()
        self._last_error = ""
        self._resolve_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=3, thread_name_prefix="online-resolve"
        )
        super().__init__(owner)
        accounts = Accounts(self._storage_root)
        backend = Path("resources/netease")
        self.providers = {
            "netease": NeteaseProvider(accounts, backend, self._storage_root),
            "qq": QQProvider(self._storage_root),
        }

    def _scan_local(self, refresh_ui=False):
        return len(self._tracks)


    def _provider(self, name):
        if name not in self.providers:
            raise ProviderError("请选择网易云音乐或 QQ 音乐。")
        return self.providers[name]

    def _register_tracks(self, items):
        entries = []
        with self._lock:
            for item in items:
                track = Track(
                    item["title"] + (" · " + item["artist"] if item["artist"] else ""),
                    f"{item['provider']}://{item['id']}",
                    item["duration"],
                )
                self._known_tracks[track.key] = (track, item)
                self._known_tracks.move_to_end(track.key)
                entries.append(
                    {
                        **{
                            key: value
                            for key, value in item.items()
                            if key != "media_mid"
                        },
                        "key": track.key,
                    }
                )
            # Keep active queue entries even when old search results are evicted.
            active = {track.key for track in self._tracks}
            for key in list(self._known_tracks)[
                : max(0, len(self._known_tracks) - 3000)
            ]:
                if key not in active:
                    del self._known_tracks[key]
        return entries

    def _on_panel_request(self, message):
        if not isinstance(message, dict) or self._shutdown:
            return {"success": False, "message": "播放器已关闭。"}
        try:
            command = message.get("command")
            if command == "status":
                return self._panel_status(message.get("revision"))
            provider_commands = (
                "login_start",
                "login_poll",
                "logout",
                "search",
                "playlists",
                "playlist_tracks",
            )
            if command in provider_commands:
                provider = self._provider(message.get("provider"))
                capabilities = provider.status()
                if command in (
                    "login_start",
                    "login_poll",
                    "logout",
                ) and not capabilities.get("can_login", True):
                    raise ProviderError("QQ 音乐登录入口已移除，请使用网易云登录。")
                if command in ("playlists", "playlist_tracks") and not capabilities.get(
                    "can_playlists", True
                ):
                    raise ProviderError("请切换网易云查看个人歌单。")
                page = message.get("page", 1)
                if type(page) is not int or not 1 <= page <= 10000:
                    raise ProviderError("页码无效。")
                if command == "login_start":
                    result = provider.login_start()
                elif command == "login_poll":
                    result = provider.login_poll(str(message.get("session", "")))
                elif command == "logout":
                    self._logout(provider, message["provider"])
                    result = {}
                elif command == "search":
                    query = str(message.get("query", "")).strip()
                    if not 1 <= len(query) <= 160:
                        raise ProviderError("请输入不超过 160 个字的歌名或歌手。")
                    result = provider.search(query, page)
                    result["items"] = self._register_tracks(result["items"])
                elif command == "playlists":
                    result = provider.playlists(page)
                else:
                    playlist = str(message.get("playlist", ""))
                    if not playlist.isdigit() or len(playlist) > 24:
                        raise ProviderError("歌单编号无效。")
                    result = provider.playlist_tracks(playlist, page)
                    result["items"] = self._register_tracks(result["items"])
                return {"success": True, **result}
            if command == "select":
                keys = message.get("queue", [])
                if (
                    not isinstance(keys, list)
                    or len(keys) > 3000
                    or any(not isinstance(key, str) for key in keys)
                ):
                    raise ProviderError("播放队列无效。")
                with self._lock:
                    selected = message.get("track")
                    if (
                        not isinstance(selected, str)
                        or selected not in self._known_tracks
                    ):
                        raise ProviderError("歌曲列表已过期，请重新搜索。")
                    keys = list(
                        dict.fromkeys(key for key in keys if key in self._known_tracks)
                    )
                    if selected not in keys:
                        keys.append(selected)
                    self._tracks = [self._known_tracks[key][0] for key in keys]
                    self._shuffle_bag.clear()
                    self._shuffle_history.clear()
                    self._index = -1
                    self._catalog_revision += 1
                    self._select_track_locked(keys.index(selected))
                self._push_ui_state()
                return {"success": True}
            if command in ("seek", "volume"):
                number = message.get("percent", message.get("value"))
                if type(number) not in (int, float) or not math.isfinite(number):
                    raise ProviderError("数值无效。")
            return super()._on_panel_request(message)
        except ProviderError as error:
            return {"success": False, "message": str(error)}
        except Exception as error:
            self.logger.warn(f"online request failed: {type(error).__name__}")
            return {"success": False, "message": "音乐平台请求未完成，请重试。"}

    def _logout(self, provider, name):
        provider.logout()
        with self._lock:
            if self._current and self._current.location.startswith(name + "://"):
                self._pause_locked()
        self._push_ui_state()

    def on_play_pause(self, component):
        with self._lock:
            if self._current is None and not self._tracks:
                return LocaleString("Choose a song first", "请先搜索并选择歌曲")
        return super().on_play_pause(component)

    def _panel_status(self, revision):
        result = super()._panel_status(revision)
        result["accounts"] = {
            name: {"label": provider.name, **provider.status()}
            for name, provider in self.providers.items()
        }
        with self._lock:
            result["state"]["error"] = self._last_error
            result["state"]["playing"] = (
                self._handle is not None and not self._loading and not self._paused
            )
        return result

    def _position_locked(self):
        return min(self._duration, self._offset) if self._duration > 0 else self._offset

    def _start_playback_locked(self, track, offset):
        self._last_error = ""
        super()._start_playback_locked(track, offset)

    def _output_arguments(self):
        return [
            "-f",
            "pulse",
            "-buffer_duration",
            "150",
            "-stream_name",
            "K1 Online Music",
            "K1 Online Music",
        ]

    @staticmethod
    def _stop_player(player):
        if player.poll() is not None:
            return
        player.terminate()
        try:
            player.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            player.kill()
            player.wait()

    def _read_progress(self, player, generation, offset):
        for line in player.stdout:
            if not self._is_current(generation):
                return
            if not line.startswith(b"out_time_us="):
                continue
            try:
                elapsed = max(0, int(line.split(b"=", 1)[1])) / 1_000_000
            except ValueError:
                continue
            with self._lock:
                if not self._is_current(generation):
                    return
                self._offset = offset + elapsed
                self._loading = False
            self._push_ui_state()

    def _play_track(self, generation, track, offset):
        future = None
        try:
            with self._lock:
                item = dict(self._known_tracks[track.key][1])
            provider = self._provider(item["provider"])
            future = self._resolve_pool.submit(provider.resolve, item)
            while self._is_current(generation):
                try:
                    url = future.result(timeout=0.1)
                    break
                except concurrent.futures.TimeoutError:
                    continue
            else:
                return
            command = self._ffmpeg_command(
                "-loglevel",
                "error",
                "-nostats",
                "-stats_period",
                "0.25",
                "-progress",
                "pipe:1",
                "-rw_timeout",
                "15000000",
                "-ss",
                f"{offset:.3f}",
                "-i",
                url,
                "-map",
                "0:a:0",
                "-ac",
                "2",
                "-ar",
                "48000",
                *self._output_arguments(),
            )
            if not self._is_current(generation):
                return
            with subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                env=self._playback_environment(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ) as player:
                reader = None
                try:
                    with self._lock:
                        if not self._is_current(generation):
                            return
                        self._handle = player
                    reader = threading.Thread(
                        target=self._read_progress,
                        args=(player, generation, offset),
                        daemon=True,
                    )
                    reader.start()
                    started = time.monotonic()
                    while self._is_current(generation):
                        try:
                            code = player.wait(timeout=0.1)
                        except subprocess.TimeoutExpired:
                            with self._lock:
                                stalled = (
                                    self._loading and time.monotonic() - started > 20
                                )
                            if stalled:
                                raise ProviderError("音频加载超时，请检查机器人网络。")
                            continue
                        reader.join(timeout=1)
                        if code:
                            raise ProviderError(
                                "网络音频播放失败，请检查机器人网络或重新登录。"
                            )
                        self._finish_playback(generation, succeeded=True)
                        return
                finally:
                    self._stop_player(player)
                    if reader:
                        reader.join(timeout=1)
        except Exception as error:
            if self._is_current(generation):
                message = (
                    str(error)
                    if isinstance(error, ProviderError)
                    else "这首歌暂时无法播放，请重新选择或登录。"
                )
                with self._lock:
                    if not self._is_current(generation):
                        return
                    self._last_error = message
                self._finish_playback(
                    generation,
                    succeeded=False,
                    error=LocaleString("Playback failed", message),
                )
        finally:
            if future:
                future.cancel()

    def on_agent_close(self):
        super().on_agent_close()
        for provider in self.providers.values():
            provider.close()
        self._resolve_pool.shutdown(wait=False, cancel_futures=True)

    def on_agent_deactivated(self):
        with self._lock:
            if self._current:
                self._pause_locked()
        self._push_ui_state()
