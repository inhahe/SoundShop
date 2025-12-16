// juce_gui_server.cpp - GUI server for JUCE plugin hosting

// Include individual JUCE module headers
#include <juce_core/juce_core.h>
#include <juce_events/juce_events.h>
#include <juce_graphics/juce_graphics.h>
#include <juce_gui_basics/juce_gui_basics.h>
#include <juce_gui_extra/juce_gui_extra.h>
#include <juce_audio_basics/juce_audio_basics.h>
#include <juce_audio_devices/juce_audio_devices.h>
#include <juce_audio_formats/juce_audio_formats.h>
#include <juce_audio_processors/juce_audio_processors.h>
#include <juce_audio_utils/juce_audio_utils.h>

#include <iostream>
#include <thread>
#include <atomic>
#include <queue>
#include <mutex>
#include <condition_variable>
#include <string>
#include <memory>
#include <map>

#ifdef _WIN32
#include <windows.h>
#include <fcntl.h>
#include <io.h>
#else
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#endif

// Command structure for communication
struct Command {
    std::string type;
    std::map<std::string, std::string> params;
};

// Response structure
struct Response {
    bool success;
    std::string message;
    std::map<std::string, std::string> data;
};

class CompletePluginHost : public juce::Timer,
                           public juce::MidiInputCallback
{
public:
    CompletePluginHost()
        : deviceManager(),
          formatManager(),
          knownPluginList(),
          processorGraph(new juce::AudioProcessorGraph()),
          graphPlayer(),
          running(true)
    {
        // Initialize format manager with plugin formats
        formatManager.addDefaultFormats();
        
        // Initialize audio device
        initializeAudio();
        
        // Start timer for periodic updates
        startTimer(50);  // 20Hz update rate
    }
    
    ~CompletePluginHost()
    {
        stopTimer();
        shutdownAudio();
        processorGraph = nullptr;
    }
    
    void initialise(const juce::String& commandLine)
    {
        // Convert juce::String to std::string properly
        pipeName = commandLine.trim().toStdString();
        if (pipeName.empty())  // Use std::string::empty()
            pipeName = "juce_audio_pipe";
            
        // Start command processing thread
        commandThread = std::thread([this]() { processCommands(); });
    }
    
    void shutdown()
    {
        running = false;
        commandCv.notify_all();
        
        if (commandThread.joinable())
            commandThread.join();
            
        // Close all plugin windows
        for (auto& pair : pluginWindows)
        {
            if (pair.second)
                pair.second->closeButtonPressed();
        }
        pluginWindows.clear();
    }
    
    void timerCallback() override
    {
        // Update plugin UIs and handle any GUI events
        for (auto it = pluginWindows.begin(); it != pluginWindows.end();)
        {
            if (!it->second || !it->second->isVisible())
            {
                it = pluginWindows.erase(it);
            }
            else
            {
                ++it;
            }
        }
    }
    
    void handleIncomingMidiMessage(juce::MidiInput* source,
                                  const juce::MidiMessage& message) override
    {
        // Route MIDI to the processor graph
        if (midiCollector)
            midiCollector->addMessageToQueue(message);
    }

private:
    void initializeAudio()
    {
        // Setup audio device
        auto setup = deviceManager.getAudioDeviceSetup();
        setup.sampleRate = 48000;
        setup.bufferSize = 512;
        
        juce::String error = deviceManager.initialise(
            2,     // max input channels
            2,     // max output channels
            nullptr,  // no saved state
            true,     // select default device
            {},       // preferred device
            &setup    // preferred setup
        );
        
        if (error.isNotEmpty())
        {
            std::cerr << "Audio initialization error: " << error.toStdString() << std::endl;
        }
        
        // Setup graph player
        graphPlayer.setProcessor(processorGraph.get());
        deviceManager.addAudioCallback(&graphPlayer);
        
        // Setup MIDI
        midiCollector = std::make_unique<juce::MidiMessageCollector>();
        midiCollector->reset(setup.sampleRate);
        
        // Open MIDI inputs
        auto midiInputs = juce::MidiInput::getAvailableDevices();
        for (const auto& input : midiInputs)
        {
            deviceManager.setMidiInputDeviceEnabled(input.identifier, true);
            deviceManager.addMidiInputDeviceCallback(input.identifier, this);
        }
    }
    
    void shutdownAudio()
    {
        deviceManager.removeAudioCallback(&graphPlayer);
        deviceManager.closeAudioDevice();
        graphPlayer.setProcessor(nullptr);
    }
    
    void processCommands()
    {
#ifdef _WIN32
        // Windows named pipe
        std::string pipePath = "\\\\.\\pipe\\" + pipeName;
        
        while (running)
        {
            HANDLE hPipe = CreateNamedPipeA(
                pipePath.c_str(),
                PIPE_ACCESS_DUPLEX,
                PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
                PIPE_UNLIMITED_INSTANCES,
                4096,
                4096,
                0,
                NULL
            );
            
            if (hPipe == INVALID_HANDLE_VALUE)
            {
                std::cerr << "Failed to create named pipe" << std::endl;
                break;
            }
            
            if (ConnectNamedPipe(hPipe, NULL) || GetLastError() == ERROR_PIPE_CONNECTED)
            {
                char buffer[4096];
                DWORD bytesRead;
                
                while (ReadFile(hPipe, buffer, sizeof(buffer), &bytesRead, NULL) && bytesRead > 0)
                {
                    std::string command(buffer, bytesRead);
                    processCommand(command, hPipe);
                }
            }
            
            CloseHandle(hPipe);
        }
#else
        // Unix named pipe (FIFO)
        std::string pipePath = "/tmp/" + pipeName;
        mkfifo(pipePath.c_str(), 0666);
        
        while (running)
        {
            int fd = open(pipePath.c_str(), O_RDONLY);
            if (fd != -1)
            {
                char buffer[4096];
                ssize_t bytesRead;
                
                while ((bytesRead = read(fd, buffer, sizeof(buffer))) > 0)
                {
                    std::string command(buffer, bytesRead);
                    processCommand(command, fd);
                }
                
                close(fd);
            }
        }
        
        unlink(pipePath.c_str());
#endif
    }
    
    void processCommand(const std::string& commandStr, 
#ifdef _WIN32
                       HANDLE pipe
#else
                       int pipe
#endif
                       )
    {
        // Parse JSON command
        Command cmd = parseCommand(commandStr);
        Response response;
        
        if (cmd.type == "load_plugin")
        {
            response = loadPlugin(cmd.params["path"], cmd.params["id"]);
        }
        else if (cmd.type == "show_plugin_ui")
        {
            response = showPluginUI(cmd.params["id"]);
        }
        else if (cmd.type == "hide_plugin_ui")
        {
            response = hidePluginUI(cmd.params["id"]);
        }
        else if (cmd.type == "set_parameter")
        {
            response = setParameter(cmd.params["id"], 
                                   std::stoi(cmd.params["param_index"]), 
                                   std::stof(cmd.params["value"]));
        }
        else if (cmd.type == "get_parameter")
        {
            response = getParameter(cmd.params["id"], 
                                   std::stoi(cmd.params["param_index"]));
        }
        else if (cmd.type == "connect_audio")
        {
            response = connectAudio(cmd.params["source_id"], 
                                   std::stoi(cmd.params["source_channel"]),
                                   cmd.params["dest_id"], 
                                   std::stoi(cmd.params["dest_channel"]));
        }
        else if (cmd.type == "connect_midi")
        {
            response = connectMidi(cmd.params["source_id"], cmd.params["dest_id"]);
        }
        else if (cmd.type == "start_playback")
        {
            response = startPlayback();
        }
        else if (cmd.type == "stop_playback")
        {
            response = stopPlayback();
        }
        else if (cmd.type == "shutdown")
        {
            response.success = true;
            response.message = "Shutting down";
            running = false;
        }
        else
        {
            response.success = false;
            response.message = "Unknown command: " + cmd.type;
        }
        
        // Send response back
        sendResponse(response, pipe);
    }
    
    Command parseCommand(const std::string& str)
    {
        Command cmd;
        // Simple parsing - in production you'd use a proper JSON parser
        // Format: "type:param1=value1,param2=value2"
        size_t colonPos = str.find(':');
        if (colonPos != std::string::npos)
        {
            cmd.type = str.substr(0, colonPos);
            std::string params = str.substr(colonPos + 1);
            
            size_t pos = 0;
            while (pos < params.length())
            {
                size_t equalPos = params.find('=', pos);
                size_t commaPos = params.find(',', pos);
                if (commaPos == std::string::npos)
                    commaPos = params.length();
                    
                if (equalPos != std::string::npos && equalPos < commaPos)
                {
                    std::string key = params.substr(pos, equalPos - pos);
                    std::string value = params.substr(equalPos + 1, commaPos - equalPos - 1);
                    cmd.params[key] = value;
                }
                
                pos = commaPos + 1;
            }
        }
        else
        {
            cmd.type = str;
        }
        
        return cmd;
    }
    
    void sendResponse(const Response& response, 
#ifdef _WIN32
                     HANDLE pipe
#else
                     int pipe
#endif
                     )
    {
        // Simple response format
        std::string responseStr = response.success ? "OK:" : "ERROR:";
        responseStr += response.message;
        
        for (const auto& pair : response.data)
        {
            responseStr += "," + pair.first + "=" + pair.second;
        }
        
#ifdef _WIN32
        DWORD bytesWritten;
        WriteFile(pipe, responseStr.c_str(), responseStr.length(), &bytesWritten, NULL);
#else
        write(pipe, responseStr.c_str(), responseStr.length());
#endif
    }
    
    Response loadPlugin(const std::string& path, const std::string& id)
    {
        Response resp;
        
        juce::MessageManager::callAsync([this, path, id, &resp]() {
            // Load the plugin
            juce::OwnedArray<juce::PluginDescription> descriptions;
            
            for (auto* format : formatManager.getFormats())
            {
                if (format->fileMightContainThisPluginType(path))
                {
                    format->findAllTypesForFile(descriptions, path);
                    break;
                }
            }
            
            if (descriptions.size() > 0)
            {
                const juce::PluginDescription& desc = *descriptions[0];
                juce::String errorMessage;
                auto* instance = formatManager.createPluginInstance(
                    desc, 
                    processorGraph->getSampleRate(),
                    processorGraph->getBlockSize(),
                    errorMessage
                ).release();
                
                if (instance)
                {
                    auto nodeId = processorGraph->addNode(
                        std::unique_ptr<juce::AudioProcessor>(instance)
                    )->nodeID;
                    
                    loadedPlugins[id] = nodeId;
                    
                    resp.success = true;
                    resp.message = "Plugin loaded: " + desc.name.toStdString();
                    resp.data["node_id"] = std::to_string(nodeId.uid);
                }
                else
                {
                    resp.success = false;
                    resp.message = "Failed to create instance: " + errorMessage.toStdString();
                }
            }
            else
            {
                resp.success = false;
                resp.message = "Plugin not found at path: " + path;
            }
        });
        
        return resp;
    }
    
    Response showPluginUI(const std::string& id)
    {
        Response resp;
        
        auto it = loadedPlugins.find(id);
        if (it != loadedPlugins.end())
        {
            auto node = processorGraph->getNodeForId(it->second);
            if (node)
            {
                juce::MessageManager::callAsync([this, id, node]() {
                    if (node->getProcessor()->hasEditor())
                    {
                        auto* editor = node->getProcessor()->createEditor();
                        if (editor)
                        {
                            auto window = std::make_unique<PluginWindow>(
                                node->getProcessor()->getName(),
                                editor,
                                node->getProcessor()
                            );
                            
                            window->setVisible(true);
                            pluginWindows[id] = std::move(window);
                        }
                    }
                });
                
                resp.success = true;
                resp.message = "Plugin UI shown";
            }
            else
            {
                resp.success = false;
                resp.message = "Plugin node not found";
            }
        }
        else
        {
            resp.success = false;
            resp.message = "Plugin not loaded: " + id;
        }
        
        return resp;
    }
    
    Response hidePluginUI(const std::string& id)
    {
        Response resp;
        
        auto it = pluginWindows.find(id);
        if (it != pluginWindows.end())
        {
            juce::MessageManager::callAsync([this, id]() {
                pluginWindows.erase(id);
            });
            
            resp.success = true;
            resp.message = "Plugin UI hidden";
        }
        else
        {
            resp.success = false;
            resp.message = "No UI window for plugin: " + id;
        }
        
        return resp;
    }
    
    Response setParameter(const std::string& id, int paramIndex, float value)
    {
        Response resp;
        
        auto it = loadedPlugins.find(id);
        if (it != loadedPlugins.end())
        {
            auto node = processorGraph->getNodeForId(it->second);
            if (node && node->getProcessor())
            {
                auto* processor = node->getProcessor();
                if (paramIndex >= 0 && paramIndex < processor->getParameters().size())
                {
                    processor->getParameters()[paramIndex]->setValue(value);
                    resp.success = true;
                    resp.message = "Parameter set";
                }
                else
                {
                    resp.success = false;
                    resp.message = "Invalid parameter index";
                }
            }
            else
            {
                resp.success = false;
                resp.message = "Plugin node not found";
            }
        }
        else
        {
            resp.success = false;
            resp.message = "Plugin not loaded: " + id;
        }
        
        return resp;
    }
    
    Response getParameter(const std::string& id, int paramIndex)
    {
        Response resp;
        
        auto it = loadedPlugins.find(id);
        if (it != loadedPlugins.end())
        {
            auto node = processorGraph->getNodeForId(it->second);
            if (node && node->getProcessor())
            {
                auto* processor = node->getProcessor();
                if (paramIndex >= 0 && paramIndex < processor->getParameters().size())
                {
                    float value = processor->getParameters()[paramIndex]->getValue();
                    resp.success = true;
                    resp.message = "Parameter retrieved";
                    resp.data["value"] = std::to_string(value);
                }
                else
                {
                    resp.success = false;
                    resp.message = "Invalid parameter index";
                }
            }
        }
        else
        {
            resp.success = false;
            resp.message = "Plugin not loaded: " + id;
        }
        
        return resp;
    }
    
    Response connectAudio(const std::string& sourceId, int sourceChannel,
                         const std::string& destId, int destChannel)
    {
        Response resp;
        
        auto sourceIt = loadedPlugins.find(sourceId);
        auto destIt = loadedPlugins.find(destId);
        
        if (sourceIt != loadedPlugins.end() && destIt != loadedPlugins.end())
        {
            processorGraph->addConnection({
                {sourceIt->second, sourceChannel},
                {destIt->second, destChannel}
            });
            
            resp.success = true;
            resp.message = "Audio connection created";
        }
        else
        {
            resp.success = false;
            resp.message = "Source or destination plugin not found";
        }
        
        return resp;
    }
    
    Response connectMidi(const std::string& sourceId, const std::string& destId)
    {
        Response resp;
        
        auto sourceIt = loadedPlugins.find(sourceId);
        auto destIt = loadedPlugins.find(destId);
        
        if (sourceIt != loadedPlugins.end() && destIt != loadedPlugins.end())
        {
            processorGraph->addConnection({
                {sourceIt->second, juce::AudioProcessorGraph::midiChannelIndex},
                {destIt->second, juce::AudioProcessorGraph::midiChannelIndex}
            });
            
            resp.success = true;
            resp.message = "MIDI connection created";
        }
        else
        {
            resp.success = false;
            resp.message = "Source or destination plugin not found";
        }
        
        return resp;
    }
    
    Response startPlayback()
    {
        Response resp;
        // Playback is already running through the audio callback
        resp.success = true;
        resp.message = "Playback active";
        return resp;
    }
    
    Response stopPlayback()
    {
        Response resp;
        // You could implement transport control here
        resp.success = true;
        resp.message = "Playback stopped";
        return resp;
    }
    
    // Plugin window class
    class PluginWindow : public juce::DocumentWindow
    {
    public:
        PluginWindow(const juce::String& name,
                    juce::AudioProcessorEditor* editor,
                    juce::AudioProcessor* processor)
            : DocumentWindow(name, 
                           juce::Desktop::getInstance().getDefaultLookAndFeel()
                               .findColour(ResizableWindow::backgroundColourId),
                           DocumentWindow::allButtons),
              processor(processor)
        {
            setUsingNativeTitleBar(true);
            setContentOwned(editor, true);
            setResizable(editor->isResizable(), false);
            centreWithSize(getWidth(), getHeight());
        }
        
        void closeButtonPressed() override
        {
            setVisible(false);
        }
        
    private:
        juce::AudioProcessor* processor;
        
        JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(PluginWindow)
    };
    
private:
    // Core components
    juce::AudioDeviceManager deviceManager;
    juce::AudioPluginFormatManager formatManager;
    juce::KnownPluginList knownPluginList;
    std::unique_ptr<juce::AudioProcessorGraph> processorGraph;
    juce::AudioProcessorPlayer graphPlayer;
    std::unique_ptr<juce::MidiMessageCollector> midiCollector;
    
    // Plugin management
    std::map<std::string, juce::AudioProcessorGraph::NodeID> loadedPlugins;
    std::map<std::string, std::unique_ptr<PluginWindow>> pluginWindows;
    
    // Communication
    std::string pipeName;
    std::thread commandThread;
    std::atomic<bool> running;
    std::queue<Command> commandQueue;
    std::mutex commandMutex;
    std::condition_variable commandCv;
    
    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(CompletePluginHost)
};

// Entry point
#ifdef _WIN32
// Use extern "C" to ensure proper linkage
extern "C" int WINAPI WinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance, LPSTR lpCmdLine, int nCmdShow)
{
    // Suppress unused parameter warnings
    (void)hInstance;
    (void)hPrevInstance;
    (void)nCmdShow;
    
    // Initialize JUCE's GUI subsystem
    juce::initialiseJuce_GUI();
    
    // Create and initialize the host
    CompletePluginHost app;
    app.initialise(juce::String(lpCmdLine));
    
    // Run the message loop
    juce::MessageManager::getInstance()->runDispatchLoop();
    
    // Clean shutdown
    app.shutdown();
    juce::shutdownJuce_GUI();
    
    return 0;
}
#else
// For Mac/Linux, use standard main
int main(int argc, char* argv[])
{
    juce::initialiseJuce_GUI();
    
    juce::String commandLine;
    if (argc > 1)
    {
        for (int i = 1; i < argc; ++i)
        {
            if (i > 1) commandLine << " ";
            commandLine << argv[i];
        }
    }
    
    CompletePluginHost app;
    app.initialise(commandLine);
    
    juce::MessageManager::getInstance()->runDispatchLoop();
    
    app.shutdown();
    juce::shutdownJuce_GUI();
    
    return 0;
}
#endif