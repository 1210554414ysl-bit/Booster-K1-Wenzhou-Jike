# coding: utf-8
"""Music Player Agent for Booster K1.

Two tabs (online / local), sequential / shuffle / repeat-one play modes,
seek, system volume control and persistent settings.

Layout note
-----------
The App renders agent UI as an icon grid (one grid per *page*), plus a
schema-driven parameter page. So:

* the two tabs are two ``ComponentStatePageProxy`` pages;
* transport / mode / mute are icon components;
* the volume slider, the draggable progress bar and the search keyword live
  in the parameter page (``res/params/schema.json``).

Every user callback returns immediately: all audio work (ffmpeg transcoding,
network fetch, playback waiting) happens on worker threads.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

from booster_agent_framework import (
    AgentBase,
    AgentFeatures,
    Component,
    ComponentStatePageProxy,
    DefaultStateIconComponent,
    IconComponent,
    LocaleString,
    PlaceholderComponent,
    ToastIcon,
    ToastPosition,
)

from boosteros.robots.booster import BoosterRobot

# ----------------------------------------------------------------------
# Pages / modes
# ----------------------------------------------------------------------
PAGE_LOCAL = "page_local"
PAGE_ONLINE = "page_online"

MODE_ORDER = "order"
MODE_SHUFFLE = "shuffle"
MODE_REPEAT_ONE = "repeat_one"
MODES = (MODE_ORDER, MODE_SHUFFLE, MODE_REPEAT_ONE)

# ----------------------------------------------------------------------
# Tunables
# ----------------------------------------------------------------------
SETTINGS_FILE = Path("music_player_settings.json")
MUSIC_DIR = Path("music")
CACHE_DIR = Path("cache")
AUDIO_EXTS = (".mp3", ".wav")
MAX_SCAN_DEPTH = 4
MAX_ONLINE_RESULTS = 20

# PCM format used when we must transcode (seek / resume / online stream).
PCM_SAMPLE_RATE = 22050
PCM_CHANNELS = 1

NETWORK_CHECK_INTERVAL_SEC = 20.0
NETWORK_TIMEOUT_SEC = 3.0
UI_TICK_SEC = 1.0
# Grace window after activation during which parameter callbacks are ignored, so
# the framework's restored/default parameter pushes cannot clobber our settings.
PARAM_SETTLE_SEC = 2.0
FFMPEG_PROBE_TIMEOUT_SEC = 20.0
FFMPEG_TRANSCODE_TIMEOUT_SEC = 180.0
PLAYBACK_EXTRA_TIMEOUT_SEC = 30.0

ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
NETWORK_PROBES = (("itunes.apple.com", 443), ("www.baidu.com", 443))


def _fmt_time(seconds: float) -> str:
    """Format seconds as mm:ss (or h:mm:ss when long)."""

    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class Track:
    """One playable item, local file or remote URL."""

    __slots__ = ("title", "artist", "source", "location", "duration")

    def __init__(
        self,
        title: str,
        source: str,
        location: str,
        artist: str = "",
        duration: float = 0.0,
    ) -> None:
        self.title = title
        self.source = source  # "local" | "online"
        self.location = location  # absolute file path or http(s) url
        self.artist = artist
        self.duration = duration

    @property
    def display_name(self) -> str:
        if self.artist:
            return f"{self.title} - {self.artist}"
        return self.title


class MusicPlayerAgent(AgentBase):
    """Music player agent."""

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------
    def __init__(self) -> None:
        super().__init__(
            AgentFeatures(
                enable_orchestration=False,
                enable_getup_action=True,
                param_schema_path="res/params/schema.json",
            )
        )
        self.robot = BoosterRobot()

        # --- shared state -------------------------------------------------
        self._lock = threading.RLock()
        self._shutdown = False
        self._tab = PAGE_LOCAL
        self._mode = MODE_ORDER
        self._volume = 50
        self._muted = False
        self._last_nonzero_volume = 50

        # playlist (each tab owns its own list; ``_tracks`` is the active one)
        self._local_tracks: list[Track] = []
        self._online_tracks: list[Track] = []
        self._tracks: list[Track] = []
        self._index = -1
        self._shuffle_bag: list[int] = []

        # playback
        self._handle = None
        self._generation = 0
        self._loading = False
        self._paused = False
        self._offset = 0.0
        self._started_at = 0.0
        self._duration = 0.0
        self._current: Optional[Track] = None

        # online
        self._online_ok = False
        self._search_results: list[Track] = []
        self._params: dict[str, Any] = {}
        # Parameter callbacks are ignored until the persisted settings have been
        # mirrored into the parameter page (see _activate_worker).
        self._params_ready = False

        # ui refs
        self._ui: dict[str, Component] = {}
        self._last_status_text = ""
        self._toast_pending: Optional[LocaleString] = None

        self._cache_dir = Path(CACHE_DIR)
        self._music_dir = Path(MUSIC_DIR)
        self._ensure_dirs()
        self._load_settings()
        self._build_ui()
        self._register_parameter_callbacks()

    # ------------------------------------------------------------------
    # Bootstrap helpers
    # ------------------------------------------------------------------
    @property
    def _storage_root(self) -> Path:
        """Agent private storage directory (never the whole filesystem)."""

        try:
            return Path(str(self.storage_manager.node_config_path))
        except Exception:
            return Path.cwd()

    def _ensure_dirs(self) -> None:
        for path in (self._cache_dir, self._music_dir):
            try:
                (self._storage_root / path).mkdir(parents=True, exist_ok=True)
            except Exception as exc:
                self.logger.warn(f"create dir {path} failed: {exc}")

    def _load_settings(self) -> None:
        try:
            if not self.storage_manager.file_exists(SETTINGS_FILE):
                return
            raw = self.storage_manager.read_text_file(SETTINGS_FILE)
            data = json.loads(raw or "{}")
            mode = str(data.get("mode", MODE_ORDER))
            if mode in MODES:
                self._mode = mode
            volume = int(data.get("volume", 50))
            self._volume = max(0, min(100, volume))
            self._last_nonzero_volume = max(1, self._volume) if self._volume else 50
            self._muted = bool(data.get("muted", False))
            self.logger.info(
                f"settings loaded: mode={self._mode} volume={self._volume} muted={self._muted}"
            )
        except Exception as exc:
            self.logger.warn(f"load settings failed: {exc.__class__.__name__}: {exc}")

    def _save_settings(self) -> None:
        try:
            payload = {
                "mode": self._mode,
                "volume": self._volume,
                "muted": self._muted,
            }
            self.storage_manager.write_text_file(
                SETTINGS_FILE, json.dumps(payload, ensure_ascii=False)
            )
        except Exception as exc:
            self.logger.warn(f"save settings failed: {exc.__class__.__name__}: {exc}")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_agent_activated(self) -> None:
        self.logger.info("MusicPlayerAgent activated")

        threading.Thread(target=self._activate_worker, daemon=True).start()
        threading.Thread(target=self._ui_ticker, daemon=True).start()
        threading.Thread(target=self._network_watcher, daemon=True).start()

    def _activate_worker(self) -> None:
        try:
            self._scan_local(refresh_ui=True)
            # The framework restores ROS parameter values asynchronously during
            # startup and may push them a moment after activation. Mirror our
            # persisted settings first, give the framework a short settling
            # window (callbacks are ignored meanwhile), then assert the values
            # that came from our own settings file.
            self._refresh_parameter_values()
            time.sleep(PARAM_SETTLE_SEC)
            self._params_ready = True
            self._refresh_parameter_values()
            self._apply_volume(0 if self._muted else self._volume)
            self._online_ok = self._check_network()
            self._push_ui_state()
        except Exception as exc:
            self.logger.error(f"activation failed: {exc.__class__.__name__}: {exc}")

    def on_agent_close(self) -> None:
        self.logger.info("MusicPlayerAgent closing")
        with self._lock:
            self._shutdown = True
            self._generation += 1
            handle = self._handle
            self._handle = None
        if handle is not None:
            try:
                handle.cancel()
            except Exception:
                pass
        self._save_settings()
        self._cleanup_cache()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _icon(
        self,
        cid: str,
        zh: str,
        en: str,
        icon: str,
        callback,
        sequence: int,
        shortcut_id: str = "",
    ) -> IconComponent:
        return IconComponent(
            cid,
            LocaleString(en, zh),
            icon,
            callback,
            shortcut_id=shortcut_id,
            display_sequence=sequence,
        )

    def _toggle(
        self,
        cid: str,
        zh: str,
        en: str,
        icon: str,
        state: bool,
        callback,
        sequence: int,
        shortcut_id: str = "",
    ) -> DefaultStateIconComponent:
        return DefaultStateIconComponent(
            cid,
            LocaleString(en, zh),
            icon,
            state,
            callback,
            shortcut_id=shortcut_id,
            display_sequence=sequence,
        )

    def _status(self, cid: str, zh: str, en: str, sequence: int) -> PlaceholderComponent:
        return PlaceholderComponent(
            cid,
            LocaleString(en, zh),
            "res/music_note.png",
            display_sequence=sequence,
        )

    def _active_page_id(self) -> str:
        """Which page the App should show (API 10800+ page predicate)."""

        with self._lock:
            tab = self._tab
            online_ok = self._online_ok
        if tab == PAGE_ONLINE and online_ok:
            return PAGE_ONLINE
        return PAGE_LOCAL

    def _build_ui(self) -> None:
        # API 10800+: the proxy asks us which page should be visible. The old
        # register_page() form is deprecated and gets overridden by the default
        # robot-state predicate ("AutoGeneralDampingOrPVTInactive"), which made
        # the App fall back to an empty page.
        self.page_proxy = ComponentStatePageProxy(
            self, lambda _state=None: self._active_page_id()
        )

        # ---------------- local page ----------------
        status = self._status("local_status", "未在播放", "Idle", 0)
        self._ui["local_status"] = status

        back = self._icon(
            "local_tab_online", "在线音乐", "Online", "res/tab_online.png",
            self.on_tab_online, 1,
        )
        prev_btn = self._icon(
            "local_prev", "上一首", "Previous", "res/prev.png",
            self.on_prev, 2, shortcut_id="prev",
        )
        play_btn = self._toggle(
            "local_play", "播放", "Play", "res/play.png", False,
            self.on_play_pause, 3, shortcut_id="play_pause",
        )
        next_btn = self._icon(
            "local_next", "下一首", "Next", "res/next.png",
            self.on_next, 4, shortcut_id="next",
        )
        mode_btn = self._icon(
            "local_mode", "播放模式", "Play mode", "res/mode_order.png",
            self.on_cycle_mode, 5, shortcut_id="mode",
        )
        mute_btn = self._toggle(
            "local_mute", "静音", "Mute", "res/mute.png", False,
            self.on_mute, 6, shortcut_id="mute",
        )
        scan_btn = self._icon(
            "local_scan", "刷新歌单", "Rescan", "res/scan.png",
            self.on_scan, 7,
        )
        self._ui["local_tab_online"] = back
        self._ui["local_prev"] = prev_btn
        self._ui["local_play"] = play_btn
        self._ui["local_next"] = next_btn
        self._ui["local_mode"] = mode_btn
        self._ui["local_mute"] = mute_btn
        self._ui["local_scan"] = scan_btn

        self.page_proxy.register_component_v2(PAGE_LOCAL, status)
        self.page_proxy.register_components(
            PAGE_LOCAL,
            [back, prev_btn, play_btn, next_btn, mode_btn, mute_btn, scan_btn],
        )

        # ---------------- online page ----------------
        o_status = self._status("online_status", "无网络", "Offline", 0)
        o_back = self._icon(
            "online_tab_local", "本地音乐", "Local", "res/tab_local.png",
            self.on_tab_local, 1,
        )
        o_search = self._icon(
            "online_search", "搜索", "Search", "res/search.png",
            self.on_search, 2,
        )
        o_url = self._icon(
            "online_play_url", "播放直链", "Play URL", "res/link.png",
            self.on_play_url, 3,
        )
        o_prev = self._icon(
            "online_prev", "上一首", "Previous", "res/prev.png",
            self.on_prev, 4,
        )
        o_play = self._toggle(
            "online_play", "播放", "Play", "res/play.png", False,
            self.on_play_pause, 5, shortcut_id="play_pause",
        )
        o_next = self._icon(
            "online_next", "下一首", "Next", "res/next.png",
            self.on_next, 6, shortcut_id="next",
        )
        o_mute = self._toggle(
            "online_mute", "静音", "Mute", "res/mute.png", False,
            self.on_mute, 7, shortcut_id="mute",
        )
        self._ui["online_status"] = o_status
        self._ui["online_tab_local"] = o_back
        self._ui["online_search"] = o_search
        self._ui["online_play_url"] = o_url
        self._ui["online_prev"] = o_prev
        self._ui["online_play"] = o_play
        self._ui["online_next"] = o_next
        self._ui["online_mute"] = o_mute

        self.page_proxy.register_component_v2(PAGE_ONLINE, o_status)
        self.page_proxy.register_components(
            PAGE_ONLINE,
            [o_back, o_search, o_url, o_prev, o_play, o_next, o_mute],
        )

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------
    def _register_parameter_callbacks(self) -> None:
        pm = self.parameter_manager
        pm.add_parameter_callback("volume", self._on_volume_param)
        pm.add_parameter_callback("mute", self._on_mute_param)
        pm.add_parameter_callback("seek_percent", self._on_seek_param)
        pm.add_parameter_callback("play_mode", self._on_mode_param)
        pm.add_parameter_callback("search_keyword", self._on_keyword_param)
        pm.add_parameter_callback("online_url", self._on_url_param)

    def _read_param(self, name: str) -> Any:
        try:
            param = self.parameter_manager.get_parameter(name)
            if name == "volume":
                return param.as_int()
            if name == "mute":
                return param.as_bool()
            if name == "seek_percent":
                return param.as_float()
            return param.as_string()
        except Exception:
            return None

    def _write_param(self, name: str, value: Any) -> None:
        try:
            self.parameter_manager.set_parameter((name, value))
        except Exception as exc:
            self.logger.warn(f"set param {name} failed: {exc}")

    def _refresh_parameter_values(self) -> None:
        self._write_param("volume", int(self._volume))
        self._write_param("mute", bool(self._muted))
        self._write_param("play_mode", self._mode)
        self._write_param("seek_percent", float(self._seek_percent()))

    def _on_volume_param(self, param) -> None:
        if not self._params_ready:
            # Startup default push from the schema; the loaded setting wins.
            return
        try:
            value = max(0, min(100, int(param.as_int())))
        except Exception:
            return
        with self._lock:
            if value == self._volume and not self._muted:
                return
            self._volume = value
            was_muted = self._muted
            if value > 0:
                self._last_nonzero_volume = value
                self._muted = False
        self._apply_volume(value)
        if was_muted:
            # Keep the parameter page in sync with the mute button.
            self._write_param("mute", False)
        self._save_settings()
        self._push_ui_state()

    def _on_mute_param(self, param) -> None:
        if not self._params_ready:
            return
        try:
            muted = bool(param.as_bool())
        except Exception:
            return
        with self._lock:
            if muted == self._muted:
                return
            self._muted = muted
            target = 0 if muted else self._last_nonzero_volume
        self._apply_volume(target)
        self._push_ui_state()
        self._save_settings()

    def _on_seek_param(self, param) -> None:
        if not self._params_ready:
            return
        try:
            percent = float(param.as_float())
        except Exception:
            return
        with self._lock:
            track = self._current
            duration = self._duration
            if track is None or duration <= 0:
                return
        self._seek_to(duration * max(0.0, min(100.0, percent)) / 100.0)

    def _on_mode_param(self, param) -> None:
        if not self._params_ready:
            return
        try:
            mode = str(param.as_string())
        except Exception:
            return
        if mode not in MODES or mode == self._mode:
            return
        with self._lock:
            self._mode = mode
            self._shuffle_bag = []
        self._save_settings()
        self._push_ui_state()

    def _on_keyword_param(self, param) -> None:
        try:
            self._params["search_keyword"] = str(param.as_string())
        except Exception:
            pass

    def _on_url_param(self, param) -> None:
        try:
            self._params["online_url"] = str(param.as_string())
        except Exception:
            pass

    def _apply_volume(self, value_percent: int, persist: bool = True) -> None:
        """Map 0..100 onto the K1 system volume (0.0..1.0)."""

        target = max(0.0, min(1.0, float(value_percent) / 100.0))
        try:
            self.robot.audio_manager.set_system_volume(target)
        except Exception as exc:
            self.logger.warn(f"set_system_volume({target:.2f}) failed: {exc}")
        if persist:
            self.logger.info(f"system volume -> {value_percent}%")

    # ------------------------------------------------------------------
    # Tab / mode / mute handlers
    # ------------------------------------------------------------------
    def on_tab_online(self, component: Component) -> Optional[LocaleString]:
        if not self._online_ok:
            return LocaleString(
                {"en": "No network, online tab disabled", "zh": "网络不可用，在线音乐已禁用"}
            )
        with self._lock:
            self._tab = PAGE_ONLINE
        self._use_playlist(PAGE_ONLINE)
        try:
            self.page_proxy.force_update()
        except Exception:
            pass
        self._push_ui_state()
        return LocaleString("Online music", "在线音乐")

    def on_tab_local(self, component: Component) -> Optional[LocaleString]:
        with self._lock:
            self._tab = PAGE_LOCAL
        self._use_playlist(PAGE_LOCAL)
        try:
            self.page_proxy.force_update()
        except Exception:
            pass
        self._push_ui_state()
        return LocaleString("Local music", "本地音乐")

    def on_cycle_mode(self, component: Component) -> Optional[LocaleString]:
        with self._lock:
            idx = MODES.index(self._mode) if self._mode in MODES else 0
            self._mode = MODES[(idx + 1) % len(MODES)]
            self._shuffle_bag = []
            mode = self._mode
        self._write_param("play_mode", mode)
        self._save_settings()
        self._push_ui_state()
        return LocaleString(
            {
                "en": f"Mode: {self._mode_text(mode, 'en')}",
                "zh": f"播放模式：{self._mode_text(mode, 'zh')}",
            }
        )

    @staticmethod
    def _mode_text(mode: str, lang: str) -> str:
        table = {
            MODE_ORDER: {"zh": "顺序播放", "en": "Sequential"},
            MODE_SHUFFLE: {"zh": "随机播放", "en": "Shuffle"},
            MODE_REPEAT_ONE: {"zh": "单曲循环", "en": "Repeat one"},
        }
        return table.get(mode, table[MODE_ORDER]).get(lang, mode)

    def on_mute(self, component: Component) -> Optional[LocaleString]:
        with self._lock:
            self._muted = not self._muted
            muted = self._muted
            target = 0 if muted else self._last_nonzero_volume
        self._apply_volume(target)
        self._write_param("mute", muted)
        self._save_settings()
        self._push_ui_state()
        return LocaleString(
            {"en": "Muted" if muted else "Unmuted", "zh": "已静音" if muted else "已取消静音"}
        )

    # ------------------------------------------------------------------
    # Transport handlers (fast: only enqueue work)
    # ------------------------------------------------------------------
    def on_play_pause(self, component: Component) -> Optional[LocaleString]:
        with self._lock:
            track = self._current
            paused = self._paused
            playing = self._handle is not None and not paused
        if track is None:
            # Nothing loaded yet: play the current tab's first item.
            if not self._tracks:
                return LocaleString(
                    {"en": "Playlist is empty", "zh": "播放列表为空，请先添加音乐"}
                )
            with self._lock:
                self._index = 0 if self._index < 0 else self._index
                track = self._tracks[self._index]
            self._start_playback(track, 0.0)
            return LocaleString("Playing", "开始播放")

        if playing:
            self._pause()
            return LocaleString("Paused", "已暂停")
        if paused:
            with self._lock:
                offset = self._offset
            self._start_playback(track, offset)
            return LocaleString("Resumed", "继续播放")
        self._start_playback(track, 0.0)
        return LocaleString("Playing", "开始播放")

    def on_prev(self, component: Component) -> Optional[LocaleString]:
        self._advance(step=-1, manual=True)
        return LocaleString("Previous", "上一首")

    def on_next(self, component: Component) -> Optional[LocaleString]:
        self._advance(step=1, manual=True)
        return LocaleString("Next", "下一首")

    def on_scan(self, component: Component) -> Optional[LocaleString]:
        threading.Thread(target=self._scan_and_report, daemon=True).start()
        return LocaleString("Scanning local music...", "正在扫描本地音乐...")

    def _scan_and_report(self) -> None:
        count = self._scan_local(refresh_ui=True)
        self._toast_pending = LocaleString(
            {
                "en": f"Found {count} local track(s)",
                "zh": f"本地音乐扫描完成，共 {count} 首",
            }
        )

    # ------------------------------------------------------------------
    # Playlist selection
    # ------------------------------------------------------------------
    def _use_playlist(self, page: str) -> None:
        """Switch the active playlist to the one owned by ``page``."""

        with self._lock:
            target = self._online_tracks if page == PAGE_ONLINE else self._local_tracks
            self._tracks = list(target)
            current = self._current
            index = -1
            if current is not None:
                for position, track in enumerate(self._tracks):
                    if track.location == current.location:
                        index = position
                        break
            self._index = index
            self._shuffle_bag = []

    # ------------------------------------------------------------------
    # Playlist
    # ------------------------------------------------------------------
    def _scan_local(self, refresh_ui: bool) -> int:
        root = self._storage_root
        found: list[Track] = []
        for base in (root / MUSIC_DIR, root):
            if not base.exists():
                continue
            try:
                for path in self._iter_audio_files(base, MAX_SCAN_DEPTH):
                    found.append(
                        Track(
                            title=path.stem,
                            source="local",
                            location=str(path),
                            duration=0.0,
                        )
                    )
            except Exception as exc:
                self.logger.warn(f"scan {base} failed: {exc}")
        # de-duplicate by path, keep order
        seen: set[str] = set()
        unique: list[Track] = []
        for track in found:
            if track.location in seen:
                continue
            seen.add(track.location)
            unique.append(track)

        with self._lock:
            self._local_tracks = unique
            if self._tab == PAGE_LOCAL or not self._tracks:
                self._tracks = list(unique)
                if self._index >= len(unique):
                    self._index = -1
                self._shuffle_bag = []
                self._tab = PAGE_LOCAL
        self.logger.info(f"local scan: {len(unique)} track(s) under {root}")
        if refresh_ui:
            self._push_ui_state()
        return len(unique)

    @staticmethod
    def _iter_audio_files(base: Path, max_depth: int):
        base_depth = len(base.parts)
        stack = [base]
        while stack:
            current = stack.pop()
            try:
                entries = list(os.scandir(current))
            except OSError:
                continue
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if len(Path(entry.path).parts) - base_depth < max_depth:
                            stack.append(Path(entry.path))
                    elif entry.is_file() and entry.name.lower().endswith(AUDIO_EXTS):
                        yield Path(entry.path)
                except OSError:
                    continue

    # ------------------------------------------------------------------
    # Online search
    # ------------------------------------------------------------------
    def on_search(self, component: Component) -> Optional[LocaleString]:
        keyword = str(self._params.get("search_keyword", "") or "").strip()
        if not keyword:
            return LocaleString(
                {"en": "Enter a keyword in the parameter page first", "zh": "请先在参数页填写搜索关键词"}
            )
        if not self._online_ok:
            return LocaleString("No network", "网络异常，无法搜索")
        threading.Thread(target=self._search_worker, args=(keyword,), daemon=True).start()
        return LocaleString("Searching...", "正在搜索...")

    def _search_worker(self, keyword: str) -> None:
        try:
            query = urllib.parse.urlencode(
                {
                    "term": keyword,
                    "media": "music",
                    "limit": MAX_ONLINE_RESULTS,
                    "country": "CN",
                }
            )
            url = f"{ITUNES_SEARCH_URL}?{query}"
            request = urllib.request.Request(
                url, headers={"User-Agent": "BoosterK1MusicAgent/1.0"}
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))

            results: list[Track] = []
            for item in payload.get("results", []) or []:
                preview = item.get("previewUrl")
                if not preview:
                    continue
                results.append(
                    Track(
                        title=str(item.get("trackName") or "Unknown"),
                        artist=str(item.get("artistName") or ""),
                        source="online",
                        location=str(preview),
                        duration=float(item.get("trackTimeMillis") or 0) / 1000.0,
                    )
                )
            if not results:
                self._toast_pending = LocaleString(
                    {"en": "No result", "zh": "没有找到可播放的音频"}
                )
                return
            with self._lock:
                self._online_tracks = results
                self._search_results = results
                self._tab = PAGE_ONLINE
            self._use_playlist(PAGE_ONLINE)
            self._toast_pending = LocaleString(
                {
                    "en": f"{len(results)} result(s). Press play.",
                    "zh": f"找到 {len(results)} 首，按播放开始",
                }
            )
            try:
                self.page_proxy.force_update()
            except Exception:
                pass
            self._push_ui_state()
        except Exception as exc:
            self.logger.warn(f"search failed: {exc.__class__.__name__}: {exc}")
            self._online_ok = self._check_network()
            self._toast_pending = LocaleString(
                {"en": "Search failed, check network", "zh": "搜索失败，请检查网络"}
            )
            self._push_ui_state()

    def on_play_url(self, component: Component) -> Optional[LocaleString]:
        url = str(self._params.get("online_url", "") or "").strip()
        if not re.match(r"^https?://", url, re.IGNORECASE):
            return LocaleString(
                {"en": "Set a valid http(s) URL in the parameter page", "zh": "请先在参数页填写有效的 http(s) 音频直链"}
            )
        if not self._online_ok:
            return LocaleString("No network", "网络异常，无法播放在线音频")
        track = Track(
            title=os.path.basename(urllib.parse.urlparse(url).path) or "Online audio",
            source="online",
            location=url,
        )
        with self._lock:
            self._online_tracks = [track]
            self._tab = PAGE_ONLINE
        self._start_playback(track, 0.0)
        self._use_playlist(PAGE_ONLINE)
        return LocaleString("Loading URL...", "正在加载音频直链...")

    # ------------------------------------------------------------------
    # Playback engine
    # ------------------------------------------------------------------
    def _current_track(self) -> Optional[Track]:
        with self._lock:
            if 0 <= self._index < len(self._tracks):
                return self._tracks[self._index]
        return self._current

    def _start_playback(self, track: Track, offset: float) -> None:
        with self._lock:
            self._generation += 1
            generation = self._generation
            previous = self._handle
            self._handle = None
            self._current = track
            self._loading = True
            self._paused = False
            self._offset = max(0.0, offset)
            self._started_at = 0.0
            self._duration = float(track.duration or 0.0)
        if previous is not None:
            try:
                previous.cancel()
            except Exception:
                pass
        threading.Thread(
            target=self._play_worker,
            args=(generation, track, max(0.0, offset)),
            daemon=True,
        ).start()
        self._push_ui_state()

    def _is_current(self, generation: int) -> bool:
        with self._lock:
            return (not self._shutdown) and self._generation == generation

    def _play_worker(self, generation: int, track: Track, offset: float) -> None:
        try:
            source = self._prepare_source(track, offset)
            if source is None:
                raise RuntimeError("format not supported or file damaged")
            if not self._is_current(generation):
                return

            duration = float(track.duration or 0.0)
            if duration <= 0:
                duration = self._probe_duration(source)
                if duration <= 0 and track.source == "local":
                    duration = self._probe_duration(track.location)
                with self._lock:
                    if self._generation == generation:
                        self._duration = duration
                        track.duration = duration

            handle = self.robot.play_sound(str(source))
            with self._lock:
                if self._generation != generation or self._shutdown:
                    stale = True
                else:
                    stale = False
                    self._handle = handle
                    self._started_at = time.monotonic()
                    self._offset = offset
                    self._loading = False
            if stale:
                try:
                    handle.cancel()
                except Exception:
                    pass
                return

            self._push_ui_state()
            try:
                handle.wait(timeout=duration + PLAYBACK_EXTRA_TIMEOUT_SEC)
            except Exception as exc:
                self.logger.warn(f"wait playback failed: {exc}")
            self._on_track_finished(generation)
        except Exception as exc:
            self.logger.error(
                f"playback failed ({track.title}): {exc.__class__.__name__}: {exc}"
            )
            with self._lock:
                current = self._generation == generation
                if current:
                    self._loading = False
            if current:
                self._toast_pending = LocaleString(
                    {
                        "en": "Cannot play: invalid link / damaged file / unsupported format",
                        "zh": "无法播放：链接失效 / 文件损坏 / 格式不支持",
                    }
                )
                self._push_ui_state()

    def _prepare_source(self, track: Track, offset: float) -> Optional[Path]:
        """Return a path that robot.play_sound() can play.

        Local files at offset 0 are played directly (native decoder).
        Everything else (offset > 0, or a remote URL) is transcoded to PCM wav
        with ffmpeg so that playback can start at an arbitrary position.
        """

        if track.source == "local":
            path = Path(track.location)
            if not path.is_file():
                return None
            if offset <= 0.01:
                return path
            return self._transcode(track.location, offset)

        return self._transcode(track.location, offset)

    def _ffmpeg(self) -> Optional[str]:
        return shutil.which("ffmpeg")

    def _transcode(self, source: str, offset: float) -> Optional[Path]:
        ffmpeg = self._ffmpeg()
        if ffmpeg is None:
            # No ffmpeg: only whole-file playback of a local mp3/wav is possible.
            if offset <= 0.01 and not str(source).lower().startswith("http"):
                return Path(source)
            return None

        self._cache_dir.mkdir(parents=True, exist_ok=True)
        target = self._cache_dir / "current_track.wav"
        command = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error"]
        if offset > 0.01:
            command += ["-ss", f"{offset:.3f}"]
        command += [
            "-i", str(source),
            "-ac", str(PCM_CHANNELS),
            "-ar", str(PCM_SAMPLE_RATE),
            "-c:a", "pcm_s16le",
            "-f", "wav",
            str(target),
        ]
        try:
            subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=FFMPEG_TRANSCODE_TIMEOUT_SEC,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or b"").decode("utf-8", "replace")[:300]
            self.logger.warn(f"ffmpeg transcode failed: {detail}")
            return None
        except subprocess.TimeoutExpired:
            self.logger.warn("ffmpeg transcode timed out")
            return None
        except Exception as exc:
            self.logger.warn(f"ffmpeg transcode error: {exc}")
            return None
        if target.is_file() and target.stat().st_size > 44:
            return target
        return None

    def _probe_duration(self, path: Any) -> float:
        """Read track duration by asking ffmpeg for the stream info."""

        ffmpeg = self._ffmpeg()
        if ffmpeg is None:
            return 0.0
        try:
            completed = subprocess.run(
                [ffmpeg, "-hide_banner", "-i", str(path)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=FFMPEG_PROBE_TIMEOUT_SEC,
            )
            text = (completed.stderr or b"").decode("utf-8", "replace")
            match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", text)
            if match:
                hours, minutes, seconds = match.groups()
                return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except Exception as exc:
            self.logger.warn(f"probe duration failed: {exc}")
        return 0.0

    def _position(self) -> float:
        with self._lock:
            if self._handle is None or self._paused:
                return self._offset
            elapsed = time.monotonic() - self._started_at
            position = self._offset + max(0.0, elapsed)
            if self._duration > 0:
                return min(self._duration, position)
            return position

    def _seek_percent(self) -> float:
        with self._lock:
            duration = self._duration
        if duration <= 0:
            return 0.0
        return max(0.0, min(100.0, self._position() / duration * 100.0))

    def _pause(self) -> None:
        with self._lock:
            position = self._position()
            self._generation += 1
            handle = self._handle
            self._handle = None
            self._paused = True
            self._offset = position
        if handle is not None:
            try:
                handle.cancel()
            except Exception:
                pass
        self._push_ui_state()

    def _seek_to(self, position: float) -> None:
        with self._lock:
            track = self._current
            duration = self._duration
        if track is None:
            return
        position = max(0.0, min(duration if duration > 0 else position, position))
        self._start_playback(track, position)

    def _on_track_finished(self, generation: int) -> None:
        with self._lock:
            if self._generation != generation or self._shutdown:
                return
            self._handle = None
            self._paused = False
            self._offset = 0.0
            mode = self._mode
        if mode == MODE_REPEAT_ONE:
            track = self._current_track()
            if track is not None:
                self._start_playback(track, 0.0)
                return
        self._advance(step=1, manual=False)

    def _advance(self, step: int, manual: bool) -> None:
        with self._lock:
            if not self._tracks:
                self._toast_pending = LocaleString(
                    {"en": "Playlist is empty", "zh": "播放列表为空"}
                )
                return
            total = len(self._tracks)
            mode = self._mode

            if mode == MODE_SHUFFLE and step > 0 and not manual:
                if not self._shuffle_bag:
                    self._shuffle_bag = list(range(total))
                    random.shuffle(self._shuffle_bag)
                    if self._shuffle_bag and self._shuffle_bag[0] == self._index:
                        self._shuffle_bag.append(self._shuffle_bag.pop(0))
                next_index = self._shuffle_bag.pop(0)
                self._index = next_index
            else:
                next_index = self._index + step
                if next_index >= total:
                    if manual:
                        next_index = 0
                    else:
                        # Sequential mode: stop at the end of the list.
                        self._index = total - 1
                        self._stop_internal()
                        return
                elif next_index < 0:
                    next_index = total - 1
                self._index = next_index

            track = self._tracks[self._index]
            self._shuffle_bag = [i for i in self._shuffle_bag if i != self._index]
        self._start_playback(track, 0.0)

    def _stop_internal(self) -> None:
        with self._lock:
            self._generation += 1
            handle = self._handle
            self._handle = None
            self._paused = False
            self._offset = 0.0
            self._current = None
        if handle is not None:
            try:
                handle.cancel()
            except Exception:
                pass
        self._push_ui_state()

    # ------------------------------------------------------------------
    # Network supervision
    # ------------------------------------------------------------------
    @staticmethod
    def _check_network() -> bool:
        for host, port in NETWORK_PROBES:
            try:
                with socket.create_connection((host, port), timeout=NETWORK_TIMEOUT_SEC):
                    return True
            except OSError:
                continue
        return False

    def _network_watcher(self) -> None:
        while not self._shutdown:
            time.sleep(NETWORK_CHECK_INTERVAL_SEC)
            if self._shutdown:
                return
            reachable = self._check_network()
            changed = reachable != self._online_ok
            self._online_ok = reachable
            if not reachable:
                with self._lock:
                    if self._tab == PAGE_ONLINE:
                        self._tab = PAGE_LOCAL
                        changed = True
            if changed:
                try:
                    self.page_proxy.force_update()
                except Exception:
                    pass
                self._push_ui_state()

    # ------------------------------------------------------------------
    # UI refresh
    # ------------------------------------------------------------------
    def _ui_ticker(self) -> None:
        while not self._shutdown:
            time.sleep(UI_TICK_SEC)
            if self._shutdown:
                return
            try:
                self._push_ui_state()
            except Exception:
                pass

    def _status_text(self) -> str:
        with self._lock:
            track = self._current
            tracks = self._tracks
            index = self._index
            duration = self._duration
            paused = self._paused
            loading = self._loading
        if track is None:
            if not tracks:
                return "未在播放 · 歌单为空"
            return f"未在播放 · 共 {len(tracks)} 首"
        prefix = "▶" if not paused else "⏸"
        if loading:
            prefix = "…"
        total = duration if duration > 0 else (track.duration or 0.0)
        position = self._position()
        counter = f"{index + 1}/{len(tracks)}" if tracks and index >= 0 else "-"
        return f"{prefix} {counter} {track.title[:16]}  {_fmt_time(position)}/{_fmt_time(total)}"

    def _push_ui_state(self) -> None:
        pending = self._toast_pending
        if pending is not None:
            self._toast_pending = None
            try:
                self.component_manager.publish_toast(
                    pending, ToastPosition.kBOTTOM, ToastIcon.kNONE
                )
            except Exception as exc:
                self.logger.warn(f"publish toast failed: {exc}")

        text = self._status_text()
        active = self._active_page_id()
        status_key = "local_status" if active == PAGE_LOCAL else "online_status"
        if text != self._last_status_text:
            self._last_status_text = text
            for key in ("local_status", "online_status"):
                component = self._ui.get(key)
                if component is None:
                    continue
                try:
                    component.name = LocaleString(text, text)
                except Exception as exc:
                    self.logger.warn(f"set text {key} failed: {exc}")
            component = self._ui.get(status_key)
            if component is not None:
                try:
                    self.component_manager.update_component(component)
                except Exception as exc:
                    self.logger.warn(f"update status failed: {exc}")

        with self._lock:
            playing = self._handle is not None and not self._paused
            loading = self._loading
            muted = self._muted
            mode = self._mode
        self._set_toggle("local_play", playing, loading)
        self._set_toggle("online_play", playing, loading)
        self._set_toggle("local_mute", muted)
        self._set_toggle("online_mute", muted)
        self._set_icon("local_mode", self._mode_icon(mode), self._mode_text(mode, "zh"))

    def _is_active_key(self, key: str) -> bool:
        """Only components of the visible page exist in the App's list."""

        active = self._active_page_id()
        if key.startswith("local_"):
            return active == PAGE_LOCAL
        if key.startswith("online_"):
            return active == PAGE_ONLINE
        return True

    def _set_toggle(self, key: str, state: bool, loading: bool = False) -> None:
        component = self._ui.get(key)
        if component is None:
            return
        try:
            changed = bool(component.state) != state
            if changed:
                component.state = state
            try:
                component.set_selection_ring_loading(loading)
            except Exception:
                pass
            # Components on an inactive page are not in the App's component list;
            # updating them only produces "not found in component list" warnings.
            if self._is_active_key(key) and (changed or loading):
                self.component_manager.update_component(component)
        except Exception as exc:
            self.logger.warn(f"set toggle {key} failed: {exc}")

    def _set_icon(self, key: str, icon_path: str, zh_name: str) -> None:
        component = self._ui.get(key)
        if component is None:
            return
        try:
            if getattr(component, "path", "") != icon_path:
                component.path = icon_path
                component.name = LocaleString(zh_name, zh_name)
                if self._is_active_key(key):
                    self.component_manager.update_component(component)
        except Exception as exc:
            self.logger.warn(f"set icon {key} failed: {exc}")

    @staticmethod
    def _mode_icon(mode: str) -> str:
        return {
            MODE_ORDER: "res/mode_order.png",
            MODE_SHUFFLE: "res/mode_shuffle.png",
            MODE_REPEAT_ONE: "res/mode_repeat.png",
        }.get(mode, "res/mode_order.png")

    # ------------------------------------------------------------------
    # Cache cleanup
    # ------------------------------------------------------------------
    def _cleanup_cache(self) -> None:
        try:
            cache = self._storage_root / self._cache_dir
            if cache.is_dir():
                for entry in os.scandir(cache):
                    if entry.is_file() and entry.name.startswith("current_track"):
                        try:
                            os.remove(entry.path)
                        except OSError:
                            pass
        except Exception as exc:
            self.logger.warn(f"cleanup cache failed: {exc}")
