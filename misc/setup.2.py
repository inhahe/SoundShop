from pybind11.setup_helpers import Pybind11Extension, build_ext
from setuptools import setup
import glob

# Find JUCE modules
juce_modules = [
    'JUCE/modules/juce_core/juce_core.cpp',
    'JUCE/modules/juce_audio_basics/juce_audio_basics.cpp', 
    'JUCE/modules/juce_audio_devices/juce_audio_devices.cpp',
    'JUCE/modules/juce_events/juce_events.cpp'
]

ext_modules = [
    Pybind11Extension(
        "juce_audio",
        sources=['python_bindings.cpp'] + juce_modules,
        include_dirs=['JUCE/modules'],
        define_macros=[
            ('JUCE_GLOBAL_MODULE_SETTINGS_INCLUDED', '1'),
            ('JUCE_MODULE_AVAILABLE_juce_core', '1'),
            ('JUCE_MODULE_AVAILABLE_juce_audio_basics', '1'),
            ('JUCE_MODULE_AVAILABLE_juce_audio_devices', '1'),
            ('JUCE_MODULE_AVAILABLE_juce_events', '1'),
            ('JUCE_WEB_BROWSER', '0'),
            ('JUCE_USE_CURL', '0'),
            ('JUCE_WINDOWS', '1'),
            ('JUCE_WIN32', '1'),
        ],
        cxx_std=17,
    ),
]

setup(
    name="juce_audio",
    ext_modules=ext_modules,
    cmdclass={"build_ext": build_ext},
)