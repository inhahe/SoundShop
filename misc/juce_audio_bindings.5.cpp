// Minimal JUCE Audio Python Bindings
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

// JUCE Module includes (each module includes its own dependencies)
#include <juce_core/juce_core.cpp>
#include <juce_events/juce_events.cpp>
#include <juce_data_structures/juce_data_structures.cpp>
#include <juce_graphics/juce_graphics.cpp>
#include <juce_gui_basics/juce_gui_basics.cpp>
#include <juce_audio_basics/juce_audio_basics.cpp>
#include <juce_audio_devices/juce_audio_devices.cpp>
#include <juce_audio_formats/juce_audio_formats.cpp>
#include <juce_audio_processors/juce_audio_processors.cpp>

#include <memory>
#include <vector>
#include <string>
#include <map>

namespace py = pybind11;

class SimpleAudioHost
{
public:
    SimpleAudioHost() 
    {
        // Initialize JUCE
        juce::initialiseJuce_GUI();
        
        // Set up format manager
        formatManager.addDefaultFormats();
        
        // Initialize audio device manager
        setupAudio();
    }
    
    ~SimpleAudioHost()
    {
        deviceManager.closeAudioDevice();
        juce::shutdownJuce_GUI();
    }

private:
    void setupAudio()
    {
        auto setup = deviceManager.getAudioDeviceSetup();
        setup.bufferSize = 512;
        setup.sampleRate = 44100.0;
        
        juce::String error = deviceManager.initialise(2, 2, nullptr, true, {}, &setup);
        audioInitialized = error.isEmpty();
        
        if (audioInitialized) {
            deviceManager.addAudioCallback(&audioPlayer);
        }
    }

public:
    // Basic functionality
    std::string getJuceVersion()
    {
        return juce::String(JUCE_STRINGIFY(JUCE_MAJOR_VERSION) "." 
                           JUCE_STRINGIFY(JUCE_MINOR_VERSION) "." 
                           JUCE_STRINGIFY(JUCE_BUILDNUMBER)).toStdString();
    }
    
    bool isAudioWorking()
    {
        return audioInitialized && (deviceManager.getCurrentAudioDevice() != nullptr);
    }
    
    std::vector<std::string> getAudioDevices()
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
    
    std::vector<std::string> getSupportedFormats()
    {
        std::vector<std::string> formats;
        
        for (int i = 0; i < formatManager.getNumFormats(); ++i)
        {
            auto* format = formatManager.getFormat(i);
            formats.push_back(format->getFormatName().toStdString());
        }
        
        return formats;
    }
    
    // Plugin scanning (simplified)
    std::vector<std::string> scanForPlugins()
    {
        std::vector<std::string> plugins;
        
        juce::FileSearchPath searchPath;
        
        // Add Windows VST3 paths
        #if JUCE_WINDOWS
        searchPath.add(juce::File("C:\\Program Files\\Common Files\\VST3"));
        searchPath.add(juce::File("C:\\Program Files (x86)\\Common Files\\VST3"));
        #endif
        
        // Scan for plugins
        juce::PluginDirectoryScanner scanner(knownPlugins, formatManager, searchPath, true, juce::File());
        
        juce::String pluginBeingScanned;
        while (scanner.scanNextFile(false, pluginBeingScanned))
        {
            // Scanning...
        }
        
        // Get found plugins
        for (int i = 0; i < knownPlugins.getNumTypes(); ++i)
        {
            auto* desc = knownPlugins.getType(i);
            std::string pluginInfo = desc->name.toStdString() + " by " + desc->manufacturerName.toStdString();
            plugins.push_back(pluginInfo);
        }
        
        return plugins;
    }
    
    // Simple plugin loading
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
        auto it = loadedPlugins.find(nodeId);
        if (it != loadedPlugins.end())
        {
            loadedPlugins.erase(it);
        }
    }
    
    // Get plugin info
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
                                      " = " + plugin->getParameterText(i).toStdString();
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
    juce::AudioPluginFormatManager formatManager;
    juce::KnownPluginList knownPlugins;
    juce::AudioDeviceManager deviceManager;
    juce::AudioProcessorPlayer audioPlayer;
    
    std::map<int, std::unique_ptr<juce::AudioProcessor>> loadedPlugins;
    int nextNodeId = 1;
    bool audioInitialized = false;
};

// Python module definition
PYBIND11_MODULE(juce_audio, m) {
    m.doc() = "Simple JUCE Audio Python Bindings";

    py::class_<SimpleAudioHost>(m, "SimpleAudioHost")
        .def(py::init<>())
        .def("get_juce_version", &SimpleAudioHost::getJuceVersion,
             "Get JUCE version string")
        .def("is_audio_working", &SimpleAudioHost::isAudioWorking,
             "Check if audio system is working")
        .def("get_audio_devices", &SimpleAudioHost::getAudioDevices,
             "Get list of available audio devices")
        .def("get_supported_formats", &SimpleAudioHost::getSupportedFormats,
             "Get list of supported audio formats")
        .def("scan_for_plugins", &SimpleAudioHost::scanForPlugins,
             "Scan for available plugins")
        .def("load_plugin", &SimpleAudioHost::loadPlugin,
             "Load a plugin by name")
        .def("unload_plugin", &SimpleAudioHost::unloadPlugin,
             "Unload a plugin by ID")
        .def("get_plugin_parameters", &SimpleAudioHost::getPluginParameters,
             "Get plugin parameters")
        .def("set_parameter", &SimpleAudioHost::setParameter,
             "Set plugin parameter")
        .def("get_parameter", &SimpleAudioHost::getParameter,
             "Get plugin parameter value");
}