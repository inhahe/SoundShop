#!/usr/bin/env python3
"""
Script to fix the JUCE build issues.
"""

import os
import shutil
from pathlib import Path

def main():
    print("JUCE Audio Build Fix")
    print("=" * 20)
    
    # Check if we're in the right directory
    if not Path("JUCE").exists():
        print("Error: JUCE directory not found. Make sure you're in the correct directory.")
        return False
    
    # Step 1: Clean up old build
    print("1. Cleaning old build files...")
    if Path("build").exists():
        shutil.rmtree("build")
        print("   ✓ Removed build directory")
    
    # Step 2: Backup current files
    print("2. Backing up current files...")
    if Path("juce_audio_bindings.cpp").exists():
        shutil.copy("juce_audio_bindings.cpp", "juce_audio_bindings.cpp.backup")
        print("   ✓ Backed up juce_audio_bindings.cpp")
    
    if Path("CMakeLists.txt").exists():
        shutil.copy("CMakeLists.txt", "CMakeLists.txt.backup")
        print("   ✓ Backed up CMakeLists.txt")
    
    # Step 3: Check JUCE structure
    print("3. Checking JUCE structure...")
    juce_modules = Path("JUCE/modules")
    if juce_modules.exists():
        print("   ✓ JUCE modules directory found")
        
        # Check for key modules
        required_modules = [
            "juce_audio_basics",
            "juce_audio_devices", 
            "juce_audio_formats",
            "juce_audio_processors",
            "juce_core"
        ]
        
        for module in required_modules:
            module_path = juce_modules / module
            if module_path.exists():
                print(f"   ✓ Found {module}")
            else:
                print(f"   ✗ Missing {module}")
                return False
    else:
        print("   ✗ JUCE modules directory not found")
        return False
    
    # Step 4: Create updated files
    print("4. Creating updated files...")
    
    # Note: In a real scenario, you would write the fixed content here
    print("   Please replace your files with the fixed versions I provided:")
    print("   - Replace CMakeLists.txt with the 'Fixed CMakeLists.txt' version")
    print("   - Replace juce_audio_bindings.cpp with the 'Fixed C++ Bindings' version")
    
    return True

def test_build():
    print("\n5. Testing the build...")
    print("Run these commands after updating the files:")
    print("   mkdir build")
    print("   cd build")
    print("   cmake .. -A x64 -DCMAKE_BUILD_TYPE=Release")
    print("   cmake --build . --config Release --verbose")

if __name__ == "__main__":
    if main():
        test_build()
        print("\n✓ Fix preparation completed!")
        print("\nNext steps:")
        print("1. Copy the fixed CMakeLists.txt content to your CMakeLists.txt file")
        print("2. Copy the fixed C++ bindings content to your juce_audio_bindings.cpp file")
        print("3. Run: python build.py --clean")
        print("4. Run: python build.py --install")
    else:
        print("\n✗ Fix preparation failed. Check the errors above.")