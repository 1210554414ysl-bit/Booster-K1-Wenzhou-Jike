# coding: utf-8
"""Local music player Agent for Booster K1 (online playback removed).

Plays mp3/wav files from the Agent private storage directory with:
- play / pause / previous / next
- sequential / shuffle / repeat-one play modes
- seek (via a parameter slider, decoded from that position by ffmpeg)
- system volume control (buttons + parameter slider + mute)
- persisted play mode and volume

Threading model
---------------
* One worker owns the FFmpeg audio process and stops it before starting another.
* Callbacks replace the pending playback request without waiting for audio I/O.
* One UI thread publishes state changes without blocking the button callbacks.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import shutil
import subprocess
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from booster_agent_framework import (
    AgentBase,
    AgentFeatures,
    Component,
    ComponentOrientation,
    ComponentStatePageProxy,
    DefaultStateIconComponent,
    IconComponent,
    LocaleString,
    OnlineWebviewComponent,
    PlaceholderComponent,
    ToastIcon,
    ToastPosition,
)

from boosteros.robots.booster import BoosterRobot

# ----------------------------------------------------------------------
# Pages / modes
# ----------------------------------------------------------------------
PAGE_LOCAL = "page_local"

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
PANEL_BUNDLE = Path("resources/music_panel.zip")
AUDIO_EXTS = (".mp3", ".wav")
MAX_SCAN_DEPTH = 4

UI_TICK_SEC = 1.0
PARAM_SETTLE_SEC = 2.0
FFMPEG_PROBE_TIMEOUT_SEC = 20.0
PLAYBACK_POLL_SEC = 0.2
PLAYBACK_STOP_SEC = 0.5
VOLUME_STEP_PERCENT = 10


def _fmt_time(seconds: float) -> str:
    """Format seconds as mm:ss (or h:mm:ss when long)."""

    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def _serve_panel() -> tuple[ThreadingHTTPServer, str]:
    content_types = {
        "/": "text/html; charset=utf-8",
        "/player.css": "text/css; charset=utf-8",
        "/player.js": "text/javascript; charset=utf-8",
    }
    with zipfile.ZipFile(PANEL_BUNDLE) as bundle:
        assets = {
            route: bundle.read(f"dist/{'index.html' if route == '/' else route[1:]}")
            for route in content_types
        }
    interfaces = json.loads(subprocess.check_output(
        ["ip", "-j", "-4", "addr", "show", "up", "scope", "global"], timeout=3
    ))
    addresses = [
        (interface["ifname"], address["local"])
        for interface in interfaces
        for address in interface.get("addr_info", [])
        if interface["ifname"].startswith(("wl", "en", "eth", "usb"))
    ]
    if not addresses:
        raise RuntimeError("no local IPv4 address for the player panel")
    host = next((address for name, address in addresses if name.startswith("wl")), addresses[0][1])

    class PanelHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            route = urlsplit(self.path).path
            if route not in assets:
                self.send_error(404)
                return
            content = assets[route]
            self.send_response(200)
            self.send_header("Content-Type", content_types[route])
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content)

        def log_message(self, format, *args) -> None:
            pass

    server = ThreadingHTTPServer(("0.0.0.0", 0), PanelHandler)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
    return server, f"http://{host}:{server.server_port}/"


class Track:
    """One playable local file."""

    __slots__ = ("key", "title", "location", "duration")

    def __init__(self, title: str, location: str, duration: float = 0.0) -> None:
        self.key = hashlib.blake2s(os.fsencode(location), digest_size=12).hexdigest()
        self.title = title
        self.location = location
        self.duration = duration


class MusicPlayerAgent(AgentBase):
    """Local music player agent."""

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

        # --- locks / threading --------------------------------------------
        self._lock = threading.RLock()
        self._ui_changed = threading.Event()
        self._robot_lock = threading.RLock()
        self._playback_changed = threading.Condition(self._lock)
        self._pending_playback: Optional[tuple[int, Track, float]] = None
        self._shutdown = False

        # --- robot (lazy: motion services may not be ready at boot) --------
        self.robot: Optional[BoosterRobot] = None
        self._get_robot()

        # --- state ---------------------------------------------------------
        self._mode = MODE_ORDER
        self._volume = 50
        self._muted = False

        self._tracks: list[Track] = []
        self._index = -1
        self._shuffle_bag: list[int] = []
        self._shuffle_history: list[int] = []

        self._handle = None
        self._generation = 0
        self._loading = False
        self._paused = False
        self._offset = 0.0
        self._started_at = 0.0
        self._duration = 0.0
        self._current: Optional[Track] = None

        self._params: dict[str, Any] = {}
        self._params_ready = False
        self._catalog_revision = 0
        self._panel_server: Optional[ThreadingHTTPServer] = None

        self._ui: dict[str, Component] = {}
        self._last_status_text = ""
        self._play_loading = False
        self._toast_pending: Optional[LocaleString] = None

        self._cache_dir = self._storage_root / CACHE_DIR
        self._music_dir = Path(MUSIC_DIR)
        self._ensure_dirs()
        self._load_settings()
        self._build_ui()
        self._register_parameter_callbacks()
        try:
            self._scan_local(refresh_ui=False)
        except Exception as exc:
            self.logger.warn(f"initial scan failed: {exc}")
        self._playback_thread = threading.Thread(target=self._playback_worker, daemon=True)
        self._ui_thread = threading.Thread(target=self._ui_ticker, daemon=True)
        self._playback_thread.start()

    # ------------------------------------------------------------------
    # Bootstrap helpers
    # ------------------------------------------------------------------
    @property
    def _storage_root(self) -> Path:
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
            self._muted = bool(data.get("muted", False))
            self.logger.info(
                f"settings loaded: mode={self._mode} volume={self._volume} muted={self._muted}"
            )
        except Exception as exc:
            self.logger.warn(f"load settings failed: {exc.__class__.__name__}: {exc}")

    def _save_settings(self) -> None:
        try:
            self.storage_manager.write_text_file(
                SETTINGS_FILE,
                json.dumps(
                    {"mode": self._mode, "volume": self._volume, "muted": self._muted},
                    ensure_ascii=False,
                ),
            )
        except Exception as exc:
            self.logger.warn(f"save settings failed: {exc.__class__.__name__}: {exc}")

    def _get_robot(self) -> Optional[BoosterRobot]:
        with self._robot_lock:
            if self.robot is not None:
                return self.robot
            try:
                self.robot = BoosterRobot()
                self.logger.info("BoosterRobot ready")
            except Exception as exc:
                self.robot = None
                self.logger.warn(
                    f"BoosterRobot init failed (will retry): {exc.__class__.__name__}: {exc}"
                )
            return self.robot

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_agent_activated(self) -> None:
        self.logger.info("MusicPlayerAgent activated")
        self._push_ui_state()
        threading.Thread(target=self._activate_worker, daemon=True).start()

    def _activate_worker(self) -> None:
        try:
            self._scan_local(refresh_ui=True)
            self._refresh_parameter_values()
            time.sleep(PARAM_SETTLE_SEC)
            if self._shutdown:
                return
            self._refresh_parameter_values()
            self._params_ready = True
            self._get_robot()
            self._apply_volume(0 if self._muted else self._volume)
            self._push_ui_state()
        except Exception as exc:
            self.logger.error(f"activation failed: {exc.__class__.__name__}: {exc}")

    def on_agent_close(self) -> None:
        self.logger.info("MusicPlayerAgent closing")
        with self._playback_changed:
            self._shutdown = True
            self._generation += 1
            self._pending_playback = None
            self._handle = None
            self._playback_changed.notify()
        self._ui_changed.set()
        # Give the audio worker a bounded chance to stop before process teardown.
        self._playback_thread.join(timeout=1.0)
        if self._panel_server is not None:
            self._panel_server.shutdown()
            self._panel_server.server_close()
        self._save_settings()

    # ------------------------------------------------------------------
    # UI construction (single local page)
    # ------------------------------------------------------------------
    def _icon(
        self, cid, zh, en, icon, callback, sequence, shortcut_id=""
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
        self, cid, zh, en, icon, state, callback, sequence, shortcut_id=""
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

    def _build_ui(self) -> None:
        self.page_proxy = ComponentStatePageProxy(self, lambda _s=None: PAGE_LOCAL)

        status = PlaceholderComponent(
            "local_status",
            LocaleString("Idle", "未在播放"),
            "res/music_note.png",
            display_sequence=0,
        )
        prev_btn = self._icon("local_prev", "上一首", "Previous", "res/prev.png", self.on_prev, 1, shortcut_id="prev")
        play_btn = self._toggle("local_play", "播放", "Play", "res/play.png", False, self.on_play_pause, 2, shortcut_id="play_pause")
        next_btn = self._icon("local_next", "下一首", "Next", "res/next.png", self.on_next, 3, shortcut_id="next")
        mode_btn = self._icon("local_mode", "播放模式", "Play mode", "res/mode_order.png", self.on_cycle_mode, 4, shortcut_id="mode")
        mute_btn = self._toggle("local_mute", "静音", "Mute", "res/mute.png", False, self.on_mute, 5, shortcut_id="mute")
        vol_down = self._icon("local_vol_down", "音量 -", "Volume -", "res/vol_down.png", self.on_volume_down, 6)
        vol_up = self._icon("local_vol_up", "音量 +", "Volume +", "res/vol_up.png", self.on_volume_up, 7)
        scan_btn = self._icon("local_scan", "刷新歌单", "Rescan", "res/scan.png", self.on_scan, 8)

        self._ui["local_status"] = status
        self._ui["local_prev"] = prev_btn
        self._ui["local_play"] = play_btn
        self._ui["local_next"] = next_btn
        self._ui["local_mode"] = mode_btn
        self._ui["local_mute"] = mute_btn
        self._ui["local_vol_down"] = vol_down
        self._ui["local_vol_up"] = vol_up
        self._ui["local_scan"] = scan_btn

        self.page_proxy.register_component_v2(PAGE_LOCAL, status)
        self.page_proxy.register_components(
            PAGE_LOCAL,
            [prev_btn, play_btn, next_btn, mode_btn, mute_btn, vol_down, vol_up, scan_btn],
        )
        try:
            self._panel_server, url = _serve_panel()
            panel = OnlineWebviewComponent(
                "music_panel", LocaleString("Library & Progress", "歌单与进度"),
                "res/tab_local.png", url, self._on_panel_request, ComponentOrientation.kLANDSCAPE,
                display_sequence=9,
            )
            self._ui["music_panel"] = panel
            self.page_proxy.register_component(PAGE_LOCAL, panel)
            self.logger.info(f"Music panel available at {url}")
        except Exception as exc:
            self.logger.warn(f"music panel unavailable: {exc}")

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------
    def _register_parameter_callbacks(self) -> None:
        pm = self.parameter_manager
        pm.add_parameter_callback("volume", self._on_volume_param)
        pm.add_parameter_callback("mute", self._on_mute_param)
        pm.add_parameter_callback("seek_percent", self._on_seek_param)
        pm.add_parameter_callback("play_mode", self._on_mode_param)

    def _write_param(self, name: str, value: Any) -> bool:
        try:
            result = self.parameter_manager.set_parameter((name, value))
            if result.successful:
                return True
            self.logger.warn(f"set param {name} failed: {result.reason}")
        except Exception as exc:
            self.logger.warn(f"set param {name} failed: {exc}")
        return False

    def _refresh_parameter_values(self) -> None:
        self._write_param("volume", int(self._volume))
        self._write_param("mute", bool(self._muted))
        self._write_param("play_mode", self._mode)
        self._write_param("seek_percent", float(self._seek_percent()))

    def _on_volume_param(self, param) -> None:
        if not self._params_ready:
            return
        try:
            value = max(0, min(100, int(param.as_int())))
        except Exception:
            return
        with self._lock:
            if value == self._volume:
                return
            self._volume = value
            was_muted = self._muted
            if value > 0:
                self._muted = False
            muted = self._muted
        self._apply_volume(0 if muted else value)
        if was_muted != muted:
            self._write_param("mute", muted)
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
            target = 0 if muted else self._volume
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
        self._seek_to(percent)

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
            self._shuffle_bag.clear()
            self._shuffle_history.clear()
        self._save_settings()
        self._push_ui_state()

    def _on_panel_request(self, message: Any) -> dict:
        if not isinstance(message, dict) or self._shutdown:
            return {"success": False}
        action = message.get("command")
        if action == "status":
            return self._panel_status(message.get("revision"))
        if action == "play_pause":
            self.on_play_pause(None)
        elif action == "previous":
            self.on_prev(None)
        elif action == "next":
            self.on_next(None)
        elif action == "scan":
            self.on_scan(None)
        elif action == "select":
            with self._lock:
                index = next((i for i, track in enumerate(self._tracks) if track.key == message.get("track")), None)
                if index is None:
                    return {"success": False}
                self._select_track_locked(index)
            self._push_ui_state()
        elif action == "seek":
            percent = message.get("percent")
            if type(percent) not in (int, float) or not 0 <= percent <= 100:
                return {"success": False}
            self._seek_to(percent, expected_track=message.get("track"))
        elif action == "setting":
            name, value = message.get("name"), message.get("value")
            valid = (
                (name == "volume" and type(value) in (int, float) and 0 <= value <= 100 and value == int(value))
                or (name == "mute" and type(value) is bool)
                or (name == "play_mode" and value in MODES)
            )
            if not self._params_ready or not valid:
                return {"success": False}
            return {"success": self._write_param(name, int(value) if name == "volume" else value)}
        else:
            return {"success": False}
        return {"success": True}

    def _apply_volume(self, value_percent: int) -> None:
        target = max(0.0, min(1.0, float(value_percent) / 100.0))
        robot = self._get_robot()
        if robot is None:
            self.logger.warn(f"volume not applied (robot sdk not ready): {value_percent}%")
            return
        try:
            robot.audio_manager.set_system_volume(target)
            self.logger.info(f"system volume -> {value_percent}%")
        except Exception as exc:
            self.logger.warn(f"set_system_volume({target:.2f}) failed: {exc}")

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------
    def on_cycle_mode(self, component: Component) -> Optional[LocaleString]:
        with self._lock:
            idx = MODES.index(self._mode) if self._mode in MODES else 0
            self._mode = MODES[(idx + 1) % len(MODES)]
            self._shuffle_bag.clear()
            self._shuffle_history.clear()
            mode = self._mode
        self._write_param("play_mode", mode)
        self._save_settings()
        self._push_ui_state()
        return LocaleString(f"Mode: {self._mode_text(mode, 'en')}", f"播放模式：{self._mode_text(mode, 'zh')}")

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
            target = 0 if muted else self._volume
        self._apply_volume(target)
        self._write_param("mute", muted)
        self._save_settings()
        self._push_ui_state()
        return LocaleString("Muted" if muted else "Unmuted", "已静音" if muted else "已取消静音")

    def on_volume_up(self, component: Component) -> Optional[LocaleString]:
        return self._bump_volume(VOLUME_STEP_PERCENT)

    def on_volume_down(self, component: Component) -> Optional[LocaleString]:
        return self._bump_volume(-VOLUME_STEP_PERCENT)

    def _bump_volume(self, delta: int) -> Optional[LocaleString]:
        with self._lock:
            was_muted = self._muted
            target = max(0, min(100, int(self._volume) + delta))
            self._volume = target
            if target > 0:
                self._muted = False
            muted = self._muted
        self._apply_volume(0 if muted else target)
        self._write_param("volume", target)
        if was_muted != muted:
            self._write_param("mute", muted)
        self._save_settings()
        self._push_ui_state()
        return LocaleString(f"Volume {target}%", f"音量 {target}%")

    def on_play_pause(self, component: Component) -> Optional[LocaleString]:
        with self._lock:
            needs_scan = self._current is None and not self._tracks
        if needs_scan:
            self._scan_local(refresh_ui=False)
        with self._lock:
            track = self._current
            if track is None:
                if not self._tracks:
                    return LocaleString("Playlist is empty", "播放列表为空，请先添加音乐")
                if self._mode == MODE_SHUFFLE:
                    self._index = self._next_shuffle_index_locked()
                elif self._index < 0:
                    self._index = 0
                track = self._tracks[self._index]
            if self._handle is not None or self._loading:
                self._pause_locked()
                message = LocaleString("Paused", "已暂停")
            elif self._paused:
                self._start_playback_locked(track, self._offset)
                message = LocaleString("Resumed", "继续播放")
            else:
                self._start_playback_locked(track, 0.0)
                message = LocaleString("Playing", "开始播放")
        self._push_ui_state()
        return message

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
        self._toast_pending = LocaleString(f"Found {count} local track(s)", f"本地音乐扫描完成，共 {count} 首")

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
                    if self._cache_dir not in path.parents:
                        found.append(Track(title=path.stem, location=str(path), duration=0.0))
            except Exception as exc:
                self.logger.warn(f"scan {base} failed: {exc}")
        seen: set[str] = set()
        unique: list[Track] = []
        for track in found:
            if track.location in seen:
                continue
            seen.add(track.location)
            unique.append(track)
        with self._lock:
            if [track.key for track in self._tracks] != [track.key for track in unique]:
                self._shuffle_bag.clear()
                self._shuffle_history.clear()
                self._catalog_revision += 1
            self._tracks = unique
            if self._current is not None:
                self._index = -1
                for index, track in enumerate(unique):
                    if track.location == self._current.location:
                        unique[index] = self._current
                        self._index = index
                        break
            elif self._index >= len(unique):
                self._index = -1
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
    # Playback engine
    # ------------------------------------------------------------------
    def _start_playback_locked(self, track: Track, offset: float) -> None:
        if self._shutdown:
            return
        self._generation += 1
        self._handle = None
        self._current = track
        self._loading = True
        self._paused = False
        self._offset = max(0.0, offset)
        self._started_at = 0.0
        self._duration = track.duration
        self._pending_playback = (self._generation, track, self._offset)
        self._playback_changed.notify()

    def _is_current(self, generation: int) -> bool:
        with self._lock:
            return not self._shutdown and self._generation == generation

    def _playback_worker(self) -> None:
        while True:
            with self._playback_changed:
                self._playback_changed.wait_for(
                    lambda: self._shutdown or self._pending_playback is not None
                )
                if self._shutdown:
                    return
                request = self._pending_playback
                self._pending_playback = None
            self._play_track(*request)

    def _play_track(self, generation: int, track: Track, offset: float) -> None:
        try:
            source = Path(track.location)
            if not source.is_file():
                raise RuntimeError("audio file is missing")
            duration = track.duration or self._probe_duration(source, generation)
            if not self._is_current(generation):
                return
            if duration > 0 and offset >= duration:
                self._finish_playback(generation, succeeded=True)
                return

            command = self._ffmpeg_command(
                "-loglevel", "error", "-ss", f"{offset:.3f}", "-i", str(source),
                "-map", "0:a:0", "-ac", "2", "-ar", "48000",
                "-f", "pulse", "-buffer_duration", "150",
                "-stream_name", "K1 Music Player", "K1 Music Player",
            )
            with subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=self._playback_environment(),
            ) as player:
                try:
                    with self._lock:
                        if self._generation != generation or self._shutdown:
                            return
                        self._handle = player
                        self._started_at = time.monotonic()
                        self._offset = offset
                        self._duration = duration
                        self._loading = False
                        track.duration = duration
                    self._push_ui_state()

                    while self._is_current(generation):
                        try:
                            _, stderr = player.communicate(timeout=PLAYBACK_POLL_SEC)
                        except subprocess.TimeoutExpired:
                            continue
                        if player.returncode != 0:
                            detail = stderr.decode("utf-8", "replace").strip()[:300]
                            raise RuntimeError(f"ffmpeg playback failed ({player.returncode}): {detail}")
                        self._finish_playback(generation, succeeded=True)
                        return
                finally:
                    self._stop_player(player)
        except Exception as exc:
            if self._is_current(generation):
                self.logger.error(f"playback failed ({track.title}): {exc.__class__.__name__}: {exc}")
                self._finish_playback(
                    generation,
                    succeeded=False,
                    error=LocaleString("Unable to play this track", "无法播放这首歌"),
                )

    @staticmethod
    def _playback_environment() -> dict[str, str]:
        environment = os.environ.copy()
        if os.name == "posix" and not environment.get("PULSE_SERVER"):
            # The Agent service does not inherit the user's desktop session.
            pulse_socket = Path(f"/run/user/{os.getuid()}/pulse/native")
            if pulse_socket.is_socket():
                environment["PULSE_SERVER"] = f"unix:{pulse_socket}"
        return environment

    @staticmethod
    def _stop_player(player: subprocess.Popen) -> None:
        if player.poll() is not None:
            return
        player.terminate()
        try:
            player.communicate(timeout=PLAYBACK_STOP_SEC)
        except subprocess.TimeoutExpired:
            player.kill()
            player.communicate()

    @staticmethod
    def _ffmpeg_command(*arguments: str) -> list[str]:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("ffmpeg is unavailable")
        return [ffmpeg, "-nostdin", "-hide_banner", *arguments]
    def _run_ffmpeg(self, arguments: list[str], generation: int, timeout: float) -> tuple[int, bytes]:
        command = self._ffmpeg_command(*arguments)
        deadline = time.monotonic() + timeout
        with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE) as process:
            try:
                while self._is_current(generation):
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, timeout)
                    try:
                        _, stderr = process.communicate(timeout=min(remaining, PLAYBACK_POLL_SEC))
                        return process.returncode, stderr
                    except subprocess.TimeoutExpired:
                        continue
                return 0, b""
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

    def _probe_duration(self, path: Path, generation: int) -> float:
        try:
            _, stderr = self._run_ffmpeg(["-i", str(path)], generation, FFMPEG_PROBE_TIMEOUT_SEC)
            match = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr.decode("utf-8", "replace"))
            if match:
                hours, minutes, seconds = match.groups()
                return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except Exception as exc:
            self.logger.warn(f"probe duration failed: {exc}")
        return 0.0

    def _position_locked(self) -> float:
        if self._handle is None or self._paused:
            return self._offset
        position = self._offset + max(0.0, time.monotonic() - self._started_at)
        if self._duration > 0:
            return min(self._duration, position)
        return position

    def _seek_percent(self) -> float:
        with self._lock:
            if self._duration <= 0:
                return 0.0
            return max(0.0, min(100.0, self._position_locked() / self._duration * 100.0))

    def _pause_locked(self) -> None:
        self._offset = self._position_locked()
        self._paused = True
        self._loading = False
        self._handle = None
        self._generation += 1
        self._pending_playback = None

    def _seek_to(self, percent: float, expected_track: Optional[str] = None) -> None:
        with self._lock:
            if self._current is None or self._duration <= 0:
                return
            if expected_track is not None and self._current.key != expected_track:
                return
            position = self._duration * (max(0.0, min(100.0, percent)) / 100.0)
            self._start_playback_locked(self._current, position)
        self._push_ui_state()

    def _finish_playback(
        self, generation: int, succeeded: bool, error: Optional[LocaleString] = None
    ) -> None:
        with self._lock:
            if self._generation != generation or self._shutdown:
                return
            self._handle = None
            self._loading = False
            self._paused = False
            self._offset = 0.0
            if error is not None:
                self._toast_pending = error
            if succeeded:
                if self._mode == MODE_REPEAT_ONE and self._current is not None:
                    self._start_playback_locked(self._current, 0.0)
                else:
                    self._advance_locked(step=1, manual=False)
        self._push_ui_state()

    def _advance(self, step: int, manual: bool) -> None:
        with self._lock:
            self._advance_locked(step, manual)
        self._push_ui_state()

    def _advance_locked(self, step: int, manual: bool) -> None:
        if not self._tracks:
            self._toast_pending = LocaleString("Playlist is empty", "播放列表为空")
            return
        total = len(self._tracks)
        if self._mode == MODE_SHUFFLE:
            if step < 0:
                if not self._shuffle_history:
                    return
                previous = self._shuffle_history.pop()
                if self._index >= 0:
                    self._shuffle_bag.append(self._index)
                self._select_track_locked(previous, remember=False)
                return
            self._select_track_locked(self._next_shuffle_index_locked())
            return
        next_index = self._index + step
        if next_index >= total and not manual:
            self._index = total - 1
            self._current = None
            self._duration = 0.0
            return
        self._select_track_locked(total - 1 if next_index < 0 else next_index % total)

    def _next_shuffle_index_locked(self) -> int:
        if not self._shuffle_bag:
            self._shuffle_bag = list(range(len(self._tracks)))
            random.shuffle(self._shuffle_bag)
            if len(self._shuffle_bag) > 1 and self._shuffle_bag[-1] == self._index:
                other = random.randrange(len(self._shuffle_bag) - 1)
                self._shuffle_bag[other], self._shuffle_bag[-1] = self._shuffle_bag[-1], self._shuffle_bag[other]
        return self._shuffle_bag.pop()

    def _select_track_locked(self, index: int, remember: bool = True) -> None:
        if self._mode == MODE_SHUFFLE:
            if remember and self._index >= 0 and self._index != index:
                self._shuffle_history.append(self._index)
                if len(self._shuffle_history) > len(self._tracks):
                    self._shuffle_history.pop(0)
            if index in self._shuffle_bag:
                self._shuffle_bag.remove(index)
        self._index = index
        self._start_playback_locked(self._tracks[index], 0.0)
    # ------------------------------------------------------------------
    # UI refresh
    # ------------------------------------------------------------------
    def _ui_ticker(self) -> None:
        while not self._shutdown:
            self._ui_changed.wait(UI_TICK_SEC)
            self._ui_changed.clear()
            if self._shutdown:
                return
            try:
                self._refresh_ui()
            except Exception as exc:
                self.logger.warn(f"refresh UI failed: {exc}")

    def _status_text(self) -> str:
        with self._lock:
            track = self._current
            tracks = self._tracks
            index = self._index
            duration = self._duration
            paused = self._paused
            loading = self._loading
            playing = self._handle is not None
            position = self._position_locked()
        if track is None:
            if not tracks:
                return "未在播放 · 歌单为空"
            return f"未在播放 · 共 {len(tracks)} 首"
        prefix = "⏸" if paused else ("▶" if playing else "■")
        if loading:
            prefix = "…"
        total = duration if duration > 0 else (track.duration or 0.0)
        counter = f"{index + 1}/{len(tracks)}" if tracks and index >= 0 else "-"
        return f"{prefix} {counter} {track.title[:16]}  {_fmt_time(position)}/{_fmt_time(total)}"

    def _push_ui_state(self) -> None:
        with self._lock:
            if self._shutdown:
                return
            if self._ui_thread.ident is None:
                self._ui_thread.start()
        self._ui_changed.set()

    def _refresh_ui(self) -> None:
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
        if text != self._last_status_text:
            self._last_status_text = text
            component = self._ui.get("local_status")
            if component is not None:
                try:
                    component.name = LocaleString(text, text)
                    self.component_manager.update_component(component)
                except Exception as exc:
                    self.logger.warn(f"update status failed: {exc}")

        with self._lock:
            playing = self._handle is not None and not self._paused
            loading = self._loading
            muted = self._muted
            mode = self._mode
        self._set_toggle("local_play", playing, loading)
        self._set_toggle("local_mute", muted)
        self._set_icon("local_mode", self._mode_icon(mode), self._mode_text(mode, "zh"))

    def _panel_status(self, revision: Any) -> dict:
        with self._lock:
            current = self._current
            status = {
                "track": current.key if current else "",
                "title": current.title if current else "",
                "duration": self._duration,
                "position": self._position_locked(),
                "playing": self._handle is not None,
                "loading": self._loading,
                "paused": self._paused,
                "mode": self._mode,
                "volume": self._volume,
                "muted": self._muted,
                "ready": self._params_ready,
            }
            result = {"success": True, "state": status, "revision": self._catalog_revision}
            if revision != self._catalog_revision:
                result["catalog"] = [[track.key, track.title] for track in self._tracks]
            return result

    def _set_toggle(self, key: str, state: bool, loading: bool = False) -> None:
        component = self._ui.get(key)
        if component is None:
            return
        try:
            changed = bool(component.state) != state
            if changed:
                component.state = state
            loading_changed = key == "local_play" and self._play_loading != loading
            if loading_changed:
                try:
                    component.set_selection_ring_loading(loading)
                except AttributeError:
                    pass
            if changed or loading_changed:
                self.component_manager.update_component(component)
                if loading_changed:
                    self._play_loading = loading
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
