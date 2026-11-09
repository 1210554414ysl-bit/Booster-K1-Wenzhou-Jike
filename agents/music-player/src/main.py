# coding: utf-8
"""Local music player Agent for Booster K1 (online playback removed).

Plays mp3/wav files from the Agent private storage directory with:
- play / pause / previous / next
- sequential / shuffle / repeat-one play modes
- seek (via a parameter slider, transcoded with ffmpeg)
- system volume control (buttons + parameter slider + mute)
- persisted play mode and volume

Threading model
---------------
* Click callbacks NEVER call blocking SDK audio methods (play_sound / cancel).
  cancel() waits for a Python state callback that needs the GIL, so calling it
  from the click callback thread deadlocks.
* One worker thread per playback owns its handle and polls for pause/stop via a
  threading.Event; pause just flips state + sets the event.
* Every framework call (update_component / publish_toast / force_update) is
  serialized behind a single UI lock.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import threading
import time
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

PCM_SAMPLE_RATE = 22050
PCM_CHANNELS = 1

UI_TICK_SEC = 1.0
PARAM_SETTLE_SEC = 2.0
FFMPEG_PROBE_TIMEOUT_SEC = 20.0
FFMPEG_TRANSCODE_TIMEOUT_SEC = 180.0
PLAYBACK_EXTRA_TIMEOUT_SEC = 30.0
HANDLE_SETTLE_TIMEOUT_SEC = 5.0
VOLUME_STEP_PERCENT = 10


def _fmt_time(seconds: float) -> str:
    """Format seconds as mm:ss (or h:mm:ss when long)."""

    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


class Track:
    """One playable local file."""

    __slots__ = ("title", "location", "duration")

    def __init__(self, title: str, location: str, duration: float = 0.0) -> None:
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
        # Serializes every framework call (component_manager / page_proxy),
        # which is not thread-safe.
        self._ui_lock = threading.Lock()
        self._robot_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._shutdown = False

        # --- robot (lazy: motion services may not be ready at boot) --------
        self.robot: Optional[BoosterRobot] = None
        self._get_robot()

        # --- state ---------------------------------------------------------
        self._mode = MODE_ORDER
        self._volume = 50
        self._muted = False
        self._last_nonzero_volume = 50

        self._tracks: list[Track] = []
        self._index = -1
        self._shuffle_bag: list[int] = []

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

        self._ui: dict[str, Component] = {}
        self._last_status_text = ""
        self._toast_pending: Optional[LocaleString] = None

        self._cache_dir = Path(CACHE_DIR)
        self._music_dir = Path(MUSIC_DIR)
        self._ensure_dirs()
        self._load_settings()
        self._build_ui()
        self._register_parameter_callbacks()
        try:
            self._scan_local(refresh_ui=False)
        except Exception as exc:
            self.logger.warn(f"initial scan failed: {exc}")

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
            self._last_nonzero_volume = max(1, self._volume) if self._volume else 50
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
        threading.Thread(target=self._activate_worker, daemon=True).start()
        threading.Thread(target=self._ui_ticker, daemon=True).start()

    def _activate_worker(self) -> None:
        try:
            self._scan_local(refresh_ui=True)
            self._refresh_parameter_values()
            time.sleep(PARAM_SETTLE_SEC)
            self._params_ready = True
            self._refresh_parameter_values()
            self._get_robot()
            self._apply_volume(0 if self._muted else self._volume)
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
        self._stop_event.set()
        if handle is not None:
            threading.Thread(target=self._settle, args=(handle,), daemon=True).start()
        self._save_settings()
        self._cleanup_cache()

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

    # ------------------------------------------------------------------
    # Parameters
    # ------------------------------------------------------------------
    def _register_parameter_callbacks(self) -> None:
        pm = self.parameter_manager
        pm.add_parameter_callback("volume", self._on_volume_param)
        pm.add_parameter_callback("mute", self._on_mute_param)
        pm.add_parameter_callback("seek_percent", self._on_seek_param)
        pm.add_parameter_callback("play_mode", self._on_mode_param)

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
            self._shuffle_bag = []
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
            target = 0 if muted else self._last_nonzero_volume
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
            target = max(0, min(100, int(self._volume) + delta))
            self._volume = target
            if target > 0:
                self._last_nonzero_volume = target
                self._muted = False
            muted = self._muted
        self._apply_volume(0 if muted else target)
        self._write_param("volume", target)
        if muted:
            self._write_param("mute", False)
        self._save_settings()
        self._push_ui_state()
        return LocaleString(f"Volume {target}%", f"音量 {target}%")

    def on_play_pause(self, component: Component) -> Optional[LocaleString]:
        with self._lock:
            track = self._current
            paused = self._paused
            playing = self._handle is not None and not paused
        if track is None:
            if not self._tracks:
                self._scan_local(refresh_ui=False)
            if not self._tracks:
                return LocaleString("Playlist is empty", "播放列表为空，请先添加音乐")
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
            self._tracks = unique
            if self._index >= len(unique):
                self._index = -1
            self._shuffle_bag = []
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
        self._stop_event.clear()
        threading.Thread(
            target=self._play_worker,
            args=(generation, track, max(0.0, offset), previous),
            daemon=True,
        ).start()
        self._push_ui_state()

    def _is_current(self, generation: int) -> bool:
        with self._lock:
            return (not self._shutdown) and self._generation == generation

    def _settle(self, handle) -> None:
        """Cancel and wait for a handle to reach a terminal state (off-thread)."""

        try:
            handle.cancel()
            handle.wait(timeout=HANDLE_SETTLE_TIMEOUT_SEC)
        except Exception as exc:
            self.logger.warn(f"settle handle failed: {exc}")

    def _play_worker(self, generation, track, offset, previous_handle) -> None:
        try:
            if previous_handle is not None:
                self._settle(previous_handle)
            source = self._prepare_source(track, offset)
            if source is None:
                raise RuntimeError("source unavailable")
            if not self._is_current(generation):
                return

            duration = float(track.duration or 0.0)
            if duration <= 0:
                duration = self._probe_duration(source)
                with self._lock:
                    if self._generation == generation:
                        self._duration = duration
                        track.duration = duration

            robot = self._get_robot()
            if robot is None:
                raise RuntimeError("robot sdk not ready")
            handle = robot.play_sound(str(source))
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
                self._settle(handle)
                return

            self._push_ui_state()

            # Poll so pause/stop can interrupt without cancelling on the
            # callback thread.
            while True:
                with self._lock:
                    if self._generation != generation or self._shutdown:
                        return
                if self._stop_event.is_set():
                    break
                if handle.done():
                    break
                handle.wait(timeout=0.25)

            if self._stop_event.is_set():
                with self._lock:
                    self._handle = None
                self._settle(handle)
                return

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
                    "Cannot play: damaged file / unsupported format",
                    "无法播放：文件损坏 / 格式不支持",
                )
                self._push_ui_state()

    def _prepare_source(self, track: Track, offset: float) -> Optional[Path]:
        path = Path(track.location)
        if not path.is_file():
            return None
        if offset <= 0.01:
            return path
        return self._transcode(str(path), offset)

    def _ffmpeg(self) -> Optional[str]:
        return shutil.which("ffmpeg")

    def _transcode(self, source: str, offset: float) -> Optional[Path]:
        ffmpeg = self._ffmpeg()
        if ffmpeg is None:
            # No ffmpeg: cannot seek, but can restart the track from the start.
            path = Path(source)
            if path.is_file():
                if offset > 0.01:
                    self.logger.warn(
                        "ffmpeg missing; restarting the track from the beginning "
                        "instead of seeking"
                    )
                return path
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
        except Exception as exc:
            self.logger.warn(f"ffmpeg transcode error: {exc}")
            return None
        if target.is_file() and target.stat().st_size > 44:
            return target
        return None

    def _probe_duration(self, path) -> float:
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

    def _position_locked(self) -> float:
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
        return max(0.0, min(100.0, self._position_locked() / duration * 100.0))

    def _pause(self) -> None:
        with self._lock:
            if self._handle is None or self._paused:
                return
            self._paused = True
            self._offset = self._position_locked()
        self._stop_event.set()
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
            track = self._current
            if track is not None:
                self._start_playback(track, 0.0)
                return
        self._advance(step=1, manual=False)

    def _advance(self, step: int, manual: bool) -> None:
        with self._lock:
            if not self._tracks:
                self._toast_pending = LocaleString("Playlist is empty", "播放列表为空")
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
            self._handle = None
            self._paused = False
            self._offset = 0.0
            self._current = None
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
        position = self._position_locked()
        counter = f"{index + 1}/{len(tracks)}" if tracks and index >= 0 else "-"
        return f"{prefix} {counter} {track.title[:16]}  {_fmt_time(position)}/{_fmt_time(total)}"

    def _push_ui_state(self) -> None:
        with self._ui_lock:
            self._push_ui_state_locked()

    def _push_ui_state_locked(self) -> None:
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
            if changed or loading:
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
