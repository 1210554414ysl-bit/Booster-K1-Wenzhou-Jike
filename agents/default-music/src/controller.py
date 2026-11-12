"""One audio owner and shared transport for local files and online providers."""

from pathlib import Path

from booster_agent_framework import ComponentOrientation, LocaleString, OnlineWebviewComponent

from .local_player import MusicPlayerAgent, Track
from .online_player import OnlineMusicAgent
from .panel import serve_panel


class MusicController(OnlineMusicAgent):
    def __init__(self, owner):
        self._local_tracks = []
        self._local_revision = 0
        super().__init__(owner)

    def _build_ui(self):
        self._panel_server, self.panel_url = serve_panel(
            self._request_local, self._on_panel_request, self._upload_mp3)
        panel = OnlineWebviewComponent(
            'default_music_panel', LocaleString('Local & Online Music', '本地与在线音乐'),
            'res/music_note.png', self.panel_url, None, ComponentOrientation.kLANDSCAPE,
            display_sequence=100)
        self._ui['music_panel'] = panel
        self._owner._music_components = [panel]
        self.logger.info(f'Default music panel available at {self.panel_url}')

    def _scan_local(self, refresh_ui=False):
        roots = [self._storage_root / 'music',
                 Path('/opt/booster/booster_agent_data/data/agent_storage/com.wenzhoujike.k1.music/music')]
        found = {}
        with self._scan_lock:
            for root in roots:
                if not root.is_dir():
                    continue
                for file in self._iter_audio_files(root, 4):
                    track = Track(file.stem, str(file))
                    found[track.key] = track
            tracks = sorted(found.values(), key=lambda track: track.title.casefold())
            with self._lock:
                if [track.key for track in tracks] != [track.key for track in self._local_tracks]:
                    self._local_revision += 1
                self._local_tracks = tracks
        if refresh_ui:
            self._push_ui_state()
        return len(tracks)

    def _request_local(self, message):
        if not isinstance(message, dict):
            return {'success': False}
        if message.get('command') == 'status':
            result = self._panel_status(None)
            result.pop('catalog', None)
            with self._lock:
                result['revision'] = self._local_revision
                if message.get('revision') != self._local_revision:
                    result['catalog'] = [[t.key, t.title] for t in self._local_tracks]
            return result
        if message.get('command') == 'select':
            with self._lock:
                index = next((i for i, track in enumerate(self._local_tracks)
                              if track.key == message.get('track')), None)
                if index is None or self._shutdown:
                    return {'success': False}
                self._tracks = list(self._local_tracks)
                self._shuffle_bag.clear()
                self._shuffle_history.clear()
                self._catalog_revision += 1
                self._index = -1
                self._select_track_locked(index)
            self._push_ui_state()
            return {'success': True}
        if message.get('command') == 'play_pause' and self._current is None and self._local_tracks:
            return self._request_local({'command': 'select', 'track': self._local_tracks[0].key})
        return self._on_panel_request(message)

    def _play_track(self, generation, track, offset):
        if track.location.startswith(('netease://', 'qq://')):
            return super()._play_track(generation, track, offset)
        return MusicPlayerAgent._play_track(self, generation, track, offset)

    def _position_locked(self):
        if self._current and not self._current.location.startswith(('netease://', 'qq://')):
            return MusicPlayerAgent._position_locked(self)
        return super()._position_locked()

    def _output_arguments(self):
        return ['-f', 'pulse', '-buffer_duration', '150',
                '-stream_name', 'K1 Default Music', 'K1 Default Music']
