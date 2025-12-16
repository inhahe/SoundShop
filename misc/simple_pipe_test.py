#!/usr/bin/env python3
"""
Simple pipe test without pywin32 dependency
Uses basic file operations to test the named pipe
"""

import os
import sys
import time

def test_basic_connection():
    pipe_name = "audiopipe"
    pipe_path = f"\\\\.\\pipe\\{pipe_name}"
    
    print(f"Testing connection to: {pipe_path}")
    print("Server is running (confirmed from task manager)")
    print()
    
    # Method 1: Try to open as a regular file (sometimes works for named pipes)
    print("=== Method 1: Basic file open ===")
    try:
        print("Attempting to open pipe as file...")
        with open(pipe_path, 'w+b') as f:
            print("✓ Successfully opened pipe!")
            
            # Try to write
            test_data = b"shutdown:"
            print(f"Writing: {test_data}")
            f.write(test_data)
            f.flush()
            print("✓ Data written")
            
            # Try to read
            print("Attempting to read response...")
            f.seek(0)  # Reset position
            response = f.read(1024)
            print(f"Response: {response}")
            
    except FileNotFoundError:
        print("✗ Pipe not found - server may not be creating the pipe")
    except PermissionError:
        print("✗ Permission denied - try running as administrator")
    except Exception as e:
        print(f"✗ Error: {e}")
        print(f"Error type: {type(e).__name__}")
    
    print()
    
    # Method 2: Try using os.open (lower level)
    print("=== Method 2: OS-level open ===")
    try:
        print("Attempting os.open...")
        fd = os.open(pipe_path, os.O_RDWR)
        print(f"✓ Opened pipe with file descriptor: {fd}")
        
        # Write data
        test_data = b"shutdown:"
        print(f"Writing: {test_data}")
        bytes_written = os.write(fd, test_data)
        print(f"✓ Wrote {bytes_written} bytes")
        
        # Try to read
        print("Attempting to read...")
        response = os.read(fd, 1024)
        print(f"Response: {response}")
        
        os.close(fd)
        
    except FileNotFoundError:
        print("✗ Pipe not found")
    except Exception as e:
        print(f"✗ Error: {e}")
        print(f"Error type: {type(e).__name__}")
    
    print()

def check_server_more():
    print("=== ADDITIONAL SERVER CHECKS ===")
    
    # Check if server is actually creating the pipe
    print("Checking if server is creating pipes...")
    
    # List all named pipes (this requires admin privileges usually)
    print("Attempting to list pipes in \\.\pipe\\...")
    try:
        import glob
        pipes = glob.glob(r"\\.\pipe\*")
        print(f"Found {len(pipes)} pipes")
        
        # Look for our pipe specifically
        target_pipe = r"\\.\pipe\juce_audio_pipe"
        if target_pipe in pipes:
            print("✓ Our pipe exists!")
        else:
            print("✗ Our pipe not found in pipe list")
            print("Available pipes:")
            for pipe in pipes[:10]:  # Show first 10
                print(f"  {pipe}")
            if len(pipes) > 10:
                print(f"  ... and {len(pipes) - 10} more")
                
    except Exception as e:
        print(f"Could not list pipes: {e}")
    
    print()
    
    # Check server command line
    print("Server process info from tasklist:")
    os.system('tasklist /FI "IMAGENAME eq JUCE GUI Server.exe" /FO LIST')

def suggest_solutions():
    print("=== TROUBLESHOOTING SUGGESTIONS ===")
    print()
    print("1. SERVER ISSUE: The server might not be creating the named pipe")
    print("   - The server process is running but may be failing silently")
    print("   - Check if server has proper permissions")
    print("   - Try running both server and client as administrator")
    print()
    print("2. PIPE NAME ISSUE: Server might be using a different pipe name")
    print("   - Server code expects command line argument for pipe name")
    print("   - Make sure you're passing 'juce_audio_pipe' correctly")
    print()
    print("3. SERVER CRASH: Server might be crashing after startup")
    print("   - Check Windows Event Viewer for crashes")
    print("   - Server might be missing dependencies (JUCE DLLs, VC++ runtime)")
    print()
    print("4. PIPE CREATION TIMING: Server might need time to create pipe")
    print("   - Wait a few seconds after starting server")
    print("   - Try connecting multiple times")
    print()

if __name__ == "__main__":
    print("BASIC PIPE CONNECTION TEST")
    print("=" * 40)
    
    test_basic_connection()
    check_server_more()
    suggest_solutions()
    
    print("Press Enter to exit...")
    input()
