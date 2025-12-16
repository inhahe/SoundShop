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
}