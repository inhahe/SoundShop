from __future__ import annotations

import platform

class send_cmd:
    (
        load_plugin, load_plugin_by_uid, scan_plugins, list_plugins, get_plugin_info, show_plugin_ui, hide_plugin_ui,
        set_parameter, get_parameter, connect_audio, connect_midi, start_playback, cmd_shutdown, remove_plugin,
        list_bad_paths, get_params_info, get_channels_info, schedule_midi_note, schedule_midi_cc, clear_midi_schedule,
        schedule_param_change, route_keyboard_input, unroute_keyboard_input, route_cc_to_param, unroute_cc_to_param,
        show_virtual_keyboard, hide_virtual_keyboard, route_virtual_keyboard, unroute_virtual_keyboard,
        toggle_recording, toggle_monitoring,
        load_audio_file, control_audio_playback,
        schedule_ordered_notes, start_ordered_playback, stop_ordered_playback, clear_ordered_notes,
        clear_midi_cc_schedule, clear_param_schedule, clear_all_plugins,
        stop_playback_cmd
    ) = range(41)

class recv_cmd:
    (
        param_changed, param_changes_end, stop_playback, midi_note_event, midi_cc_event,
        virtual_keyboard_note_event, virtual_keyboard_cc_event,
        midi_keyboard_routed, virtual_keyboard_routed,
        recording_started, recording_stopped, monitoring_changed,
        audio_file_loaded, audio_playback_started, audio_playback_stopped,
        ordered_note_triggered, ordered_playback_started, ordered_playback_stopped
    ) = range(18)

pipe_name = "juceclientserver"

# Legacy globals kept for compatibility with old scripts
sampleRate = 44100
blockSize = 64
bpm = None

inputIndex = -2
outputIndex = -1
leftChannel = 0
rightChannel = 1

if platform.system() == "Windows":  # VST3 only
    defaultDirs = (
        r"C:\Program Files\Steinberg\VSTPlugins",
        r"C:\Program Files\Common Files\VST3",
        r"C:\Program Files\Vstplugins",
        r"C:\Program Files (x86)\Steinberg\VSTPlugins",
        r"C:\Program Files (x86)\VstPlugins",
        r"C:\VstPlugins",
    )
elif platform.system() == "Linux":
    defaultDirs = (
        "/usr/lib/vst", "/usr/local/lib/vst", "~/.vst",
        "/usr/lib/vst3", "/usr/local/lib/vst3", "~/.vst3",
        "/usr/lib/ladspa", "/usr/local/lib/ladspa", "~/.ladspa",
        "/usr/lib/lv2", "/usr/local/lib/lv2", "~/.lv2",
    )
else:  # Darwin / macOS
    defaultDirs = (
        "/Library/Audio/Plug-Ins/VST",
        "/Library/Audio/Plug-Ins/VST3",
        "/Library/Audio/Plug-Ins/Components",
        "~/Library/Audio/Plug-Ins/VST",
        "~/Library/Audio/Plug-Ins/VST3",
    )
