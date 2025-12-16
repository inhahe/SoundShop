#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

// Include only the core JUCE module
#include <juce_core/juce_core.h>

namespace py = pybind11;

class SimpleJuceTest
{
public:
    SimpleJuceTest() 
    {
        // No JUCE initialization needed for basic functionality
    }
    
    ~SimpleJuceTest()
    {
        // No cleanup needed
    }
    
    std::string getJuceVersion() const
    {
        return std::string("JUCE ") + 
               std::to_string(JUCE_MAJOR_VERSION) + "." +
               std::to_string(JUCE_MINOR_VERSION) + "." +
               std::to_string(JUCE_BUILDNUMBER);
    }
    
    std::string testBasicString() const
    {
        juce::String juceStr = "Hello from JUCE!";
        return juceStr.toStdString();
    }
    
    int testBasicMath() const
    {
        return 42 * 2;
    }
    
    std::string testTime() const
    {
        juce::Time now = juce::Time::getCurrentTime();
        return now.toString(true, true).toStdString();
    }
};

PYBIND11_MODULE(juce_audio, m) {
    m.doc() = "Minimal JUCE Test Bindings";
    
    py::class_<SimpleJuceTest>(m, "SimpleJuceTest")
        .def(py::init<>())
        .def("get_juce_version", &SimpleJuceTest::getJuceVersion)
        .def("test_basic_string", &SimpleJuceTest::testBasicString)
        .def("test_basic_math", &SimpleJuceTest::testBasicMath)
        .def("test_time", &SimpleJuceTest::testTime);
}from pybind11.setup_helpers import Pybind11Extension, build_ext
from setuptools import setup
import os

# Override the Python library name
import distutils.util
import sysconfig

class CustomBuildExt(build_ext):
    def build_extensions(self):
        # Force use of regular python library
        for ext in self.extensions:
            # Remove any threaded library references
            if hasattr(ext, 'libraries'):
                ext.libraries = [lib for lib in ext.libraries if not lib.endswith('t')]
            # Add the regular python library explicitly
            if not hasattr(ext, 'libraries'):
                ext.libraries = []
            ext.libraries.append('python313')  # or python3 depending on what you have
        super().build_extensions()

ext_modules = [
    Pybind11Extension(
        "juce_audio",
        sources=['python_bindings.cpp', 'JUCE/modules/juce_core/juce_core.cpp'],
        include_dirs=['JUCE/modules'],
        define_macros=[
            ('JUCE_GLOBAL_MODULE_SETTINGS_INCLUDED', '1'),
            ('JUCE_MODULE_AVAILABLE_juce_core', '1'),
            ('JUCE_WEB_BROWSER', '0'),
            ('JUCE_WINDOWS', '1'),
        ],
        cxx_std=17,
    ),
]

setup(
    name="juce_audio",
    ext_modules=ext_modules,
    cmdclass={"build_ext": CustomBuildExt},
)