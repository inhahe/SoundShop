#include "services/ServerServices.h"
#include "ServerState.h"
#include <cctype>
#include <cstring>

#ifdef _WIN32

static bool isAllWhitespace(const char* str)
{
    if (!str) return true;
    while (*str) {
        if (!std::isspace(static_cast<unsigned char>(*str)))
            return false;
        ++str;
    }
    return true;
}

extern "C" int WINAPI WinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance, LPSTR lpCmdLine, int nCmdShow)
{
    try
    {
        (void)hInstance;
        (void)hPrevInstance;
        (void)nCmdShow;

        AllocConsole();
        FILE* p = nullptr;
        freopen_s(&p, "CONOUT$", "w", stdout);
        freopen_s(&p, "CONOUT$", "w", stderr);
        freopen_s(&p, "CONIN$",  "r", stdin);

        std::cerr.clear();
        std::cin.clear();

        juce::initialiseJuce_GUI();

        ServerState state;
        if (lpCmdLine && !isAllWhitespace(lpCmdLine))
            state.config.pipeName = std::string(lpCmdLine);

        ServerServices services(state);
        services.start(juce::String(lpCmdLine ? lpCmdLine : ""));

        juce::MessageManager::getInstance()->runDispatchLoop();

        services.stop();
        juce::shutdownJuce_GUI();
        return 0;
    }
    catch (const std::exception& e)
    {
        std::cerr << "Fatal error: " << e.what() << std::endl;
        return 1;
    }
}

#else

int main(int argc, char* argv[])
{
    juce::initialiseJuce_GUI();

    juce::String commandLine;
    for (int i = 1; i < argc; ++i)
    {
        if (i > 1) commandLine << " ";
        commandLine << argv[i];
    }

    ServerState state;
    if (commandLine.isNotEmpty())
        state.config.pipeName = commandLine.toStdString();

    ServerServices services(state);
    services.start(commandLine);

    juce::MessageManager::getInstance()->runDispatchLoop();

    services.stop();
    juce::shutdownJuce_GUI();
    return 0;
}

#endif
