#!/usr/bin/env python3
"""
Minimal test to see if the issue is in the import or class creation
"""

print("Step 1: About to import...")
try:
    from juce_audio_client import JuceAudioClient
    print("✓ Import successful")
except Exception as e:
    print(f"✗ Import failed: {e}")
    exit(1)

print("Step 2: About to create client...")
try:
    client = JuceAudioClient("audiopipe")
    print("✓ Client created")
except Exception as e:
    print(f"✗ Client creation failed: {e}")
    exit(1)

print("Step 3: About to connect...")
try:
    result = client.connect()
    print(f"Connect result: {result}")
except Exception as e:
    print(f"✗ Connect failed: {e}")
    exit(1)

if result:
    print("Step 4: About to send command...")
    try:
        success, message = client.start_playback()
        print(f"Command result: success={success}, message='{message}'")
    except Exception as e:
        print(f"✗ Command failed: {e}")
    
    print("Step 5: Disconnecting...")
    try:
        client.disconnect()
        print("✓ Disconnected")
    except Exception as e:
        print(f"Disconnect error: {e}")

print("Test completed!")
