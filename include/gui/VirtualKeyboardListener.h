#pragma once
#include "Common.h"
#include "audio/Events.h"


class VirtualKeyboardListener : public juce::MidiKeyboardStateListener
{
public:
  VirtualKeyboardListener(PluginHostService* host) : hostApp(host) {}

  void handleNoteOn(juce::MidiKeyboardState*, int midiChannel, int midiNoteNumber, float velocity) override
  {
    auto message = juce::MidiMessage::noteOn(midiChannel, midiNoteNumber, velocity);
    routeToHost(message);
  }

  void handleNoteOff(juce::MidiKeyboardState*, int midiChannel, int midiNoteNumber, float velocity) override
  {
    auto message = juce::MidiMessage::noteOff(midiChannel, midiNoteNumber, velocity);
    routeToHost(message);
  }

private:
  PluginHostService* hostApp;
  void routeToHost(const juce::MidiMessage& message);
};
