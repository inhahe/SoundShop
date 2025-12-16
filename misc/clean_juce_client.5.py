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
            return False, "Not connected to server", {}
        
        try:
            command_bytes = command.encode('utf-8')
            self.pipe_handle.write(command_bytes)
            self.pipe_handle.flush()
            time.sleep(0.5)  # Give server time to process
            
            # Try to read response for commands that return data
            if any(cmd in command for cmd in ["list_plugins", "get_plugin_info", "scan_plugins"]):
                try:
                    response_data = self.pipe_handle.read(8192)  # Larger buffer for plugin lists
                    if response_data:
                        response = response_data.decode('utf-8', errors='ignore').strip()
                        return self._parse_response(response)
                except:
                    pass
            
            return True, "Command sent successfully", {}
        except Exception as e:
            return False, f"Communication error: {e}", {}
    
    def _parse_response(self, response):
        """Parse server response"""
        if response.startswith("OK:"):
            success = True
            rest = response[3:]  # Remove "OK:"
        elif response.startswith("ERROR:"):
            success = False
            rest = response[6:]  # Remove "ERROR:"
        else:
            return True, response, {}
        
        # Parse message and data
        parts = rest.split(',')
        message = parts[0] if parts else ""
        data = {}
        
        for part in parts[1:]:
            if '=' in part:
                key, value = part.split('=', 1)
                data[key] = value
        
        return success, message, data
    
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
    
    def scan_plugins(self, plugin_directory=""):
        """Scan for available plugins"""
        if plugin_directory:
            command = f"scan_plugins:directory={plugin_directory}"
        else:
            command = "scan_plugins:"
        return self._send_command(command)
    
    def list_plugins(self):
        """List available plugins"""
        return self._send_command("list_plugins:")
    
    def get_plugin_info(self, plugin_id):
        """Get information about a specific plugin"""
        command = f"get_plugin_info:id={plugin_id}"
        return self._send_command(command)


def server_plugin_browser():
    """Plugin browser using server-side scanning"""
    print("SERVER-SIDE PLUGIN BROWSER")
    print("=" * 40)
    
    client = JuceAudioClient("audiopipe")
    
    if not client.connect():
        print("Failed to connect. Make sure server is running:")
        print('"JUCE GUI Server.exe" audiopipe')
        return
    
    try:
        while True:
            print("\n--- Server Plugin Browser ---")
            print("1. Scan plugins (server-side)")
            print("2. List available plugins")
            print("3. Get plugin info")
            print("4. Load plugin by index")
            print("5. Show plugin UI")
            print("6. Test playback")
            print("7. Quit")
            
            choice = input("\nEnter choice (1-7): ").strip()
            
            if choice == "1":
                directory = input("Enter directory to scan (or press Enter for default): ").strip()
                print("Scanning plugins...")
                success, message, data = client.scan_plugins(directory)
                print(f"Scan result: {success} - {message}")
                if data and "plugins_found" in data:
                    print(f"Found {data['plugins_found']} plugins")
            
            elif choice == "2":
                print("Getting plugin list from server...")

                response = client.list_plugins()

                success, message, plugins = response

                print(f"{response=}") #debug
                
                if success and plugins:
                    print(f"\nFound {len(plugins)} plugins:")
                    print("-" * 60)
                    for plugin in plugins:
                        print(f"{plugin['index']:2d}. {plugin['name']}")
                        print(f"    Path: {plugin['path']}")
                    print("-" * 60)
                else:
                    print("No plugins found or failed to get list")
                    print(f"Result: {success} - {message}")
            
            elif choice == "3":
                index = input("Enter plugin index: ").strip()
                if index.isdigit():
                    success, message, data = client.get_plugin_info(index)
                    print(f"Plugin info: {success} - {message}")
                    if success and data:
                        for key, value in data.items():
                            print(f"  {key}: {value}")
                else:
                    print("Please enter a valid number")
            
            elif choice == "4":
                index = input("Enter plugin index to load: ").strip()
                plugin_id = input("Enter plugin ID: ").strip()
                
                if index.isdigit() and plugin_id:
                    print(f"Loading plugin {index} as '{plugin_id}'...")
                    success, message = client.load_plugin_by_index(index, plugin_id)
                    print(f"Load result: {success} - {message}")
                    
                    if success:
                        show_ui = input("Show plugin UI? (y/n): ").strip().lower()
                        if show_ui == 'y':
                            success, message = client.show_plugin_ui(plugin_id)
                            print(f"Show UI: {success} - {message}")
                else:
                    print("Please enter valid values")
            
            elif choice == "5":
                plugin_id = input("Enter plugin ID to show UI: ").strip()
                if plugin_id:
                    success, message = client.show_plugin_ui(plugin_id)
                    print(f"Show UI: {success} - {message}")
            
            elif choice == "6":
                print("Testing playback...")
                success, message = client.start_playback()
                print(f"Start: {success} - {message}")
                
                input("Press Enter to stop playback...")
                
                success, message = client.stop_playback()
                print(f"Stop: {success} - {message}")
            
            elif choice == "7":
                break
            
            else:
                print("Invalid choice!")
    
    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        client.disconnect()
    """Interactive plugin browser"""
    print("JUCE PLUGIN BROWSER")
    print("=" * 30)
    
    client = JuceAudioClient("audiopipe")
    
    if not client.connect():
        print("Failed to connect. Make sure server is running:")
        print('"JUCE GUI Server.exe" audiopipe')
        return
    
    try:
        while True:
            print("\n--- Plugin Browser Menu ---")
            print("1. Scan for plugins in a directory")
            print("2. Scan common plugin directories")
            print("3. List available plugins")
            print("4. Load a plugin")
            print("5. Show loaded plugin UI")
            print("6. Test playback")
            print("7. Quit")
            
            choice = input("\nEnter choice (1-7): ").strip()
            
            if choice == "1":
                directory = input("Enter plugin directory path: ").strip()
                if directory:
                    print(f"Scanning {directory}...")
                    success, message = client.scan_plugins(directory)
                    print(f"Scan result: {success} - {message}")
            
            elif choice == "2":
                print("Scanning common plugin directories...")
                common_dirs = [
                    r"C:\Program Files\Steinberg\VstPlugins",
                    r"C:\Program Files\Common Files\VST2",
                    r"C:\Program Files\Common Files\VST3",
                    r"C:\Program Files (x86)\Steinberg\VstPlugins",
                    r"C:\Program Files (x86)\VstPlugins"
                ]
                
                for directory in common_dirs:
                    print(f"  Scanning {directory}...")
                    success, message = client.scan_plugins(directory)
                    print(f"    Result: {success} - {message}")
            
            elif choice == "3":
                print("Listing available plugins...")
                success, message = client.list_plugins()
                print(f"Plugin list: {success} - {message}")
            
            elif choice == "4":
                plugin_path = input("Enter full path to plugin file: ").strip()
                plugin_id = input("Enter ID for this plugin instance: ").strip()
                
                if plugin_path and plugin_id:
                    print(f"Loading {plugin_path} as '{plugin_id}'...")
                    success, message = client.load_plugin(plugin_path, plugin_id)
                    print(f"Load result: {success} - {message}")
                    
                    if success:
                        show_ui = input("Show plugin UI? (y/n): ").strip().lower()
                        if show_ui == 'y':
                            success, message = client.show_plugin_ui(plugin_id)
                            print(f"Show UI: {success} - {message}")
            
            elif choice == "5":
                plugin_id = input("Enter plugin ID to show UI: ").strip()
                if plugin_id:
                    success, message = client.show_plugin_ui(plugin_id)
                    print(f"Show UI: {success} - {message}")
            
            elif choice == "6":
                print("Testing playback...")
                success, message = client.start_playback()
                print(f"Start: {success} - {message}")
                
                input("Press Enter to stop playback...")
                
                success, message = client.stop_playback()
                print(f"Stop: {success} - {message}")
            
            elif choice == "7":
                break
            
            else:
                print("Invalid choice!")
    
    except KeyboardInterrupt:
        print("\nInterrupted")
    finally:
        client.disconnect()


def main():
    """Basic test function"""
    print("BASIC JUCE CLIENT TEST")
    print("=" * 30)
    
    client = JuceAudioClient("audiopipe")
    
    if client.connect():
        try:
            # Test basic commands
            print("Testing basic playback...")
            success, message = client.start_playback()
            print(f"Start playback: {success} - {message}")
            
            time.sleep(1)
            
            success, message = client.stop_playback()
            print(f"Stop playback: {success} - {message}")
            
            print("Basic test completed!")
            
        except KeyboardInterrupt:
            print("\nInterrupted")
        finally:
            client.disconnect()
    else:
        print("Failed to connect. Make sure server is running:")
        print('"JUCE GUI Server.exe" audiopipe')
    """Simple plugin loader for common scenarios"""
    import os
    
    print("SIMPLE PLUGIN LOADER")
    print("=" * 30)
    
    # Common plugin directories to check
    plugin_dirs = [
        r"C:\Program Files\Steinberg\VstPlugins",
        r"C:\Program Files\Common Files\VST3", 
        r"C:\Program Files (x86)\Steinberg\VstPlugins",
        r"C:\VstPlugins"
    ]
    
    # Find VST files
    plugins_found = []
    for plugin_dir in plugin_dirs:
        if os.path.exists(plugin_dir):
            print(f"Scanning {plugin_dir}...")
            try:
                for file in os.listdir(plugin_dir):
                    if file.lower().endswith(('.dll', '.vst3', '.vst')):
                        full_path = os.path.join(plugin_dir, file)
                        plugins_found.append((file, full_path))
                        print(f"  Found: {file}")
            except PermissionError:
                print(f"  Permission denied accessing {plugin_dir}")
    
    if not plugins_found:
        print("No plugins found in common directories!")
        print("You can manually specify a plugin path in the interactive browser.")
        return
    
    print(f"\nFound {len(plugins_found)} plugins:")
    for i, (name, path) in enumerate(plugins_found):
        print(f"{i+1}. {name}")
    
    try:
        choice = input(f"\nSelect plugin (1-{len(plugins_found)}) or 'q' to quit: ").strip()
        if choice.lower() == 'q':
            return
            
        plugin_index = int(choice) - 1
        if 0 <= plugin_index < len(plugins_found):
            plugin_name, plugin_path = plugins_found[plugin_index]
            
            print(f"\nSelected: {plugin_name}")
            print(f"Path: {plugin_path}")
            
            # Connect and load
            client = JuceAudioClient("audiopipe")
            if client.connect():
                plugin_id = f"plugin_{plugin_index}"
                print(f"Loading as '{plugin_id}'...")
                
                success, message = client.load_plugin(plugin_path, plugin_id)
                print(f"Load result: {success} - {message}")
                
                if success:
                    print("Plugin loaded successfully!")
                    
                    show_ui = input("Show plugin UI? (y/n): ").strip().lower()
                    if show_ui == 'y':
                        success, message = client.show_plugin_ui(plugin_id)
                        print(f"UI result: {success} - {message}")
                        
                        input("Press Enter when done with plugin...")
                
                client.disconnect()
            else:
                print("Failed to connect to JUCE server!")
        else:
            print("Invalid selection!")
            
    except ValueError:
        print("Invalid input!")
    except KeyboardInterrupt:
        print("\nInterrupted")


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "--browse":
            server_plugin_browser()
        elif sys.argv[1] == "--simple":
            simple_plugin_loader()
        elif sys.argv[1] == "--local":
            interactive_plugin_browser()
        else:
            print("Usage:")
            print("  python clean_juce_client.py --browse    # Server-side plugin browser")
            print("  python clean_juce_client.py --simple    # Simple local plugin loader") 
            print("  python clean_juce_client.py --local     # Local plugin browser")
            print("  python clean_juce_client.py             # Interactive menu")
    else:
        # Ask user what they want to do
        print("JUCE Client Options:")
        print("1. Server-side plugin browser (recommended)")
        print("2. Simple local plugin loader")
        print("3. Local plugin browser")
        print("4. Run basic tests")
        
        choice = input("\nSelect option (1-4): ").strip()
        
        if choice == "1":
            server_plugin_browser()
        elif choice == "2":
            simple_plugin_loader()
        elif choice == "3":
            interactive_plugin_browser()
        elif choice == "4":
            main()
        else:
            print("Invalid choice!")
