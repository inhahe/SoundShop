#!/usr/bin/env python3
"""
Test script for JUCE audio Python bindings
"""

import juce_audio
import time
import os

def main():
    # Create host instance
    # You can specify the path to juce_gui_server.exe if it's not in the current directory
    host = juce_audio.JuceAudioHost("./juce_gui_server.exe")
    
    try:
        # Connect to the GUI server
        print("Connecting to JUCE GUI server...")
        if not host.connect():
            print("Failed to connect to GUI server")
            return
        
        print("Connected successfully!")
        
        # Load a VST plugin (replace with your actual plugin path)
        plugin_path = "C:/Program Files/Common Files/VST3/YourPlugin.vst3"
        if os.path.exists(plugin_path):
            print(f"Loading plugin: {plugin_path}")
            response = host.load_plugin(plugin_path, "plugin1")
            print(f"Response: {response}")
            
            # Show the plugin UI
            print("Showing plugin UI...")
            response = host.show_plugin_ui("plugin1")
            print(f"Response: {response}")
            
            # Set a parameter
            print("Setting parameter 0 to 0.5...")
            response = host.set_parameter("plugin1", 0, 0.5)
            print(f"Response: {response}")
            
            # Get the parameter value
            print("Getting parameter 0 value...")
            value = host.get_parameter("plugin1", 0)
            print(f"Parameter value: {value}")
            
            # Start playback
            print("Starting playback...")
            response = host.start_playback()
            print(f"Response: {response}")
            
            # Keep the program running for a while
            print("Running for 10 seconds...")
            time.sleep(10)
            
            # Stop playback
            print("Stopping playback...")
            response = host.stop_playback()
            print(f"Response: {response}")
            
            # Hide the UI
            print("Hiding plugin UI...")
            response = host.hide_plugin_ui("plugin1")
            print(f"Response: {response}")
        else:
            print(f"Plugin not found at: {plugin_path}")
            print("Please update the plugin_path variable with a valid VST3 plugin path")
        
        # You can also send raw commands
        print("\nSending raw command...")
        response = host.send_raw_command("get_parameter:id=plugin1,param_index=0")
        print(f"Raw response: {response}")
        
    finally:
        # Always disconnect when done
        print("\nDisconnecting...")
        host.disconnect()
        print("Disconnected")

if __name__ == "__main__":
    main()