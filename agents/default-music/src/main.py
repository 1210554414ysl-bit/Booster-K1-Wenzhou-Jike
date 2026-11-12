"""Extend the installed default Agent; never patch, replace or repackage it."""

from .default_loader import load_default
from .controller import MusicController

DefaultAgent = load_default()


class DefaultMusicAgent(DefaultAgent):
    def __init__(self):
        self._music_components = []
        self.music = None
        super().__init__()
        self.music = MusicController(self)
        # K1 default actions come from the original skill_refs/orchestration.
        self._replace_components(list(self._walk_components))

    def _replace_components(self, components):
        # Original robot-state callbacks rebuild the UI on mode changes.
        # Preserve the music entry while retaining the original implementation.
        combined = list(components)
        identifiers = {component.id for component in combined}
        combined.extend(component for component in self._music_components
                        if component.id not in identifiers)
        super()._replace_components(combined)

    def on_agent_activated(self):
        callback = getattr(super(), 'on_agent_activated', None)
        if callback:
            callback()
        if self.music:
            self.music.on_agent_activated()

    def on_agent_deactivated(self):
        if self.music:
            self.music.on_agent_deactivated()
        callback = getattr(super(), 'on_agent_deactivated', None)
        if callback:
            callback()

    def on_agent_close(self):
        if self.music:
            self.music.on_agent_close()
        super().on_agent_close()
