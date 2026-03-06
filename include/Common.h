#pragma once
// JUCE module headers
#include <juce_core/juce_core.h>
#include <juce_events/juce_events.h>
#include <juce_graphics/juce_graphics.h>
#include <juce_gui_basics/juce_gui_basics.h>
#include <juce_gui_extra/juce_gui_extra.h>
#include <juce_audio_basics/juce_audio_basics.h>
#include <juce_audio_devices/juce_audio_devices.h>
#include <juce_audio_formats/juce_audio_formats.h>
#include <juce_audio_processors/juce_audio_processors.h>
#include <juce_audio_utils/juce_audio_utils.h>

#if defined __has_include
  #if __has_include(<juce_audio_plugin_client/juce_audio_plugin_client.h>)
    #include <juce_audio_plugin_client/juce_audio_plugin_client.h>
  #endif
#endif

#include <iostream>
#include <thread>
#include <atomic>
#include <queue>
#include <mutex>
#include <condition_variable>
#include <string>
#include <memory>
#include <map>
#include <vector>
#include <array>
#include <fstream>
#include <unordered_map>

#ifdef _WIN32
  #include <windows.h>
  #include <fcntl.h>
  #include <io.h>
#else
  #include <sys/stat.h>
  #include <fcntl.h>
  #include <unistd.h>
#endif

using namespace std;

// --------------------
// IPC command enums
// --------------------
enum recv_cmd
{
  load_plugin, load_plugin_by_uid, scan_plugins, list_plugins, get_plugin_info, show_plugin_ui, hide_plugin_ui, set_parameter, get_parameter, connect_audio, connect_midi, start_playback,
  cmd_shutdown, remove_plugin, list_bad_paths, get_params_info, get_channels_info, schedule_midi_note, schedule_midi_cc, clear_midi_schedule, schedule_param_change,
  route_keyboard_input, unroute_keyboard_input, route_cc_to_param, unroute_cc_to_param,
  show_virtual_keyboard, hide_virtual_keyboard, route_virtual_keyboard, unroute_virtual_keyboard,
  toggle_recording, toggle_monitoring,
  load_audio_file, control_audio_playback,
  schedule_ordered_notes, start_ordered_playback, stop_ordered_playback, clear_ordered_notes,
  clear_midi_cc_schedule, clear_param_schedule, clear_all_plugins,
  stop_playback_cmd
};

enum send_cmd : uint8_t
{
  param_changed, param_changes_end, stop_playback, midi_note_event, midi_cc_event,
  virtual_keyboard_note_event, virtual_keyboard_cc_event,
  midi_keyboard_routed, virtual_keyboard_routed,
  recording_started, recording_stopped, monitoring_changed,
  audio_file_loaded, audio_playback_started, audio_playback_stopped,
  ordered_note_triggered, ordered_playback_started, ordered_playback_stopped
};
