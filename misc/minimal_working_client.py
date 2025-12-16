#!/usr/bin/env python3
"""
Minimal client based on what we know works from the persistent connection test
"""

import time

class SimpleJuceClient:
    def __init__(self, pipe_name="audiopipe"):
        self.pipe_path = f"\\\\.\\pipe\\{pipe_name}"
        self.pipe = None
    
    def connect(self):
        """Connect to the server"""
        try:
            print(f"Connecting to {self.pipe_path}...")
            self.pipe = open(self.pipe_path, 'w+b', buffering=0)
            print("✓ Connected!")
            return True
        except Exception as e:
            print(f"✗ Connection failed: {e}")
            return False
    
    def send_command(self, command):
        """Send a command without trying to read response"""
        if not self.pipe:
            print("Not connected!")
            return False
        
        try:
            print(f"Sending: {command}")
            self.pipe.write(command.encode())
            self.pipe.flush()
            print("✓ Command sent")
            
            # Don't try to read - just give server time to process
            time.sleep(0.5)
            return True
            
        except Exception as e:
            print(f"✗ Send failed: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from server"""
        if self.pipe:
            try:
                self.pipe.close()
                print("✓ Disconnected")
            except:
                pass
            self.pipe = None

def test_basic_commands():
    """Test basic commands that we know the server supports"""
    client = SimpleJuceClient("audiopipe")
    
    if not client.connect():
        print("Make sure server is running: \"JUCE GUI Server.exe\" audiopipe")
        return
    
    try:
        # Test commands from our successful test
        print("\n=== Testing basic commands ===")
        
        # Start playback
        client.send_command("start_playback:")
        
        # Stop playback  
        client.send_command("stop_playback:")
        
        # Try loading a plugin (this will probably fail without a real path)
        client.send_command("load_plugin:path=C:\\test.dll,id=test1")
        
        # Show non-existent plugin UI (will fail gracefully)
        client.send_command("show_plugin_ui:id=test1")
        
        print("\n=== All commands sent successfully ===")
        print("Check your server console/output for any responses")
        
    except KeyboardInterrupt:
        print("\nInterrupted")
    
    finally:
        client.disconnect()

if __name__ == "__main__":
    print("MINIMAL JUCE CLIENT TEST")
    print("=" * 40)
    
    print("Make sure JUCE server is running with 'audiopipe' parameter")
    print("Press Enter to start test...")
    input()
    
    test_basic_commands()
    
    print("\nTest completed!")
    print("If no errors appeared, the client is working!")
    print("Press Enter to exit...")
    input()
