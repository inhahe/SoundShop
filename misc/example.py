#!/usr/bin/env python3
"""
Example usage of the JUCE Audio Python bindings.
This script demonstrates how to use the various features of the library.
"""

import juce_audio
import time
import threading
import sys

def main():
    print("JUCE Audio Plugin Host Example")
    print("=" * 40)
    
    # Create the audio host
    try:
        host = juce_audio.AudioPluginHost()
        print("✓ Audio host initialized successfully")
    except Exception as e:
        print(f"✗ Failed to initialize audio host: {e}")
        return
    
    # Scan for available plugins
    print("\n1. Scanning for plugins...")
    try:
        plugins = host.scan_for_plugins()
        print(f"Found {len(plugins)} plugins:")
        for i, plugin in enumerate(plugins):
            print(f"  {i+1}. {plugin}")
    except Exception as e:
        print(f"Error scanning plugins: {e}")
        return
    
    if not plugins:
        print("No plugins found. Make sure you have VST3 or AU plugins installed.")
        return
    
    # Load a plugin (using the first one found)
    print("\n2. Loading plugin...")
    try:
        plugin_name = plugins[0]
        node_id = host.load_plugin(plugin_name)
        print(f"✓ Loaded plugin '{plugin_name}' with node ID: {node_id}")
    except Exception as e:
        print(f"✗ Failed to load plugin: {e}")
        return
    
    # Get plugin information
    print("\n3. Plugin information:")
    try:
        # Get parameters
        params = host.get_plugin_parameters(node_id)
        print(f"Parameters ({len(params)}):")
        for param in params[:5]:  # Show first 5 parameters
            print(f"  - {param['name']}: {param['value']} ({param['text']})")
        if len(params) > 5:
            print(f"  ... and {len(params) - 5} more parameters")
        
        # Get pins (inputs/outputs)
        pins = host.get_plugin_pins(node_id)
        print(f"Audio Inputs: {len(pins.get('audioInputs', []))}")
        print(f"Audio Outputs: {len(pins.get('audioOutputs', []))}")
        print(f"MIDI Inputs: {len(pins.get('midiInputs', []))}")
        print(f"MIDI Outputs: {len(pins.get('midiOutputs', []))}")
    except Exception as e:
        print(f"Error getting plugin info: {e}")
    
    # Demonstrate parameter control
    print("\n4. Parameter control example:")
    try:
        if params:
            # Set first parameter to different values
            param_idx = 0
            original_value = float(params[param_idx]['value'])
            print(f"Original value of '{params[param_idx]['name']}': {original_value}")
            
            # Set to 0.5
            host.set_plugin_parameter(node_id, param_idx, 0.5)
            print(f"Set parameter to: 0.5")
            
            # Wait a bit
            time.sleep(0.5)
            
            # Set back to original value
            host.set_plugin_parameter(node_id, param_idx, original_value)
            print(f"Restored to original value: {original_value}")
    except Exception as e:
        print(f"Error controlling parameters: {e}")
    
    # Show plugin editor (if available)
    print("\n5. Plugin editor:")
    try:
        host.show_plugin_editor(node_id)
        print("✓ Plugin editor opened (if supported)")
        print("  Note: The editor window should appear on your screen")
    except Exception as e:
        print(f"Error showing plugin editor: {e}")
    
    # MIDI example
    print("\n6. MIDI example:")
    try:
        # Start playback
        host.start_playback()
        print("✓ Started real-time playback")
        
        # Send some MIDI notes
        notes = [60, 64, 67, 72]  # C major chord
        print("Playing C major chord...")
        
        # Note on
        for note in notes:
            host.send_midi_message(node_id, 1, note, 100, True)  # channel 1, velocity 100
        
        time.sleep(2.0)  # Hold for 2 seconds
        
        # Note off
        for note in notes:
            host.send_midi_message(node_id, 1, note, 0, False)
        
        print("✓ MIDI notes sent")
        
    except Exception as e:
        print(f"Error with MIDI: {e}")
    
    # MIDI controller binding example
    print("\n7. MIDI controller binding:")
    try:
        if params:
            # Bind MIDI CC 1 (mod wheel) to first parameter
            host.bind_midi_controller_to_parameter(node_id, 0, 1)
            print(f"✓ Bound MIDI CC 1 to parameter '{params[0]['name']}'")
            print("  You can now control this parameter with a MIDI controller's mod wheel")
    except Exception as e:
        print(f"Error binding MIDI controller: {e}")
    
    # Render to file example
    print("\n8. Rendering to file:")
    try:
        print("Rendering 5 seconds of audio to 'output.wav'...")
        
        # Send a note to create some sound
        host.send_midi_message(node_id, 1, 60, 100, True)  # Note on
        
        # Render to file
        host.render_to_file("output.wav", 5.0)
        
        # Note off
        host.send_midi_message(node_id, 1, 60, 0, False)
        
        print("✓ Audio rendered to 'output.wav'")
        
    except Exception as e:
        print(f"Error rendering to file: {e}")
    
    # Interactive session
    print("\n9. Interactive control:")
    print("Press Enter to continue, 'q' to quit, or try these commands:")
    print("  'play <note>' - Play a MIDI note (e.g., 'play 60')")
    print("  'param <index> <value>' - Set parameter (e.g., 'param 0 0.75')")
    print("  'info' - Show plugin info again")
    
    try:
        while True:
            user_input = input("> ").strip().lower()
            
            if user_input == 'q' or user_input == 'quit':
                break
            elif user_input == '':
                continue
            elif user_input == 'info':
                params = host.get_plugin_parameters(node_id)
                print(f"Plugin: {plugin_name}")
                print(f"Parameters: {len(params)}")
                for i, param in enumerate(params[:5]):
                    print(f"  {i}: {param['name']} = {param['value']}")
            elif user_input.startswith('play '):
                try:
                    note = int(user_input.split()[1])
                    host.send_midi_message(node_id, 1, note, 100, True)
                    time.sleep(0.1)
                    host.send_midi_message(node_id, 1, note, 0, False)
                    print(f"Played note {note}")
                except (IndexError, ValueError):
                    print("Usage: play <note_number>")
            elif user_input.startswith('param '):
                try:
                    parts = user_input.split()
                    param_idx = int(parts[1])
                    value = float(parts[2])
                    host.set_plugin_parameter(node_id, param_idx, value)
                    print(f"Set parameter {param_idx} to {value}")
                except (IndexError, ValueError):
                    print("Usage: param <index> <value>")
            else:
                print("Unknown command. Type 'q' to quit.")
                
    except KeyboardInterrupt:
        print("\nInterrupted by user")
    
    # Cleanup
    print("\n10. Cleanup:")
    try:
        host.stop_playback()
        host.hide_plugin_editor(node_id)
        host.remove_plugin(node_id)
        print("✓ Cleaned up successfully")
    except Exception as e:
        print(f"Error during cleanup: {e}")
    
    print("\nExample completed!")

def demonstrate_plugin_chain():
    """Demonstrate connecting multiple plugins in a chain."""
    print("\nPlugin Chain Example")
    print("=" * 20)
    
    host = juce_audio.AudioPluginHost()
    plugins = host.scan_for_plugins()
    
    if len(plugins) < 2:
        print("Need at least 2 plugins for chain example")
        return
    
    try:
        # Load two plugins
        node1 = host.load_plugin(plugins[0])
        node2 = host.load_plugin(plugins[1])
        
        print(f"Loaded plugin 1: {plugins[0]} (ID: {node1})")
        print(f"Loaded plugin 2: {plugins[1]} (ID: {node2})")
        
        # Connect them in series (output of plugin 1 to input of plugin 2)
        host.connect_plugins(node1, 0, node2, 0)  # Left channel
        host.connect_plugins(node1, 1, node2, 1)  # Right channel
        
        print("✓ Connected plugins in series")
        
        # Start playback and send MIDI to first plugin
        host.start_playback()
        
        print("Playing note through plugin chain...")
        host.send_midi_message(node1, 1, 60, 100, True)
        time.sleep(2)
        host.send_midi_message(node1, 1, 60, 0, False)
        
        # Cleanup
        host.stop_playback()
        host.disconnect_plugins(node1, 0, node2, 0)
        host.disconnect_plugins(node1, 1, node2, 1)
        host.remove_plugin(node1)
        host.remove_plugin(node2)
        
        print("✓ Plugin chain example completed")
        
    except Exception as e:
        print(f"Error in plugin chain example: {e}")

if __name__ == "__main__":
    try:
        main()
        
        # Ask if user wants to see plugin chain example
        response = input("\nWould you like to see the plugin chain example? (y/n): ")
        if response.lower().startswith('y'):
            demonstrate_plugin_chain()
            
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)