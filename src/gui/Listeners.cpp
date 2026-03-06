#include "host/PluginHostService.h"
#include "gui/VirtualKeyboardListener.h"
#include "audio/ParameterChangeListener.h"
void VirtualKeyboardListener::routeToHost(const juce::MidiMessage& message)
{
  if (hostApp)
  {
    hostApp->handleVirtualKeyboardMessage(message);
  }
}


void ParameterChangeListener::audioProcessorParameterChanged(juce::AudioProcessor* processor,
                                                            int paramIndex,
                                                            float value)
{
    if (!serverState || !hostApp) return;
    if (serverState->config.suppressNotifications) return;

    const int key = hostApp->findkey(processor);
    if (key == -1) return;

    const int64_t atSample = hostApp->paramScheduler.getTimelineSample();

    ParameterChangeEvent event{
        key,
        paramIndex,
        value,
        atSample
    };

    serverState->queues.parameterQueue.push(event);
}