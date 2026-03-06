#pragma once
#include "Common.h"
#include "ServerState.h"
#include "host/PluginHostService.h"
#include "services/IpcService.h"
#include "services/AudioService.h"
#include "services/UiService.h"

// Composition root: owns services and wires dependencies.
// Goal: PluginHostService = graph + command handlers only;
// Ipc/Audio/UI are owned and managed by their respective services.
class ServerServices
{
public:
    explicit ServerServices(ServerState& state);

    void start(const juce::String& commandLine);
    void stop();

    ServerState& state() { return state_; }
    PluginHostService& host() { return host_; }
    IpcService& ipc() { return ipc_; }
    AudioService& audio() { return audio_; }
    UiService& ui() { return ui_; }

private:
    ServerState& state_;
    PluginHostService host_;
    IpcService ipc_;
    AudioService audio_;
    UiService ui_;
};
