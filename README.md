# Soundshop

Soundshop is a **headless, Python-scriptable VST3 host and audio engine**. A
C++/JUCE server does the real-time audio work (plugin hosting, sample-accurate
MIDI/parameter scheduling, offline rendering); an external Python client library
drives it over named pipes. You write ordinary Python — no GUI required — to
load plugins, schedule notes, automate parameters, and render audio to WAV.

On top of the low-level host, Soundshop ships high-level Python composition
libraries: music theory (`music_theory`), a control-signal DSL (`signals`),
tempo maps with beat↔sample conversion (`tempo`), and a DAW-style project/graph
model (`daw`).

> Full API and protocol details: **[REFERENCE.md](REFERENCE.md)**.

---

## Why it's built this way

The audio engine runs as a **separate process** from your Python script, and the
two talk over two named pipes. This keeps Python's GIL and garbage collector off
the real-time audio thread, and — more importantly for scripting — means the
engine can be driven entirely programmatically from any external Python program:
tests, batch pipelines, notebooks, or other tools.

```
┌──────────────────┐   commands pipe (Py → C++)    ┌─────────────────────────┐
│  Your Python      │ ────────────────────────────▶ │  juce_gui_server.exe     │
│  (juce_client)    │                               │  (JUCE audio engine)     │
│                   │ ◀──────────────────────────── │  - VST3/AU/LV2 hosting   │
└──────────────────┘   notifications pipe (C++→Py)  │  - MIDI/param scheduler  │
                                                     │  - offline WAV render    │
                                                     └─────────────────────────┘
```

- **Commands pipe** — Python → C++ requests (load plugin, schedule MIDI, set
  parameter, start playback, …). Each command gets a status byte back
  (`0x00` success / `0xFF` + error string), so failures raise `ServerError`
  in Python instead of desyncing the pipe.
- **Notifications pipe** — C++ → Python events (parameter changed, MIDI input,
  playback stopped, audio file loaded, …), read on a background thread.

## Quick start

```python
from juce_client import JuceAudioClient
from juce_client.protocol import outputIndex, sampleRate

client = JuceAudioClient()          # locates juce_gui_server.exe next to the package
client.connect()                    # spawns the server and connects both pipes
client.start_notification_listener(callback=lambda cmd, data: None)

# 1. Discover and load a synth
client.scanplugins([r"D:\vst3\free"])
synth = next(p for p in client.listplugins() if p["isInstrument"])
proc = client.loadpluginbyuid(synth["pluginId"], key=1)   # your chosen int key

# 2. Route its stereo output to the graph output
client.connectaudio(proc.key, 0, outputIndex, 0)
client.connectaudio(proc.key, 1, outputIndex, 1)

# 3. Schedule a C-major chord (note, velocity, start_s, dur_s, channel)
client.clearmidischedule()
for note in (60, 64, 67):
    client.schedulemidinote(proc.key, note, velocity=100, startTime=0.0,
                            duration=1.5, channel=1)

# 4. Render 2 seconds offline to a WAV
client.startplayback(int(2.0 * sampleRate), toFile=True, filename="chord.wav")

client.disconnect(shutdown_server=True)
```

See `test_headless.py`, `test_features.py`, `test_coverage.py`, and
`test_nested_modulation.py` for complete, runnable examples.

## Building the engine

**Requirements:** CMake 3.15+, a C++17 compiler (MSVC 2019+ on Windows), and the
JUCE framework. `CMakeLists.txt` auto-detects JUCE at `C:/JUCE` or `D:/JUCE`, or
pass `-DJUCE_DIR=<path>`.

```bat
build.bat
```

This configures + builds Release and copies `juce_gui_server.exe` to the project
root (where the Python client expects it). See [REFERENCE.md](REFERENCE.md) for
manual/cross-platform builds.

## Repository layout

| Path | What it is |
|---|---|
| `juce_client/` | Python client package (entry point: `from juce_client import JuceAudioClient`) |
| `juce_client/music_theory.py` | Notes, scales/modes, key changes, transposition, note↔MIDI |
| `juce_client/signals/` | Composable control-signal DSL (oscillators, math, smoothing, wavetables) |
| `juce_client/tempo/` | `TempoMap`, beat↔sample conversion, tempo modulation, `SignalEngine` |
| `juce_client/daw/` | Project/Track/Processor graph model, audio clips/comping, `Song` save/load |
| `src/`, `include/` | C++/JUCE audio engine source |
| `CMakeLists.txt`, `build.bat` | Build configuration |
| `test_*.py` | Headless test/example scripts |
| `REFERENCE.md` | Full API + protocol reference |
| `known-issues.md` | Running log of bugs and tech debt |

## Platform support

VST3 on all platforms; AU on macOS; LV2/LADSPA on Linux. Primary/tested platform
is Windows. Default sample rate 44100 Hz, block size 64 samples.


## License

The code in this repository is MIT licensed - see [LICENSE](LICENSE).

**Distributing binaries is a separate question.** This project links JUCE,
which is offered under either the GPL or a paid commercial licence. The MIT
grant above covers *this* source code; it does not and cannot relicense JUCE.
If you distribute a compiled build, that combined work must satisfy JUCE's
terms - in practice either releasing the binary under the GPL, or holding a
JUCE commercial licence. Building it yourself for your own use is unaffected.
