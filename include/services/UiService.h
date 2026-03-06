#pragma once
#include "Common.h"
#include "ServerState.h"

class PluginHostService;
class VirtualKeyboardWindow;

// UI service boundary. Owns top-level windows and UI lifetime.
class UiService
{
public:
    UiService(ServerState& state, PluginHostService& host)
        : state_(state), host_(host) {}

    void start();
    void stop();

    void showVirtualKeyboard();
    void hideVirtualKeyboard();

private:
    ServerState& state_;
    PluginHostService& host_;

    std::unique_ptr<VirtualKeyboardWindow> virtualKeyboardWindow_;
};
