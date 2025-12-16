from pybind11.setup_helpers import Pybind11Extension, build_ext
from setuptools import setup, Extension
from glob import glob
import pybind11
import platform
import os

# Define the extension module
ext_modules = [
    Pybind11Extension(
        "juce_audio",
        sources=["juce_audio_bindings.cpp"],
        include_dirs=[
            pybind11.get_cmake_dir() + "/../../../include",
            "JUCE/modules",
        ],
        define_macros=[
            ("VERSION_INFO", '"dev"'),
            ("JUCE_GLOBAL_MODULE_SETTINGS_INCLUDED", "1"),
            ("JUCE_MODULE_AVAILABLE_juce_audio_basics", "1"),
            ("JUCE_MODULE_AVAILABLE_juce_audio_devices", "1"),
            ("JUCE_MODULE_AVAILABLE_juce_audio_formats", "1"),
            ("JUCE_MODULE_AVAILABLE_juce_audio_processors", "1"),
            ("JUCE_MODULE_AVAILABLE_juce_audio_utils", "1"),
            ("JUCE_MODULE_AVAILABLE_juce_core", "1"),
            ("JUCE_MODULE_AVAILABLE_juce_data_structures", "1"),
            ("JUCE_MODULE_AVAILABLE_juce_events", "1"),
            ("JUCE_MODULE_AVAILABLE_juce_graphics", "1"),
            ("JUCE_MODULE_AVAILABLE_juce_gui_basics", "1"),
            ("JUCE_MODULE_AVAILABLE_juce_gui_extra", "1"),
        ],
        cxx_std=17,
    ),
]

# Platform-specific settings
if platform.system() == "Windows":
    ext_modules[0].define_macros.extend([
        ("JUCE_WINDOWS", "1"),
        ("JUCE_WASAPI", "1"),
        ("JUCE_DIRECTSOUND", "1"),
    ])
    ext_modules[0].libraries.extend([
        "winmm", "ole32", "oleaut32", "imm32", "comdlg32", 
        "shlwapi", "rpcrt4", "wininet", "version", "ws2_32"
    ])
elif platform.system() == "Darwin":
    ext_modules[0].define_macros.extend([
        ("JUCE_MAC", "1"),
        ("JUCE_COREAUDIO", "1"),
        ("JUCE_COREIMAGE_AVAILABLE", "1"),
    ])
    # macOS frameworks will be linked automatically by the system
elif platform.system() == "Linux":
    ext_modules[0].define_macros.extend([
        ("JUCE_LINUX", "1"),
        ("JUCE_ALSA", "1"),
        ("JUCE_JACK", "1"),
    ])
    ext_modules[0].libraries.extend([
        "alsa", "freetype", "X11", "Xext", "Xinerama", 
        "Xrandr", "Xcursor", "pthread", "dl"
    ])

# Common JUCE settings
ext_modules[0].define_macros.extend([
    ("JUCE_WEB_BROWSER", "0"),
    ("JUCE_USE_CURL", "0"),
    ("JUCE_APPLICATION_NAME_STRING", '"JUCE Audio Python"'),
    ("JUCE_APPLICATION_VERSION_STRING", '"1.0.0"'),
    ("JUCE_REPORT_APP_USAGE", "0"),
    ("JUCE_DISPLAY_SPLASH_SCREEN", "0"),
    ("JUCE_USE_DARK_SPLASH_SCREEN", "0"),
])

class CustomBuildExt(build_ext):
    """Custom build extension to handle JUCE-specific build requirements."""
    
    def build_extensions(self):
        # Add optimization flags
        if self.compiler.compiler_type == "msvc":
            for ext in self.extensions:
                ext.extra_compile_args.extend(["/O2", "/std:c++17"])
        else:
            for ext in self.extensions:
                ext.extra_compile_args.extend(["-O3", "-std=c++17"])
        
        super().build_extensions()

setup(
    name="juce_audio",
    version="1.0.0",
    author="Your Name",
    author_email="your.email@example.com",
    description="Python bindings for JUCE audio plugin hosting",
    long_description=open("README.md").read() if os.path.exists("README.md") else "",
    long_description_content_type="text/markdown",
    ext_modules=ext_modules,
    cmdclass={"build_ext": CustomBuildExt},
    zip_safe=False,
    python_requires=">=3.7",
    install_requires=[
        "pybind11>=2.6.0",
        "numpy",
    ],
    extras_require={
        "dev": [
            "pytest",
            "black",
            "flake8",
        ],
    },
    classifiers=[
        "Development Status :: 4 - Beta",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: C++",
        "Topic :: Multimedia :: Sound/Audio",
        "Topic :: Software Development :: Libraries :: Python Modules",
    ],
)