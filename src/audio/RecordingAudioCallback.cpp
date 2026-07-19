#include "audio/RecordingAudioCallback.h"
#include "host/PluginHostService.h"

// SEH wrappers — each step isolated so we can identify which one crashes
static int sehParamProcess(PluginHostService* host, int numSamples)
{
    __try {
        // Align the MIDI scheduler cursor with the timeline before the graph
        // renders, so each MidiSourceNode emits this block's notes.
        host->syncMidiSchedulerToTimeline();
        host->paramScheduler.processBlock(numSamples);
        // Drive host CC->parameter mappings from scheduled CCs in this block,
        // so scheduled CCs behave like live controller input.
        host->applyScheduledCcMappings(numSamples);
        return 0;
    }
    __except(EXCEPTION_EXECUTE_HANDLER) { return (int)GetExceptionCode(); }
}

static int sehGraphRender(juce::AudioIODeviceCallback* cb,
  const float* const* in, int nIn, float* const* out, int nOut, int nSamp,
  const juce::AudioIODeviceCallbackContext& ctx)
{
    __try { cb->audioDeviceIOCallbackWithContext(in, nIn, out, nOut, nSamp, ctx); return 0; }
    __except(EXCEPTION_EXECUTE_HANDLER) { return (int)GetExceptionCode(); }
}

static int sehPostProcess(PluginHostService* host, float* const* out, int nOut, int nSamp)
{
    __try {
        host->captureAudioForRecording(out, nOut, nSamp);
        host->paramScheduler.advance(nSamp);
        host->currentSamplePosition.store(host->paramScheduler.getTimelineSample());
        host->updateAudioFilePlayerTimeline();
        return 0;
    }
    __except(EXCEPTION_EXECUTE_HANDLER) { return (int)GetExceptionCode(); }
}

void RecordingAudioCallback::audioDeviceIOCallbackWithContext(
  const float* const* inputChannelData,
  int numInputChannels,
  float* const* outputChannelData,
  int numOutputChannels,
  int numSamples,
  const juce::AudioIODeviceCallbackContext& context)
{
    if (crashed_.load())
    {
        // Already crashed — just output silence
        for (int ch = 0; ch < numOutputChannels; ++ch)
            if (outputChannelData[ch])
                memset(outputChannelData[ch], 0, sizeof(float) * numSamples);
        return;
    }

    const char* step = nullptr;
    int code = 0;

    if (host)
    {
        code = sehParamProcess(host, numSamples);
        if (code != 0) { step = "paramScheduler.processBlock"; goto crashed; }
    }

    if (wrappedCallback)
    {
        code = sehGraphRender(wrappedCallback, inputChannelData, numInputChannels,
                               outputChannelData, numOutputChannels, numSamples, context);
        if (code != 0) { step = "graph render"; goto crashed; }
    }

    if (host)
    {
        code = sehPostProcess(host, outputChannelData, numOutputChannels, numSamples);
        if (code != 0) { step = "post-process"; goto crashed; }
    }

    return;

crashed:
    crashed_.store(true);
    for (int ch = 0; ch < numOutputChannels; ++ch)
        if (outputChannelData[ch])
            memset(outputChannelData[ch], 0, sizeof(float) * numSamples);

    {
        char buf[16];
        snprintf(buf, sizeof(buf), "%08x", (unsigned)code);
        std::string msg = std::string("Audio callback crash in ") + step + " (exception 0x" + buf + ")";
        std::cerr << msg << std::endl;
        if (host)
            host->sendErrorNotification(msg);

        // Fatal — shut down the server process
        juce::MessageManager::callAsync([]() {
            juce::JUCEApplicationBase::quit();
        });
    }
}
