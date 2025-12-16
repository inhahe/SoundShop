#!/usr/bin/env python3
"""
Build script for JUCE Audio Python bindings.
This script automates the build process using CMake or setuptools.
"""

import os
import sys
import subprocess
import argparse
import platform
from pathlib import Path

def run_command(cmd, cwd=None):
    """Run a command and return the result."""
    print(f"Running: {' '.join(cmd)}")
    try:
        result = subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True)
        if result.stdout:
            print(result.stdout)
        return True
    except subprocess.CalledProcessError as e:
        print(f"Error: {e}")
        if e.stderr:
            print(f"Stderr: {e.stderr}")
        return False

def check_dependencies():
    """Check if required dependencies are available."""
    print("Checking dependencies...")
    
    # Check for CMake
    try:
        subprocess.run(["cmake", "--version"], check=True, capture_output=True)
        print("✓ CMake found")
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("✗ CMake not found. Please install CMake.")
        return False
    
    # Check for Python development headers
    try:
        import pybind11
        print("✓ pybind11 found")
    except ImportError:
        print("✗ pybind11 not found. Installing...")
        if not run_command([sys.executable, "-m", "pip", "install", "pybind11[global]"]):
            return False
        print("✓ pybind11 installed")
    
    # Check for JUCE
    if not Path("JUCE").exists():
        print("✗ JUCE not found. Please clone JUCE into the JUCE directory.")
        print("  git clone https://github.com/juce-framework/JUCE.git")
        return False
    else:
        print("✓ JUCE found")
    
    return True

def build_with_cmake(build_type="Release"):
    """Build using CMake."""
    print(f"Building with CMake ({build_type})...")
    
    build_dir = Path("build")
    build_dir.mkdir(exist_ok=True)
    
    # Configure
    cmake_args = [
        "cmake",
        "..",
        f"-DCMAKE_BUILD_TYPE={build_type}",
        "-DJUCE_BUILD_EXAMPLES=OFF",
        "-DJUCE_BUILD_EXTRAS=OFF",
    ]
    
    if platform.system() == "Windows":
        cmake_args.extend(["-A", "x64"])
    
    if not run_command(cmake_args, cwd=build_dir):
        return False
    
    # Build
    build_args = ["cmake", "--build", ".", "--config", build_type]
    if platform.system() != "Windows":
        build_args.extend(["-j", str(os.cpu_count() or 4)])
    
    return run_command(build_args, cwd=build_dir)

def build_with_setuptools():
    """Build using setuptools."""
    print("Building with setuptools...")
    
    return run_command([
        sys.executable, "setup.py", "build_ext", "--inplace"
    ])

def install_package():
    """Install the package."""
    print("Installing package...")
    
    return run_command([
        sys.executable, "-m", "pip", "install", "-e", "."
    ])

def clean_build():
    """Clean build artifacts."""
    print("Cleaning build artifacts...")
    
    import shutil
    
    paths_to_clean = [
        "build",
        "dist",
        "*.egg-info",
        "juce_audio.*.so",
        "juce_audio.*.pyd",
        "juce_audio.*.dylib",
    ]
    
    for pattern in paths_to_clean:
        if "*" in pattern:
            import glob
            for path in glob.glob(pattern):
                if os.path.isdir(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                print(f"Removed: {path}")
        else:
            path = Path(pattern)
            if path.exists():
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink()
                print(f"Removed: {path}")

def main():
    parser = argparse.ArgumentParser(description="Build JUCE Audio Python bindings")
    parser.add_argument(
        "--method", 
        choices=["cmake", "setuptools", "auto"], 
        default="auto",
        help="Build method to use"
    )
    parser.add_argument(
        "--build-type", 
        choices=["Debug", "Release"], 
        default="Release",
        help="Build type (for CMake)"
    )
    parser.add_argument(
        "--install", 
        action="store_true",
        help="Install the package after building"
    )
    parser.add_argument(
        "--clean", 
        action="store_true",
        help="Clean build artifacts before building"
    )
    parser.add_argument(
        "--clean-only", 
        action="store_true",
        help="Only clean build artifacts, don't build"
    )
    
    args = parser.parse_args()
    
    if args.clean_only:
        clean_build()
        return
    
    if args.clean:
        clean_build()
    
    if not check_dependencies():
        sys.exit(1)
    
    # Determine build method
    if args.method == "auto":
        if Path("CMakeLists.txt").exists():
            method = "cmake"
        else:
            method = "setuptools"
    else:
        method = args.method
    
    print(f"Using build method: {method}")
    
    # Build
    success = False
    if method == "cmake":
        success = build_with_cmake(args.build_type)
    else:
        success = build_with_setuptools()
    
    if not success:
        print("Build failed!")
        sys.exit(1)
    
    print("✓ Build completed successfully!")
    
    # Install if requested
    if args.install:
        if not install_package():
            print("Installation failed!")
            sys.exit(1)
        print("✓ Package installed successfully!")
    
    # Show next steps
    print("\nNext steps:")
    if not args.install:
        print("1. Install the package: python build.py --install")
    print("2. Run the example: python example_usage.py")
    print("3. Check that your plugins are in the expected directories")

if __name__ == "__main__":
    main()