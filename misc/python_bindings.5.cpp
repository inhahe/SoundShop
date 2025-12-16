#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <windows.h>
#include <string>
#include <vector>
#include <map>
#include <iostream>
#include <memory>

namespace py = pybind11;

class JuceAudioPluginHost
{
public:
    JuceAudioPluginHost() : pipeHandle(INVALID_HANDLE_VALUE), guiProcessHandle(nullptr)
    {
        startGuiProcess();
        connectToGuiProcess();
        
        // Test connection
        if (sendCommand("PING") != "PONG")
        {
            throw std::runtime_error("Failed to establish connection with GUI process");
        }
    }
    
    ~JuceAudioPluginHost()
    {
        cleanup();
    }
    
    std::string getJuceVersion() const
    {
        return "JUCE 8.0.8 (Complete Plugin Host via GUI process)";
    }
    
    // Plugin Discovery and Loading
    std::vector<std::string> scanForPlugins()
    {
        std::string response = sendCommand("SCAN_PLUGINS");
        return parsePluginList(response);
    }
    
    int loadPlugin(const std::string& pluginName)
    {
        std::string response = sendCommand("LOAD_PLUGIN|" + pluginName);
        
        if (response.substr(0, 7) == "LOADED:")
        {
            return std::stoi(response.substr(7));
        }
        else
        {
            throw std::runtime_error("Failed to load plugin: " + response);
        }
    }
    
    void unloadPlugin(int pluginId)
    {
        std::string response = sendCommand("UNLOAD_PLUGIN|" + std::to_string(pluginId));
        if (response != "OK")
        {
            throw std::runtime_error("Failed to unload plugin: " + response);
        }
    }
    
    // Plugin Information
    std::vector<std::map<std::string, std::string>> getPluginParameters(int pluginId)
    {
        std::string response = sendCommand("GET_PARAMETERS|" + std::to_string(pluginId));
        return parseParameters(response);
    }
    
    std::map<std::string, int> getPluginPins(int pluginId)
    {
        std::string response = sendCommand("GET_PLUGIN_PINS|" + std::to_string(pluginId));
        return parsePins(response);
    }
    
    // Parameter Control
    void setPluginParameter(int pluginId, int paramIndex, float value)
    {
        std::string cmd = "SET_PARAMETER|" + std::to_string(pluginId) + "|" + 
                         std::to_string(paramIndex) + "|" + std::to_string(value);
        std::string response = sendCommand(cmd);
        
        if (response != "OK")
        {
            throw std::runtime_error("Failed to set parameter: " + response);
        }
    }
    
    void setPluginParameterById(int pluginId, const std::string& paramId, float value)
    {
        auto params = getPluginParameters(pluginId);
        for (const auto& param : params)
        {
            auto idIt = param.find("id");
            auto indexIt = param.find("index");
            
            if (idIt != param.end() && indexIt != param.end() && idIt->second == paramId)
            {
                setPluginParameter(pluginId, std::stoi(indexIt->second), value);
                return;
            }
        }
        throw std::runtime_error("Parameter ID not found: " + paramId);
    }
    
    // Plugin Connections (AudioProcessorGraph)
    void connectPlugins(int sourcePluginId, int sourceChannel, int destPluginId, int destChannel)
    {
        std::string cmd = "CONNECT_PLUGINS|" + std::to_string(sourcePluginId) + "|" +
                         std::to_string(sourceChannel) + "|" + std::to_string(destPluginId) + "|" +
                         std::to_string(destChannel);
        std::string response = sendCommand(cmd);
        
        if (response != "OK")
        {
            throw std::runtime_error("Failed to connect plugins: " + response);
        }
    }
    
    void disconnectPlugins(int sourcePluginId, int sourceChannel, int destPluginId, int destChannel)
    {
        std::string cmd = "DISCONNECT_PLUGINS|" + std::to_string(sourcePluginId) + "|" +
                         std::to_string(sourceChannel) + "|" + std::to_string(destPluginId) + "|" +
                         std::to_string(destChannel);
        std::string response = sendCommand(cmd);
        
        if (response != "OK")
        {
            throw std::runtime_error("Failed to disconnect plugins: " + response);
        }
    }
    
    // Real-time Audio Playback
    void startPlayback()
    {
        std::string response = sendCommand("START_PLAYBACK");
        if (response != "OK")
        {
            throw std::runtime_error("Failed to start playback: " + response);
        }
    }
    
    void stopPlayback()
    {
        std::string response = sendCommand("STOP_PLAYBACK");
        if (response != "OK")
        {
            throw std::runtime_error("Failed to stop playback: " + response);
        }
    }
    
    // MIDI Support
    void sendMidiMessage(int pluginId, int channel, int noteNumber, int velocity, bool isNoteOn)
    {
        std::string cmd = "SEND_MIDI|" + std::to_string(pluginId) + "|" + 
                         std::to_string(channel) + "|" + std::to_string(noteNumber) + "|" +
                         std::to_string(velocity) + "|" + std::to_string(isNoteOn ? 1 : 0);
        std::string response = sendCommand(cmd);
        
        if (response != "OK")
        {
            throw std::runtime_error("Failed to send MIDI message: " + response);
        }
    }
    
    void bindMidiControllerToParameter(int pluginId, int paramIndex, int midiCC)
    {
        std::string cmd = "BIND_MIDI_CC|" + std::to_string(pluginId) + "|" + 
                         std::to_string(paramIndex) + "|" + std::to_string(midiCC);
        std::string response = sendCommand(cmd);
        
        if (response != "OK")
        {
            throw std::runtime_error("Failed to bind MIDI controller: " + response);
        }
    }
    
    // Plugin GUI Support
    void showPluginEditor(int pluginId)
    {
        std::string response = sendCommand("SHOW_EDITOR|" + std::to_string(pluginId));
        if (response != "OK")
        {
            throw std::runtime_error("Failed to show editor: " + response);
        }
    }
    
    void hidePluginEditor(int pluginId)
    {
        std::string response = sendCommand("HIDE_EDITOR|" + std::to_string(pluginId));
        if (response != "OK")
        {
            throw std::runtime_error("Failed to hide editor: " + response);
        }
    }
    
    // File Rendering
    void renderToFile(const std::string& filename, double lengthInSeconds)
    {
        std::string cmd = "RENDER_TO_FILE|" + filename + "|" + std::to_string(lengthInSeconds);
        std::string response = sendCommand(cmd);
        
        if (response != "OK")
        {
            throw std::runtime_error("Failed to render to file: " + response);
        }
    }
    
    // System Status
    bool isGuiProcessRunning() const
    {
        if (!guiProcessHandle) return false;
        
        DWORD exitCode;
        if (GetExitCodeProcess(guiProcessHandle, &exitCode))
        {
            return exitCode == STILL_ACTIVE;
        }
        return false;
    }

    void startGuiProcess()
    {
        // Create unique pipe name
        DWORD processId = GetCurrentProcessId();
        pipeName = "juce_audio_pipe_" + std::to_string(processId);
        
        // Construct command line for GUI process
        std::string exePath = "juce_gui_server.exe";  // Adjust path as needed
        std::string cmdLine = exePath + " " + pipeName;
        
        STARTUPINFOA si = {};
        si.cb = sizeof(si);
        
        if (!CreateProcessA(
            nullptr,
            const_cast<char*>(cmdLine.c_str()),
            nullptr, nullptr, FALSE, 0, nullptr, nullptr, &si, &guiProcessInfo))
        {
            throw std::runtime_error("Failed to start GUI process. Make sure juce_gui_server.exe is in PATH or current directory.");
        }
        
        guiProcessHandle = guiProcessInfo.hProcess;
        
        // Give GUI process time to start
        Sleep(1500);
    }
    
    void connectToGuiProcess()
    {
        std::string fullPipeName = "\\\\.\\pipe\\" + pipeName;
        
        // Try to connect for up to 10 seconds
        for (int attempts = 0; attempts < 100; ++attempts)
        {
            pipeHandle = CreateFileA(
                fullPipeName.c_str(),
                GENERIC_READ | GENERIC_WRITE,
                0, nullptr, OPEN_EXISTING, 0, nullptr
            );
            
            if (pipeHandle != INVALID_HANDLE_VALUE)
            {
                DWORD mode = PIPE_READMODE_MESSAGE;
                SetNamedPipeHandleState(pipeHandle, &mode, nullptr, nullptr);
                return;
            }
            
            if (GetLastError() != ERROR_PIPE_BUSY)
                Sleep(100);
            else
                WaitNamedPipeA(fullPipeName.c_str(), 100);
        }
        
        throw std::runtime_error("Failed to connect to GUI process pipe");
    }
    
    std::string sendCommand(const std::string& command) const
    {
        if (pipeHandle == INVALID_HANDLE_VALUE)
            return "ERROR:No connection";
        
        // Send command
        DWORD bytesWritten;
        if (!WriteFile(pipeHandle, command.c_str(), command.length(), &bytesWritten, nullptr))
            return "ERROR:Write failed";
        
        FlushFileBuffers(pipeHandle);
        
        // Read response
        char buffer[8192];
        DWORD bytesRead;
        
        if (!ReadFile(pipeHandle, buffer, sizeof(buffer) - 1, &bytesRead, nullptr))
            return "ERROR:Read failed";
        
        buffer[bytesRead] = '\0';
        std::string response(buffer);
        
        // Remove trailing newline
        if (!response.empty() && response.back() == '\n')
            response.pop_back();
        
        return response;
    }
    
    std::vector<std::string> parsePluginList(const std::string& response) const
    {
        std::vector<std::string> plugins;
        
        if (response.substr(0, 8) == "PLUGINS:")
        {
            std::string pluginData = response.substr(8);
            
            size_t pos = 0;
            while (pos < pluginData.length())
            {
                size_t nextSemi = pluginData.find(';', pos);
                if (nextSemi == std::string::npos)
                {
                    if (pos < pluginData.length())
                        plugins.push_back(pluginData.substr(pos));
                    break;
                }
                
                plugins.push_back(pluginData.substr(pos, nextSemi - pos));
                pos = nextSemi + 1;
            }
        }
        
        return plugins;
    }
    
    std::vector<std::map<std::string, std::string>> parseParameters(const std::string& response) const
    {
        std::vector<std::map<std::string, std::string>> parameters;
        
        if (response.substr(0, 11) == "PARAMETERS:")
        {
            std::string paramData = response.substr(11);
            
            size_t pos = 0;
            while (pos < paramData.length())
            {
                size_t nextSemi = paramData.find(';', pos);
                std::string paramInfo = (nextSemi == std::string::npos) ? 
                    paramData.substr(pos) : paramData.substr(pos, nextSemi - pos);
                
                // Parse "index:name:value"
                size_t colon1 = paramInfo.find(':');
                size_t colon2 = paramInfo.find(':', colon1 + 1);
                
                if (colon1 != std::string::npos && colon2 != std::string::npos)
                {
                    std::map<std::string, std::string> param;
                    param["index"] = paramInfo.substr(0, colon1);
                    param["name"] = paramInfo.substr(colon1 + 1, colon2 - colon1 - 1);
                    param["value"] = paramInfo.substr(colon2 + 1);
                    parameters.push_back(param);
                }
                
                if (nextSemi == std::string::npos)
                    break;
                pos = nextSemi + 1;
            }
        }
        
        return parameters;
    }
    
    std::map<std::string, int> parsePins(const std::string& response) const
    {
        std::map<std::string, int> pins;
        
        if (response.substr(0, 5) == "PINS:")
        {
            std::string pinData = response.substr(5);
            
            size_t pos = 0;
            while (pos < pinData.length())
            {
                size_t nextSemi = pinData.find(';', pos);
                std::string pinInfo = (nextSemi == std::string::npos) ? 
                    pinData.substr(pos) : pinData.substr(pos, nextSemi - pos);
                
                size_t colon = pinInfo.find(':');
                if (colon != std::string::npos)
                {
                    std::string pinType = pinInfo.substr(0, colon);
                    int pinCount = std::stoi(pinInfo.substr(colon + 1));
                    pins[pinType] = pinCount;
                }
                
                if (nextSemi == std::string::npos)
                    break;
                pos = nextSemi + 1;
            }
        }
        
        return pins;
    }
    
    void cleanup()
    {
        if (pipeHandle != INVALID_HANDLE_VALUE)
        {
            CloseHandle(pipeHandle);
            pipeHandle = INVALID_HANDLE_VALUE;
        }
        
        if (guiProcessHandle)
        {
            // Gracefully terminate the GUI process
            TerminateProcess(guiProcessHandle, 0);
            WaitForSingleObject(guiProcessHandle, 5000);
            CloseHandle(guiProcessHandle);
            CloseHandle(guiProcessInfo.hThread);
            guiProcessHandle = nullptr;
        }
    }
};

PYBIND11_MODULE(juce_audio, m) {
    m.doc() = "Complete JUCE Audio Plugin Host (Inter-Process)";
    
    py::class_<JuceAudioPluginHost>(m, "AudioPluginHost")
        .def(py::init<>())
        .def("get_juce_version", &JuceAudioPluginHost::getJuceVersion)
        
        // Plugin Discovery and Loading
        .def("scan_for_plugins", &JuceAudioPluginHost::scanForPlugins,
             "Scan for available audio plugins")
        .def("load_plugin", &JuceAudioPluginHost::loadPlugin,
             "Load a plugin by name, returns plugin ID")
        .def("unload_plugin", &JuceAudioPluginHost::unloadPlugin,
             "Unload a plugin by ID")
        
        // Plugin Information
        .def("get_plugin_parameters", &JuceAudioPluginHost::getPluginParameters,
             "Get all parameters for a plugin")
        .def("get_plugin_pins", &JuceAudioPluginHost::getPluginPins,
             "Get input/output pin counts for a plugin")
        
        // Parameter Control
        .def("set_plugin_parameter", &JuceAudioPluginHost::setPluginParameter,
             "Set plugin parameter by index (0.0 to 1.0)")
        .def("set_plugin_parameter_by_id", &JuceAudioPluginHost::setPluginParameterById,
             "Set plugin parameter by string ID")
        
        // Plugin Connections
        .def("connect_plugins", &JuceAudioPluginHost::connectPlugins,
             "Connect audio between two plugins")
        .def("disconnect_plugins", &JuceAudioPluginHost::disconnectPlugins,
             "Disconnect audio between two plugins")
        
        // Audio Playback
        .def("start_playback", &JuceAudioPluginHost::startPlayback,
             "Start real-time audio playback")
        .def("stop_playback", &JuceAudioPluginHost::stopPlayback,
             "Stop real-time audio playback")
        
        // MIDI Support
        .def("send_midi_message", &JuceAudioPluginHost::sendMidiMessage,
             "Send MIDI note on/off to a plugin")
        .def("bind_midi_controller_to_parameter", &JuceAudioPluginHost::bindMidiControllerToParameter,
             "Bind MIDI CC to plugin parameter")
        
        // GUI Support
        .def("show_plugin_editor", &JuceAudioPluginHost::showPluginEditor,
             "Show plugin's graphical editor window")
        .def("hide_plugin_editor", &JuceAudioPluginHost::hidePluginEditor,
             "Hide plugin's graphical editor window")
        
        // File Rendering
        .def("render_to_file", &JuceAudioPluginHost::renderToFile,
             "Render audio to WAV file")
        
        // System Status
        .def("is_gui_process_running", &JuceAudioPluginHost::isGuiProcessRunning,
             "Check if GUI process is still running");
}