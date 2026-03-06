from __future__ import annotations

from .protocol import send_cmd

class AudioPlaybackAPI:
    """Audio graph connections + timeline playback + audio file player control."""

    def connectaudio(self, sourceId: int, sourceChannel: int, destId: int, destChannel: int) -> int:
        self.sendcmd(send_cmd.connect_audio)
        self.sendinfo("IIII", int(sourceId), int(sourceChannel), int(destId), int(destChannel))
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def connectmidi(self, sourceId: int, destId: int) -> int:
        self.sendcmd(send_cmd.connect_midi)
        self.sendinfo("II", int(sourceId), int(destId))
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def startplayback(self, endSample: int, *, toFile: bool = False, filename: str = "") -> int:
        self.sendcmd(send_cmd.start_playback)
        self.sendinfo("QI", int(endSample), int(1 if toFile else 0))
        if toFile:
            self.sendstr(filename)
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def stopplayback(self) -> int:
        self.sendcmd(send_cmd.stop_playback_cmd)
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def loadaudiofileintoplayer(self, filename: str) -> int:
        self.sendcmd(send_cmd.load_audio_file)
        self.sendstr(filename)
        self.commands_pipe_handle_flush()
        # server returns signed int
        return int(self.readinfo1c("i"))

    def startaudioregion(self, playerId: int, fileStartSample: int = 0) -> int:
        self.sendcmd(send_cmd.control_audio_playback)
        self.sendinfo("IBQ", int(playerId), 1, int(fileStartSample))
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def stopaudioregion(self, playerId: int) -> int:
        self.sendcmd(send_cmd.control_audio_playback)
        self.sendinfo("IB", int(playerId), 0)
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def scheduleaudioregion(self, playerId: int, startSample: int, fileStartSample: int) -> int:
        self.sendcmd(send_cmd.control_audio_playback)
        self.sendinfo("IBQQ", int(playerId), 2, int(startSample), int(fileStartSample))
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def togglerecording(self, filename: str = "") -> int:
        """Toggle audio recording on/off. Pass filename to start, empty to stop."""
        self.sendcmd(send_cmd.toggle_recording)
        self.sendstr(filename)
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def togglemonitoring(self) -> int:
        """Toggle monitoring (playback during recording)."""
        self.sendcmd(send_cmd.toggle_monitoring)
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def routecctoparam(self, pluginKey: int, paramIndex: int, ccController: int, midiChannel: int = -1) -> int:
        """Map a MIDI CC controller to a plugin parameter."""
        self.sendcmd(send_cmd.route_cc_to_param)
        self.sendinfo("IIIi", int(pluginKey), int(paramIndex), int(ccController), int(midiChannel))
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def unroutecctoparam(self, pluginKey: int, paramIndex: int, ccController: int) -> int:
        """Remove a MIDI CC to parameter mapping."""
        self.sendcmd(send_cmd.unroute_cc_to_param)
        self.sendinfo("III", int(pluginKey), int(paramIndex), int(ccController))
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))
