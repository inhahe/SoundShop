#!/usr/bin/env python3
"""
Simple test client to debug JUCE server connection
"""

import os
import sys
import time

def test_pipe_connection():
    pipe_name = "juce_audio_pipe"
    
    if sys.platform == "win32":
        import win32file
        import win32pipe
        import pywintypes
        
        pipe_path = f"\\\\.\\pipe\\{pipe_name}"
        print(f"Windows: Trying to connect to {pipe_path}")
        
        try:
            # Wait for pipe
            print("Waiting for named pipe to become available...")
            win32pipe.WaitNamedPipe(pipe_path, 5000)  # 5 second timeout
            print("Pipe is available!")
            
            # Try to open
            handle = win32file.CreateFile(
                pipe_path,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0, None,
                win32file.OPEN_EXISTING,
                0, None
            )
            print("Successfully opened pipe!")
            
            # Send a test command
            test_cmd = "shutdown:"
            win32file.WriteFile(handle, test_cmd.encode())
            print("Sent shutdown command")
            
            # Try to read response
            try:
                result, data = win32file.ReadFile(handle, 1024)
                print(f"Server response: {data.decode()}")
            except:
                print("No response from server (this is normal for shutdown)")
            
            win32file.CloseHandle(handle)
            print("Connection test successful!")
            
        except pywintypes.error as e:
            error_code, error_name, error_desc = e.args
            print(f"Windows pipe error: {error_code} - {error_name} - {error_desc}")
            
            if error_code == 2:  # File not found
                print("ERROR: Named pipe does not exist!")
                print("Make sure JUCE server is running with: juce_gui_server.exe juce_audio_pipe")
            elif error_code == 231:  # Pipe busy
                print("ERROR: Pipe is busy (another client connected?)")
        
    else:
        # Unix/Linux/macOS
        pipe_path = f"/tmp/{pipe_name}"
        print(f"Unix: Trying to connect to {pipe_path}")
        
        # Check if pipe exists
        if not os.path.exists(pipe_path):
            print(f"ERROR: Pipe does not exist at {pipe_path}")
            print("Make sure JUCE server is running!")
            return
            
        # Check if it's actually a pipe
        stat_info = os.stat(pipe_path)
        if not os.path.stat.S_ISFIFO(stat_info.st_mode):
            print(f"ERROR: {pipe_path} exists but is not a FIFO pipe")
            return
            
        print("Pipe exists and is a FIFO!")
        
        try:
            print("Opening pipe...")
            with open(pipe_path, 'w+b', buffering=0) as pipe:
                print("Successfully opened pipe!")
                
                # Send test command
                test_cmd = b"shutdown:"
                pipe.write(test_cmd)
                pipe.flush()
                print("Sent shutdown command")
                
                # Try to read response
                try:
                    response = pipe.read(1024)
                    print(f"Server response: {response.decode()}")
                except:
                    print("No response from server (this is normal for shutdown)")
                
                print("Connection test successful!")
                
        except Exception as e:
            print(f"Unix pipe error: {e}")

if __name__ == "__main__":
    print("JUCE Server Connection Test")
    print("=" * 40)
    test_pipe_connection()
