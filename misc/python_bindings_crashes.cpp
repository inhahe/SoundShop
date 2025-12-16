#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

// Include JUCE modules
#include <juce_core/juce_core.h>
#include <juce_audio_basics/juce_audio_basics.h>
#include <juce_audio_devices/juce_audio_devices.h>
#include <juce_audio_processors/juce_audio_processors.h>

namespace py = pybind11;

class AudioPluginHost
{
public:
    AudioPluginHost() 
    {
        // Initialize audio formats
        formatManager.addDefaultFormats();
        
        // Initialize audio device manager
        setupAudioDevice();
    }
    
    ~AudioPluginHost()
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
    
    bool isAudioWorking() const
    {
        return deviceManager.getCurrentAudioDevice() != nullptr;
    }
    
    std::vector<std::string> scanForPlugins()
    {
        juce::FileSearchPath searchPath;
        
        // Add Windows VST3 paths
        searchPath.add(juce::File("C:\\Program Files\\Common Files\\VST3"));
        searchPath.add(juce::File("C:\\Program Files (x86)\\Common Files\\VST3"));
        
        // Scan for plugins
        for (int i = 0; i < formatManager.getNumFormats(); ++i)
        {
            auto* format = formatManager.getFormat(i);
            juce::PluginDirectoryScanner scanner(knownPlugins, *format, searchPath, true, juce::File());
            
            juce::String pluginBeingScanned;
            while (scanner.scanNextFile(false, pluginBeingScanned))
            {
                // Scanning in progress
            }
        }
        
        // Return list of found plugins
        std::vector<std::string> plugins;
        for (int i = 0; i < knownPlugins.getNumTypes(); ++i)
        {
            auto* desc = knownPlugins.getType(i);
            std::string pluginInfo = desc->name.toStdString() + " by " + desc->manufacturerName.toStdString();
            plugins.push_back(pluginInfo);
        }
        
        return plugins;
    }
    
    int loadPlugin(const std::string& pluginName)
    {
        for (int i = 0; i < knownPlugins.getNumTypes(); ++i)
        {
            auto* desc = knownPlugins.getType(i);
            std::string fullName = desc->name.toStdString() + " by " + desc->manufacturerName.toStdString();
            
            if (fullName == pluginName)
            {
                juce::String errorMessage;
                auto instance = formatManager.createPluginInstance(*desc, 44100.0, 512, errorMessage);
                
                if (instance != nullptr)
                {
                    int nodeId = nextNodeId++;
                    loadedPlugins[nodeId] = std::move(instance);
                    return nodeId;
                }
                else
                {
                    throw std::runtime_error("Failed to load plugin: " + errorMessage.toStdString());
                }
            }
        }
        
        throw std::runtime_error("Plugin not found: " + pluginName);
    }
    
    void unloadPlugin(int nodeId)
    {
        loadedPlugins.erase(nodeId);
    }
    
    std::vector<std::string> getPluginParameters(int nodeId)
    {
        std::vector<std::string> params;
        
        auto it = loadedPlugins.find(nodeId);
        if (it != loadedPlugins.end())
        {
            auto& plugin = it->second;
            for (int i = 0; i < plugin->getNumParameters(); ++i)
            {
                std::string paramInfo = plugin->getParameterName(i).toStdString() + 
                                      " = " + std::to_string(plugin->getParameter(i));
                params.push_back(paramInfo);
            }
        }
        
        return params;
    }
    
    void setParameter(int nodeId, int paramIndex, float value)
    {
        auto it = loadedPlugins.find(nodeId);
        if (it != loadedPlugins.end())
        {
            auto& plugin = it->second;
            if (paramIndex >= 0 && paramIndex < plugin->getNumParameters())
            {
                plugin->setParameter(paramIndex, juce::jlimit(0.0f, 1.0f, value));
            }
        }
    }
    
    float getParameter(int nodeId, int paramIndex)
    {
        auto it = loadedPlugins.find(nodeId);
        if (it != loadedPlugins.end())
        {
            auto& plugin = it->second;
            if (paramIndex >= 0 && paramIndex < plugin->getNumParameters())
            {
                return plugin->getParameter(paramIndex);
            }
        }
        return 0.0f;
    }

private:
    void setupAudioDevice()
    {
        auto setup = deviceManager.getAudioDeviceSetup();
        setup.bufferSize = 512;
        setup.sampleRate = 44100.0;
        
        juce::String error = deviceManager.initialise(2, 2, nullptr, true, {}, &setup);
        // Don't throw on audio setup failure - just log it
        if (!error.isEmpty())
        {
            juce::Logger::writeToLog("Audio setup warning: " + error);
        }
    }

    juce::AudioPluginFormatManager formatManager;
    juce::KnownPluginList knownPlugins;
    juce::AudioDeviceManager deviceManager;
    
    std::map<int, std::unique_ptr<juce::AudioProcessor>> loadedPlugins;
    int nextNodeId = 1;
};

PYBIND11_MODULE(juce_audio, m) {
    m.doc() = "JUCE Audio Plugin Host Python Bindings";
    
    py::class_<AudioPluginHost>(m, "AudioPluginHost")
        .def(py::init<>())
        .def("get_juce_version", &AudioPluginHost::getJuceVersion)
        .def("is_audio_working", &AudioPluginHost::isAudioWorking)
        .def("scan_for_plugins", &AudioPluginHost::scanForPlugins)
        .def("load_plugin", &AudioPluginHost::loadPlugin)
        .def("unload_plugin", &AudioPluginHost::unloadPlugin)
        .def("get_plugin_parameters", &AudioPluginHost::getPluginParameters)
        .def("set_parameter", &AudioPluginHost::setParameter)
        .def("get_parameter", &AudioPluginHost::getParameter);
}