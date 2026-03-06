#pragma once
#include "Common.h"
#include "ServerState.h"
#include "audio/RecordingAudioCallback.h"

class PluginHostService;

/**
 * AudioService owns JUCE realtime audio plumbing:
 *  - AudioDeviceManager lifetime
 *  - AudioProcessorPlayer (graph player)
 *  - RecordingAudioCallback wrapper
 *  - MidiMessageCollector
 *
 * The plugin graph itself remains owned by PluginHostService (AudioProcessorGraph),
 * but this service wires the graph into the realtime device and provides accessors.
 */
class AudioService
{
public:
    struct RealtimeConfig
    {
        double sampleRate = 0.0;
        int bufferSize = 0;
        juce::String error;
    };

    AudioService(ServerState& state, PluginHostService& host)
        : state_(state), host_(host) {}

    // Called by ServerServices. Does not necessarily open devices immediately.
    void start() {}

    void stop() { shutdown(); }

    // Ensure realtime device + callbacks are initialized and graphPlayer is attached.
    // Safe to call multiple times.
    RealtimeConfig ensureRealtimeInitialised(juce::AudioProcessorGraph& graph);

    // Stop realtime audio and detach callbacks.
    void shutdown();

    // Accessors used by host code.
    juce::AudioDeviceManager& deviceManager() { return deviceManager_; }
    juce::AudioProcessorPlayer& graphPlayer() { return graphPlayer_; }
    juce::MidiMessageCollector* midiCollector() { return midiCollector_.get(); }

    bool isRealtimeInitialised() const { return realtimeInitialised_; }

private:
    ServerState& state_;
    PluginHostService& host_;

    bool realtimeInitialised_ = false;

    juce::AudioDeviceManager deviceManager_;
    juce::AudioProcessorPlayer graphPlayer_;
    std::unique_ptr<juce::MidiMessageCollector> midiCollector_;
    std::unique_ptr<RecordingAudioCallback> recordingCallback_;
};
