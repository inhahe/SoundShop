#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/functional.h>

// JUCE includes - use the modular approach
#include <juce_audio_basics/juce_audio_basics.h>
#include <juce_audio_devices/juce_audio_devices.h>
#include <juce_audio_formats/juce_audio_formats.h>
#include <juce_audio_processors/juce_audio_processors.h>
#include <juce_audio_utils/juce_audio_utils.h>
#include <juce_core/juce_core.h>
#include <juce_data_structures/juce_data_structures.h>
#include <juce_events/juce_events.h>
#include <juce_graphics/juce_graphics.h>
#include <juce_gui_basics/juce_gui_basics.h>
#include <juce_gui_extra/juce_gui_extra.h>

#include <memory>
#include <vector>
#include <string>
#include <map>
#include <functional>

namespace py = pybind11;

class AudioPluginHost : public juce::AudioProcessor::Listener,
                       public juce::ChangeListener
{
public:
    AudioPluginHost() 
        : formatManager()
        , knownPluginList()
        , pluginGraph(std::make_unique<juce::AudioProcessorGraph>())
        , deviceManager()
    {
        // Initialize audio formats
        formatManager.addDefaultFormats();
        
        // Initialize message manager for GUI operations
        if (juce::MessageManager::getInstance() == nullptr) {
            juce::MessageManager::getInstance();
        }
        
        // Setup audio device
        setupAudioDevice();
    }
    
    ~AudioPluginHost()
    {
        deviceManager.closeAudioDevice();
    }

private:
    void setupAudioDevice()
    {
        auto audioSetup = deviceManager.getAudioDeviceSetup();
        audioSetup.bufferSize = 512;
        audioSetup.sampleRate = 44100.0;
        
        juce::String error = deviceManager.initialise(2, 2, nullptr, true, {}, &audioSetup);
        if (error.isNotEmpty()) {
            // Don't throw exception in constructor, just log
            juce::Logger::writeToLog("Audio device setup warning: " + error);
        }
        else {
            deviceManager.addAudioCallback(&player);
            player.setProcessor(pluginGraph.get());
        }
    }

public:
    std::vector<std::string> scanForPlugins()
    {
        juce::FileSearchPath path;
        
        #if JUCE_WINDOWS
        path.add(juce::File("C:\\Program Files\\Common Files\\VST3"));
        path.add(juce::File("C:\\Program Files (x86)\\Common Files\\VST3"));
        path.add(juce::File("C:\\Program Files\\VstPlugins"));
        #elif JUCE_MAC
        path.add(juce::File("/Library/Audio/Plug-Ins/VST3"));
        path.add(juce::File("/Library/Audio/Plug-Ins/Components"));
        path.add(juce::File("~/Library/Audio/Plug-Ins/VST3"));
        path.add(juce::File("~/Library/Audio/Plug-Ins/Components"));
        #elif JUCE_LINUX
        path.add(juce::File("/usr/lib/vst3"));
        path.add(juce::File("/usr/local/lib/vst3"));
        path.add(juce::File("~/.vst3"));
        #endif

        juce::PluginDirectoryScanner scanner(knownPluginList, formatManager, path, true, juce::File());
        
        juce::String pluginBeingScanned;
        while (scanner.scanNextFile(false, pluginBeingScanned)) {
            // Scanning in progress
        }

        std::vector<std::string> pluginNames;
        for (int i = 0; i < knownPluginList.getNumTypes(); ++i) {
            auto* desc = knownPluginList.getType(i);
            pluginNames.push_back(desc->name.toStdString() + " (" + desc->manufacturerName.toStdString() + ")");
        }
        
        return pluginNames;
    }

    int loadPlugin(const std::string& pluginName)
    {
        for (int i = 0; i < knownPluginList.getNumTypes(); ++i) {
            auto* desc = knownPluginList.getType(i);
            std::string fullName = desc->name.toStdString() + " (" + desc->manufacturerName.toStdString() + ")";
            
            if (fullName == pluginName) {
                juce::String errorMessage;
                auto plugin = formatManager.createPluginInstance(*desc, 44100.0, 512, errorMessage);
                
                if (plugin != nullptr) {
                    plugin->addListener(this);
                    
                    auto node = pluginGraph->addNode(std::move(plugin));
                    int nodeId = node->nodeID.uid;
                    
                    pluginNodes[nodeId] = node;
                    return nodeId;
                }
                else {
                    throw std::runtime_error("Failed to create plugin: " + errorMessage.toStdString());
                }
            }
        }
        
        throw std::runtime_error("Plugin not found: " + pluginName);
    }

    void removePlugin(int nodeId)
    {
        auto it = pluginNodes.find(nodeId);
        if (it != pluginNodes.end()) {
            pluginGraph->removeNode(it->second);
            pluginNodes.erase(it);
        }
    }

    std::vector<std::map<std::string, std::string>> getPluginParameters(int nodeId)
    {
        auto it = pluginNodes.find(nodeId);
        if (it == pluginNodes.end()) {
            return {};
        }

        auto processor = it->second->getProcessor();
        std::vector<std::map<std::string, std::string>> params;
        
        for (int i = 0; i < processor->getNumParameters(); ++i) {
            std::map<std::string, std::string> param;
            param["index"] = std::to_string(i);
            param["name"] = processor->getParameterName(i).toStdString();
            param["value"] = std::to_string(processor->getParameter(i));
            param["text"] = processor->getParameterText(i).toStdString();
            params.push_back(param);
        }
        
        return params;
    }

    void setPluginParameter(int nodeId, int paramIndex, float value)
    {
        auto it = pluginNodes.find(nodeId);
        if (it != pluginNodes.end()) {
            it->second->getProcessor()->setParameter(paramIndex, value);
        }
    }

    std::map<std::string, std::vector<std::string>> getPluginPins(int nodeId)
    {
        auto it = pluginNodes.find(nodeId);
        if (it == pluginNodes.end()) {
            return {};
        }

        auto processor = it->second->getProcessor();
        std::map<std::string, std::vector<std::string>> pins;
        
        for (int i = 0; i < processor->getTotalNumInputChannels(); ++i) {
            pins["audioInputs"].push_back("Audio Input " + std::to_string(i + 1));
        }
        
        for (int i = 0; i < processor->getTotalNumOutputChannels(); ++i) {
            pins["audioOutputs"].push_back("Audio Output " + std::to_string(i + 1));
        }
        
        if (processor->acceptsMidi()) {
            pins["midiInputs"].push_back("MIDI Input");
        }
        
        if (processor->producesMidi()) {
            pins["midiOutputs"].push_back("MIDI Output");
        }
        
        return pins;
    }

    void startPlayback()
    {
        player.setProcessor(pluginGraph.get());
        isPlaying = true;
    }

    void stopPlayback()
    {
        player.setProcessor(nullptr);
        isPlaying = false;
    }

    void sendMidiMessage(int nodeId, int channel, int noteNumber, int velocity, bool isNoteOn)
    {
        auto it = pluginNodes.find(nodeId);
        if (it == pluginNodes.end()) return;

        juce::MidiMessage message;
        if (isNoteOn) {
            message = juce::MidiMessage::noteOn(channel, noteNumber, (juce::uint8)velocity);
        } else {
            message = juce::MidiMessage::noteOff(channel, noteNumber, (juce::uint8)velocity);
        }

        // Store for next audio callback
        pendingMidiMessages.push_back({nodeId, message});
    }

    std::string getJuceVersion()
    {
        return juce::String(JUCE_STRINGIFY(JUCE_MAJOR_VERSION) "." 
                           JUCE_STRINGIFY(JUCE_MINOR_VERSION) "." 
                           JUCE_STRINGIFY(JUCE_BUILDNUMBER)).toStdString();
    }

    bool testAudio()
    {
        return deviceManager.getCurrentAudioDevice() != nullptr;
    }

private:
    // AudioProcessor::Listener
    void audioProcessorParameterChanged(juce::AudioProcessor* processor, int parameterIndex, float newValue) override
    {
        // Handle parameter changes
    }

    void audioProcessorChanged(juce::AudioProcessor* processor, const ChangeDetails& details) override
    {
        // Handle processor changes
    }

    // ChangeListener
    void changeListenerCallback(juce::ChangeBroadcaster* source) override
    {
        // Handle change notifications
    }

    juce::AudioPluginFormatManager formatManager;
    juce::KnownPluginList knownPluginList;
    std::unique_ptr<juce::AudioProcessorGraph> pluginGraph;
    juce::AudioDeviceManager deviceManager;
    juce::AudioProcessorPlayer player;
    
    std::map<int, juce::AudioProcessorGraph::Node::Ptr> pluginNodes;
    std::vector<std::pair<int, juce::MidiMessage>> pendingMidiMessages;
    bool isPlaying = false;
};

// Python module definition
PYBIND11_MODULE(juce_audio, m) {
    m.doc() = "JUCE Audio Plugin Host Python Bindings";

    py::class_<AudioPluginHost>(m, "AudioPluginHost")
        .def(py::init<>())
        .def("scan_for_plugins", &AudioPluginHost::scanForPlugins,
             "Scan for available audio plugins")
        .def("load_plugin", &AudioPluginHost::loadPlugin,
             "Load a plugin by name, returns node ID")
        .def("remove_plugin", &AudioPluginHost::removePlugin,
             "Remove a plugin by node ID")
        .def("get_plugin_parameters", &AudioPluginHost::getPluginParameters,
             "Get all parameters for a plugin")
        .def("set_plugin_parameter", &AudioPluginHost::setPluginParameter,
             "Set plugin parameter by index")
        .def("get_plugin_pins", &AudioPluginHost::getPluginPins,
             "Get input/output pins for a plugin")
        .def("start_playback", &AudioPluginHost::startPlayback,
             "Start real-time audio playback")
        .def("stop_playback", &AudioPluginHost::stopPlayback,
             "Stop real-time audio playback")
        .def("send_midi_message", &AudioPluginHost::sendMidiMessage,
             "Send MIDI note on/off to a plugin")
        .def("get_juce_version", &AudioPluginHost::getJuceVersion,
             "Get JUCE version string")
        .def("test_audio", &AudioPluginHost::testAudio,
             "Test if audio system is working");
}