#pragma once
#include "Common.h"

class PluginHostService;

class RecordingAudioCallback : public juce::AudioIODeviceCallback
{
private:
  juce::AudioIODeviceCallback* wrappedCallback;  // The actual callback (graphPlayer)
  PluginHostService* host;
  std::atomic<bool> crashed_{false};

public:
  RecordingAudioCallback(juce::AudioIODeviceCallback* callback, PluginHostService* h)
    : wrappedCallback(callback), host(h) {}

  void audioDeviceIOCallbackWithContext(const float* const* inputChannelData,
                                        int numInputChannels,
                                        float* const* outputChannelData,
                                        int numOutputChannels,
                                        int numSamples,
                                        const juce::AudioIODeviceCallbackContext& context) override;

  void audioDeviceAboutToStart(juce::AudioIODevice* device) override
  {
    if (wrappedCallback)
      wrappedCallback->audioDeviceAboutToStart(device);
  }

  void audioDeviceStopped() override
  {
    if (wrappedCallback)
      wrappedCallback->audioDeviceStopped();
  }
};
