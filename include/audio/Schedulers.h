#pragma once
#include "Common.h"
#include "audio/Events.h"

class PluginHostService;

class SampleParamScheduler
{
public:
    void setHost(PluginHostService* h) { host = h; }

    void reset(int64_t startSample = 0)
    {
        std::lock_guard<std::mutex> lock(mtx);
        timelineSample = startSample;
        nextIndex = 0;
    }

    int64_t getTimelineSample() const noexcept { return timelineSample; }

    void setAutomationQuantum(int q) noexcept { automationQuantum = (q <= 0 ? 1 : q); }

    // Schedule at absolute sample time. Called from command thread.
    void scheduleAtSample(int key, int paramIndex, float value, int64_t atSample)
    {
        std::lock_guard<std::mutex> lock(mtx);

        if (automationQuantum > 1)
            atSample = (atSample / automationQuantum) * automationQuantum;

        ScheduledParameterChange e{ key, paramIndex, value, atSample };
        events.push_back(e);

        std::sort(events.begin(), events.end(),
                  [](auto const& a, auto const& b) { return a.atSample < b.atSample; });

        nextIndex = 0;
    }

    void scheduleInSeconds(int key, int paramIndex, float value, double secondsFromNow, double sampleRate)
    {
        int64_t at = timelineSample + (int64_t) std::llround(secondsFromNow * sampleRate);
        scheduleAtSample(key, paramIndex, value, at);
    }

    // Process events in [blockStart, blockEnd). Called from audio thread.
    void processBlock(int numSamples);

    void advance(int numSamples) noexcept
    {
        timelineSample += numSamples;
    }

    void clear()
    {
        std::lock_guard<std::mutex> lock(mtx);
        events.clear();
        nextIndex = 0;
    }

private:
    PluginHostService* host = nullptr;

    std::mutex mtx;
    std::vector<ScheduledParameterChange> events;
    int nextIndex = 0;

    std::atomic<int64_t> timelineSample = 0;

    int automationQuantum = 64;
};

class ParamScheduler {
public:
  void setHost(PluginHostService* h) { host = h; }

  void reset(int64_t startSample = 0) {
    timelineSample = startSample;
    nextIndex = 0;
  }

  void scheduleAtSample(int key, int paramIndex, float value, int64_t atSample)
  {
    events.push_back({ key, paramIndex, value, atSample });
    // sort only here (command thread), never in audio thread
    std::sort(events.begin(), events.end(),
              [](auto& a, auto& b){ return a.atSample < b.atSample; });
  }

  // your processBlock() from earlier, with timelineSample advance
  void processBlock(int numSamples);

  void clear() { events.clear(); nextIndex = 0; }

private:
  PluginHostService* host = nullptr;
  std::vector<ParameterChangeEvent> events;
  int nextIndex = 0;
  int64_t timelineSample = 0;
};

class LockFreeParameterQueue
{
private:
  static constexpr size_t QUEUE_SIZE = 1000000;
  std::array<ParameterChangeEvent, QUEUE_SIZE> queue;
  std::atomic<size_t> writeIndex{0};
  std::atomic<size_t> readIndex{0};

public:
  // Called from audio thread (fast, lock-free)
  bool push(const ParameterChangeEvent& event)
  {
    size_t currentWrite = writeIndex.load();
    size_t nextWrite = (currentWrite + 1) % QUEUE_SIZE;

    if (nextWrite == readIndex.load())
    {
      return false; // Queue full
    }

    queue[currentWrite] = event;
    writeIndex.store(nextWrite);
    return true;
  }

  // Called from main thread
  bool pop(ParameterChangeEvent& event)
  {
    size_t currentRead = readIndex.load();
    if (currentRead == writeIndex.load())
    {
      return false; // Queue empty
    }
    event = queue[currentRead];
    readIndex.store((currentRead + 1) % QUEUE_SIZE);
    return true;
  }
};

template<typename T>
class LockFreeMidiQueue
{
private:
  static constexpr size_t QUEUE_SIZE = 100000;
  std::array<T, QUEUE_SIZE> queue;
  std::atomic<size_t> writeIndex{0};
  std::atomic<size_t> readIndex{0};

public:
  // Called from audio thread (fast, lock-free)
  bool push(const T& event)
  {
    size_t currentWrite = writeIndex.load();
    size_t nextWrite = (currentWrite + 1) % QUEUE_SIZE;

    if (nextWrite == readIndex.load())
    {
      return false; // Queue full
    }

    queue[currentWrite] = event;
    writeIndex.store(nextWrite);
    return true;
  }

  // Called from main thread
  bool pop(T& event)
  {
    size_t currentRead = readIndex.load();
    if (currentRead == writeIndex.load())
    {
      return false; // Queue empty
    }
    event = queue[currentRead];
    readIndex.store((currentRead + 1) % QUEUE_SIZE);
    return true;
  }
};
