#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

// Include JUCE headers
#define JUCE_GLOBAL_MODULE_SETTINGS_INCLUDED 1
#include <JuceHeader.h>

#include <memory>
#include <vector>
#include <string>

namespace py = pybind11;

class SimpleAudioHost
{
public:
    SimpleAudioHost() : formatManager()
    {
        // Initialize audio formats
        formatManager.addDefaultFormats();
        
        // Initialize JUCE GUI if needed
        if (juce::MessageManager::getInstance() == nullptr) {
            juce::MessageManager::getInstance();
        }
    }
    
    std::vector<std::string> getAvailableFormats()
    {
        std::vector<std::string> formats;
        
        for (int i = 0; i < formatManager.getNumFormats(); ++i) {
            auto* format = formatManager.getFormat(i);
            formats.push_back(format->getFormatName().toStdString());
        }
        
        return formats;
    }
    
    std::string getJuceVersion()
    {
        return juce::String(JUCE_STRINGIFY(JUCE_MAJOR_VERSION) "." 
                           JUCE_STRINGIFY(JUCE_MINOR_VERSION) "." 
                           JUCE_STRINGIFY(JUCE_BUILDNUMBER)).toStdString();
    }
    
    bool testAudioDeviceManager()
    {
        try {
            juce::AudioDeviceManager deviceManager;
            auto error = deviceManager.initialise(0, 2, nullptr, true);
            deviceManager.closeAudioDevice();
            return error.isEmpty();
        } catch (...) {
            return false;
        }
    }

private:
    juce::AudioPluginFormatManager formatManager;
};

PYBIND11_MODULE(juce_audio, m) {
    m.doc() = "Minimal JUCE Audio Python Bindings";
    
    py::class_<SimpleAudioHost>(m, "SimpleAudioHost")
        .def(py::init<>())
        .def("get_available_formats", &SimpleAudioHost::getAvailableFormats,
             "Get list of available audio formats")
        .def("get_juce_version", &SimpleAudioHost::getJuceVersion,
             "Get JUCE version")
        .def("test_audio_device_manager", &SimpleAudioHost::testAudioDeviceManager,
             "Test if audio device manager works");
}