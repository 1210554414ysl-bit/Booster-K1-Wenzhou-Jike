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
* One worker owns the audio handle and cancels it before starting another.
* Callbacks replace the pending playback request without waiting for audio I/O.
* UI refreshes take the UI lock before reading playback state.
"""

from __future__ import annotations

import json
import os
import random
import re
import shutil
import subprocess
import tempfile
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
PLAYBACK_POLL_SEC = 0.2
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
        threading.Thread(target=self._activate_worker, daemon=True).start()
        threading.Thread(target=self._ui_ticker, daemon=True).start()

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
        # Give the audio worker a bounded chance to stop before process teardown.
        self._playback_thread.join(timeout=1.0)
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
                self._index = 0 if self._index < 0 else self._index
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
        handle = None
        cache = None
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
            if offset > 0.01:
                cache = tempfile.TemporaryDirectory(
                    dir=self._cache_dir, prefix="playback-", ignore_cleanup_errors=True
                )
                source = self._transcode(source, offset, Path(cache.name) / "track.wav", generation)

            if not self._is_current(generation):
                return
            robot = self._get_robot()
            if robot is None:
                raise RuntimeError("robot sdk not ready")
            if not self._is_current(generation):
                return
            handle = robot.play_sound(str(source))
            with self._lock:
                if self._generation != generation or self._shutdown:
                    return
                self._handle = handle
                self._started_at = time.monotonic()
                self._offset = offset
                self._duration = duration
                self._loading = False
                track.duration = duration

            self._push_ui_state()
            while self._is_current(generation) and not handle.done():
                handle.wait(timeout=PLAYBACK_POLL_SEC)
            if not self._is_current(generation):
                return
            if handle.status == "FAILED":
                raise RuntimeError(f"audio SDK failed: {handle.error}")
            self._finish_playback(generation, succeeded=handle.status == "SUCCEEDED")
        except Exception as exc:
            if self._is_current(generation):
                self.logger.error(f"playback failed ({track.title}): {exc.__class__.__name__}: {exc}")
                self._finish_playback(
                    generation,
                    succeeded=False,
                    error=LocaleString("Unable to play this track", "无法播放这首歌"),
                )
        finally:
            if handle is not None:
                self._settle(handle)
            if cache is not None:
                cache.cleanup()

    def _settle(self, handle) -> None:
        try:
            if not handle.done():
                handle.cancel()
            while not handle.done() and not self._shutdown:
                handle.wait(timeout=PLAYBACK_POLL_SEC)
        except Exception as exc:
            self.logger.warn(f"settle handle failed: {exc}")

    def _run_ffmpeg(self, arguments: list[str], generation: int, timeout: float) -> tuple[int, bytes]:
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise RuntimeError("ffmpeg is unavailable")
        command = [ffmpeg, "-nostdin", "-hide_banner", *arguments]
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

    def _transcode(self, source: Path, offset: float, target: Path, generation: int) -> Path:
        returncode, stderr = self._run_ffmpeg(
            [
                "-y", "-loglevel", "error", "-ss", f"{offset:.3f}", "-i", str(source),
                "-ac", str(PCM_CHANNELS), "-ar", str(PCM_SAMPLE_RATE),
                "-c:a", "pcm_s16le", "-f", "wav", str(target),
            ],
            generation,
            FFMPEG_TRANSCODE_TIMEOUT_SEC,
        )
        if self._is_current(generation) and (
            returncode != 0 or not target.is_file() or target.stat().st_size <= 44
        ):
            detail = stderr.decode("utf-8", "replace")[:300]
            raise RuntimeError(f"ffmpeg could not seek: {detail}")
        return target

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

    def _seek_to(self, percent: float) -> None:
        with self._lock:
            if self._current is None or self._duration <= 0:
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
        if self._mode == MODE_SHUFFLE and step > 0 and not manual:
            if not self._shuffle_bag:
                self._shuffle_bag = list(range(total))
                random.shuffle(self._shuffle_bag)
                if self._shuffle_bag[0] == self._index:
                    self._shuffle_bag.append(self._shuffle_bag.pop(0))
            self._index = self._shuffle_bag.pop(0)
        else:
            next_index = self._index + step
            if next_index >= total and not manual:
                self._index = total - 1
                self._current = None
                self._duration = 0.0
                return
            self._index = total - 1 if next_index < 0 else next_index % total

        self._shuffle_bag = [index for index in self._shuffle_bag if index != self._index]
        self._start_playback_locked(self._tracks[self._index], 0.0)
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
