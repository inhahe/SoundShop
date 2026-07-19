"""
Test project for soundshop - exercises the main features of the Python/C++ audio engine.
  1. Connects to the JUCE server (auto-starts juce_gui_server.exe)
  2. Scans for VST3 plugins using the default Windows directories, respecting badpaths.json2. Scans for VST3 plugins using the default Windows directories, respecting badpaths.json
  3. Loads the first instrument plugin it finds (falls back to any plugin)                                                4. Shows the plugin UI editor window
  5. Queries all parameters and channel/bus info, printing the first 10 params
  6. Sets a parameter to its midpoint and reads it back to verify
  7. Connects audio from the plugin output to the system output (stereo)
  8. Schedules a C major scale (C4-C5) at 120 BPM as MIDI notes
  9. Schedules a C major chord (C4, E4, G4) held for 2 beats after the scale
  10. Schedules parameter automation - sweeps the first automatable param from min to max over the whole piece
  11. Plays back in real-time through your audio output
  12. Renders offline to test_render.wav in the project directory
  13. Tests ordered-note playback - loads the scale as ordered notes, opens the virtual keyboard, and lets you step
  through notes by pressing keys (10 second window)
  14. Cleans up - hides UI, clears all schedules, removes the plugin, disconnects
"""

import time
import json
import os

from juce_client import JuceAudioClient
from juce_client.protocol import sampleRate, outputIndex, leftChannel, rightChannel, defaultDirs
from juce_client.music_theory import Note, Notes, notes_dict

SERVER_EXE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "juce_gui_server.exe")

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def seconds_to_samples(sec):
    return int(sec * sampleRate)


def print_section(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


# ---------------------------------------------------------------------------
# Notification callback (optional - prints events as they arrive)
# ---------------------------------------------------------------------------

def on_notification(cmd, data):
    print(f"  [notification] cmd={cmd}  data={data}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    client = JuceAudioClient(server_exe_path=SERVER_EXE)

    # Load bad-paths cache so we skip plugins that are known to fail
    badpaths_file = os.path.join(os.path.dirname(__file__), "badpaths.json")
    if os.path.exists(badpaths_file):
        with open(badpaths_file, "r") as f:
            client.badPluginPaths = json.load(f)

    # ------------------------------------------------------------------
    # 1. Connect
    # ------------------------------------------------------------------
    print_section("1. Connecting to JUCE server")
    if not client.connect():
        print("ERROR: could not connect to the JUCE audio server. Exiting.")
        return

    # Start listening for notifications from the server
    client.start_notification_listener(callback=on_notification)

    # ------------------------------------------------------------------
    # 2. Scan plugins
    # ------------------------------------------------------------------
    print_section("2. Scanning for plugins")
    num_found = client.scanplugins((r"d:\vst3\free",))
    print(f"Scan complete - server found {num_found} plugin(s)")

    plugins = client.listplugins()
    if not plugins:
        print("No plugins found. Install some VST3 instruments and try again.")
        client.disconnect(shutdown_server=True)
        return

    print(f"\nAvailable plugins ({len(plugins)}):")
    for i, p in enumerate(plugins):
        kind = "Instrument" if p["isInstrument"] else "Effect"
        print(f"  [{i}] {p['name']}  ({kind}, {p['pluginFormatName']})")

    # ------------------------------------------------------------------
    # 3. Load the first instrument (or first plugin if no instruments)
    # ------------------------------------------------------------------
    print_section("3. Loading a plugin")

    instrument = next((p for p in plugins if p["isInstrument"]), None)
    chosen = instrument or plugins[0]
    plugin_key = 1  # arbitrary key we assign

    proc = client.loadpluginbyuid(chosen["pluginId"], plugin_key)
    print(f"Loaded '{chosen['name']}' -> key={proc.key}")

    # ------------------------------------------------------------------
    # 4. Show plugin UI
    # ------------------------------------------------------------------
    print_section("4. Showing plugin UI")
    success, errmsg = client.showpluginui(proc.key)
    if success:
        print("Plugin editor window opened.")
    else:
        print(f"Could not open UI: {errmsg}")

    # ------------------------------------------------------------------
    # 5. Query parameters & channel info
    # ------------------------------------------------------------------
    print_section("5. Querying plugin info")

    params = client.getParamsInfo(proc.key)
    print(f"Plugin has {len(params)} parameter(s). First 10:")
    for p in params[:10]:
        print(f"  [{p['originalIndex']}] {p['name']}  "
              f"range=[{p['minValue']:.2f}, {p['maxValue']:.2f}]  "
              f"value={p['value']:.4f}  default={p['defaultValue']:.4f}")

    channels = client.getChannelsInfo(proc.key)
    print(f"\nAccepts MIDI: {bool(channels['acceptsMidi'])}")
    print(f"Produces MIDI: {bool(channels['producesMidi'])}")
    print(f"Input buses: {len(channels['inputBuses'])}")
    print(f"Output buses: {len(channels['outputBuses'])}")

    # ------------------------------------------------------------------
    # 6. Set a parameter (first automatable one, halfway through range)
    # ------------------------------------------------------------------
    print_section("6. Setting a parameter")

    automatable = [p for p in params if p["isAutomatable"]]
    if automatable:
        target = automatable[0]
        mid_val = (target["minValue"] + target["maxValue"]) / 2.0
        client.setparameter(proc.key, target["originalIndex"], mid_val)
        readback = client.getparameter(proc.key, target["originalIndex"])
        print(f"Set param '{target['name']}' to {mid_val:.4f}  (read back: {readback:.4f})")
    else:
        print("No automatable parameters found; skipping.")

    # ------------------------------------------------------------------
    # 7. Connect plugin audio output -> system audio output
    # ------------------------------------------------------------------
    print_section("7. Connecting audio graph")

    out_buses = channels["outputBuses"]
    if out_buses:
        num_ch = out_buses[0]["numChannels"]
        for ch in range(min(num_ch, 2)):
            result = client.connectaudio(proc.key, ch, outputIndex, ch)
            print(f"  connect plugin ch {ch} -> output ch {ch}  (result={result})")
    else:
        # fallback: try stereo
        client.connectaudio(proc.key, leftChannel, outputIndex, leftChannel)
        client.connectaudio(proc.key, rightChannel, outputIndex, rightChannel)
        print("  connected stereo (fallback)")

    # ------------------------------------------------------------------
    # 8. Schedule MIDI notes - C major scale
    # ------------------------------------------------------------------
    print_section("8. Scheduling MIDI notes (C major scale)")

    client.clearmidischedule()

    scale_notes = Notes("C4 D4 E4 F4 G4 A4 B4 C5")
    bpm = 120
    beat_duration = 60.0 / bpm  # seconds per beat

    notes_to_schedule = []
    for i, n in enumerate(scale_notes):
        start = i * beat_duration
        dur = beat_duration * 0.9  # slightly shorter than a full beat for articulation
        vel = 100 / 127.0  # velocity as 0-1 float
        notes_to_schedule.append((proc.key, n.midi, vel, start, dur, 1))
        print(f"  {n} (MIDI {n.midi}) at t={start:.2f}s  dur={dur:.2f}s")

    client.schedulemidinotes(notes_to_schedule)
    print(f"Scheduled {len(notes_to_schedule)} notes.")

    # ------------------------------------------------------------------
    # 9. Schedule a chord after the scale
    # ------------------------------------------------------------------
    print_section("9. Scheduling a C major chord")

    chord_start = len(scale_notes) * beat_duration
    chord_dur = beat_duration * 2  # hold for 2 beats
    chord_notes = Notes("C4 E4 G4")

    chord_to_schedule = []
    for n in chord_notes:
        chord_to_schedule.append((proc.key, n.midi, 110 / 127.0, chord_start, chord_dur, 1))
        print(f"  {n} (MIDI {n.midi}) at t={chord_start:.2f}s  dur={chord_dur:.2f}s")

    client.schedulemidinotes(chord_to_schedule)
    print("Chord scheduled.")

    # ------------------------------------------------------------------
    # 10. Schedule parameter automation (sweep first automatable param)
    # ------------------------------------------------------------------
    print_section("10. Scheduling parameter automation")

    if automatable:
        target = automatable[0]
        param_changes = []
        num_steps = 20
        total_time = chord_start + chord_dur
        for step in range(num_steps + 1):
            t = (step / num_steps) * total_time
            # sweep from min to max over the entire playback
            value = target["minValue"] + (target["maxValue"] - target["minValue"]) * (step / num_steps)
            sample = seconds_to_samples(t)
            param_changes.append((proc.key, target["originalIndex"], value, sample))

        client.clearparamschedule()
        client.scheduleparamchanges(param_changes)
        print(f"Scheduled {len(param_changes)} automation points for '{target['name']}'")
    else:
        print("No automatable params - skipping automation.")

    # ------------------------------------------------------------------
    # 11. Real-time playback
    # ------------------------------------------------------------------
    print_section("11. Real-time playback")

    total_duration = chord_start + chord_dur + 0.5  # a little extra silence at the end
    end_sample = seconds_to_samples(total_duration)

    print(f"Playing {total_duration:.1f}s of audio in real-time...")
    client.startplayback(end_sample)

    # Wait for playback to finish (the server sends a stop_playback notification)
    time.sleep(total_duration + 1.0)
    print("Real-time playback done.")

    # ------------------------------------------------------------------
    # 12. Offline render to file
    # ------------------------------------------------------------------
    print_section("12. Offline render to WAV file")

    output_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_render.wav")
    print(f"Rendering to: {output_file}")

    client.startplayback(end_sample, toFile=True, filename=output_file)

    # Offline render is fast - wait a moment for it to complete
    time.sleep(2.0)

    if os.path.exists(output_file):
        size_kb = os.path.getsize(output_file) / 1024
        print(f"Render complete! File size: {size_kb:.1f} KB")
    else:
        print("Render may still be in progress or failed.")

    # ------------------------------------------------------------------
    # 13. Ordered-note playback test
    # ------------------------------------------------------------------
    print_section("13. Ordered-note playback (press keys to trigger)")

    client.clearorderednotes()

    # Schedule notes in order - each key press triggers the next note
    ordered = []
    for i, n in enumerate(scale_notes):
        # (order, note, velocity, channel, duration_ms)
        ordered.append((i, n.midi, 100, 1, int(beat_duration * 1000)))

    count = client.scheduleorderednotes(ordered)
    print(f"Loaded {count} ordered notes.")

    # Show virtual keyboard so user can trigger them
    client.showvirtualkeyboard()
    client.routevirtualkeyboard(proc.key)
    result = client.startorderedplayback(use_keyboard_velocity=True, use_keyboard_duration=False)
    print(f"Ordered playback started (result={result}).")
    print("Press keys on the virtual keyboard to step through the scale...")
    print("(Waiting 10 seconds for you to try it)")

    time.sleep(10)

    client.stoporderedplayback()
    client.unroutevirtualkeyboard()
    print("Ordered playback stopped.")

    # ------------------------------------------------------------------
    # 14. Cleanup
    # ------------------------------------------------------------------
    print_section("14. Cleanup")

    client.hidepluginui(proc.key)
    client.hidevirtualkeyboard()
    client.clearmidischedule()
    client.clearparamschedule()
    client.clearorderednotes()
    client.removeplugin(proc.key)
    print("Plugin removed, schedules cleared.")

    client.disconnect(shutdown_server=True)
    print("Disconnected. Test project complete!")


if __name__ == "__main__":
    main()
