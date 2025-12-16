#!/usr/bin/env python3
"""
JUCE Audio Client - Pure Python client for communicating with the JUCE GUI server
No JUCE bindings required - uses named pipes for communication
"""

import os
import sys
import time
import threading
from typing import Dict, Any, Optional, Tuple

if sys.platform == "win32":
    import win32file
    import win32pipe
    import pywintypes
else:
    import stat

class JuceAudioClient:
    def __init__(self, pipe_name: str = "juce_audio_pipe", timeout: float = 5.0):
        """
        Initialize the JUCE audio client.
        
        Args:
            pipe_name: Name of the named pipe to connect to
            timeout: Connection timeout in seconds
        """
        self.pipe_name = pipe_name
        self.timeout = timeout
        self.connected = False
        
        if sys.platform == "win32":
            self.pipe_path = f"\\\\.\\pipe\\{pipe_name}"
        else:
            self.pipe_path = f"/tmp/{pipe_name}"
    
    def connect(self) -> bool:
        """
        Connect to the JUCE server.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            if sys.platform == "win32":
                return self._connect_windows()
            else:
                return self._connect_unix()
        except Exception as e:
            print(f"Connection failed: {e}")
            return False
    
    def _connect_windows(self) -> bool:
        """Connect using Windows named pipes."""
        try:
            # Wait for pipe to become available
            win32pipe.WaitNamedPipe(self.pipe_path, int(self.timeout * 1000))
            
            # Open the pipe
            self.pipe_handle = win32file.CreateFile(
                self.pipe_path,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                0,
                None
            )
            
            self.connected = True
            return True
            
        except pywintypes.error as e:
            print(f"Windows pipe connection failed: {e}")
            return False
    
    def _connect_unix(self) -> bool:
        """Connect using Unix FIFO."""
        try:
            # Wait for pipe to exist
            start_time = time.time()
            while not os.path.exists(self.pipe_path):
                if time.time() - start_time > self.timeout:
                    print(f"Timeout waiting for pipe: {self.pipe_path}")
                    return False
                time.sleep(0.1)
            
            # Open the pipe
            self.pipe_handle = open(self.pipe_path, 'w+b', buffering=0)
            self.connected = True
            return True
            
        except Exception as e:
            print(f"Unix pipe connection failed: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from the server."""
        if self.connected:
            try:
                if sys.platform == "win32":
                    win32file.CloseHandle(self.pipe_handle)
                else:
                    self.pipe_handle.close()
            except:
                pass
            self.connected = False
    
    def _send_command(self, command_type: str, params: Dict[str, str] = None) -> Tuple[bool, str, Dict[str, str]]:
        """
        Send a command to the server and get response.
        
        Args:
            command_type: Type of command to send
            params: Dictionary of parameters
            
        Returns:
            Tuple of (success, message, data_dict)
        """
        if not self.connected:
            return False, "Not connected to server", {}
        
        # Format command according to server's expected format
        # Format: "type:param1=value1,param2=value2"
        command = command_type
        if params:
            param_str = ','.join([f"{k}={v}" for k, v in params.items()])
            command += f":{param_str}"
        
        try:
            # Send command
            command_bytes = command.encode('utf-8')
            
            if sys.platform == "win32":
                win32file.WriteFile(self.pipe_handle, command_bytes)
                # Read response
                result, response_bytes = win32file.ReadFile(self.pipe_handle, 4096)
            else:
                self.pipe_handle.write(command_bytes)
                self.pipe_handle.flush()
                # Read response  
                response_bytes = self.pipe_handle.read(4096)
            
            response = response_bytes.decode('utf-8').strip()
            
            # Parse response format: "OK:message,key1=value1,key2=value2" or "ERROR:message"
            if response.startswith("OK:"):
                success = True
                rest = response[3:]  # Remove "OK:"
            elif response.startswith("ERROR:"):
                success = False
                rest = response[6:]  # Remove "ERROR:"
            else:
                return False, f"Invalid response format: {response}", {}
            
            # Parse message and data
            parts = rest.split(',')
            message = parts[0] if parts else ""
            data = {}
            
            for part in parts[1:]:
                if '=' in part:
                    key, value = part.split('=', 1)
                    data[key] = value
            
            return success, message, data
            
        except Exception as e:
            return False, f"Communication error: {e}", {}
    
    # Plugin management methods
    def load_plugin(self, plugin_path: str, plugin_id: str) -> Tuple[bool, str]:
        """
        Load a plugin from the specified path.
        
        Args:
            plugin_path: Full path to the plugin file
            plugin_id: Unique identifier for this plugin instance
            
        Returns:
            Tuple of (success, message)
        """
        success, message, data = self._send_command("load_plugin", {
            "path": plugin_path,
            "id": plugin_id
        })
        return success, message
    
    def show_plugin_ui(self, plugin_id: str) -> Tuple[bool, str]:
        """
        Show the UI for a loaded plugin.
        
        Args:
            plugin_id: ID of the plugin to show UI for
            
        Returns:
            Tuple of (success, message)
        """
        success, message, _ = self._send_command("show_plugin_ui", {
            "id": plugin_id
        })
        return success, message
    
    def hide_plugin_ui(self, plugin_id: str) -> Tuple[bool, str]:
        """
        Hide the UI for a loaded plugin.
        
        Args:
            plugin_id: ID of the plugin to hide UI for
            
        Returns:
            Tuple of (success, message)
        """
        success, message, _ = self._send_command("hide_plugin_ui", {
            "id": plugin_id
        })
        return success, message
    
    def set_parameter(self, plugin_id: str, param_index: int, value: float) -> Tuple[bool, str]:
        """
        Set a plugin parameter value.
        
        Args:
            plugin_id: ID of the plugin
            param_index: Index of the parameter to set
            value: New parameter value (0.0 to 1.0)
            
        Returns:
            Tuple of (success, message)
        """
        success, message, _ = self._send_command("set_parameter", {
            "id": plugin_id,
            "param_index": str(param_index),
            "value": str(value)
        })
        return success, message
    
    def get_parameter(self, plugin_id: str, param_index: int) -> Tuple[bool, str, Optional[float]]:
        """
        Get a plugin parameter value.
        
        Args:
            plugin_id: ID of the plugin
            param_index: Index of the parameter to get
            
        Returns:
            Tuple of (success, message, value)
        """
        success, message, data = self._send_command("get_parameter", {
            "id": plugin_id,
            "param_index": str(param_index)
        })
        
        value = None
        if success and "value" in data:
            try:
                value = float(data["value"])
            except ValueError:
                pass
        
        return success, message, value
    
    # Audio routing methods
    def connect_audio(self, source_id: str, source_channel: int, 
                     dest_id: str, dest_channel: int) -> Tuple[bool, str]:
        """
        Connect audio from source plugin to destination plugin.
        
        Args:
            source_id: ID of source plugin
            source_channel: Output channel of source
            dest_id: ID of destination plugin  
            dest_channel: Input channel of destination
            
        Returns:
            Tuple of (success, message)
        """
        success, message, _ = self._send_command("connect_audio", {
            "source_id": source_id,
            "source_channel": str(source_channel),
            "dest_id": dest_id,
            "dest_channel": str(dest_channel)
        })
        return success, message
    
    def connect_midi(self, source_id: str, dest_id: str) -> Tuple[bool, str]:
        """
        Connect MIDI from source plugin to destination plugin.
        
        Args:
            source_id: ID of source plugin
            dest_id: ID of destination plugin
            
        Returns:
            Tuple of (success, message)
        """
        success, message, _ = self._send_command("connect_midi", {
            "source_id": source_id,
            "dest_id": dest_id
        })
        return success, message
    
    # Playback control methods
    def start_playback(self) -> Tuple[bool, str]:
        """
        Start audio playback.
        
        Returns:
            Tuple of (success, message)
        """
        success, message, _ = self._send_command("start_playback")
        return success, message
    
    def stop_playback(self) -> Tuple[bool, str]:
        """
        Stop audio playback.
        
        Returns:
            Tuple of (success, message)
        """
        success, message, _ = self._send_command("stop_playback")
        return success, message
    
    def shutdown_server(self) -> Tuple[bool, str]:
        """
        Tell the server to shut down.
        
        Returns:
            Tuple of (success, message)
        """
        success, message, _ = self._send_command("shutdown")
        return success, message


def example_usage():
    """Example of how to use the JUCE Audio Client."""
    client = JuceAudioClient()
    
    print("Connecting to JUCE server...")
    if not client.connect():
        print("Failed to connect to server. Make sure the JUCE server is running.")
        return
    
    print("Connected successfully!")
    
    try:
        # Example: Load a plugin (you'll need to provide a real plugin path)
        plugin_path = r"C:\Program Files\Steinberg\VstPlugins\YourPlugin.dll"  # Windows
        # plugin_path = "/Library/Audio/Plug-Ins/VST/YourPlugin.vst"  # macOS
        # plugin_path = "/usr/lib/vst/YourPlugin.so"  # Linux
        
        success, message = client.load_plugin(plugin_path, "synth1")
        print(f"Load plugin: {success} - {message}")
        
        if success:
            # Show the plugin UI
            success, message = client.show_plugin_ui("synth1")
            print(f"Show UI: {success} - {message}")
            
            # Set a parameter
            success, message = client.set_parameter("synth1", 0, 0.5)
            print(f"Set parameter: {success} - {message}")
            
            # Get a parameter
            success, message, value = client.get_parameter("synth1", 0)
            print(f"Get parameter: {success} - {message} - Value: {value}")
            
            # Start playback
            success, message = client.start_playback()
            print(f"Start playback: {success} - {message}")
            
            # Wait a bit
            print("Playing for 5 seconds...")
            time.sleep(5)
            
            # Stop playback
            success, message = client.stop_playback()
            print(f"Stop playback: {success} - {message}")
            
            # Hide UI
            success, message = client.hide_plugin_ui("synth1")
            print(f"Hide UI: {success} - {message}")
    
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    
    finally:
        print("Disconnecting...")
        client.disconnect()
        print("Done!")


if __name__ == "__main__":
    example_usage()
