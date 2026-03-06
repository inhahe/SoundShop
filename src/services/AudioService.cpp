#include "services/AudioService.h"
#include "host/PluginHostService.h"

AudioService::RealtimeConfig AudioService::ensureRealtimeInitialised(juce::AudioProcessorGraph& graph)
{
    RealtimeConfig cfg;

    if (realtimeInitialised_)
    {
        auto setup = deviceManager_.getAudioDeviceSetup();
        cfg.sampleRate = setup.sampleRate;
        cfg.bufferSize = setup.bufferSize;
        return cfg;
    }

    // Configure device (basic stereo in/out).
    auto setup = deviceManager_.getAudioDeviceSetup();
    // Prefer config values if user set them; otherwise keep current and let JUCE choose.
    if (state_.config.sampleRate > 0)
        setup.sampleRate = (double) state_.config.sampleRate;
    if (state_.config.blockSize > 0)
        setup.bufferSize = state_.config.blockSize;

    cfg.error = deviceManager_.initialise(2, 2, nullptr, true, {}, &setup);
    if (cfg.error.isNotEmpty())
    {
        // Still continue; device might partially initialise depending on backend.
        std::cerr << "Audio initialization error: " << cfg.error.toStdString() << std::endl;
    }

    setup = deviceManager_.getAudioDeviceSetup();
    cfg.sampleRate = setup.sampleRate;
    cfg.bufferSize = setup.bufferSize;

    // Attach graph to player and install callback wrapper.
    graphPlayer_.setProcessor(&graph);

    recordingCallback_ = std::make_unique<RecordingAudioCallback>(&graphPlayer_, &host_);
    deviceManager_.addAudioCallback(recordingCallback_.get());

    // MIDI collection
    midiCollector_ = std::make_unique<juce::MidiMessageCollector>();
    midiCollector_->reset(cfg.sampleRate);

    // Enable and connect all MIDI inputs; host receives callbacks.
    auto midiInputs = juce::MidiInput::getAvailableDevices();
    for (const auto& input : midiInputs)
    {
        deviceManager_.setMidiInputDeviceEnabled(input.identifier, true);
        deviceManager_.addMidiInputDeviceCallback(input.identifier, &host_);
    }

    realtimeInitialised_ = true;
    return cfg;
}

void AudioService::shutdown()
{
    if (recordingCallback_)
        deviceManager_.removeAudioCallback(recordingCallback_.get());

    // Remove MIDI callbacks
    auto midiInputs = juce::MidiInput::getAvailableDevices();
    for (const auto& input : midiInputs)
        deviceManager_.removeMidiInputDeviceCallback(input.identifier, &host_);

    deviceManager_.closeAudioDevice();
    graphPlayer_.setProcessor(nullptr);

    recordingCallback_.reset();
    midiCollector_.reset();
    realtimeInitialised_ = false;
}

