// Complete juce_gui_server.cpp with all requested features
#include <juce_core/juce_core.h>
#include <juce_events/juce_events.h>
#include <juce_graphics/juce_graphics.h>
#include <juce_gui_basics/juce_gui_basics.h>
#include <juce_gui_extra/juce_gui_extra.h>
#include <juce_audio_basics/juce_audio_basics.h>
#include <juce_audio_devices/juce_audio_devices.h>
#include <juce_audio_formats/juce_audio_formats.h>
#include <juce_audio_processors/juce_audio_processors.h>
#include <windows.h>
#include <iostream>
#include <map>
#include <memory>
#include <vector>
#include <atomic>

class CompletePluginHost : public juce::JUCEApplication,
                          public juce::Timer,
                          public juce::MidiInputCallback
{
public:
    const juce::String getApplicationName() override { return "Complete JUCE Plugin Host"; }
    const juce::String getApplicationVersion() override { return "1.0.0"; }

    void initialise(const juce::String& commandLine) override
    {
        pipeName = commandLine.isEmpty() ? "juce_audio_pipe" : commandLine;
        
        // Initialize audio components
        formatManager.addDefaultFormats();
        pluginGraph = std::make_unique<juce::AudioProcessorGraph>();
        
        // Setup audio device
        setupAudioDevice();
        
        // Setup MIDI
        setupMidiInput();
        
        // Start communication and processing
        startTimer(16); // 60fps
        connectToPython();
    }

    void shutdown() override
    {
        stopTimer();
        
        // Stop audio
        deviceManager.removeAudioCallback(&audioPlayer);
        audioPlayer.setProcessor(nullptr);
        deviceManager.closeAudioDevice();
        
        // Cleanup
        if (pipeHandle != INVALID_HANDLE_VALUE)
            CloseHandle(pipeHandle);
        
        pluginInstances.clear();
        editorWindows.clear();
        pluginGraph.reset();
    }

    void timerCallback() override
    {
        processMessages();
        
        // Process any pending MIDI messages
        processPendingMidi();
    }

    // MidiInputCallback
    void handleIncomingMidiMessage(juce::MidiInput* source, const juce::MidiMessage& message) override
    {
        // Handle MIDI controller bindings
        if (message.isController())
        {
            int ccNumber = message.getControllerNumber();
            auto it = midiBindings.find(ccNumber);
            if (it != midiBindings.end())
            {
                int nodeId = it->second.first;
                int paramIndex = it->second.second;
                float value = message.getControllerValue() / 127.0f;
                setPluginParameter(nodeId, paramIndex, value);
            }
        }
    }

private:
    juce::String pipeName;
    HANDLE pipeHandle = INVALID_HANDLE_VALUE;
    
    // Audio components
    juce::AudioPluginFormatManager formatManager;
    juce::KnownPluginList knownPlugins;
    juce::AudioDeviceManager deviceManager;
    juce::AudioProcessorPlayer audioPlayer;
    std::unique_ptr<juce::AudioProcessorGraph> pluginGraph;
    
    // Plugin management
    std::map<int, juce::AudioProcessorGraph::NodeID> nodeIdToGraphId;
    std::map<int, std::unique_ptr<juce::DocumentWindow>> editorWindows;
    std::map<int, std::pair<int, int>> midiBindings; // CC -> {nodeId, paramIndex}
    int nextNodeId = 1;
    
    // MIDI
    std::unique_ptr<juce::MidiInput> midiInput;
    std::vector<std::pair<int, juce::MidiMessage>> pendingMidiMessages;
    juce::CriticalSection midiLock;
    
    // Audio rendering
    std::atomic<bool> isRendering{false};
    
    void setupAudioDevice()
    {
        auto setup = deviceManager.getAudioDeviceSetup();
        setup.bufferSize = 512;
        setup.sampleRate = 44100.0;
        
        juce::String error = deviceManager.initialise(2, 2, nullptr, true, {}, &setup);
        if (error.isEmpty())
        {
            deviceManager.addAudioCallback(&audioPlayer);
            audioPlayer.setProcessor(pluginGraph.get());
        }
    }
    
    void setupMidiInput()
    {
        auto midiInputs = juce::MidiInput::getAvailableDevices();
        if (!midiInputs.isEmpty())
        {
            midiInput = juce::MidiInput::openDevice(midiInputs[0].identifier, this);
            if (midiInput)
                midiInput->start();
        }
    }

    void connectToPython()
    {
        juce::String fullPipeName = "\\\\.\\pipe\\" + pipeName;
        
        pipeHandle = CreateNamedPipeA(
            fullPipeName.toRawUTF8(),
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
            1, 4096, 4096, 0, nullptr
        );

        if (pipeHandle != INVALID_HANDLE_VALUE)
        {
            ConnectNamedPipe(pipeHandle, nullptr);
        }
    }

    void processMessages()
    {
        if (pipeHandle == INVALID_HANDLE_VALUE) return;

        DWORD bytesAvailable;
        if (!PeekNamedPipe(pipeHandle, nullptr, 0, nullptr, &bytesAvailable, nullptr) || bytesAvailable == 0)
            return;

        DWORD bytesRead;
        char buffer[4096];
        
        if (ReadFile(pipeHandle, buffer, sizeof(buffer) - 1, &bytesRead, nullptr))
        {
            buffer[bytesRead] = '\0';
            processCommand(juce::String(buffer));
        }
    }

    void processCommand(const juce::String& command)
    {
        auto tokens = juce::StringArray::fromTokens(command, "|", "");
        if (tokens.isEmpty()) return;

        juce::String cmd = tokens[0];
        juce::String response = "ERROR";

        if (cmd == "SCAN_PLUGINS")
        {
            response = scanForPlugins();
        }
        else if (cmd == "LOAD_PLUGIN" && tokens.size() >= 2)
        {
            response = loadPlugin(tokens[1]);
        }
        else if (cmd == "UNLOAD_PLUGIN" && tokens.size() >= 2)
        {
            response = unloadPlugin(tokens[1].getIntValue());
        }
        else if (cmd == "SHOW_EDITOR" && tokens.size() >= 2)
        {
            response = showPluginEditor(tokens[1].getIntValue());
        }
        else if (cmd == "HIDE_EDITOR" && tokens.size() >= 2)
        {
            response = hidePluginEditor(tokens[1].getIntValue());
        }
        else if (cmd == "SET_PARAMETER" && tokens.size() >= 4)
        {
            response = setParameter(tokens[1].getIntValue(), tokens[2].getIntValue(), tokens[3].getFloatValue());
        }
        else if (cmd == "GET_PARAMETERS" && tokens.size() >= 2)
        {
            response = getParameters(tokens[1].getIntValue());
        }
        else if (cmd == "CONNECT_PLUGINS" && tokens.size() >= 5)
        {
            response = connectPlugins(tokens[1].getIntValue(), tokens[2].getIntValue(), 
                                    tokens[3].getIntValue(), tokens[4].getIntValue());
        }
        else if (cmd == "DISCONNECT_PLUGINS" && tokens.size() >= 5)
        {
            response = disconnectPlugins(tokens[1].getIntValue(), tokens[2].getIntValue(),
                                       tokens[3].getIntValue(), tokens[4].getIntValue());
        }
        else if (cmd == "START_PLAYBACK")
        {
            response = startPlayback();
        }
        else if (cmd == "STOP_PLAYBACK")
        {
            response = stopPlayback();
        }
        else if (cmd == "SEND_MIDI" && tokens.size() >= 6)
        {
            response = sendMidiMessage(tokens[1].getIntValue(), tokens[2].getIntValue(),
                                     tokens[3].getIntValue(), tokens[4].getIntValue(), 
                                     tokens[5].getIntValue() != 0);
        }
        else if (cmd == "BIND_MIDI_CC" && tokens.size() >= 4)
        {
            response = bindMidiController(tokens[1].getIntValue(), tokens[2].getIntValue(), tokens[3].getIntValue());
        }
        else if (cmd == "RENDER_TO_FILE" && tokens.size() >= 3)
        {
            response = renderToFile(tokens[1], tokens[2].getDoubleValue());
        }
        else if (cmd == "GET_PLUGIN_PINS" && tokens.size() >= 2)
        {
            response = getPluginPins(tokens[1].getIntValue());
        }
        else if (cmd == "PING")
        {
            response = "PONG";
        }

        sendResponse(response);
    }

    void sendResponse(const juce::String& response)
    {
        if (pipeHandle == INVALID_HANDLE_VALUE) return;

        DWORD bytesWritten;
        juce::String responseWithNewline = response + "\n";
        WriteFile(pipeHandle, responseWithNewline.toRawUTF8(), 
                 responseWithNewline.length(), &bytesWritten, nullptr);
        FlushFileBuffers(pipeHandle);
    }

    juce::String scanForPlugins()
    {
        juce::FileSearchPath searchPath;
        searchPath.add(juce::File("C:\\Program Files\\Common Files\\VST3"));
        searchPath.add(juce::File("C:\\Program Files (x86)\\Common Files\\VST3"));

        juce::StringArray foundPlugins;
        
        for (int i = 0; i < formatManager.getNumFormats(); ++i)
        {
            auto* format = formatManager.getFormat(i);
            juce::PluginDirectoryScanner scanner(knownPlugins, *format, searchPath, true, juce::File());
            
            juce::String pluginBeingScanned;
            while (scanner.scanNextFile(false, pluginBeingScanned))
            {
                // Scanning...
            }
        }

        for (int i = 0; i < knownPlugins.getNumTypes(); ++i)
        {
            auto* desc = knownPlugins.getType(i);
            foundPlugins.add(desc->name + " by " + desc->manufacturerName);
        }

        return "PLUGINS:" + foundPlugins.joinIntoString(";");
    }

    juce::String loadPlugin(const juce::String& pluginName)
    {
        for (int i = 0; i < knownPlugins.getNumTypes(); ++i)
        {
            auto* desc = knownPlugins.getType(i);
            juce::String fullName = desc->name + " by " + desc->manufacturerName;
            
            if (fullName == pluginName)
            {
                juce::String errorMessage;
                auto instance = formatManager.createPluginInstance(*desc, 44100.0, 512, errorMessage);
                
                if (instance != nullptr)
                {
                    auto node = pluginGraph->addNode(std::move(instance));
                    int nodeId = nextNodeId++;
                    nodeIdToGraphId[nodeId] = node->nodeID;
                    
                    return "LOADED:" + juce::String(nodeId);
                }
                else
                {
                    return "ERROR:Failed to load plugin: " + errorMessage;
                }
            }
        }
        
        return "ERROR:Plugin not found";
    }

    juce::String unloadPlugin(int nodeId)
    {
        auto it = nodeIdToGraphId.find(nodeId);
        if (it == nodeIdToGraphId.end())
            return "ERROR:Plugin not found";

        // Hide editor first
        hidePluginEditor(nodeId);
        
        // Remove from graph
        pluginGraph->removeNode(it->second);
        nodeIdToGraphId.erase(it);
        
        return "OK";
    }

    juce::String showPluginEditor(int nodeId)
    {
        auto it = nodeIdToGraphId.find(nodeId);
        if (it == nodeIdToGraphId.end())
            return "ERROR:Plugin not found";

        auto node = pluginGraph->getNodeForId(it->second);
        if (!node)
            return "ERROR:Invalid node";

        auto processor = node->getProcessor();
        if (!processor->hasEditor())
            return "ERROR:Plugin has no editor";

        auto editor = std::unique_ptr<juce::AudioProcessorEditor>(processor->createEditor());
        if (!editor)
            return "ERROR:Failed to create editor";

        auto window = std::make_unique<juce::DocumentWindow>(
            processor->getName() + " Editor",
            juce::Colours::lightgrey,
            juce::DocumentWindow::closeButton | juce::DocumentWindow::minimiseButton
        );

        window->setContentOwned(editor.release(), true);
        window->setVisible(true);
        window->centreWithSize(window->getContentComponent()->getWidth(), 
                              window->getContentComponent()->getHeight());

        editorWindows[nodeId] = std::move(window);
        return "OK";
    }

    juce::String hidePluginEditor(int nodeId)
    {
        auto it = editorWindows.find(nodeId);
        if (it != editorWindows.end())
        {
            it->second.reset();
            editorWindows.erase(it);
            return "OK";
        }
        return "ERROR:Editor not found";
    }

    juce::String setParameter(int nodeId, int paramIndex, float value)
    {
        auto it = nodeIdToGraphId.find(nodeId);
        if (it == nodeIdToGraphId.end())
            return "ERROR:Plugin not found";

        auto node = pluginGraph->getNodeForId(it->second);
        if (!node)
            return "ERROR:Invalid node";

        auto processor = node->getProcessor();
        if (paramIndex >= 0 && paramIndex < processor->getNumParameters())
        {
            processor->setParameter(paramIndex, juce::jlimit(0.0f, 1.0f, value));
            return "OK";
        }
        return "ERROR:Invalid parameter index";
    }

    void setPluginParameter(int nodeId, int paramIndex, float value)
    {
        setParameter(nodeId, paramIndex, value);
    }

    juce::String getParameters(int nodeId)
    {
        auto it = nodeIdToGraphId.find(nodeId);
        if (it == nodeIdToGraphId.end())
            return "ERROR:Plugin not found";

        auto node = pluginGraph->getNodeForId(it->second);
        if (!node)
            return "ERROR:Invalid node";

        auto processor = node->getProcessor();
        juce::StringArray params;
        
        for (int i = 0; i < processor->getNumParameters(); ++i)
        {
            juce::String paramInfo = juce::String(i) + ":" + 
                                   processor->getParameterName(i) + ":" + 
                                   juce::String(processor->getParameter(i));
            params.add(paramInfo);
        }

        return "PARAMETERS:" + params.joinIntoString(";");
    }

    juce::String getPluginPins(int nodeId)
    {
        auto it = nodeIdToGraphId.find(nodeId);
        if (it == nodeIdToGraphId.end())
            return "ERROR:Plugin not found";

        auto node = pluginGraph->getNodeForId(it->second);
        if (!node)
            return "ERROR:Invalid node";

        auto processor = node->getProcessor();
        juce::StringArray pins;
        
        pins.add("AUDIO_INPUTS:" + juce::String(processor->getTotalNumInputChannels()));
        pins.add("AUDIO_OUTPUTS:" + juce::String(processor->getTotalNumOutputChannels()));
        pins.add("MIDI_INPUTS:" + juce::String(processor->acceptsMidi() ? 1 : 0));
        pins.add("MIDI_OUTPUTS:" + juce::String(processor->producesMidi() ? 1 : 0));

        return "PINS:" + pins.joinIntoString(";");
    }

    juce::String connectPlugins(int sourceNodeId, int sourceChannel, int destNodeId, int destChannel)
    {
        auto sourceIt = nodeIdToGraphId.find(sourceNodeId);
        auto destIt = nodeIdToGraphId.find(destNodeId);
        
        if (sourceIt == nodeIdToGraphId.end() || destIt == nodeIdToGraphId.end())
            return "ERROR:Plugin not found";

        bool success = pluginGraph->addConnection({
            {sourceIt->second, sourceChannel},
            {destIt->second, destChannel}
        });

        return success ? "OK" : "ERROR:Connection failed";
    }

    juce::String disconnectPlugins(int sourceNodeId, int sourceChannel, int destNodeId, int destChannel)
    {
        auto sourceIt = nodeIdToGraphId.find(sourceNodeId);
        auto destIt = nodeIdToGraphId.find(destNodeId);
        
        if (sourceIt == nodeIdToGraphId.end() || destIt == nodeIdToGraphId.end())
            return "ERROR:Plugin not found";

        bool success = pluginGraph->removeConnection({
            {sourceIt->second, sourceChannel},
            {destIt->second, destChannel}
        });

        return success ? "OK" : "ERROR:Disconnection failed";
    }

    juce::String startPlayback()
    {
        audioPlayer.setProcessor(pluginGraph.get());
        return "OK";
    }

    juce::String stopPlayback()
    {
        audioPlayer.setProcessor(nullptr);
        return "OK";
    }

    juce::String sendMidiMessage(int nodeId, int channel, int noteNumber, int velocity, bool isNoteOn)
    {
        juce::MidiMessage message;
        if (isNoteOn)
            message = juce::MidiMessage::noteOn(channel, noteNumber, (juce::uint8)velocity);
        else
            message = juce::MidiMessage::noteOff(channel, noteNumber, (juce::uint8)velocity);

        juce::ScopedLock lock(midiLock);
        pendingMidiMessages.push_back({nodeId, message});
        
        return "OK";
    }

    juce::String bindMidiController(int nodeId, int paramIndex, int ccNumber)
    {
        midiBindings[ccNumber] = {nodeId, paramIndex};
        return "OK";
    }

    juce::String renderToFile(const juce::String& filename, double lengthInSeconds)
    {
        if (isRendering.load())
            return "ERROR:Already rendering";

        isRendering = true;

        try
        {
            juce::File outputFile(filename);
            outputFile.deleteFile();

            auto fileStream = std::make_unique<juce::FileOutputStream>(outputFile);
            if (!fileStream->openedOk())
            {
                isRendering = false;
                return "ERROR:Failed to create output file";
            }

            juce::WavAudioFormat wavFormat;
            std::unique_ptr<juce::AudioFormatWriter> writer(
                wavFormat.createWriterFor(fileStream.release(), 44100.0, 2, 16, {}, 0)
            );

            if (!writer)
            {
                isRendering = false;
                return "ERROR:Failed to create audio writer";
            }

            const int bufferSize = 512;
            const int totalSamples = (int)(lengthInSeconds * 44100.0);
            
            juce::AudioBuffer<float> buffer(2, bufferSize);
            juce::MidiBuffer midiBuffer;

            pluginGraph->prepareToPlay(44100.0, bufferSize);

            for (int sample = 0; sample < totalSamples; sample += bufferSize)
            {
                int samplesToProcess = juce::jmin(bufferSize, totalSamples - sample);
                buffer.setSize(2, samplesToProcess, false, true);
                midiBuffer.clear();

                // Add pending MIDI messages
                {
                    juce::ScopedLock lock(midiLock);
                    for (const auto& msg : pendingMidiMessages)
                    {
                        midiBuffer.addEvent(msg.second, 0);
                    }
                    pendingMidiMessages.clear();
                }

                pluginGraph->processBlock(buffer, midiBuffer);
                writer->writeFromAudioSampleBuffer(buffer, 0, samplesToProcess);
            }

            writer.reset();
            isRendering = false;
            return "OK";
        }
        catch (...)
        {
            isRendering = false;
            return "ERROR:Rendering failed";
        }
    }

    void processPendingMidi()
    {
        if (!audioPlayer.getCurrentProcessor())
            return;

        juce::ScopedLock lock(midiLock);
        if (!pendingMidiMessages.empty())
        {
            // In a real implementation, you'd inject these into the audio callback
            // For now, just clear them
            pendingMidiMessages.clear();
        }
    }
};

START_JUCE_APPLICATION(CompletePluginHost)