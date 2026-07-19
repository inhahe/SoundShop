#pragma once
#include "Common.h"


class MidiScheduler 
{
private:
  struct ScheduledMidiEvent 
  {
    juce::MidiMessage message;
    int64_t samplePosition;
    int key;
  };
  std::vector<ScheduledMidiEvent> scheduledEvents;
  std::mutex schedulerMutex;
  size_t nextEventIndex = 0;
  int64_t currentSamplePosition = 0;
  double sampleRate;
  
public:
  void processBlockWithkeys(std::unordered_map<int, juce::MidiBuffer>& buffersByPlugin,
    int blockSize) 
  {
    // Clear all buffers
    buffersByPlugin.clear();
    int64_t blockEnd = currentSamplePosition + blockSize;
    while (nextEventIndex < scheduledEvents.size()) //todo: figure this out and see if it's as efficient as my solution in the param scheduler
    {
      const auto& event = scheduledEvents[nextEventIndex];
      if (event.samplePosition >= blockEnd) break;
      if (event.samplePosition >= currentSamplePosition) 
      {
        int sampleOffset = static_cast<int>(event.samplePosition - currentSamplePosition);
        // Add to the appropriate plugin's buffer
        buffersByPlugin[event.key].addEvent(event.message, sampleOffset);
      }
      nextEventIndex++;
    }
    currentSamplePosition += blockSize;
  }
  explicit MidiScheduler(double sr) 
    : sampleRate(sr), 
    currentSamplePosition(0), 
    nextEventIndex(0) {}

  void scheduleNote(int key, int noteNumber, float velocity, 
    double startTimeSeconds, double durationSeconds, int channel = 1) 
  {
    int64_t startSample = currentSamplePosition + 
      static_cast<int64_t>(startTimeSeconds * sampleRate);
    int64_t endSample = startSample + 
      static_cast<int64_t>(durationSeconds * sampleRate);

    std::lock_guard<std::mutex> lock(schedulerMutex);

    scheduledEvents.push_back({
      juce::MidiMessage::noteOn(channel, noteNumber, velocity),
      startSample, 
      key
      });

    scheduledEvents.push_back({
      juce::MidiMessage::noteOff(channel, noteNumber),
      endSample,
      key
      });

    // Sort only from nextEventIndex onward (unprocessed events)
    std::sort(scheduledEvents.begin(), scheduledEvents.end(),
      [](const auto& a, const auto& b) { 
        return a.samplePosition < b.samplePosition;  // Must return bool
      });  }

  void scheduleMidiMessage(int key, const juce::MidiMessage& msg, 
    double timeSeconds) 
  {
    int64_t sample = currentSamplePosition + 
      static_cast<int64_t>(timeSeconds * sampleRate);

    std::lock_guard<std::mutex> lock(schedulerMutex);
    scheduledEvents.push_back({msg, sample, key});

    std::sort(scheduledEvents.begin() + nextEventIndex, scheduledEvents.end(),
      [](const auto& a, const auto& b) { 
        return a.samplePosition < b.samplePosition; 
      });
  }

  void scheduleCC(int key, int controller, int value, 
    double timeSeconds, int channel = 1) 
  {
    auto msg = juce::MidiMessage::controllerEvent(channel, controller, value);
    scheduleMidiMessage(key, msg, timeSeconds);
  }

  void schedulePitchBend(int key, int value, double timeSeconds, int channel = 1) 
  {
    auto msg = juce::MidiMessage::pitchWheel(channel, value);
    scheduleMidiMessage(key, msg, timeSeconds);
  }

  void processBlock(juce::MidiBuffer& midiBuffer, int64_t blockStartSample, int blockSize)
  {
      midiBuffer.clear();
      int64_t blockEnd = blockStartSample + blockSize;

      while (nextEventIndex < scheduledEvents.size())
      {
          auto& e = scheduledEvents[nextEventIndex];
          if (e.samplePosition >= blockEnd) break;

          if (e.samplePosition >= blockStartSample)
          {
              int offset = (int)(e.samplePosition - blockStartSample);
              midiBuffer.addEvent(e.message, offset);
          }
          ++nextEventIndex;
      }
  }
  void getEventsForPlugin(int key, juce::MidiBuffer& buffer, int blockSize) 
  {
    buffer.clear();
    int64_t blockEnd = currentSamplePosition + blockSize;

    size_t index = nextEventIndex;
    while (index < scheduledEvents.size()) 
    {
      const auto& event = scheduledEvents[index];
      if (event.samplePosition >= blockEnd) break;

      if (event.key == key && 
        event.samplePosition >= currentSamplePosition) 
      {
        int offset = static_cast<int>(event.samplePosition - currentSamplePosition);
        buffer.addEvent(event.message, offset);
      }
      index++;
    }
  }

  // Invoke fn(key, controller, value, channel) for every controller (CC)
  // event scheduled within the current block window
  // [currentSamplePosition, currentSamplePosition + blockSize). Unlike
  // getEventsForPlugin this does not depend on / advance nextEventIndex and
  // does not consume events, so it can run alongside the per-plugin
  // MidiSourceNode reads. Used by the host to apply CC->parameter mappings to
  // scheduled CCs, mirroring the live-input path.
  template <typename Fn>
  void forEachCcInBlock(int blockSize, Fn&& fn)
  {
    const int64_t blockStart = currentSamplePosition;
    const int64_t blockEnd = blockStart + blockSize;
    for (size_t i = 0; i < scheduledEvents.size(); ++i)
    {
      const auto& e = scheduledEvents[i];
      if (e.samplePosition >= blockEnd) break;  // sorted by samplePosition
      if (e.samplePosition >= blockStart && e.message.isController())
        fn(e.key,
           e.message.getControllerNumber(),
           e.message.getControllerValue(),
           e.message.getChannel());
    }
  }

  void clearSchedule()
  {
    std::lock_guard<std::mutex> lock(schedulerMutex);
    scheduledEvents.clear();
    nextEventIndex = 0;
  }

  void clearCCSchedule()
  {
    std::lock_guard<std::mutex> lock(schedulerMutex);
    // Remove only CC events, keep notes and other MIDI messages
    scheduledEvents.erase(
      std::remove_if(scheduledEvents.begin(), scheduledEvents.end(),
        [](const ScheduledMidiEvent& event) {
          return event.message.isController();
        }),
      scheduledEvents.end()
    );
    nextEventIndex = 0;
  }

  void reset() 
  {
    std::lock_guard<std::mutex> lock(schedulerMutex);
    currentSamplePosition = 0;
    nextEventIndex = 0;
  }

  void setSampleRate(double sr)
  {
    std::lock_guard<std::mutex> lock(schedulerMutex);
    sampleRate = sr;
  }

  // Set the scheduler's playback cursor to an absolute sample position.
  // MidiSourceNode::processBlock reads events relative to this position, so the
  // host must advance it (once per audio block) from the authoritative timeline.
  void setCurrentPosition(int64_t pos)
  {
    currentSamplePosition = pos;
  }

  void cleanupProcessedEvents() 
  {
    std::lock_guard<std::mutex> lock(schedulerMutex);
    if (nextEventIndex > 1000) {
      scheduledEvents.erase(scheduledEvents.begin(), 
        scheduledEvents.begin() + nextEventIndex);
      nextEventIndex = 0;
    }
  }

  size_t getNumPendingEvents() const 
  {
    return scheduledEvents.size() - nextEventIndex;
  }

  int64_t getCurrentPosition() const 
  { 
    return currentSamplePosition; 
  }
};
