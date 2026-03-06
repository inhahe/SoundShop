from __future__ import annotations

from typing import Iterable, Tuple

from .protocol import send_cmd

class MidiScheduleAPI:
    """MIDI scheduling + routing. Mixed into JuceAudioClient."""

    def schedulemidinote(self, pluginId: int, note: int, velocity: int, startTime: float, duration: float, channel: int):
        self.sendcmd(send_cmd.schedule_midi_note)
        self.sendinfo("IIfddI", int(pluginId), int(note), float(velocity), float(startTime), float(duration), int(channel))
        self.commands_pipe_handle_flush()

    def schedulemidinotes(self, notes):
        """Schedule multiple MIDI notes. Each entry is (pluginId, note, velocity, startTime, duration, channel)."""
        for pluginId, note, velocity, startTime, duration, channel in notes:
            self.sendcmd(send_cmd.schedule_midi_note)
            self.sendinfo("IIfddI", int(pluginId), int(note), float(velocity), float(startTime), float(duration), int(channel))
        self.commands_pipe_handle_flush()

    def schedulemidicc(self, pluginId: int, controller: int, value: int, time_s: float, channel: int):
        self.sendcmd(send_cmd.schedule_midi_cc)
        self.sendinfo("IIIdI", int(pluginId), int(controller), int(value), float(time_s), int(channel))
        self.commands_pipe_handle_flush()

    def schedulemidiccs(self, ccs: Iterable[Tuple[int, int, int, float, int]]):
        for key, controller, value, time_s, ch in ccs:
            self.sendcmd(send_cmd.schedule_midi_cc)
            self.sendinfo("IIIdI", int(key), int(controller), int(value), float(time_s), int(ch))
        self.commands_pipe_handle_flush()

    def clearmidischedule(self):
        self.sendcmd(send_cmd.clear_midi_schedule)
        self.commands_pipe_handle_flush()

    def clearmidiccschedule(self):
        self.sendcmd(send_cmd.clear_midi_cc_schedule)
        self.commands_pipe_handle_flush()

    def scheduleorderednotes(self, notes):
        self.sendcmd(send_cmd.schedule_ordered_notes)
        self.sendinfo("I", len(notes))
        for order, note, velocity, channel, duration in notes:
            self.sendinfo("IIIII", int(order), int(note), int(velocity), int(channel), int(duration))
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def startorderedplayback(self, use_keyboard_velocity: bool = True, use_keyboard_duration: bool = True):
        self.sendcmd(send_cmd.start_ordered_playback)
        self.sendinfo("II", int(use_keyboard_velocity), int(use_keyboard_duration))
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def stoporderedplayback(self):
        self.sendcmd(send_cmd.stop_ordered_playback)
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def clearorderednotes(self):
        self.sendcmd(send_cmd.clear_ordered_notes)
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    # keyboard / virtual keyboard routing kept from original
    def routekeyboardinput(self, pluginId: int, use_velocity: bool = True, fixed_velocity: float = 1.0):
        self.sendcmd(send_cmd.route_keyboard_input)
        self.sendinfo("IIf", int(pluginId), int(use_velocity), float(fixed_velocity))
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def unroutekeyboardinput(self):
        self.sendcmd(send_cmd.unroute_keyboard_input)
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def showvirtualkeyboard(self):
        self.sendcmd(send_cmd.show_virtual_keyboard)
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def hidevirtualkeyboard(self):
        self.sendcmd(send_cmd.hide_virtual_keyboard)
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def routevirtualkeyboard(self, pluginId: int, use_velocity: bool = True, fixed_velocity: float = 1.0):
        self.sendcmd(send_cmd.route_virtual_keyboard)
        self.sendinfo("IIf", int(pluginId), int(use_velocity), float(fixed_velocity))
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))

    def unroutevirtualkeyboard(self):
        self.sendcmd(send_cmd.unroute_virtual_keyboard)
        self.commands_pipe_handle_flush()
        return int(self.readinfo1c("I"))
