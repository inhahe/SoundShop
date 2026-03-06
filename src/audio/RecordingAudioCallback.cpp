#include "audio/RecordingAudioCallback.h"
#include "host/PluginHostService.h"



// RecordingAudioCallback implementation
void RecordingAudioCallback::audioDeviceIOCallbackWithContext(
  const float* const* inputChannelData,
  int numInputChannels,
  float* const* outputChannelData,
  int numOutputChannels,
  int numSamples,
  const juce::AudioIODeviceCallbackContext& context)
{
    if (host)
    {
        // 1) apply scheduled param changes for THIS block
        host->paramScheduler.processBlock(numSamples);
    }

    // 2) render audio
    if (wrappedCallback)
        wrappedCallback->audioDeviceIOCallbackWithContext(inputChannelData, numInputChannels,
                                                          outputChannelData, numOutputChannels,
                                                          numSamples, context);

    if (host)
    {
        // 3) record the rendered output
        host->captureAudioForRecording(outputChannelData, numOutputChannels, numSamples);

        // 4) advance timeline by actual callback size
        host->paramScheduler.advance(numSamples);

        // 5) keep globals in sync
        host->currentSamplePosition.store(host->paramScheduler.getTimelineSample());

        // 6) push timeline to audio file players etc.
        host->updateAudioFilePlayerTimeline();
    }
}
