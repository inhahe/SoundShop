#!/usr/bin/env python3
"""
Test to understand the exact protocol expected by the JUCE server
"""

import os
import sys
import time
import threading

def test_communication_protocol():
    pipe_path = r"\\.\pipe\audiopipe"
    
    print("Testing JUCE server communication protocol...")
    print(f"Pipe: {pipe_path}")
    print()
    
    # Test 1: Send command and try to read immediately
    print("=== Test 1: Immediate read after write ===")
    try:
        with open(pipe_path, 'w+b', buffering=0) as pipe:
            print("✓ Connected to pipe")
            
            # Send a simple command that should get a response
            test_cmd = b"start_playback:"
            print(f"Sending: {test_cmd}")
            
            pipe.write(test_cmd)
            pipe.flush()
            print("✓ Command sent")
            
            # Try to read with a timeout
            import select
            print("Waiting for response (2 second timeout)...")
            
            # On Windows, select doesn't work with files, so we'll use threading
            response_data = [None]
            error_data = [None]
            
            def read_thread():
                try:
                    response_data[0] = pipe.read(4096)
                except Exception as e:
                    error_data[0] = e
            
            read_t = threading.Thread(target=read_thread)
            read_t.daemon = True
            read_t.start()
            read_t.join(timeout=2.0)
            
            if read_t.is_alive():
                print("✗ Read timeout - server not responding")
            elif error_data[0]:
                print(f"✗ Read error: {error_data[0]}")
            elif response_data[0]:
                response = response_data[0].decode('utf-8', errors='ignore')
                print(f"✓ Got response: '{response}'")
            else:
                print("✓ Read completed but no data")
                
    except Exception as e:
        print(f"✗ Test 1 failed: {e}")
    
    print()
    
    # Test 2: Try different command format
    print("=== Test 2: Different command format ===")
    try:
        with open(pipe_path, 'w+b', buffering=0) as pipe:
            # Try the exact format your server expects
            test_cmd = b"start_playback"  # No colon
            print(f"Sending: {test_cmd}")
            
            pipe.write(test_cmd)
            pipe.flush()
            
            def read_thread2():
                try:
                    response_data[0] = pipe.read(1024)
                except Exception as e:
                    error_data[0] = e
            
            response_data = [None]
            error_data = [None]
            read_t = threading.Thread(target=read_thread2)
            read_t.daemon = True
            read_t.start()
            read_t.join(timeout=2.0)
            
            if response_data[0]:
                response = response_data[0].decode('utf-8', errors='ignore')
                print(f"✓ Response: '{response}'")
            else:
                print("No response or timeout")
                
    except Exception as e:
        print(f"✗ Test 2 failed: {e}")
    
    print()
    
    # Test 3: Try with newline terminator
    print("=== Test 3: Command with newline ===")
    try:
        with open(pipe_path, 'w+b', buffering=0) as pipe:
            test_cmd = b"start_playback:\n"
            print(f"Sending: {test_cmd}")
            
            pipe.write(test_cmd)
            pipe.flush()
            
            # Immediate check if data is available
            try:
                # Try reading just 1 byte to see if anything is there
                first_byte = pipe.read(1)
                if first_byte:
                    rest = pipe.read(1023)
                    response = (first_byte + rest).decode('utf-8', errors='ignore')
                    print(f"✓ Response: '{response}'")
                else:
                    print("No immediate response")
            except Exception as e:
                print(f"Read attempt: {e}")
                
    except Exception as e:
        print(f"✗ Test 3 failed: {e}")
    
    print()
    print("=== Test 4: One-way communication test ===")
    print("Sending shutdown command without trying to read...")
    try:
        with open(pipe_path, 'wb') as pipe:
            pipe.write(b"shutdown:")
            pipe.flush()
        print("✓ Shutdown sent - check if server terminates")
    except Exception as e:
        print(f"✗ Shutdown test failed: {e}")

if __name__ == "__main__":
    print("JUCE SERVER PROTOCOL TEST")
    print("=" * 40)
    
    # Make sure server is running
    print("Make sure your JUCE server is running with 'audiopipe' parameter")
    print("Press Enter when ready...")
    input()
    
    test_communication_protocol()
    
    print("\nTest completed. Check if server process is still running in Task Manager.")
    print("Press Enter to exit...")
    input()
