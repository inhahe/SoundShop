from __future__ import annotations

from .client import JuceAudioClient as _Base
from .plugins import PluginAPI
from .midi_schedule import MidiScheduleAPI
from .audio_playback import AudioPlaybackAPI
from .params import ParamAutomationAPI

class JuceAudioClient(_Base, PluginAPI, MidiScheduleAPI, AudioPlaybackAPI, ParamAutomationAPI):
    """Full client with all API mixins."""
    pass
