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
    """Comprehensive test function"""
    print("CLEAN JUCE CLIENT TEST")
    print("=" * 30)
    
    client = JuceAudioClient("audiopipe")
    
    if client.connect():
        try:
            # Test basic commands
            print("\n--- Testing basic playback ---")
            success, message = client.start_playback()
            print(f"Start playback: {success} - {message}")
            
            time.sleep(1)
            
            success, message = client.stop_playback()
            print(f"Stop playback: {success} - {message}")
            
            # Test plugin loading (you'll need to provide a real plugin path)
            print("\n--- Testing plugin loading ---")
            print("Note: This will fail without a real plugin path")
            
            # Example plugin paths (uncomment and modify as needed):
            # plugin_path = r"C:\Program Files\Steinberg\VstPlugins\YourPlugin.dll"  # Windows VST
            # plugin_path = r"C:\Program Files\Common Files\VST3\YourPlugin.vst3"   # Windows VST3
            plugin_path = "test.dll"  # This will fail, but test the command
            
            success, message = client.load_plugin(plugin_path, "synth1")
            print(f"Load plugin: {success} - {message}")
            
            if success:
                print("\n--- Testing plugin UI ---")
                success, message = client.show_plugin_ui("synth1")
                print(f"Show UI: {success} - {message}")
                
                time.sleep(1)
                
                print("\n--- Testing parameters ---")
                success, message = client.set_parameter("synth1", 0, 0.5)
                print(f"Set parameter: {success} - {message}")
                
                success, message, value = client.get_parameter("synth1", 0)
                print(f"Get parameter: {success} - {message}")
                
                time.sleep(1)
                
                success, message = client.hide_plugin_ui("synth1")
                print(f"Hide UI: {success} - {message}")
            
            print("\n--- Testing audio routing ---")
            success, message = client.connect_audio("synth1", 0, "master", 0)
            print(f"Connect audio: {success} - {message}")
            
            success, message = client.connect_midi("input", "synth1")
            print(f"Connect MIDI: {success} - {message}")
            
            print("\nAll tests completed successfully!")
            print("\nTo use with real plugins:")
            print("1. Modify the plugin_path variable above")
            print("2. Use actual plugin file paths on your system")
            print("3. Check your JUCE server console for any error messages")
            
        except KeyboardInterrupt:
            print("\nInterrupted")
        finally:
            client.disconnect()
    else:
        print("Failed to connect. Make sure server is running:")
        print('"JUCE GUI Server.exe" audiopipe')
        print("\nTroubleshooting:")
        print("- Check Task Manager for 'JUCE GUI Server.exe'")
        print("- Make sure you're using the same pipe name")
        print("- Try running both as administrator")


if __name__ == "__main__":
    main()
