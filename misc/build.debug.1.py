#!/usr/bin/env python3
"""
Debug build script to get more detailed error output.
"""

import os
import sys
import subprocess
from pathlib import Path

def run_verbose_build():
    """Run CMake build with verbose output to see the actual errors."""
    
    build_dir = Path("build")
    if not build_dir.exists():
        print("Build directory doesn't exist. Run configuration first:")
        print("cd build && cmake .. -DCMAKE_BUILD_TYPE=Release -A x64")
        return False
    
    print("Running verbose build to see detailed errors...")
    
    # Try building with verbose output
    cmd = [
        "cmake", "--build", ".", 
        "--config", "Release", 
        "--verbose",  # This will show the actual compile commands
        "--", "/verbosity:normal"  # MSBuild verbosity
    ]
    
    print(f"Running: {' '.join(cmd)}")
    
    try:
        # Don't capture output so we can see it in real-time
        result = subprocess.run(cmd, cwd=build_dir, check=False)
        return result.returncode == 0
    except Exception as e:
        print(f"Error running build: {e}")
        return False

def check_build_files():
    """Check if the necessary build files exist."""
    print("Checking build setup...")
    
    required_files = [
        "CMakeLists.txt",
        "juce_audio_bindings.cpp",
        "JUCE/CMakeLists.txt"
    ]
    
    for file in required_files:
        if Path(file).exists():
            print(f"✓ {file}")
        else:
            print(f"✗ {file} - MISSING!")
            return False
    
    return True

def try_simple_build():
    """Try building just a simple test to isolate the issue."""
    print("Trying to build a minimal test...")
    
    # Create a minimal test file
    test_cpp = """
#include <pybind11/pybind11.h>

PYBIND11_MODULE(test_module, m) {
    m.doc() = "Test module";
    m.def("hello", []() {
        return "Hello from test module!";
    });
}
"""
    
    with open("test_module.cpp", "w") as f:
        f.write(test_cpp)
    
    # Create minimal CMakeLists.txt for test
    test_cmake = """
cmake_minimum_required(VERSION 3.15)
project(test_project)

set(CMAKE_CXX_STANDARD 17)

find_package(pybind11 REQUIRED)

pybind11_add_module(test_module test_module.cpp)
"""
    
    test_dir = Path("test_build")
    test_dir.mkdir(exist_ok=True)
    
    with open(test_dir / "CMakeLists.txt", "w") as f:
        f.write(test_cmake)
    
    # Copy test file
    import shutil
    shutil.copy("test_module.cpp", test_dir)
    
    print("Building test module...")
    
    # Configure
    config_result = subprocess.run([
        "cmake", "..", "-A", "x64"
    ], cwd=test_dir, capture_output=True, text=True)
    
    if config_result.returncode != 0:
        print("Test configuration failed:")
        print(config_result.stderr)
        return False
    
    # Build
    build_result = subprocess.run([
        "cmake", "--build", ".", "--config", "Release", "--verbose"
    ], cwd=test_dir, capture_output=True, text=True)
    
    if build_result.returncode != 0:
        print("Test build failed:")
        print(build_result.stdout)
        print(build_result.stderr)
        return False
    else:
        print("✓ Test build succeeded - basic setup is working")
        return True

def main():
    print("JUCE Audio Build Debugger")
    print("=" * 40)
    
    # Check if files exist
    if not check_build_files():
        print("Missing required files. Please ensure all files are in place.")
        return
    
    # Try simple build first
    print("\n1. Testing basic build setup...")
    if not try_simple_build():
        print("Basic build test failed. There might be an issue with your C++ compiler setup.")
        print("\nTroubleshooting steps:")
        print("1. Make sure Visual Studio 2019+ is installed with C++ support")
        print("2. Try running this from a Visual Studio Developer Command Prompt")
        print("3. Check that Windows SDK is installed")
        return
    
    # Now try the verbose build of the main project
    print("\n2. Building main project with verbose output...")
    if not run_verbose_build():
        print("\nMain project build failed. Check the verbose output above for specific errors.")
        print("\nCommon issues:")
        print("1. Missing JUCE modules or configuration")
        print("2. C++17 compiler compatibility")
        print("3. Missing Windows SDK components")
        print("4. JUCE CMake configuration issues")
    
    # Cleanup test files
    import shutil
    try:
        shutil.rmtree("test_build", ignore_errors=True)
        Path("test_module.cpp").unlink(missing_ok=True)
    except:
        pass

if __name__ == "__main__":
    main()