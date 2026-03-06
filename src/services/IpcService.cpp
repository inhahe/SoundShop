#include "services/IpcService.h"
#include "host/PluginHostService.h"

IpcService::IpcService(ServerState& state, PluginHostService& host)
    : state_(state), host_(host)
{
}

IpcService::~IpcService()
{
    stop();
}

void IpcService::start(const juce::String& /*commandLine*/)
{
    if (running_.exchange(true))
        return;

    ensurePipesCreated();

    commandThread_ = std::thread([this]() { commandLoop(); });
}

void IpcService::stop()
{
    if (!running_.exchange(false))
        return;

    if (commandThread_.joinable())
        commandThread_.join();

#ifdef _WIN32
    if (hCommandPipe_ != INVALID_HANDLE_VALUE) { CloseHandle(hCommandPipe_); hCommandPipe_ = INVALID_HANDLE_VALUE; }
    if (hNotificationPipe_ != INVALID_HANDLE_VALUE) { CloseHandle(hNotificationPipe_); hNotificationPipe_ = INVALID_HANDLE_VALUE; }
#else
    if (commandPipeFd_ >= 0) { close(commandPipeFd_); commandPipeFd_ = -1; }
    if (notificationPipeFd_ >= 0) { close(notificationPipeFd_); notificationPipeFd_ = -1; }
    if (!commandPipePath_.empty()) unlink(commandPipePath_.c_str());
    if (!notificationPipePath_.empty()) unlink(notificationPipePath_.c_str());
#endif
    notificationPipeReady_ = false;
}

void IpcService::ensurePipesCreated()
{
#ifdef _WIN32
    std::string commandPipeName = "\\\\.\\pipe\\" + state_.config.pipeName + "_commands";
    std::string notificationPipeName = "\\\\.\\pipe\\" + state_.config.pipeName + "_notifications";

    hCommandPipe_ = CreateNamedPipeA(
        commandPipeName.c_str(),
        PIPE_ACCESS_DUPLEX,
        PIPE_TYPE_BYTE | PIPE_WAIT,
        PIPE_UNLIMITED_INSTANCES,
        4096, 4096, 0, NULL);

    if (hCommandPipe_ == INVALID_HANDLE_VALUE)
        throw std::runtime_error("Failed to create named command pipe");

    hNotificationPipe_ = CreateNamedPipeA(
        notificationPipeName.c_str(),
        PIPE_ACCESS_OUTBOUND,
        PIPE_TYPE_BYTE | PIPE_WAIT,
        PIPE_UNLIMITED_INSTANCES,
        4096, 4096, 0, NULL);

    if (hNotificationPipe_ == INVALID_HANDLE_VALUE)
        throw std::runtime_error("Failed to create named notification pipe");

    notificationPipeReady_ = true;
#else
    commandPipePath_ = "/tmp/" + state_.config.pipeName + "_commands";
    notificationPipePath_ = "/tmp/" + state_.config.pipeName + "_notifications";

    if (mkfifo(commandPipePath_.c_str(), 0666) == -1 && errno != EEXIST)
        throw std::runtime_error("Failed to create command FIFO: " + std::string(strerror(errno)));
    if (mkfifo(notificationPipePath_.c_str(), 0666) == -1 && errno != EEXIST)
        throw std::runtime_error("Failed to create notification FIFO: " + std::string(strerror(errno)));

    commandPipeFd_ = open(commandPipePath_.c_str(), O_RDWR);
    if (commandPipeFd_ < 0)
        throw std::runtime_error("Failed to open command FIFO: " + std::string(strerror(errno)));

    notificationPipeFd_ = open(notificationPipePath_.c_str(), O_WRONLY);
    if (notificationPipeFd_ < 0)
        throw std::runtime_error("Failed to open notification FIFO: " + std::string(strerror(errno)));

    notificationPipeReady_ = true;
#endif
}

void IpcService::acceptConnectionsIfNeeded()
{
#ifdef _WIN32
    if (connected_) return;

    BOOL ok = ConnectNamedPipe(hCommandPipe_, NULL) ? TRUE : (GetLastError() == ERROR_PIPE_CONNECTED);
    if (!ok) throw std::runtime_error("ConnectNamedPipe failed for command pipe");

    ok = ConnectNamedPipe(hNotificationPipe_, NULL) ? TRUE : (GetLastError() == ERROR_PIPE_CONNECTED);
    if (!ok) throw std::runtime_error("ConnectNamedPipe failed for notification pipe");

    connected_ = true;
#endif
}

bool IpcService::readCommandExact(void* dst, size_t n)
{
    if (n == 0) return true;
#ifdef _WIN32
    DWORD total = 0;
    while (total < n)
    {
        DWORD got = 0;
        if (!ReadFile(hCommandPipe_, (char*)dst + total, (DWORD)(n - total), &got, NULL))
            return false;
        if (got == 0) return false;
        total += got;
    }
    return true;
#else
    size_t total = 0;
    while (total < n)
    {
        ssize_t got = read(commandPipeFd_, (char*)dst + total, n - total);
        if (got <= 0) return false;
        total += (size_t)got;
    }
    return true;
#endif
}

bool IpcService::writeCommand(const void* src, size_t n)
{
#ifdef _WIN32
    DWORD wrote = 0;
    return WriteFile(hCommandPipe_, src, (DWORD)n, &wrote, NULL) && wrote == n;
#else
    return write(commandPipeFd_, src, n) == (ssize_t)n;
#endif
}

bool IpcService::writeNotification(const void* src, size_t n)
{
    if (!notificationPipeReady_) return false;
#ifdef _WIN32
    DWORD wrote = 0;
    if (!WriteFile(hNotificationPipe_, src, (DWORD)n, &wrote, NULL))
        return false;
    return wrote == n;
#else
    return write(notificationPipeFd_, src, n) == (ssize_t)n;
#endif
}

void IpcService::commandLoop()
{
    try
    {
        acceptConnectionsIfNeeded();

        while (running_)
        {
            char cmd = 0;
            if (!readCommandExact(&cmd, 1))
                break;

            host_.processCommand(cmd);
        }
    }
    catch (...)
    {
        // swallow; host will shut down
    }
    running_ = false;
}
