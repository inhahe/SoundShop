# CLAUDE_NOTES.md

## Project Structure
- **Only 3 files matter**: `juce_gui_server.cpp`, `juce_client.py`, `CMakeLists.txt`
- This is a Python/JUCE music project using pipes for IPC
- C++ handles JUCE audio processing and plugin hosting
- Python defines music data (notes, parameters, plugin graphs, etc.)

## Architecture
- **Two named pipes for communication**:
  - Commands pipe: Python → C++ (bidirectional)
  - Notifications pipe: C++ → Python (for parameter changes)
- Real-time playback with sound output OR offline rendering to file
- User can change parameters during playback; changes are sent back to Python for storage
- User can play notes over MIDI input during playback and send that info to Python, too.
- Notifications pipe will be used for more than just parameter change notifications in the future. It will probably be used for C++ to send commands to Python, among other things, so the pipe is probably misnamed.

