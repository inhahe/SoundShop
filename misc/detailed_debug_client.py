#!/usr/bin/env python3
"""
Detailed diagnostic client for JUCE server connection
"""

import os
import sys
import time
import traceback

def detailed_debug():
    pipe_name = "juce_audio_pipe"
    
    print(f"Python version: {sys.version}")
    print(f"Platform: {sys.platform}")
    print(f"Current directory: {os.getcwd()}")
    print()
    
    if sys.platform == "win32":
        print("=== WINDOWS DEBUGGING ===")
        
        try:
            import win32file
            import win32pipe
            import pywintypes
            print("✓ Win32 modules imported successfully")
        except ImportError as e:
            print(f"✗ Failed to import Win32 modules: {e}")
            print("Install pywin32: pip install pywin32")
            return
        
        pipe_path = f"\\\\.\\pipe\\{pipe_name}"
        print(f"Pipe path: {pipe_path}")
        
        print("\n--- Step 1: Checking if pipe exists ---")
        try:
            # Try to check if pipe exists by attempting a quick connection
            print("Attempting WaitNamedPipe with 100ms timeout...")
            result = win32pipe.WaitNamedPipe(pipe_path, 100)  # Very short timeout
            print(f"WaitNamedPipe result: {result}")
            
            if result:
                print("✓ Pipe exists and is available!")
            else:
                print("✗ Pipe does not exist or is not available")
                print("Make sure server is running and creating the pipe")
                return
                
        except pywintypes.error as e:
            error_code, error_name, error_desc = e.args
            print(f"WaitNamedPipe error: {error_code} - {error_name} - {error_desc}")
            
            if error_code == 2:
                print("✗ ERROR: Pipe does not exist - server may not be creating it")
            elif error_code == 121:
                print("✗ ERROR: Timeout - pipe exists but no server response")
            return
        except Exception as e:
            print(f"Unexpected error in WaitNamedPipe: {e}")
            traceback.print_exc()
            return
        
        print("\n--- Step 2: Attempting to open pipe ---")
        try:
            print("Opening pipe with CreateFile...")
            handle = win32file.CreateFile(
                pipe_path,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0, None,
                win32file.OPEN_EXISTING,
                0, None
            )
            print("✓ Successfully opened pipe!")
            
            print("\n--- Step 3: Testing communication ---")
            # Send a simple command
            test_cmd = "shutdown:"
            print(f"Sending command: '{test_cmd}'")
            
            try:
                bytes_written = win32file.WriteFile(handle, test_cmd.encode())
                print(f"✓ Wrote {bytes_written[1]} bytes")
            except Exception as e:
                print(f"✗ Write failed: {e}")
                win32file.CloseHandle(handle)
                return
            
            # Try to read response
            print("Attempting to read response...")
            try:
                result, data = win32file.ReadFile(handle, 1024)
                if data:
                    response = data.decode('utf-8', errors='ignore')
                    print(f"✓ Server response: '{response}'")
                else:
                    print("✓ No data returned (normal for some commands)")
            except Exception as e:
                print(f"Read failed (might be normal): {e}")
            
            win32file.CloseHandle(handle)
            print("✓ Connection test completed successfully!")
            
        except pywintypes.error as e:
            error_code, error_name, error_desc = e.args
            print(f"CreateFile error: {error_code} - {error_name} - {error_desc}")
            
            if error_code == 2:
                print("✗ File not found - pipe disappeared between checks")
            elif error_code == 231:
                print("✗ Pipe busy - another client may be connected")
            elif error_code == 5:
                print("✗ Access denied - try running as administrator")
        except Exception as e:
            print(f"Unexpected error in CreateFile: {e}")
            traceback.print_exc()
    
    else:
        print("=== UNIX/LINUX/MACOS DEBUGGING ===")
        pipe_path = f"/tmp/{pipe_name}"
        print(f"Pipe path: {pipe_path}")
        
        print("\n--- Step 1: Checking if pipe exists ---")
        if not os.path.exists(pipe_path):
            print("✗ Pipe file does not exist")
            print("Server may not be running or not creating the pipe")
            return
        else:
            print("✓ Pipe file exists")
        
        print("\n--- Step 2: Checking pipe type ---")
        try:
            stat_info = os.stat(pipe_path)
            if stat.S_ISFIFO(stat_info.st_mode):
                print("✓ File is a FIFO pipe")
            else:
                print("✗ File exists but is not a FIFO pipe")
                print(f"Mode: {oct(stat_info.st_mode)}")
                return
        except Exception as e:
            print(f"✗ Error checking file stats: {e}")
            return
        
        print("\n--- Step 3: Attempting to open pipe ---")
        try:
            print("Opening pipe for read/write...")
            with open(pipe_path, 'w+b', buffering=0) as pipe:
                print("✓ Successfully opened pipe")
                
                print("\n--- Step 4: Testing communication ---")
                test_cmd = b"shutdown:"
                print(f"Sending command: {test_cmd}")
                
                pipe.write(test_cmd)
                pipe.flush()
                print("✓ Command sent")
                
                print("Attempting to read response...")
                try:
                    # Set a short timeout for reading
                    import select
                    ready, _, _ = select.select([pipe], [], [], 1.0)  # 1 second timeout
                    if ready:
                        response = pipe.read(1024)
                        print(f"✓ Server response: {response}")
                    else:
                        print("✓ No response within timeout (normal for some commands)")
                except Exception as e:
                    print(f"Read failed (might be normal): {e}")
                
                print("✓ Connection test completed successfully!")
                
        except Exception as e:
            print(f"✗ Error opening/using pipe: {e}")
            traceback.print_exc()

def check_server_process():
    print("\n=== SERVER PROCESS CHECK ===")
    
    if sys.platform == "win32":
        try:
            import psutil
            for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
                if 'juce' in proc.info['name'].lower():
                    print(f"Found process: {proc.info['name']} (PID: {proc.info['pid']})")
                    if proc.info['cmdline']:
                        print(f"  Command line: {' '.join(proc.info['cmdline'])}")
        except ImportError:
            print("Install psutil for better process checking: pip install psutil")
            
            # Fallback using tasklist
            os.system('tasklist | findstr /i juce')
    else:
        print("Checking for JUCE processes:")
        os.system('ps aux | grep -i juce')

if __name__ == "__main__":
    print("DETAILED JUCE SERVER CONNECTION DEBUG")
    print("=" * 50)
    
    try:
        check_server_process()
        detailed_debug()
    except Exception as e:
        print(f"Fatal error in debug script: {e}")
        traceback.print_exc()
    
    print("\nDebug completed. Press Enter to exit...")
    input()
