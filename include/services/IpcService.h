#pragma once
#include "Common.h"
#include "ServerState.h"

class PluginHostService;

// Owns IPC transports (named pipes / FIFOs) and the command/notification threads.
// Provides read/write primitives used by PluginHostService command handlers.
class IpcService
{
public:
    IpcService(ServerState& state, PluginHostService& host);
    ~IpcService();

    void start(const juce::String& commandLine);
    void stop();

    // Command pipe (Python <-> C++), used for request/reply.
    bool readCommandExact(void* dst, size_t n);
    bool writeCommand(const void* src, size_t n);

    // Notification pipe (C++ -> Python), used for async events.
    bool writeNotification(const void* src, size_t n);

    bool isNotificationReady() const { return notificationPipeReady_; }

private:
    void ensurePipesCreated();
    void acceptConnectionsIfNeeded();
    void commandLoop();

    ServerState& state_;
    PluginHostService& host_;

    std::atomic<bool> running_{false};
    std::thread commandThread_;

    bool notificationPipeReady_{false};

#ifdef _WIN32
    HANDLE hCommandPipe_{INVALID_HANDLE_VALUE};
    HANDLE hNotificationPipe_{INVALID_HANDLE_VALUE};
    bool connected_{false};
#else
    int commandPipeFd_{-1};
    int notificationPipeFd_{-1};
    std::string commandPipePath_;
    std::string notificationPipePath_;
#endif
};
