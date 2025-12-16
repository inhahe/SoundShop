// juce_gui_server.cpp - Standalone JUCE GUI process

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

class PluginGUIServer : public juce::JUCEApplication,
                       public juce::Timer
{
public:
    const juce::String getApplicationName() override { return "JUCE Plugin GUI Server"; }
    const juce::String getApplicationVersion() override { return "1.0.0"; }

    void initialise(const juce::String& commandLine) override
    {
        // Parse command line for named pipe name
        pipeName = commandLine.isEmpty() ? "juce_audio_pipe" : commandLine;
        
        // Initialize audio plugin formats
        formatManager.addDefaultFormats();
        
        // Start communication with Python process
        startTimer(16); // 60fps for responsive GUI
        connectToPython();
    }

    void shutdown() override
    {
        stopTimer();
        if (pipeHandle != INVALID_HANDLE_VALUE)
        {
            CloseHandle(pipeHandle);
        }
        
        // Close all plugin editors
        pluginInstances.clear();
        editorWindows.clear();
    }

    void timerCallback() override
    {
        processMessages();
    }

private:
    juce::String pipeName;
    HANDLE pipeHandle = INVALID_HANDLE_VALUE;
    juce::AudioPluginFormatManager formatManager;
    juce::KnownPluginList knownPlugins;
    
    std::map<int, std::unique_ptr<juce::AudioProcessor>> pluginInstances;
    std::map<int, std::unique_ptr<juce::DocumentWindow>> editorWindows;
    int nextPluginId = 1;

    void connectToPython()
    {
        juce::String fullPipeName = "\\\\.\\pipe\\" + pipeName;
        
        pipeHandle = CreateNamedPipeA(
            fullPipeName.toRawUTF8(),
            PIPE_ACCESS_DUPLEX,
            PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT,
            1, 1024, 1024, 0, nullptr
        );

        if (pipeHandle == INVALID_HANDLE_VALUE)
        {
            std::cerr << "Failed to create named pipe" << std::endl;
            quit();
            return;
        }

        // Wait for Python to connect
        if (!ConnectNamedPipe(pipeHandle, nullptr))
        {
            std::cerr << "Failed to connect to Python process" << std::endl;
            CloseHandle(pipeHandle);
            pipeHandle = INVALID_HANDLE_VALUE;
        }
    }

    void processMessages()
    {
        if (pipeHandle == INVALID_HANDLE_VALUE) return;

        DWORD bytesRead;
        char buffer[1024];
        
        DWORD bytesAvailable;
        if (!PeekNamedPipe(pipeHandle, nullptr, 0, nullptr, &bytesAvailable, nullptr) || bytesAvailable == 0)
            return;

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
        else if (cmd == "PING")
        {
            response = "PONG";
        }

        // Send response back to Python
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
                    int pluginId = nextPluginId++;
                    pluginInstances[pluginId] = std::move(instance);
                    return "LOADED:" + juce::String(pluginId);
                }
                else
                {
                    return "ERROR:Failed to load plugin: " + errorMessage;
                }
            }
        }
        
        return "ERROR:Plugin not found";
    }

    juce::String showPluginEditor(int pluginId)
    {
        auto it = pluginInstances.find(pluginId);
        if (it == pluginInstances.end())
            return "ERROR:Plugin not found";

        auto& plugin = it->second;
        if (!plugin->hasEditor())
            return "ERROR:Plugin has no editor";

        auto editor = std::unique_ptr<juce::AudioProcessorEditor>(plugin->createEditor());
        if (!editor)
            return "ERROR:Failed to create editor";

        auto window = std::make_unique<juce::DocumentWindow>(
            plugin->getName() + " Editor",
            juce::Colours::lightgrey,
            juce::DocumentWindow::closeButton | juce::DocumentWindow::minimiseButton
        );

        window->setContentOwned(editor.release(), true);
        window->setVisible(true);
        window->centreWithSize(window->getContentComponent()->getWidth(), 
                              window->getContentComponent()->getHeight());

        editorWindows[pluginId] = std::move(window);
        return "OK";
    }

    juce::String hidePluginEditor(int pluginId)
    {
        auto it = editorWindows.find(pluginId);
        if (it != editorWindows.end())
        {
            it->second.reset();
            editorWindows.erase(it);
            return "OK";
        }
        return "ERROR:Editor not found";
    }

    juce::String setParameter(int pluginId, int paramIndex, float value)
    {
        auto it = pluginInstances.find(pluginId);
        if (it == pluginInstances.end())
            return "ERROR:Plugin not found";

        auto& plugin = it->second;
        if (paramIndex >= 0 && paramIndex < plugin->getNumParameters())
        {
            plugin->setParameter(paramIndex, juce::jlimit(0.0f, 1.0f, value));
            return "OK";
        }
        return "ERROR:Invalid parameter index";
    }

    juce::String getParameters(int pluginId)
    {
        auto it = pluginInstances.find(pluginId);
        if (it == pluginInstances.end())
            return "ERROR:Plugin not found";

        auto& plugin = it->second;
        juce::StringArray params;
        
        for (int i = 0; i < plugin->getNumParameters(); ++i)
        {
            juce::String paramInfo = juce::String(i) + ":" + 
                                   plugin->getParameterName(i) + ":" + 
                                   juce::String(plugin->getParameter(i));
            params.add(paramInfo);
        }

        return "PARAMETERS:" + params.joinIntoString(";");
    }
};

START_JUCE_APPLICATION(PluginGUIServer)