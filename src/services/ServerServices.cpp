#include "services/ServerServices.h"

ServerServices::ServerServices(ServerState& state)
    : state_(state)
    , host_(state_)
    , ipc_(state_, host_)
    , audio_(state_, host_)
    , ui_(state_, host_)
{
    // Wire back-pointers so host can delegate pipe/audio/ui work when needed.
    host_.setIpc(&ipc_);
    host_.setAudio(&audio_);
    host_.setUi(&ui_);
}

void ServerServices::start(const juce::String& commandLine)
{
    // Host still owns plugin graph and command handlers.
    // Services own their subsystems.
    ui_.start();
    audio_.start();
    ipc_.start(commandLine);
}

void ServerServices::stop()
{
    // Stop in reverse order.
    ipc_.stop();
    audio_.stop();
    ui_.stop();
    host_.shutdown(); // retains existing cleanup for plugin windows etc.
}
