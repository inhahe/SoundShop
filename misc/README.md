# JUCE Audio Python Bindings

A Python library that provides bindings for the JUCE audio framework, enabling you to load and control audio plugins (VST3, AU), create plugin chains, handle MIDI, and render audio from Python.

## Features

- 🎵 **Plugin Loading**: Load VST3 and AU plugins
- 🎛️ **Parameter Control**: Get/set plugin parameters by index or ID
- 🔗 **Plugin Chains**: Connect plugins in series or parallel configurations
- 🎹 **MIDI Support**: Send MIDI messages and bind MIDI controllers to parameters
- 🖥️ **GUI Support**: Show and interact with plugin editors
- 🎧 **Real-time Audio**: Play audio in real-time through your system's audio interface
- 💾 **File Rendering**: Render audio to WAV files
- 🔍 **Plugin Discovery**: Scan for available plugins on your system

## Requirements

### System Requirements

- **Python**: 3.7 or later (tested with 3.13)
- **CMake**: 3.15 or later
- **C++ Compiler**: 
  - Windows: Visual Studio 2019 or later
  - macOS: Xcode 12 or later
  - Linux: GCC 9 or later

### Audio System Requirements

- **Windows**: WASAPI or DirectSound compatible audio interface
- **macOS**: Core Audio compatible audio interface  
- **Linux**: ALSA or JACK compatible audio interface

### Plugin Requirements

- **Windows**: VST3 plugins installed in standard locations
- **macOS**: VST3 or Audio Unit plugins installed in standard locations
- **Linux**: VST3 plugins installed in standard locations

## Installation

### 1. Clone the Repository

```bash
git clone <your-repo-url>
cd juce-audio-python
```

### 2. Get JUCE Framework

```bash
git clone https://github.com/juce-framework/JUCE.git
```

### 3. Install Python Dependencies

```bash
pip install pybind11[global] numpy
```

### 4. Build the Extension

#### Option A: Using the Build Script (Recommended)

```bash
python build.py --install
```

#### Option B: Using CMake Directly

```bash
mkdir build
cd build
cmake .. -DCMAKE_BUILD_TYPE=Release
cmake --build . --config Release
```

#### Option C: Using setuptools

```bash
python setup.py build_ext --inplace
pip install -e .
```

## Quick Start

```python
import juce_audio
import time

# Create the audio host
host = juce_audio.AudioPluginHost()

# Scan for plugins
plugins = host.scan_for_plugins()
print(f"Found {len(plugins)} plugins")

# Load a plugin
if plugins:
    node_id = host.load_plugin(plugins[0])
    print(f"Loaded plugin with ID: {node_id}")
    
    # Show plugin editor
    host.show_plugin_editor(node_id)
    
    # Start real-time playback
    host.start_playback()
    
    # Send a MIDI note
    host.send_midi_message(node_id, 1, 60, 100, True)  # Note on
    time.sleep(1.0)
    host.send_midi_message(node_id, 1, 60, 0, False)   # Note off
    
    # Stop playback
    host.stop_playback()
```

## API Reference

### AudioPluginHost

The main class for hosting audio plugins.

#### Methods

##### Plugin Management

- `scan_for_plugins()` → `List[str]`
  - Scan system for available plugins
  - Returns list of plugin names

- `load_plugin(plugin_name: str)` → `int`
  - Load a plugin by name
  - Returns node ID for the loaded plugin

- `remove_plugin(node_id: int)`
  - Remove a loaded plugin

##### Plugin Information

- `get_plugin_parameters(node_id: int)` → `List[Dict[str, str]]`
  - Get all parameters for a plugin
  - Returns list of parameter info dictionaries

- `get_plugin_pins(node_id: int)` → `Dict[str, List[str]]`
  - Get input/output pins for a plugin
  - Returns dictionary with 'audioInputs', 'audioOutputs', 'midiInputs', 'midiOutputs'

##### Parameter Control

- `set_plugin_parameter(node_id: int, param_index: int, value: float)`
  - Set parameter by index (0.0 to 1.0)

- `set_plugin_parameter_by_id(node_id: int, param_id: str, value: float)`
  - Set parameter by string ID

##### Plugin Connections

- `connect_plugins(source_node: int, source_channel: int, dest_node: int, dest_channel: int)`
  - Connect audio between plugins

- `disconnect_plugins(source_node: int, source_channel: int, dest_node: int, dest_channel: int)`
  - Disconnect audio between plugins

##### Audio Playback

- `start_playback()`
  - Start real-time audio playback

- `stop_playback()`
  - Stop real-time audio playback

- `render_to_file(filename: str, length_seconds: float)`
  - Render audio to WAV file

##### MIDI

- `send_midi_message(node_id: int, channel: int, note: int, velocity: int, is_note_on: bool)`
  - Send MIDI note on/off messages

- `bind_midi_controller_to_parameter(node_id: int, param_index: int, midi_cc: int)`
  - Bind MIDI CC to plugin parameter

##### GUI

- `show_plugin_editor(node_id: int)`
  - Show plugin's graphical editor window

- `hide_plugin_editor(node_id: int)`
  - Hide plugin's graphical editor window

## Examples

### Basic Plugin Loading and Control

```python
import juce_audio

host = juce_audio.AudioPluginHost()
plugins = host.scan_for_plugins()

# Load first available plugin
node_id = host.load_plugin(plugins[0])

# Get parameters
params = host.get_plugin_parameters(node_id)
for param in params:
    print(f"Parameter: {param['name']} = {param['value']}")

# Set a parameter
host.set_plugin_parameter(node_id, 0, 0.5)
```

### Creating a Plugin Chain

```python
# Load two plugins
synth_id = host.load_plugin("My Synth")
effect_id = host.load_plugin("My Reverb")

# Connect synth output to effect input
host.connect_plugins(synth_id, 0, effect_id, 0)  # Left channel
host.connect_plugins(synth_id, 1, effect_id, 1)  # Right channel

# Now MIDI sent to synth will be processed and sent through effect
host.start_playback()
host.send_midi_message(synth_id, 1, 60, 100, True)
```

### MIDI Controller Binding

```python
# Bind MIDI CC 1 (mod wheel) to first parameter of plugin
host.bind_midi_controller_to_parameter(node_id, 0, 1)

# Now moving the mod wheel on your MIDI controller will control the parameter
```

### Audio Rendering

```python
# Set up your plugin chain and parameters
host.start_playback()

# Send some MIDI to create sound
host.send_midi_message(synth_id, 1, 60, 100, True)  # C4 on

# Render 10 seconds to file
host.render_to_file("output.wav", 10.0)

host.send_midi_message(synth_id, 1, 60, 0, False)  # C4 off
```

## Plugin Locations

The library scans these standard locations for plugins:

### Windows
- `C:\Program Files\Common Files\VST3\`
- `C:\Program Files (x86)\Common Files\VST3\`
- `C:\Program Files\VstPlugins\`

### macOS
- `/Library/Audio/Plug-Ins/VST3/`
- `/Library/Audio/Plug-Ins/Components/`
- `~/Library/Audio/Plug-Ins/VST3/`
- `~/Library/Audio/Plug-Ins/Components/`

### Linux
- `/usr/lib/vst3/`
- `/usr/local/lib/vst3/`
- `~/.vst3/`

## Troubleshooting

### Build Issues

1. **CMake not found**: Install CMake from https://cmake.org
2. **JUCE not found**: Make sure JUCE is cloned in the `JUCE/` directory
3. **Compiler errors**: Ensure you have a C++17 compatible compiler
4. **Missing audio libraries**: Install platform-specific audio development libraries

### Runtime Issues

1. **No plugins found**: Check that plugins are installed in standard locations
2. **Audio device error**: Ensure no other applications are using the audio device
3. **Plugin won't load**: Check that the plugin is compatible with your system architecture (32-bit vs 64-bit)

### Platform-Specific Issues

#### Linux
- Install ALSA development libraries: `sudo apt-get install libasound2-dev`
- Install X11 development libraries: `sudo apt-get install libx11-dev libxext-dev`

#### macOS
- Install Xcode command line tools: `xcode-select --install`

#### Windows
- Install Visual Studio with C++ support
- Ensure Windows SDK is installed

## Performance Tips

1. **Buffer Size**: The default buffer size is 512 samples. Adjust based on your needs
2. **Sample Rate**: Default is 44.1kHz. Higher rates increase CPU usage
3. **Plugin Count**: More loaded plugins increase CPU usage and memory consumption
4. **GUI Updates**: Plugin GUIs can be CPU intensive; hide when not needed

## Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests if applicable
5. Submit a pull request

## License

This project is licensed under the MIT License - see the LICENSE file for details.

## Acknowledgments

- [JUCE Framework](https://juce.com/) - The audio framework this library is built upon
- [pybind11](https://github.com/pybind/pybind11) - For Python-C++ bindings
- The audio plugin development community

## Support

If you encounter issues or have questions:

1. Check the troubleshooting section above
2. Search existing GitHub issues
3. Create a new issue with detailed information about your system and the problem

---

**Note**: This library is designed for educational and development purposes. For commercial use, please ensure you comply with the licenses of JUCE, the plugins you use, and any other dependencies.