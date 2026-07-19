# Soundshop Reference

Complete reference for the Soundshop Python client API, the named-pipe protocol,
and the high-level composition libraries. For a gentle intro see
[README.md](README.md).

**Contents**

1. [Architecture & protocol](#1-architecture--protocol)
2. [Connecting & lifecycle](#2-connecting--lifecycle)
3. [Notifications](#3-notifications)
4. [Plugin discovery, loading & metadata](#4-plugin-discovery-loading--metadata)
5. [Parameters](#5-parameters)
6. [Plugin state (patch save/restore)](#6-plugin-state-patch-saverestore)
7. [Audio & MIDI graph wiring](#7-audio--midi-graph-wiring)
8. [MIDI scheduling](#8-midi-scheduling)
9. [Parameter automation](#9-parameter-automation)
10. [CC → parameter routing](#10-cc--parameter-routing)
11. [Playback & offline rendering](#11-playback--offline-rendering)
12. [Audio file player](#12-audio-file-player)
13. [Live keyboard & ordered playback](#13-live-keyboard--ordered-playback)
14. [Recording & monitoring](#14-recording--monitoring)
15. [High-level libraries](#15-high-level-libraries)
16. [Constants](#16-constants)
17. [Building the engine](#17-building-the-engine)
18. [Testing](#18-testing)

---

## 1. Architecture & protocol

Soundshop is two processes:

- **`juce_gui_server.exe`** — a C++/JUCE app that hosts plugins in a
  `juce::AudioProcessorGraph`, schedules sample-accurate MIDI/parameter events,
  and renders realtime or offline. Source in `src/` and `include/` (core engine
  logic lives in `include/host/PluginHostService.h`).
- **`juce_client`** — the Python package you import.

They communicate over **two named pipes** derived from a base name (default
`juceclientserver`):

| Pipe | Path | Direction |
|---|---|---|
| Commands | `\\.\pipe\<name>_commands` | Python → C++ (request/response) |
| Notifications | `\\.\pipe\<name>_notifications` | C++ → Python (async events) |

### Wire format

All integers are little-endian (`struct.pack("<…")`). Strings are a `uint32`
length followed by UTF-8 bytes. Binary blobs use the same length-prefixed frame
(binary-safe — see `sendbytes`/`readbytes1`).

Each command is a single command byte (`send_cmd` enum, 44 commands) followed by
its arguments. Before the server writes a command's response payload it writes a
**status byte**:

- `0x00` — success; the normal response follows.
- `0xFF` — error; a length-prefixed UTF-8 message follows and the Python client
  raises `ServerError` (`from juce_client import ServerError`).

The status byte is written for *every* command (the dispatcher wraps each handler
in try/catch), so a failing command never desyncs or crashes the pipe. Bulk
scheduling helpers read one status byte per queued command.

The `send_cmd` / `recv_cmd` enums are defined in `juce_client/protocol.py` and
must stay in lockstep with the C++ `Common.h` enums.

---

## 2. Connecting & lifecycle

```python
from juce_client import JuceAudioClient

client = JuceAudioClient(
    pipe_name="juceclientserver",   # base name for both pipes
    server_exe_path=None,           # defaults to juce_gui_server.exe beside the package
    pluginDirectories=None,         # optional default scan dirs
    badPluginPaths=None,            # optional cached failing paths
)
```

| Method | Description |
|---|---|
| `start_server() -> bool` | Launch `juce_gui_server.exe` if not already running. Called automatically by `connect()`. |
| `connect(auto_start=True, max_retries=5, retry_delay=1.0) -> bool` | Open both pipes, auto-starting the server on the first attempt. Returns `True` on success. |
| `disconnect(shutdown_server=False)` | Close pipes and stop the notification thread. With `shutdown_server=True`, sends `cmd_shutdown` first and waits for the process to exit. |

`__del__` calls `disconnect(shutdown_server=True)` as a safety net, but prefer an
explicit `disconnect()` in your scripts.

> **Note:** the pipe name is fixed per server instance. If a previous run left a
> `juce_gui_server.exe` alive it will hold the pipe name and block new
> connections — kill stale servers (`taskkill /F /IM juce_gui_server.exe`) if a
> connect hangs.

---

## 3. Notifications

The server pushes asynchronous events on the notifications pipe. Start a
background listener:

```python
def on_event(cmd, data):
    # cmd is a recv_cmd value; data is a dict
    ...

client.start_notification_listener(callback=on_event)
```

If no callback is given, events are appended to `client.notification_log` as
`(cmd, data)` tuples. Server-error notifications are also stashed so the *next*
command raises `ServerError` (this surfaces async faults like an audio-callback
crash).

`recv_cmd` events and their `data` dicts:

| Event | `data` keys |
|---|---|
| `param_changed` | `pluginKey, parameterIndex, value, atSample` |
| `param_changes_end` | *(empty)* |
| `stop_playback` | *(empty)* |
| `midi_note_event` | `noteNumber, velocity, channel, isNoteOn, samplePosition` |
| `midi_cc_event` | `controller, value, channel, atSample` |
| `virtual_keyboard_note_event` | same as `midi_note_event` |
| `virtual_keyboard_cc_event` | same as `midi_cc_event` |
| `midi_keyboard_routed` / `virtual_keyboard_routed` | `pluginId, samplePosition` |
| `recording_started` | `filename, startSample` |
| `recording_stopped` | `filename, endSample` |
| `monitoring_changed` | `state, samplePosition` |
| `audio_file_loaded` | `filename, playerId` |
| `audio_playback_started` / `audio_playback_stopped` | `playerId, samplePosition` |
| `ordered_note_triggered` | `orderNumber, currentIndex, totalCount` |
| `ordered_playback_started` / `ordered_playback_stopped` | `samplePosition` |
| `server_error` | `message` |

---

## 4. Plugin discovery, loading & metadata

Loaded plugins are identified by an integer **key you choose** at load time; you
use that key for all later operations on the plugin. This is distinct from a
plugin's scan-list **`pluginId`** (its `desc.uniqueId`), reported by
`listplugins`.

| Method | Description |
|---|---|
| `scanplugins(directories=None, badpaths=None) -> int` | Scan directories for plugins; returns the number found. Falls back to `client.pluginDirectories` / `client.badPluginPaths`. |
| `listplugins() -> list[dict]` | Scanned plugins. Each dict: `isInstrument, pluginId, numInputChannels, numOutputChannels, name, descriptiveName, pluginFormatName, category, manufacturerName, version, fileOrIdentifier, lastFileModTime, path`. Also cached on `client.availablePlugins`. |
| `listbadpaths() -> list[str]` | Paths that failed to load during scanning. |
| `loadplugin(path, key) -> (success, name, uid, errmsg)` | Load a plugin by file path under the given key. |
| `loadpluginbyuid(uid, key) -> Processor` | Load by scan `pluginId` (`uid`) under `key`. Raises `ValueError` on failure. Returns a `Processor(uid, key)`. |
| `getPluginInfo(pluginId) -> dict` | Metadata for a scanned `pluginId` (same fields as `listplugins`). A bad id returns a blank record (empty name) — never crashes. |
| `getChannelsInfo(pluginKey) -> dict` | `success, acceptsMidi, producesMidi, inputBuses, outputBuses, errmsg`; each bus has `numChannels, channelTypes, isEnabled, mainBusLayout`. |
| `showpluginui(pluginId) -> (success, errmsg)` | Open the plugin's editor window. |
| `hidepluginui(pluginId) -> (success, "")` | Close the editor window. |
| `removeplugin(pluginKey) -> int` | Remove one loaded plugin. |
| `clearallplugins() -> int` | Remove all loaded plugins. |

> Loading a plugin also creates and connects a `MidiSourceNode` so the plugin can
> receive scheduled MIDI, and (in realtime mode) registers a parameter-change
> listener. This applies to both `loadplugin` and `loadpluginbyuid`.

---

## 5. Parameters

`getParamsInfo(pluginKey)` returns a list of parameter dicts:

| Field | Meaning |
|---|---|
| `originalIndex` | **Use this** as the parameter index for `setparameter` / scheduling — not the array position. |
| `name` | Display name. |
| `minValue, maxValue, interval, defaultValue, skewFactor, value` | Range / current normalized value. |
| `numSteps` | Step count (continuous params report `INT_MAX`). |
| `isDiscrete, isBoolean, isOrientationInverted, isAutomatable, isMetaParameter` | Flags. |

Continuous parameters are included (a past bug that filtered them out is fixed).

| Method | Description |
|---|---|
| `getParamsInfo(pluginKey) -> list[dict]` | All parameters. Raises `RuntimeError` on failure. |
| `setparameter(pluginKey, parameterIndex, value) -> int` | Set a parameter immediately (normalized 0..1). |
| `getparameter(pluginKey, parameterIndex) -> float` | Read current normalized value. Raises `RuntimeError` on failure. |

---

## 6. Plugin state (patch save/restore)

These carry the plugin's opaque state blob (`getStateInformation` /
`setStateInformation`) over a binary-safe frame, capturing a **full patch** —
including routing that is *not* exposed as host-automatable parameters (e.g.
Vital's modulation matrix).

| Method | Description |
|---|---|
| `getpluginstate(pluginKey) -> bytes` | The state blob; `b""` on failure. |
| `setpluginstate(pluginKey, data) -> int` | Restore from a blob; `1`/`0`. |

> A plugin may re-normalize its blob on load, so a byte-for-byte round trip is
> not guaranteed (Vital's blob grows by a few bytes). Verify functionally
> (restore a perturbed parameter), not by comparing bytes.

---

## 7. Audio & MIDI graph wiring

Build the processing graph explicitly. Node ids resolve to loaded plugins *and*
audio-file-player nodes. Use `outputIndex` (`-1`) for the graph audio output and
`inputIndex` (`-2`) for the audio input device.

| Method | Description |
|---|---|
| `connectaudio(sourceId, sourceChannel, destId, destChannel) -> int` | Connect one audio channel edge. Returns `1` on success, `0` if a node/channel can't be resolved. |
| `connectmidi(sourceId, destId) -> int` | Connect a MIDI edge. |

```python
client.connectaudio(proc.key, 0, outputIndex, 0)   # left
client.connectaudio(proc.key, 1, outputIndex, 1)   # right
```

---

## 8. MIDI scheduling

MIDI events are scheduled relative to the start of the *next* playback (the
scheduler cursor resets to 0 when playback ends, so renders are deterministic).

| Method | Description |
|---|---|
| `schedulemidinote(pluginId, note, velocity, startTime, duration, channel)` | One note. `startTime`/`duration` in **seconds**; `velocity` 0..127; `channel` 1-based. |
| `schedulemidinotes(notes)` | Bulk; each entry `(pluginId, note, velocity, startTime, duration, channel)`. |
| `schedulemidicc(pluginId, controller, value, time_s, channel)` | One CC. |
| `schedulemidiccs(ccs)` | Bulk; each `(pluginId, controller, value, time_s, channel)`. |
| `schedulepitchbend(pluginId, value, time_s, channel=1)` | 14-bit pitch bend, `value` 0..16383 (centre 8192). |
| `schedulepitchbends(bends)` | Bulk; each `(pluginId, value, time_s, channel)`. |
| `clearmidischedule()` | Clear **all** scheduled MIDI (notes *and* CCs). |
| `clearmidiccschedule()` | Clear scheduled CCs only. |

> **`clearmidischedule()` wipes notes and CCs.** Call it *before* scheduling a
> take, never between scheduling notes and CCs, or you'll delete events you just
> queued. Use `clearmidiccschedule()` to clear only CCs.

Pitch bend is handled natively by every instrument, making it a clean vehicle for
continuous per-note modulation without needing a host-exposed continuous
parameter.

---

## 9. Parameter automation

Sample-accurate automation of a plugin parameter.

| Method | Description |
|---|---|
| `scheduleparamchange(pluginId, parameterIndex, value, atSample)` | One change at an absolute sample position. |
| `scheduleparamchanges(changes, *, epsilon=0.0)` | Bulk; each `(pluginKey, parameterIndex, value, atSample)`. Consecutive changes within `epsilon` are coalesced. |
| `clearparamschedule()` | Clear scheduled parameter changes. |

---

## 10. CC → parameter routing

Register a host-side MIDI-CC → parameter mapping (like MIDI Learn). Scheduled CCs
honor the mapping exactly like live input.

| Method | Description |
|---|---|
| `routecctoparam(pluginKey, paramIndex, ccController, midiChannel=-1) -> int` | Map a CC to a parameter (`midiChannel=-1` = any). |
| `unroutecctoparam(pluginKey, paramIndex, ccController) -> int` | Remove a mapping. |

---

## 11. Playback & offline rendering

| Method | Description |
|---|---|
| `startplayback(endSample, *, toFile=False, filename="") -> int` | Start playback until `endSample`. Default = realtime audio out; `toFile=True` renders offline to `filename` (WAV). |
| `stopplayback() -> int` | Stop playback; resets the scheduler cursor to 0. |

```python
# Offline render 3 seconds to WAV
client.startplayback(int(3.0 * sampleRate), toFile=True, filename="out.wav")
```

Offline render is fully deterministic: five identical renders produce identical
peaks. `startplayback` blocks the command until the render is set up; poll the
output file for completion (see `test_coverage.py`'s `render()` helper).

---

## 12. Audio file player

Load a WAV into a graph player node and schedule regions of it. Player ids come
from a distinct high range (starting at 1,000,000) so they never collide with
your plugin keys, and they resolve in `connectaudio`/`connectmidi`.

| Method | Description |
|---|---|
| `loadaudiofileintoplayer(filename) -> int` | Load a WAV; returns a player id (`>= 0`) or negative on failure. |
| `scheduleaudioregion(playerId, startSample, fileStartSample) -> int` | Play the file from `fileStartSample`, positioned at timeline `startSample`. |
| `startaudioregion(playerId, fileStartSample=0) -> int` | Start playing immediately from a file offset. |
| `stopaudioregion(playerId) -> int` | Stop the player. |

```python
pid = client.loadaudiofileintoplayer("loop.wav")
client.connectaudio(pid, 0, outputIndex, 0)
client.connectaudio(pid, 1, outputIndex, 1)
client.scheduleaudioregion(pid, 0, 0)
client.startplayback(int(2 * sampleRate), toFile=True, filename="out.wav")
```

---

## 13. Live keyboard & ordered playback

**Keyboard routing** sends live physical/virtual keyboard MIDI to a plugin:

| Method | Description |
|---|---|
| `routekeyboardinput(pluginId, use_velocity=True, fixed_velocity=1.0) -> int` | Route physical MIDI keyboard to a plugin. |
| `unroutekeyboardinput() -> int` | Stop routing. |
| `showvirtualkeyboard()` / `hidevirtualkeyboard() -> int` | Show/hide the on-screen keyboard. |
| `routevirtualkeyboard(pluginId, use_velocity=True, fixed_velocity=1.0) -> int` | Route the virtual keyboard to a plugin. |
| `unroutevirtualkeyboard() -> int` | Stop routing. |

**Ordered playback** pre-schedules note groups that advance one group per
incoming keyboard note-on:

| Method | Description |
|---|---|
| `scheduleorderednotes(notes) -> int` | Queue notes; each `(order, note, velocity, channel, duration_samples)`. Returns the count. |
| `startorderedplayback(use_keyboard_velocity=True, use_keyboard_duration=True) -> int` | Arm the sequence. |
| `stoporderedplayback() -> int` | Stop. |
| `clearorderednotes() -> int` | Clear the queue. |

> **Headless limitation:** ordered playback only advances on live keyboard
> note-ons (`triggerNextOrderedNotes` fires from `handleIncomingMidiMessage`).
> With no keyboard feeding note-ons it produces no audio, so it can only be
> verified at the command-plumbing level headlessly.

---

## 14. Recording & monitoring

| Method | Description |
|---|---|
| `togglerecording(filename="") -> int` | Start recording to `filename`, or stop when called with an empty string. |
| `togglemonitoring() -> int` | Toggle monitoring (hearing input during recording). |

---

## 15. High-level libraries

These are plain, importable Python packages usable with or without the engine.

### `music_theory` (`juce_client/music_theory.py`)

Scientific-pitch note model and scale/key tools.

- `Note(name)` / `Note(key=…, degree=…)` — e.g. `Note("C4").midi == 60`,
  `Note("A4").midi == 69`.
- `Notes(list)` — a list of notes with helpers; `merge_notes(*notess)`.
- `build_table(key, mode=0) -> list[int]` — scale pitch classes
  (e.g. `build_table("C", 0) == [0,2,4,5,7,9,11]`).
- `change_key(notes, key1, mode1, key2, mode2)` — remap notes between keys.
- `shift_semitones(notes, x)`, `shift_octaves(notes, octave)` — transpose.
- `get_keys`, `get_notes`, `build_notes`, `make_tables` — supporting utilities.

### `signals` (`juce_client/signals/`)

A composable control-signal DAG evaluated per-sample, with common-subexpression
elimination, cycle detection, and beat-awareness.

- **Sources:** `Const`, `TimeFn`, `BeatFn`, `RefSignal` (named reference),
  `WavetableOsc`, `WavetableOverTime`.
- **Math / shaping:** `Add`, `Sub`, `Mul`, `Neg`, `Clamp`, `Smooth`, `Rectify`,
  `Power`, `MapRange`, `Mix`.
- **Infrastructure:** `Signal` base + `as_signal`, `GlobalCanonicalizer`,
  `canonicalize(root) -> CanonResult`, `detect_signal_cycle`,
  `signal_depends_on_beat`, `ControlCache`, `EvalContext`.

### `tempo` (`juce_client/tempo/`)

Tempo maps and beat↔sample/time conversion.

- **Segments:** `ConstBpmByBeat`, `LinearBpmByBeat`, `LambdaBpmByBeat`.
- **`TempoMapBuilder`** → `add_const`, `add_linear_by_beat`,
  `add_lambda_by_beat`, `build(...)`.
- **`TempoMap`** → `beat_to_sample(...)`, `sample_to_beat(...)`,
  `set_bpm_modulation(signal)` (modulate tempo by a `signals` graph),
  `ensure_boundary_anchors(...)`.
- **Quantization:** `quantize_beat_float_to_fraction`, `round_float_to_int`,
  `schedule_beats_to_samples`, `retime_samples_to_beats`.
- **`SignalEngine`** / `compile_project_to_render_plan(...)` — compile a project
  + signal graph into a `RenderPlan`.

### `daw` (`juce_client/daw/project.py`)

A DAW-style project/graph model that compiles to server commands.

- **Graph model:** `Project`, `Connection`, `Track`, `Processor`,
  `ProcessorConnection(s)`, `MidiToParamConnection(s)`.
- **Compilation:** `compile_audio_graph` (topological order + cycle detection),
  `validate_ports`, `insert_gain_processors`, `insert_mixers_for_fanin`,
  `compile_graph`, `compile_to_server_plan`, `send_plan_to_server`.
- **Scheduling helpers:** `coalesce_param_changes`,
  `sample_signal_to_param_changes`, `iter_project_midi_note_events`,
  `iter_project_midi_cc_events`, `iter_project_param_signal_lanes`,
  `schedule_everything_else`.
- **Audio content:** `AudioTrack`, `AudioLane`, `AudioClip`, `AudioFileRef`,
  `Fade`, `Comp`, `CompSegment` (comping/takes), `RenderAudioRegion`.
- **Persistence:** `Song`, `load_song(path)`, `SignalGraphSerializer` /
  `SignalGraphDeserializer`.

---

## 16. Constants

From `juce_client.protocol`:

| Constant | Value | Meaning |
|---|---|---|
| `pipe_name` | `"juceclientserver"` | Default pipe base name. |
| `sampleRate` | `44100` | Engine sample rate (must match C++). |
| `blockSize` | `64` | Engine block size. |
| `inputIndex` | `-2` | Audio input device node id. |
| `outputIndex` | `-1` | Audio output device node id. |
| `leftChannel` / `rightChannel` | `0` / `1` | Stereo channel indices. |
| `defaultDirs` | *(platform-specific)* | Common plugin install directories. |

---

## 17. Building the engine

**Requirements:** CMake 3.15+, C++17 compiler (MSVC 2019+ on Windows), JUCE.
`CMakeLists.txt` auto-detects JUCE at `C:/JUCE` or `D:/JUCE`; otherwise pass
`-DJUCE_DIR=<path>`.

### Windows (one-shot)

```bat
build.bat
```

Configures + builds Release and copies `juce_gui_server.exe` to the project root
(where the Python client looks for it by default).

### Manual

```bat
mkdir build & cd build
cmake .. -A x64 -DCMAKE_BUILD_TYPE=Release
cmake --build . --config Release --verbose
copy /y "bin\Release\juce_gui_server.exe" ..\
```

The engine source is split across `src/` (`.cpp`) and `include/` (`.h`); the core
host logic is in `include/host/PluginHostService.h`. Cross-platform builds work
(Linux needs ALSA/GTK3; macOS needs the Cocoa frameworks).

---

## 18. Testing

Headless test/example scripts (each spawns the server, runs checks, prints a
PASS/FAIL summary, and exits non-zero on failure):

| Script | Covers |
|---|---|
| `test_headless.py` | End-to-end instrument render sanity. |
| `test_features.py` | Broad feature exercise. |
| `test_coverage.py` | Engine paths not covered elsewhere — music-theory regression, audio-file player route/schedule/render, `connect_midi` resolution, plugin metadata (`loadplugin` by path, `getPluginInfo`, `listbadpaths`), ordered-playback plumbing, `clearallplugins`. |
| `test_nested_modulation.py` | Nested modulation reaching audio (LFO-rate-modulated pitch-bend vibrato). |

Known bugs and technical debt are tracked in
[known-issues.md](known-issues.md) — consult and update it when you find or fix
issues.
