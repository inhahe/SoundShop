#!/usr/bin/env python3
"""
Complete JUCE Audio Plugin Host Example
Demonstrates all features: plugin loading, GUI, MIDI, connections, real-time audio, and file rendering
"""

import juce_audio
import time
import threading

def main():
    print("Complete JUCE Audio Plugin Host Demo")
    print("=" * 50)
    
    try:
        # Initialize the plugin host (starts GUI server process)
        host = juce_audio.AudioPluginHost()
        print(f"✓ {host.get_juce_version()}")
        print(f"✓ GUI process running: {host.is_gui_process_running()}")
        
        # 1. Plugin Discovery
        print("\n1. Scanning for plugins...")
        plugins = host.scan_for_plugins()
        print(f"Found {len(plugins)} plugins:")
        for i, plugin in enumerate(plugins[:5]):  # Show first 5
            print(f"  {i+1}. {plugin}")
        
        if not plugins:
            print("No plugins found. Install some VST3 plugins to continue.")
            return
        
        # 2. Load plugins
        print("\n2. Loading plugins...")
        synth_id = None
        effect_id = None
        
        # Try to find a synth and an effect
        for plugin in plugins:
            if synth_id is None and any(word in plugin.lower() for word in ['synth', 'piano', 'instrument']):
                try:
                    synth_id = host.load_plugin(plugin)
                    print(f"✓ Loaded synth: {plugin} (ID: {synth_id})")
                    break
                except:
                    continue
        
        for plugin in plugins:
            if effect_id is None and any(word in plugin.lower() for word in ['reverb', 'delay', 'chorus', 'effect']):
                try:
                    effect_id = host.load_plugin(plugin)
                    print(f"✓ Loaded effect: {plugin} (ID: {effect_id})")
                    break
                except:
                    continue
        
        # If no synth/effect found, just load first two plugins
        if synth_id is None:
            synth_id = host.load_plugin(plugins[0])
            print(f"✓ Loaded plugin 1: {plugins[0]} (ID: {synth_id})")
        
        if effect_id is None and len(plugins) > 1:
            effect_id = host.load_plugin(plugins[1])
            print(f"✓ Loaded plugin 2: {plugins[1]} (ID: {effect_id})")
        
        # 3. Plugin Information
        print("\n3. Plugin information:")
        synth_params = host.get_plugin_parameters(synth_id)
        synth_pins = host.get_plugin_pins(synth_id)
        
        print(f"Synth parameters ({len(synth_params)}):")
        for param in synth_params[:3]:  # Show first 3
            print(f"  - {param['name']}: {param['value']}")
        
        print(f"Synth I/O: {synth_pins.get('AUDIO_INPUTS', 0)} inputs, "
              f"{synth_pins.get('AUDIO_OUTPUTS', 0)} outputs, "
              f"MIDI: {synth_pins.get('MIDI_INPUTS', 0)} in/{synth_pins.get('MIDI_OUTPUTS', 0)} out")
        
        if effect_id:
            effect_pins = host.get_plugin_pins(effect_id)
            print(f"Effect I/O: {effect_pins.get('AUDIO_INPUTS', 0)} inputs, "
                  f"{effect_pins.get('AUDIO_OUTPUTS', 0)} outputs")
        
        # 4. Plugin GUI
        print("\n4. Plugin editors:")
        try:
            host.show_plugin_editor(synth_id)
            print(f"✓ Opened synth editor (plugin ID: {synth_id})")
            
            if effect_id:
                host.show_plugin_editor(effect_id)
                print(f"✓ Opened effect editor (plugin ID: {effect_id})")
        except Exception as e:
            print(f"Note: Editor display failed - {e}")
        
        # 5. Plugin connections
        if effect_id and synth_pins.get('AUDIO_OUTPUTS', 0) > 0 and effect_pins.get('AUDIO_INPUTS', 0) > 0:
            print("\n5. Connecting plugins...")
            try:
                # Connect synth output to effect input
                host.connect_plugins(synth_id, 0, effect_id, 0)  # Left channel
                if synth_pins.get('AUDIO_OUTPUTS', 0) > 1 and effect_pins.get('AUDIO_INPUTS', 0) > 1:
                    host.connect_plugins(synth_id, 1, effect_id, 1)  # Right channel
                print("✓ Connected synth → effect")
            except Exception as e:
                print(f"Connection failed: {e}")
        
        # 6. Parameter control
        print("\n6. Parameter automation:")
        if synth_params:
            try:
                # Animate first parameter
                param_idx = int(synth_params[0]['index'])
                original_value = float(synth_params[0]['value'])
                
                print(f"Animating '{synth_params[0]['name']}' from {original_value}")
                
                for i in range(5):
                    value = 0.1 + (i * 0.2)  # 0.1, 0.3, 0.5, 0.7, 0.9
                    host.set_plugin_parameter(synth_id, param_idx, value)
                    print(f"  Set to: {value:.1f}")
                    time.sleep(0.5)
                
                # Restore original value
                host.set_plugin_parameter(synth_id, param_idx, original_value)
                print(f"✓ Restored to: {original_value}")
            except Exception as e:
                print(f"Parameter control failed: {e}")
        
        # 7. MIDI controller binding
        print("\n7. MIDI controller binding:")
        if synth_params:
            try:
                # Bind MIDI CC 1 (mod wheel) to first parameter
                param_idx = int(synth_params[0]['index'])
                host.bind_midi_controller_to_parameter(synth_id, param_idx, 1)
                print(f"✓ Bound MIDI CC 1 to '{synth_params[0]['name']}'")
                print("  (Move your MIDI controller's mod wheel to control this parameter)")
            except Exception as e:
                print(f"MIDI binding failed: {e}")
        
        # 8. Real-time audio and MIDI
        print("\n8. Real-time audio with MIDI:")
        try:
            host.start_playback()
            print("✓ Started real-time playback")
            
            # Play a chord progression
            chords = [
                [60, 64, 67],  # C major
                [62, 65, 69],  # D minor  
                [64, 67, 71],  # E minor
                [60, 64, 67],  # C major
            ]
            
            print("Playing chord progression...")
            for i, chord in enumerate(chords):
                print(f"  Chord {i+1}: {chord}")
                
                # Note on
                for note in chord:
                    host.send_midi_message(synth_id, 1, note, 100, True)
                
                time.sleep(1.5)  # Hold chord
                
                # Note off
                for note in chord:
                    host.send_midi_message(synth_id, 1, note, 0, False)
                
                time.sleep(0.5)  # Brief pause
            
            print("✓ Completed chord progression")
            
        except Exception as e:
            print(f"Real-time audio failed: {e}")
        
        # 9. File rendering
        print("\n9. Rendering to file:")
        try:
            print("Setting up for render...")
            
            # Send notes for rendering
            host.send_midi_message(synth_id, 1, 60, 100, True)  # C4 on
            host.send_midi_message(synth_id, 1, 64, 100, True)  # E4 on
            host.send_midi_message(synth_id, 1, 67, 100, True)  # G4 on
            
            print("Rendering 10 seconds to 'output.wav'...")
            host.render_to_file("output.wav", 10.0)
            print("✓ Rendered to output.wav")
            
            # Note off
            host.send_midi_message(synth_id, 1, 60, 0, False)
            host.send_midi_message(synth_id, 1, 64, 0, False)
            host.send_midi_message(synth_id, 1, 67, 0, False)
            
        except Exception as e:
            print(f"File rendering failed: {e}")
        
        # 10. Interactive mode
        print("\n10. Interactive control:")
        print("Available commands:")
        print("  'play <note>' - Play MIDI note (e.g., 'play 60')")
        print("  'chord <note1> <note2> <note3>' - Play chord")
        print("  'param <index> <value>' - Set parameter")  
        print("  'editor <plugin_id>' - Toggle editor")
        print("  'stop' - Stop playback")
        print("  'start' - Start playback")
        print("  'info' - Show plugin info")
        print("  'quit' - Exit")
        
        try:
            while True:
                cmd = input("\n> ").strip().lower().split()
                
                if not cmd:
                    continue
                    
                if cmd[0] in ['quit', 'exit', 'q']:
                    break
                    
                elif cmd[0] == 'play' and len(cmd) >= 2:
                    try:
                        note = int(cmd[1])
                        host.send_midi_message(synth_id, 1, note, 100, True)
                        time.sleep(0.5)
                        host.send_midi_message(synth_id, 1, note, 0, False)
                        print(f"Played note {note}")
                    except ValueError:
                        print("Invalid note number")
                        
                elif cmd[0] == 'chord' and len(cmd) >= 4:
                    try:
                        notes = [int(cmd[i]) for i in range(1, 4)]
                        for note in notes:
                            host.send_midi_message(synth_id, 1, note, 100, True)
                        print(f"Chord on: {notes}")
                        time.sleep(2)
                        for note in notes:
                            host.send_midi_message(synth_id, 1, note, 0, False)
                        print("Chord off")
                    except ValueError:
                        print("Invalid chord notes")
                        
                elif cmd[0] == 'param' and len(cmd) >= 3:
                    try:
                        param_idx = int(cmd[1])
                        value = float(cmd[2])
                        host.set_plugin_parameter(synth_id, param_idx, value)
                        print(f"Set parameter {param_idx} to {value}")
                    except (ValueError, Exception) as e:
                        print(f"Parameter error: {e}")
                        
                elif cmd[0] == 'editor' and len(cmd) >= 2:
                    try:
                        plugin_id = int(cmd[1])
                        host.show_plugin_editor(plugin_id)
                        print(f"Toggled editor for plugin {plugin_id}")
                    except Exception as e:
                        print(f"Editor error: {e}")
                        
                elif cmd[0] == 'stop':
                    host.stop_playback()
                    print("Stopped playback")
                    
                elif cmd[0] == 'start':
                    host.start_playback()
                    print("Started playback")
                    
                elif cmd[0] == 'info':
                    print(f"Synth ID: {synth_id}")
                    if effect_id:
                        print(f"Effect ID: {effect_id}")
                    print(f"Parameters: {len(synth_params)}")
                    
                else:
                    print("Unknown command")
                    
        except KeyboardInterrupt:
            print("\nInterrupted")
        
        # Cleanup
        print("\n11. Cleanup:")
        try:
            host.stop_playback()
            print("✓ Stopped playback")
            
            host.hide_plugin_editor(synth_id)
            if effect_id:
                host.hide_plugin_editor(effect_id)
            print("✓ Closed editors")
            
            if effect_id:
                host.unload_plugin(effect_id)
                print("✓ Unloaded effect")
                
            host.unload_plugin(synth_id)
            print("✓ Unloaded synth")
            
        except Exception as e:
            print(f"Cleanup warning: {e}")
        
        print("✓ Demo completed successfully!")
        
    except Exception as e:
        print(f"Demo failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()