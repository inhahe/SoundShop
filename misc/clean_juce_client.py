#!/usr/bin/env python3
"""
Clean JUCE Audio Client - minimal version to avoid import issues
"""

import time

class JuceAudioClient:
    def __init__(self, pipe_name="audiopipe"):
        self.pipe_name = pipe_name
        self.pipe_path = f"\\\\.\\pipe\\{pipe_name}"
        self.pipe_handle = None
        self.connected = False
    
    def connect(self):
        """Connect to the JUCE server"""
        try:
            print(f"Connecting to {self.pipe_path}...")
            self.pipe_handle = open(self.pipe_path, 'w+b', buffering=0)
            self.connected = True
            print("✓ Connected to JUCE server!")
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from the server"""
        if self.connected and self.pipe_handle:
            try:
                self.pipe_handle.close()
                print("✓ Disconnected")
            except:
                pass
            self.connected = False
    
    def _send_command(self, command):
        """Send a command to the server"""
        if not self.connected:
            return False, "Not connected to server"
        
        try:
            command_bytes = command.encode('utf-8')
            self.pipe_handle.write(command_bytes)
            self.pipe_handle.flush()
            time.sleep(0.5)  # Give server time to process
            return True, "Command sent successfully"
        except Exception as e:
            return False, f"Communication error: {e}"
    
    # Plugin management methods
    def load_plugin(self, plugin_path, plugin_id):
        """Load a plugin"""
        command = f"load_plugin:path={plugin_path},id={plugin_id}"
        return self._send_command(command)
    
    def show_plugin_ui(self, plugin_id):
        """Show plugin UI"""
        command = f"show_plugin_ui:id={plugin_id}"
        return self._send_command(command)
    
    def hide_plugin_ui(self, plugin_id):
        """Hide plugin UI"""
        command = f"hide_plugin_ui:id={plugin_id}"
        return self._send_command(command)
    
    def set_parameter(self, plugin_id, param_index, value):
        """Set plugin parameter"""
        command = f"set_parameter:id={plugin_id},param_index={param_index},value={value}"
        return self._send_command(command)
    
    def get_parameter(self, plugin_id, param_index):
        """Get plugin parameter"""
        command = f"get_parameter:id={plugin_id},param_index={param_index}"
        success, message = self._send_command(command)
        return success, message, None  # Don't try to parse response for now
    
    def start_playback(self):
        """Start playback"""
        return self._send_command("start_playback:")
    
    def stop_playback(self):
        """Stop playback"""
        return self._send_command("stop_playback:")
    
    def connect_audio(self, source_id, source_channel, dest_id, dest_channel):
        """Connect audio"""
        command = f"connect_audio:source_id={source_id},source_channel={source_channel},dest_id={dest_id},dest_channel={dest_channel}"
        return self._send_command(command)
    
    def connect_midi(self, source_id, dest_id):
        """Connect MIDI"""
        command = f"connect_midi:source_id={source_id},dest_id={dest_id}"
        return self._send_command(command)


def main():
    """Simple test function"""
    print("CLEAN JUCE CLIENT TEST")
    print("=" * 30)
    
    client = JuceAudioClient("audiopipe")
    
    if client.connect():
        try:
            # Test basic commands
            success, message = client.start_playback()
            print(f"Start playback: {success} - {message}")
            
            success, message = client.stop_playback()
            print(f"Stop playback: {success} - {message}")
            
            print("\nBasic test completed successfully!")
            
        except KeyboardInterrupt:
            print("\nInterrupted")
        finally:
            client.disconnect()
    else:
        print("Failed to connect. Make sure server is running:")
        print('"JUCE GUI Server.exe" audiopipe')


if __name__ == "__main__":
    main()
