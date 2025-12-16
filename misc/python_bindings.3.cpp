#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

// Include JUCE modules directly without JuceHeader.h
#include <juce_core/juce_core.h>
#include <juce_audio_basics/juce_audio_basics.h>
#include <juce_audio_devices/juce_audio_devices.h>

namespace py = pybind11;

class AudioHost
{
public:
    AudioHost() 
    {
        // Initialize JUCE manually
        juce::initialiseJuce_GUI();
        formatManager.addDefaultFormats();
    }
    
    ~AudioHost()
    {
        juce::shutdownJuce_GUI();
    }
    
    std::string getJuceVersion() const
    {
        return juce::String(JUCE_MAJOR_VERSION).toStdString() + "." +
               juce::String(JUCE_MINOR_VERSION).toStdString() + "." +
               juce::String(JUCE_BUILDNUMBER).toStdString();
    }
    
    std::vector<std::string> getAudioFormats() const
    {
        std::vector<std::string> formats;
        for (int i = 0; i < formatManager.getNumFormats(); ++i)
        {
            formats.push_back(formatManager.getFormat(i)->getFormatName().toStdString());
        }
        return formats;
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

private:
    juce::AudioPluginFormatManager formatManager;
};

PYBIND11_MODULE(juce_audio, m) {
    m.doc() = "Simple JUCE Audio Bindings";
    
    py::class_<AudioHost>(m, "AudioHost")
        .def(py::init<>())
        .def("get_juce_version", &AudioHost::getJuceVersion)
        .def("get_audio_formats", &AudioHost::getAudioFormats)  
        .def("test_audio_device", &AudioHost::testAudioDevice);
}