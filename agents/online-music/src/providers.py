"""Adapters around QQMusicApi and NeteaseCloudMusicApiEnhanced.

Account credentials stay in the Agent's private storage. Playback URLs are
resolved again on each play/resume so expiring CDN URLs are never persisted.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit


class ProviderError(Exception):
    """A message that can be displayed without exposing account credentials."""


def media_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username:
        raise ProviderError("音乐平台返回了无效的播放地址。")
    return value


class Accounts:
    def __init__(self, root: Path):
        self.path = root / "online_music_accounts.json"
        self.lock = threading.RLock()
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(self.data, dict):
                self.data = {}
        except (OSError, ValueError):
            self.data = {}

    def get(self, provider: str) -> dict:
        with self.lock:
            return dict(self.data.get(provider, {}))

    def save(self, provider: str, data: dict) -> None:
        with self.lock:
            values = {**self.data, provider: data}
            temporary = self.path.with_name(f".accounts-{secrets.token_hex(8)}")
            try:
                with temporary.open("x", encoding="utf-8") as stream:
                    os.chmod(temporary, 0o600)
                    json.dump(values, stream, ensure_ascii=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
                self.data = values
            finally:
                temporary.unlink(missing_ok=True)


class NeteaseBridge:
    def __init__(self, backend: Path, storage: Path):
        node = shutil.which("node")
        bundled = backend / "node/bin/node"
        if node is None and bundled.is_file():
            node = str(bundled.resolve())
        if node is None:
            raise ProviderError("网易云运行环境尚未安装。")
        environment = os.environ.copy()
        environment["TMPDIR"] = str(storage / "cache")
        environment["TEMP"] = environment["TMPDIR"]
        self.lock = threading.Lock()
        self.pending: dict[str, concurrent.futures.Future] = {}
        self.closed = False
        self.process = subprocess.Popen(
            [node, str((backend / "bridge.cjs").resolve())],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            env=environment,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        try:
            for line in self.process.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    continue
                with self.lock:
                    future = self.pending.get(message.get("id"))
                    if future is not None and not future.done():
                        future.set_result(message)
        finally:
            with self.lock:
                for future in self.pending.values():
                    if not future.done():
                        future.set_exception(
                            ProviderError("网易云服务已退出，请重新打开 Agent。")
                        )

    def call(self, method: str, **args) -> dict:
        request_id = secrets.token_hex(8)
        future = concurrent.futures.Future()
        with self.lock:
            if self.closed or self.process.poll() is not None:
                raise ProviderError("网易云服务未运行，请重新打开 Agent。")
            self.pending[request_id] = future
            try:
                self.process.stdin.write(
                    json.dumps({"id": request_id, "method": method, "args": args})
                    + "\n"
                )
                self.process.stdin.flush()
            except (OSError, ValueError):
                self.pending.pop(request_id, None)
                raise ProviderError("网易云服务连接中断。") from None
        try:
            message = future.result(timeout=18)
            if message.get("error"):
                raise ProviderError(message["error"])
            body = message.get("body", {})
            if not isinstance(body, dict):
                raise ProviderError("网易云返回的数据格式有误。")
            code = body.get("code", 200)
            if code in (301, 302):
                raise ProviderError("网易云登录已失效，请重新扫码。")
            if code not in (200, 800, 801, 802, 803):
                raise ProviderError("网易云暂时无法完成请求，请稍后重试。")
            return body
        except concurrent.futures.TimeoutError:
            raise ProviderError("网易云请求超时，请检查机器人网络。") from None
        finally:
            with self.lock:
                self.pending.pop(request_id, None)

    def close(self) -> None:
        with self.lock:
            self.closed = True
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait()
        self.reader.join(timeout=2)
        self.process.stdin.close()
        self.process.stdout.close()


class NeteaseProvider:
    name = "网易云音乐"

    def __init__(self, accounts: Accounts, backend: Path, storage: Path):
        self.accounts = accounts
        self.backend = backend
        self.storage = storage
        self.bridge = None
        self.lock = threading.RLock()
        self.qr = None

    def call(self, method: str, **args) -> dict:
        with self.lock:
            if self.bridge is None:
                self.bridge = NeteaseBridge(self.backend, self.storage)
            bridge = self.bridge
        return bridge.call(
            method, cookie=self.accounts.get("netease").get("cookie", ""), **args
        )

    def status(self) -> dict:
        account = self.accounts.get("netease")
        return {
            "logged_in": bool(account.get("cookie")),
            "name": account.get("name", "未登录"),
            "can_login": True,
            "can_playlists": True,
        }

    def login_start(self) -> dict:
        with self.lock:
            token = secrets.token_urlsafe(18)
            self.qr = {"token": token, "key": "", "deadline": time.monotonic() + 180}
        key = self.call("login_qr_key")["data"]["unikey"]
        data = self.call("login_qr_create", key=key, qrimg=True)["data"]
        with self.lock:
            if not self.qr or self.qr["token"] != token:
                raise ProviderError("登录已取消，请重新获取二维码。")
            self.qr["key"] = key
        return {
            "session": token,
            "image": data["qrimg"],
            "hint": "用网易云音乐 App 扫码并确认登录",
            "expires_in": 180,
        }

    def login_poll(self, session: str) -> dict:
        with self.lock:
            qr = dict(self.qr or {})
        if qr.get("token") != session or time.monotonic() > qr.get("deadline", 0):
            return {"status": "expired"}
        result = self.call("login_qr_check", key=qr["key"])
        code = result.get("code")
        if code == 803:
            cookie = result.get("cookie", "")
            if not cookie:
                raise ProviderError("扫码成功，但平台没有返回登录凭据，请重新扫码。")
            with self.lock:
                if not self.qr or self.qr["token"] != session:
                    return {"status": "expired"}
                self.accounts.save("netease", {"cookie": cookie, "name": "已登录"})
                self.qr = None
            return {"status": "confirmed"}
        return {
            "status": {800: "expired", 801: "waiting", 802: "scanned"}.get(
                code, "waiting"
            )
        }

    @staticmethod
    def track(song: dict) -> dict:
        artists = song.get("ar", song.get("artists", []))
        return {
            "provider": "netease",
            "id": str(song["id"]),
            "title": song.get("name", "未命名歌曲"),
            "artist": " / ".join(artist.get("name", "") for artist in artists),
            "duration": float(song.get("dt", song.get("duration", 0))) / 1000,
        }

    def search(self, query: str, page: int) -> dict:
        result = self.call(
            "cloudsearch", keywords=query, limit=30, offset=(page - 1) * 30, type=1
        ).get("result", {})
        songs = result.get("songs", [])
        return {
            "items": [self.track(song) for song in songs],
            "more": page * 30 < result.get("songCount", 0),
        }

    def playlists(self, page: int) -> dict:
        if not self.status()["logged_in"]:
            raise ProviderError("请先登录网易云音乐，再读取我的歌单。")
        profile = self.call("login_status").get("data", {}).get("profile") or {}
        uid = profile.get("userId")
        if not uid:
            raise ProviderError("网易云登录已失效，请重新扫码。")
        result = self.call("user_playlist", uid=uid, limit=50, offset=(page - 1) * 50)
        return {
            "items": [
                {
                    "id": str(item["id"]),
                    "title": item["name"],
                    "count": item.get("trackCount", 0),
                }
                for item in result.get("playlist", [])
            ],
            "more": bool(result.get("more")),
        }

    def playlist_tracks(self, playlist: str, page: int) -> dict:
        result = self.call(
            "playlist_track_all", id=playlist, limit=100, offset=(page - 1) * 100
        )
        songs = result.get("songs", [])
        return {
            "items": [self.track(song) for song in songs],
            "more": len(songs) == 100,
        }

    def resolve(self, track: dict) -> str:
        result = self.call("song_url", id=track["id"], br=128000)
        entries = result.get("data", [])
        if not entries or not entries[0].get("url"):
            raise ProviderError("这首歌当前账号无权播放，或平台暂未提供播放地址。")
        if entries[0].get("freeTrialInfo"):
            raise ProviderError("当前账号只能试听这首歌，请使用有完整播放权限的账号。")
        return media_url(entries[0]["url"])

    def logout(self) -> None:
        with self.lock:
            self.qr = None
            self.accounts.save("netease", {})

    def close(self) -> None:
        if self.bridge:
            self.bridge.close()


class QQProvider:
    name = "QQ 音乐"

    def __init__(self, storage: Path):
        self.storage = storage
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        self.client = None

    def status(self) -> dict:
        return {
            "logged_in": False,
            "name": "公共搜索",
            "can_login": False,
            "can_playlists": False,
        }

    def call(self, operation: str, *args):
        future = asyncio.run_coroutine_threadsafe(
            self._request(operation, *args), self.loop
        )
        try:
            return future.result(timeout=18)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise ProviderError("QQ 音乐请求超时，请检查机器人网络。") from None
        except ProviderError:
            raise
        except Exception:
            raise ProviderError("QQ 音乐接口暂时不可用，请稍后重试。") from None

    @staticmethod
    def track(song) -> dict:
        return {
            "provider": "qq",
            "id": song.mid,
            "title": song.title or song.name,
            "artist": " / ".join(artist.name for artist in song.singer),
            "duration": float(song.interval),
            "media_mid": song.file.media_mid,
        }

    async def _request(self, operation: str, *args):
        from qqmusic_api import Client
        from qqmusic_api.modules.song import SongFileInfo, SongFileType

        if self.client is None:
            self.client = Client(
                device_path=str(self.storage / "qq-device.json"),
            )
        client = self.client
        if operation == "search":
            result = await client.search.search_by_type(
                args[0], num=30, page=args[1], highlight=False
            )
            return {
                "items": [self.track(song) for song in result.song],
                "more": result.nextpage != -1,
            }
        if operation == "resolve":
            track = args[0]
            result = await client.song.get_song_urls(
                [SongFileInfo(track["id"], media_mid=track.get("media_mid") or None)],
                file_type=SongFileType.MP3_128,
            )
            if not result.data or not result.data[0].purl or result.data[0].result:
                raise ProviderError(
                    "QQ 音乐未提供这首歌的公开播放地址，请切换网易云播放。"
                )
            return media_url(
                urljoin("https://isure.stream.qqmusic.qq.com/", result.data[0].purl)
            )
        raise ProviderError("不支持的 QQ 音乐操作。")

    def search(self, query: str, page: int) -> dict:
        return self.call("search", query, page)

    def resolve(self, track: dict) -> str:
        return self.call("resolve", track)

    def close(self) -> None:
        async def finish():
            tasks = [
                task
                for task in asyncio.all_tasks()
                if task is not asyncio.current_task()
            ]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            if self.client:
                await self.client.close()

        future = asyncio.run_coroutine_threadsafe(finish(), self.loop)
        try:
            future.result(timeout=3)
        except Exception:
            pass
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.thread.join(timeout=2)
