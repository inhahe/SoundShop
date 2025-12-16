#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

// Include JUCE modules
#include <juce_core/juce_core.h>
#include <juce_events/juce_events.h>
#include <juce_audio_devices/juce_audio_devices.h>

namespace py = pybind11;

class JuceAudioHost
{
public:
    JuceAudioHost() 
    {
        // Initialize JUCE infrastructure properly
        juceInit = std::make_unique<juce::ScopedJuceInitialiser_GUI>();
        
        // Start the message manager thread for audio callbacks
        if (!juce::MessageManager::getInstance()->isThisTheMessageThread())
        {
            // We need to be on the message thread for audio operations
        }
    }
    
    ~JuceAudioHost()
    {
        // Cleanup audio first
        if (deviceManager)
        {
            deviceManager->closeAudioDevice();
            deviceManager.reset();
        }
        
        // Then cleanup JUCE
        juceInit.reset();
    }
    
    std::string getJuceVersion() const
    {
        return std::string("JUCE ") + 
               std::to_string(JUCE_MAJOR_VERSION) + "." +
               std::to_string(JUCE_MINOR_VERSION) + "." +
               std::to_string(JUCE_BUILDNUMBER);
    }
    
    bool initializeAudio()
    {
        if (!deviceManager)
        {
            deviceManager = std::make_unique<juce::AudioDeviceManager>();
        }
        
        try 
        {
            // Initialize with safe defaults
            auto error = deviceManager->initialise(0, 2, nullptr, true);
            return error.isEmpty();
        }
        catch (...)
        {
            return false;
        }
    }
    
    std::vector<std::string> getAudioDeviceTypes() const
    {
        std::vector<std::string> types;
        
        if (deviceManager)
        {
            auto& deviceTypes = deviceManager->getAvailableDeviceTypes();
            for (int i = 0; i < deviceTypes.size(); ++i)
            {
                types.push_back(deviceTypes.getUnchecked(i)->getTypeName().toStdString());
            }
        }
        
        return types;
    }
    
    bool isAudioInitialized() const
    {
        return deviceManager && (deviceManager->getCurrentAudioDevice() != nullptr);
    }
    
    std::string testBasicString() const
    {
        juce::String juceStr = "JUCE Infrastructure Working!";
        return juceStr.toStdString();
    }

private:
    std::unique_ptr<juce::ScopedJuceInitialiser_GUI> juceInit;
    std::unique_ptr<juce::AudioDeviceManager> deviceManager;
};

PYBIND11_MODULE(juce_audio, m) {
    m.doc() = "JUCE Audio Host with Infrastructure";
    
    py::class_<JuceAudioHost>(m, "JuceAudioHost")
        .def(py::init<>())
        .def("get_juce_version", &JuceAudioHost::getJuceVersion)
        .def("initialize_audio", &JuceAudioHost::initializeAudio)
        .def("get_audio_device_types", &JuceAudioHost::getAudioDeviceTypes)
        .def("is_audio_initialized", &JuceAudioHost::isAudioInitialized)
        .def("test_basic_string", &JuceAudioHost::testBasicString);
}