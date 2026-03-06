#pragma once
#include "Common.h"
#include "audio/Events.h"
#include "audio/Schedulers.h"

class PluginHostService;
struct ServerState;

class ParameterChangeListener : public juce::AudioProcessorListener
{
public:
    ParameterChangeListener() = default;

    void setContext(PluginHostService* host, ServerState* state)
    {
        hostApp = host;
        serverState = state;
    }

    void audioProcessorParameterChanged(juce::AudioProcessor* processor,
                                        int parameterIndex,
                                        float newValue) override;

    void audioProcessorChanged(juce::AudioProcessor*, const ChangeDetails&) override
    {
        // Not used in this implementation
    }

private:
    PluginHostService* hostApp = nullptr;
    ServerState* serverState = nullptr;
};
