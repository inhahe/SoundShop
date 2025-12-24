# SoundShop
Library for making music in Python 

Uses JUCE to load VST3s and Audio Units

This program is unfinished.

Claude made almost all of the C++ part and the makefile, I made almost all of the Python part.

Features:
- Load VST3 and AU
- Route plugin graph
- Schedule playback of MIDI notes, MIDI cc signals, effects parameters, and audio files to any plugins, or render directly to audio file in faster-than-real-time
- Record audio, MIDI/MIDI-cc input or effects parameter changes while song is playing, with a midi controller, or with onscreen graphics
- Scan plugin directories and get info on any plugin and its parameters and channels
- Automatically saves a list of plugins that wouldn't load and avoids them in the future, so you don't have to spend forever waiting for popups asking you to register when it scans for plugins

To do:
  - Finish Note and Notes Python classes, add timestamps to notes in Notes class and Note object
  - Add functionality for manipulating songs wrt their notes' timestamps
  - Add ability to group Midi notes, midi-cc, effects parameter changes, and plugin routing in "tracks" that can themselves be grouped into other tracks and manipulated together
  - Add ability to save songs to and load them from file (json? yaml would be more editable)
  - Add minor scales to notes_manipulation.py
  - Fix detection of special scales in note sequences
  - Make detection of major keys and modes output in the same format that detection of special keys and minor keys and modes will
  - Add a button to send the current time/block/sample number during playback to the client for storage in the song data for using it as a marker for manipulating the song programmatically
  - Test it to see how much if it actually works
  
------

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Soundshop is a hybrid Python/C++ music production application that combines:
- **Python** for high-level music composition, sequencing, and control logic
- **C++ (JUCE)** for real-time audio processing, VST plugin hosting, and low-latency audio I/O

The architecture uses named pipes for inter-process communication, allowing Python to control a C++ audio engine without the overhead of Python's GIL affecting audio performance.

## Core Architecture

**Only 3 files matter for core functionality:**
1. `juce_gui_server.cpp` - C++ JUCE audio engine (server)
2. `juce_client.py` - Python client library and API
3. `CMakeLists.txt` - Build configuration

### Communication Model

**Two named pipes** handle bidirectional communication:
- **Commands pipe**: Python → C++ commands (load plugins, schedule MIDI, set parameters, etc.)
- **Notifications pipe**: C++ → Python events (parameter changes, MIDI input, playback state, etc.)

**Command/Response Protocol:**
- Python sends binary commands via the commands pipe
- Commands include: `load_plugin`, `schedule_midi_note`, `set_parameter`, `start_playback`, etc. (37 total - see `send_cmd` class in `juce_client.py:32-40`)
- C++ sends notifications like `param_changed`, `midi_note_event`, `stop_playback`, etc. (18 total - see `recv_cmd` class in `juce_client.py:42-48`)

### Key Features

- **Plugin hosting**: VST3 on Windows, VST3/AU on macOS, VST3/LV2/LADSPA on Linux
- **MIDI scheduling**: Sample-accurate MIDI note/CC scheduling with two modes:
  - Time-based scheduling (schedule notes at specific timestamps)
  - Ordered playback (trigger pre-scheduled notes sequentially)
- **Parameter automation**: Schedule parameter changes with sample accuracy
- **Live input routing**: Route physical MIDI keyboard or virtual keyboard to plugins
- **Real-time or offline**: Supports both real-time playback with audio output AND offline rendering to file
- **Plugin UI management**: Show/hide VST plugin GUIs
- **Audio graph**: Connect audio/MIDI between plugins (currently stored in Python, not C++)

## Build Instructions

### Windows (Primary Platform)

```bash
# Full build from scratch
build.bat

# Manual build
rmdir /s /q build
mkdir build
cd build
cmake .. -A x64 -DCMAKE_BUILD_TYPE=Release
cmake --build . --config Release --verbose
copy "bin\Release\JUCE GUI Server.exe" ..\juce_gui_server.exe
cd ..
```

**Output**: `juce_gui_server.exe` in the soundshop directory

**Requirements:**
- CMake 3.15+
- JUCE framework at `D:/JUCE` (hardcoded in CMakeLists.txt:9)
- Visual Studio 2019+ (for MSVC compiler)

### Cross-Platform Build

The CMakeLists.txt supports Linux and macOS, but platform-specific libraries are required (ALSA, GTK3 on Linux; Cocoa frameworks on macOS).

## Development Workflow

### Running the Application

1. **Start C++ server** (automatically started by Python client):
   ```python
   from juce_client import JuceAudioClient
   client = JuceAudioClient()  # Spawns juce_gui_server.exe
   client.connect()
   ```

2. **Scan and load plugins**:
   ```python
   client.scanplugins([r"C:\Program Files\Common Files\VST3"])
   lpindex = client.loadpluginbyindex(0)  # Returns loaded plugin index
   client.showpluginui(lpindex)
   ```

3. **Schedule MIDI and play**:
   ```python
   client.schedule_midi_note(lpindex, note=60, velocity=1.0, start=0.0, duration=1.0)
   client.start_playback(duration=10.0, output_file="output.wav")  # Or "" for real-time
   ```

See `commands.txt` for example usage patterns.

### Python Client API

Key methods in `JuceAudioClient` class:
- `connect()` - Start server and establish pipe connection
- `scanplugins(dirs)` - Scan directories for plugins
- `loadpluginbyindex(idx)` → lpindex - Load plugin, returns loaded plugin index
- `schedule_midi_note(lpindex, note, velocity, start, duration, channel=1)` - Schedule MIDI
- `schedule_param_change(lpindex, paramIdx, value, timeSeconds)` - Automate parameters
- `set_parameter(lpindex, paramIdx, value)` - Set parameter immediately
- `start_playback(duration, output_file="")` - Start audio (empty string = real-time)
- `showpluginui(lpindex)` / `hidepluginui(lpindex)` - Show/hide plugin UI

**Important**: Use `lpindex` (loaded plugin index) returned from `loadpluginbyindex()` for all plugin operations, NOT the scan index.

### Development Files

- `get_scales.py` - Music theory utilities (scales, chord generation)
- `notes_manipulation.py` - Note data manipulation helpers
- `badpaths.json` - Cached list of plugin paths that failed to load
- `debug_log.txt` - C++ server debug output

## Code Architecture Details

### C++ Server (juce_gui_server.cpp)

**Main classes:**
- `CompletePluginHost` - Main application class, manages plugins and audio graph
- `MidiScheduler` - Sample-accurate MIDI event scheduling
- `ParamScheduler` - Sample-accurate parameter automation
- `OrderedNotePlayer` - Sequential note playback system
- `VirtualKeyboard` - On-screen MIDI keyboard component

**Audio processing:**
- Sample rate: 44100 Hz (configurable via `sampleRate` global)
- Block size: 64 samples (configurable via `blockSize` global)
- Named pipe: "juceclientserver" (configurable via `pipeName` global)

**Key globals:**
- `app` - Pointer to CompletePluginHost instance
- `isRecording` - Whether to record parameter changes
- `suppressNotifications` - Disable notifications temporarily

### Python Client (juce_client.py)

**Key constants:**
- `inputIndex = -2` - Special index for audio input device
- `outputIndex = -1` - Special index for audio output device
- `leftChannel = 0`, `rightChannel = 1` - Audio channel indices
- `sampleRate = 44100`, `blockSize = 64` - Must match C++ settings

**Threading:**
- Notification listener runs in background thread
- Callbacks for parameter changes, MIDI events, playback state

## Important Notes

- **Parameter indices**: Use `originalIndex` from parameter info, not array position
- **Plugin indices**: `loadpluginbyindex()` scan index ≠ `lpindex` used for control
- **Pipe protocol**: Binary protocol using `struct.pack()` for serialization
- **Thread safety**: C++ uses mutexes for scheduler access; Python uses threading for notifications
- **Future plans**: Notifications pipe will expand beyond just parameter changes (currently somewhat misnamed)

## Known TODOs

From code comments:
- Audio recording functionality (currently incomplete)
- Move bad plugin path logic entirely to Python
- Add MPE MIDI support (no programmatic detection available)
- Consider using alternative IPC instead of named pipes (investigate "Popsicle")
- Fix screenWidth/screenHeight definition shadowing issues

