#pragma once
#include "Common.h"


class MidiSourceNode : public juce::AudioProcessor
{
private:
  MidiScheduler* midiScheduler;
  int targetkey;

public:
  MidiSourceNode(MidiScheduler* s, int key)
    : AudioProcessor(BusesProperties()  // No audio buses needed
      .withInput("Input", juce::AudioChannelSet::stereo(), false)
      .withOutput("Output", juce::AudioChannelSet::stereo(), false)),
    midiScheduler(s),
    targetkey(key) {}

  // Main processing - just outputs MIDI for this plugin
  void processBlock(juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midiMessages) override
  {
    midiMessages.clear();
    midiScheduler->getEventsForPlugin(targetkey, midiMessages, buffer.getNumSamples());
  }

  // Required AudioProcessor methods
  const juce::String getName() const override { return "MIDI Source " + juce::String(targetkey); }
  void prepareToPlay(double sampleRate, int samplesPerBlock) override {}
  void releaseResources() override {}

  bool acceptsMidi() const override { return false; }  // Doesn't accept external MIDI
  bool producesMidi() const override { return true; }   // Produces MIDI
  bool isMidiEffect() const override { return true; }

  double getTailLengthSeconds() const override { return 0; }

  int getNumPrograms() override { return 1; }
  int getCurrentProgram() override { return 0; }
  void setCurrentProgram(int index) override {}
  const juce::String getProgramName(int index) override { return {}; }
  void changeProgramName(int index, const juce::String& newName) override {}

  void getStateInformation(juce::MemoryBlock& destData) override {}
  void setStateInformation(const void* data, int sizeInBytes) override {}

  juce::AudioProcessorEditor* createEditor() override { return nullptr; }
  bool hasEditor() const override { return false; }
};
