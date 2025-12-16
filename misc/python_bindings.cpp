#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

// Include only working JUCE modules
#include <juce_core/juce_core.h>
#include <juce_audio_basics/juce_audio_basics.h>
#include <juce_audio_devices/juce_audio_devices.h>

namespace py = pybind11;

class AudioHost
{
public:
    AudioHost() 
    {
        // Basic initialization only
    }
    
    ~AudioHost()
    {
        deviceManager.closeAudioDevice();
    }
    
    std::string getJuceVersion() const
    {
        return std::string("JUCE ") + 
               std::to_string(JUCE_MAJOR_VERSION) + "." +
               std::to_string(JUCE_MINOR_VERSION) + "." +
               std::to_string(JUCE_BUILDNUMBER);
    }
    
    bool testAudioDevice()
    {
        try {
            juce::AudioDeviceManager manager;
            auto error = manager.initialise(0, 2, nullptr, true);
            manager.closeAudioDevice();
            return error.isEmpty();
        } catch (...) {
            return false;
        }
    }
    
    std::vector<std::string> getAudioDevices() const
    {
        std::vector<std::string> devices;
        
        auto* deviceType = deviceManager.getCurrentDeviceTypeObject();
        if (deviceType != nullptr)
        {
            auto deviceNames = deviceType->getDeviceNames();
            for (int i = 0; i < deviceNames.size(); ++i)
            {
                devices.push_back(deviceNames[i].toStdString());
            }
        }
        
        return devices;
    }
    
    std::string testBasicString() const
    {
        juce::String juceStr = "Hello from JUCE Audio!";
        return juceStr.toStdString();
    }

private:
    juce::AudioDeviceManager deviceManager;
};

PYBIND11_MODULE(juce_audio, m) {
    m.doc() = "JUCE Audio Python Bindings";
    
    py::class_<AudioHost>(m, "AudioHost")
        .def(py::init<>())
        .def("get_juce_version", &AudioHost::getJuceVersion)
        .def("test_audio_device", &AudioHost::testAudioDevice)
        .def("get_audio_devices", &AudioHost::getAudioDevices)
        .def("test_basic_string", &AudioHost::testBasicString);
}