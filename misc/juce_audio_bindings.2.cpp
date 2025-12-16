#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/functional.h>
#include <pybind11/numpy.h>
#include <JuceHeader.h>
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
        
        // Setup audio device
        juce::RuntimePermissions::request(juce::RuntimePermissions::recordAudio,
            [this](bool granted) {
                if (granted)
                    setupAudioDevice();
            });
        
        setupAudioDevice();
    }
    
    ~AudioPluginHost()
    {
        deviceManager.closeAudioDevice();
    }

    void setupAudioDevice()
    {
        auto audioSetup = deviceManager.getAudioDeviceSetup();
        audioSetup.bufferSize = 512;
        audioSetup.sampleRate = 44100.0;
        
        juce::String error = deviceManager.initialise(2, 2, nullptr, true, {}, &audioSetup);
        if (error.isNotEmpty()) {
            throw std::runtime_error("Failed to initialize audio device: " + error.toStdString());
        }
        
        deviceManager.addAudioCallback(&player);
        player.setProcessor(pluginGraph.get());
    }

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
            
            if (auto* paramWithID = dynamic_cast<juce::AudioProcessorParameterWithID*>(processor->getParameters()[i])) {
                param["id"] = paramWithID->paramID.toStdString();
            }
            
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

    void setPluginParameterById(int nodeId, const std::string& paramId, float value)
    {
        auto it = pluginNodes.find(nodeId);
        if (it == pluginNodes.end()) return;

        auto processor = it->second->getProcessor();
        for (int i = 0; i < processor->getNumParameters(); ++i) {
            if (auto* paramWithID = dynamic_cast<juce::AudioProcessorParameterWithID*>(processor->getParameters()[i])) {
                if (paramWithID->paramID.toStdString() == paramId) {
                    processor->setParameter(i, value);
                    break;
                }
            }
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

    void connectPlugins(int sourceNodeId, int sourceChannel, int destNodeId, int destChannel)
    {
        pluginGraph->addConnection({
            {juce::AudioProcessorGraph::NodeID(sourceNodeId), sourceChannel},
            {juce::AudioProcessorGraph::NodeID(destNodeId), destChannel}
        });
    }

    void disconnectPlugins(int sourceNodeId, int sourceChannel, int destNodeId, int destChannel)
    {
        pluginGraph->removeConnection({
            {juce::AudioProcessorGraph::NodeID(sourceNodeId), sourceChannel},
            {juce::AudioProcessorGraph::NodeID(destNodeId), destChannel}
        });
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

        // Add to MIDI buffer for next audio callback
        pendingMidiMessages.push_back({nodeId, message});
    }

    void renderToFile(const std::string& filename, double lengthInSeconds)
    {
        juce::File outputFile(filename);
        outputFile.deleteFile();

        auto fileStream = std::make_unique<juce::FileOutputStream>(outputFile);
        if (!fileStream->openedOk()) {
            throw std::runtime_error("Failed to create output file");
        }

        juce::WavAudioFormat wavFormat;
        std::unique_ptr<juce::AudioFormatWriter> writer(
            wavFormat.createWriterFor(fileStream.release(), 44100.0, 2, 16, {}, 0)
        );

        if (!writer) {
            throw std::runtime_error("Failed to create audio writer");
        }

        const int bufferSize = 512;
        const int totalSamples = (int)(lengthInSeconds * 44100.0);
        
        juce::AudioBuffer<float> buffer(2, bufferSize);
        juce::MidiBuffer midiBuffer;

        pluginGraph->prepareToPlay(44100.0, bufferSize);

        for (int sample = 0; sample < totalSamples; sample += bufferSize) {
            int samplesToProcess = juce::jmin(bufferSize, totalSamples - sample);
            buffer.setSize(2, samplesToProcess, false, true);
            midiBuffer.clear();

            pluginGraph->processBlock(buffer, midiBuffer);
            writer->writeFromAudioSampleBuffer(buffer, 0, samplesToProcess);
        }

        writer.reset();
    }

    // GUI functionality
    void showPluginEditor(int nodeId)
    {
        auto it = pluginNodes.find(nodeId);
        if (it == pluginNodes.end()) return;

        auto processor = it->second->getProcessor();
        if (processor->hasEditor()) {
            auto editor = std::unique_ptr<juce::AudioProcessorEditor>(processor->createEditor());
            if (editor) {
                // Store editor in map for later access
                pluginEditors[nodeId] = std::move(editor);
                
                // Create window for the editor
                auto window = std::make_unique<juce::DocumentWindow>(
                    processor->getName() + " Editor",
                    juce::Colours::lightgrey,
                    juce::DocumentWindow::closeButton
                );
                
                window->setContentOwned(pluginEditors[nodeId].get(), true);
                window->setVisible(true);
                window->centreWithSize(pluginEditors[nodeId]->getWidth(), 
                                     pluginEditors[nodeId]->getHeight());
                
                editorWindows[nodeId] = std::move(window);
            }
        }
    }

    void hidePluginEditor(int nodeId)
    {
        auto it = editorWindows.find(nodeId);
        if (it != editorWindows.end()) {
            it->second.reset();
            editorWindows.erase(it);
        }
        
        auto editorIt = pluginEditors.find(nodeId);
        if (editorIt != pluginEditors.end()) {
            editorIt->second.reset();
            pluginEditors.erase(editorIt);
        }
    }

    // Parameter automation binding
    void bindMidiControllerToParameter(int nodeId, int paramIndex, int midiCC)
    {
        midiBindings[midiCC] = {nodeId, paramIndex};
    }

    void processMidiForBindings(const juce::MidiMessage& message)
    {
        if (message.isController()) {
            int cc = message.getControllerNumber();
            auto it = midiBindings.find(cc);
            if (it != midiBindings.end()) {
                float value = message.getControllerValue() / 127.0f;
                setPluginParameter(it->second.first, it->second.second, value);
            }
        }
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
    std::map<int, std::unique_ptr<juce::AudioProcessorEditor>> pluginEditors;
    std::map<int, std::unique_ptr<juce::DocumentWindow>> editorWindows;
    std::map<int, std::pair<int, int>> midiBindings; // CC -> {nodeId, paramIndex}
    
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
        .def("set_plugin_parameter_by_id", &AudioPluginHost::setPluginParameterById,
             "Set plugin parameter by ID string")
        .def("get_plugin_pins", &AudioPluginHost::getPluginPins,
             "Get input/output pins for a plugin")
        .def("connect_plugins", &AudioPluginHost::connectPlugins,
             "Connect audio between two plugins")
        .def("disconnect_plugins", &AudioPluginHost::disconnectPlugins,
             "Disconnect audio between two plugins")
        .def("start_playback", &AudioPluginHost::startPlayback,
             "Start real-time audio playback")
        .def("stop_playback", &AudioPluginHost::stopPlayback,
             "Stop real-time audio playback")
        .def("send_midi_message", &AudioPluginHost::sendMidiMessage,
             "Send MIDI note on/off to a plugin")
        .def("render_to_file", &AudioPluginHost::renderToFile,
             "Render audio to WAV file")
        .def("show_plugin_editor", &AudioPluginHost::showPluginEditor,
             "Show plugin's graphical editor")
        .def("hide_plugin_editor", &AudioPluginHost::hidePluginEditor,
             "Hide plugin's graphical editor")
        .def("bind_midi_controller_to_parameter", &AudioPluginHost::bindMidiControllerToParameter,
             "Bind MIDI CC to plugin parameter");

    // Initialize JUCE
    juce::initialiseJuce_GUI();
    
    // Cleanup on module destruction
    m.add_object("_cleanup", py::capsule([]() {
        juce::shutdownJuce_GUI();
    }));
}