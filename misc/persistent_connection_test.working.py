#!/usr/bin/env python3
"""
Test persistent connection to match server's expected behavior
"""

import os
import sys
import time
import threading

def test_persistent_connection():
    pipe_path = r"\\.\pipe\audiopipe"
    
    print("Testing persistent connection...")
    print("This will keep the pipe open and send multiple commands")
    print()
    
    try:
        print("Opening pipe connection...")
        pipe = open(pipe_path, 'w+b', buffering=0)
        print("✓ Connected successfully")
        
        def send_command_and_wait(command, description):
            print(f"\n--- {description} ---")
            print(f"Sending: {command}")
            
            try:
                pipe.write(command.encode() if isinstance(command, str) else command)
                pipe.flush()
                print("✓ Command sent")
                
                # Give server time to process
                time.sleep(0.5)
                
                # Try to read response (non-blocking approach)
                response_received = False
                
                def read_with_timeout():
                    nonlocal response_received
                    try:
                        data = pipe.read(1024)
                        if data:
                            response = data.decode('utf-8', errors='ignore').strip()
                            print(f"✓ Response: '{response}'")
                            response_received = True
                        else:
                            print("  No data received")
                    except Exception as e:
                        print(f"  Read error: {e}")
                
                # Try reading with a short timeout
                read_thread = threading.Thread(target=read_with_timeout)
                read_thread.daemon = True
                read_thread.start()
                read_thread.join(timeout=1.0)
                
                if not response_received and read_thread.is_alive():
                    print("  No response within timeout")
                
            except Exception as e:
                print(f"✗ Error sending command: {e}")
                return False
            
            return True
        
        # Test sequence of commands
        commands = [
            ("start_playback:", "Start playback command"),
            ("stop_playback:", "Stop playback command"),
            ("start_playback", "Start playback (no colon)"),  
            ("stop_playback", "Stop playback (no colon)"),
        ]
        
        for cmd, desc in commands:
            if not send_command_and_wait(cmd, desc):
                break
            time.sleep(1)  # Wait between commands
        
        print("\n--- Final test: Shutdown ---")
        print("Sending shutdown command...")
        try:
            pipe.write(b"shutdown:")
            pipe.flush()
            print("✓ Shutdown sent")
            
            # Give server time to shut down
            time.sleep(2)
            
        except Exception as e:
            print(f"Shutdown error: {e}")
        
        print("\nClosing connection...")
        pipe.close()
        
    except FileNotFoundError:
        print("✗ Pipe not found - server is not running or crashed")
        print("Restart the server with: \"JUCE GUI Server.exe\" audiopipe")
    except Exception as e:
        print(f"✗ Connection failed: {e}")

def check_server_status():
    print("=== SERVER STATUS CHECK ===")
    print("Checking for JUCE server process...")
    
    # Use tasklist to check if server is running
    import subprocess
    try:
        result = subprocess.run(['tasklist', '/FI', 'IMAGENAME eq JUCE GUI Server.exe', '/FO', 'CSV'], 
                              capture_output=True, text=True)
        
        if 'JUCE GUI Server.exe' in result.stdout:
            print("✓ JUCE GUI Server.exe is running")
            lines = result.stdout.strip().split('\n')
            if len(lines) > 1:  # Skip header
                print("Process info:")
                print(lines[1])  # Show the process line
        else:
            print("✗ JUCE GUI Server.exe is NOT running")
            print("Start it with: \"JUCE GUI Server.exe\" audiopipe")
            
    except Exception as e:
        print(f"Could not check process status: {e}")

if __name__ == "__main__":
    print("JUCE SERVER PERSISTENT CONNECTION TEST")
    print("=" * 50)
    
    check_server_status()
    print()
    
    print("Make sure JUCE server is running with 'audiopipe' parameter")
    print("Press Enter when ready to test...")
    input()
    
    test_persistent_connection()
    
    print("\n" + "="*50)
    print("Test completed!")
    check_server_status()  # Check if server is still running
    
    print("Press Enter to exit...")
    input()
