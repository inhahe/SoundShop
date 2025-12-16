#!/usr/bin/env python3
"""
Manual clean script to remove all build artifacts.
"""

import os
import shutil
import glob
from pathlib import Path

def clean_build_artifacts():
    """Remove all build artifacts."""
    print("Cleaning build artifacts...")
    
    # Directories to remove
    dirs_to_clean = [
        "build",
        "dist", 
        "__pycache__",
        "test_build",
        ".pytest_cache"
    ]
    
    # File patterns to remove
    file_patterns = [
        "*.pyd",      # Windows Python extensions
        "*.so",       # Linux Python extensions  
        "*.dylib",    # macOS Python extensions
        "*.egg-info", # Python egg info
        "test_module.cpp",  # Test files
    ]
    
    removed_count = 0
    
    # Remove directories
    for dir_name in dirs_to_clean:
        dir_path = Path(dir_name)
        if dir_path.exists():
            try:
                shutil.rmtree(dir_path)
                print(f"✓ Removed directory: {dir_name}")
                removed_count += 1
            except Exception as e:
                print(f"✗ Failed to remove {dir_name}: {e}")
    
    # Remove files matching patterns
    for pattern in file_patterns:
        matches = glob.glob(pattern, recursive=True)
        for match in matches:
            try:
                if os.path.isfile(match):
                    os.remove(match)
                    print(f"✓ Removed file: {match}")
                    removed_count += 1
                elif os.path.isdir(match):
                    shutil.rmtree(match)
                    print(f"✓ Removed directory: {match}")
                    removed_count += 1
            except Exception as e:
                print(f"✗ Failed to remove {match}: {e}")
    
    # Also check for any egg-info directories
    for item in Path(".").iterdir():
        if item.is_dir() and item.name.endswith(".egg-info"):
            try:
                shutil.rmtree(item)
                print(f"✓ Removed egg-info: {item.name}")
                removed_count += 1
            except Exception as e:
                print(f"✗ Failed to remove {item.name}: {e}")
    
    if removed_count == 0:
        print("No build artifacts found to clean.")
    else:
        print(f"\n✓ Cleaned {removed_count} items successfully!")

if __name__ == "__main__":
    clean_build_artifacts()