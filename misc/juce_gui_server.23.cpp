//todo: record audio
//todo: find out how Popsicle works and see about making this work that way instead of using a pipe
//todo: move all logic of adding to badPaths when a plugin fails to load to the .py file. this should be possible since it won't call loadplugin unless python calls it and gets its success result?
//todo: there is a problem with the places screenWidth and screenHeight are defined. apparently they're not used and also a local definition is supposedly eclipses a less local one.
//todo: add MPE MIDI support? there's no programmatic way to check if a plugin supports MPE.
// Include individual JUCE module headers
#include <juce_core/juce_core.h>
#include <juce_events/juce_events.h>
#include <juce_graphics/juce_graphics.h>
#include <juce_gui_basics/juce_gui_basics.h>
#include <juce_gui_extra/juce_gui_extra.h>
#include <juce_audio_basics/juce_audio_basics.h>
#include <juce_audio_devices/juce_audio_devices.h>
#include <juce_audio_formats/juce_audio_formats.h>
#include <juce_audio_processors/juce_audio_processors.h>
#include <juce_audio_utils/juce_audio_utils.h>

// Make sure we have the plugin host utilities
#if defined __has_include
#if __has_include(<juce_audio_plugin_client/juce_audio_plugin_client.h>)
#include <juce_audio_plugin_client/juce_audio_plugin_client.h>
#endif
#endif

#include <iostream>
#include <thread>
#include <atomic>
#include <queue>
#include <mutex>
#include <condition_variable>
#include <string>
#include <memory>
#include <map>
#include <vector>
#include <atomic>
#include <array>
#include <iostream>
#include <fstream>
#include <string>

#ifdef _WIN32
#include <windows.h>
#include <fcntl.h>
#include <io.h>
#else
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>
#endif

using namespace std;
int sampleRate = 44100;
int blockSize = 64;
bool isRecording = true;
bool suppressNotifications = false;
string pipeName = "juceclientserver";
int updateRate = 50;

enum recv_cmd
{
  load_plugin, load_plugin_by_index, scan_plugins, list_plugins, get_plugin_info, show_plugin_ui, hide_plugin_ui, set_parameter, get_parameter, connect_audio, connect_midi, start_playback,
  cmd_shutdown, remove_plugin, list_bad_paths, get_params_info, get_channels_info, schedule_midi_note, schedule_midi_cc, clear_midi_schedule, schedule_param_change,
  route_keyboard_input, unroute_keyboard_input, route_cc_to_param, unroute_cc_to_param,
  show_virtual_keyboard, hide_virtual_keyboard, route_virtual_keyboard, unroute_virtual_keyboard,
  toggle_recording, toggle_monitoring,
  load_audio_file, control_audio_playback,
  schedule_ordered_notes, start_ordered_playback, stop_ordered_playback, clear_ordered_notes,
  clear_midi_cc_schedule, clear_param_schedule, clear_all_plugins
};

enum send_cmd : uint8_t
{
  param_changed, param_changes_end, stop_playback, midi_note_event, midi_cc_event,
  virtual_keyboard_note_event, virtual_keyboard_cc_event,
  midi_keyboard_routed, virtual_keyboard_routed,
  recording_started, recording_stopped, monitoring_changed,
  audio_file_loaded, audio_playback_started, audio_playback_stopped,
  ordered_note_triggered, ordered_playback_started, ordered_playback_stopped
};

class CompletePluginHost; 
CompletePluginHost* app = nullptr; 

int64_t currentSamplePosition = 0;

class MidiScheduler 
{
private:
  struct ScheduledMidiEvent 
  {
    juce::MidiMessage message;
    int64_t samplePosition;
    int lpindex;
  };
  std::vector<ScheduledMidiEvent> scheduledEvents;
  std::mutex schedulerMutex;
  size_t nextEventIndex = 0;
  int64_t currentSamplePosition = 0;
  double sampleRate;
  
public:
  void processBlockWithlpindexs(std::unordered_map<int, juce::MidiBuffer>& buffersByPlugin, 
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
        buffersByPlugin[event.lpindex].addEvent(event.message, sampleOffset);
      }
      nextEventIndex++;
    }
    currentSamplePosition += blockSize;
  }
  explicit MidiScheduler(double sr) 
    : sampleRate(sr), 
    currentSamplePosition(0), 
    nextEventIndex(0) {}

  void scheduleNote(int lpindex, int noteNumber, float velocity, 
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
      lpindex
      });

    scheduledEvents.push_back({
      juce::MidiMessage::noteOff(channel, noteNumber),
      endSample,
      lpindex
      });

    // Sort only from nextEventIndex onward (unprocessed events)
    std::sort(scheduledEvents.begin(), scheduledEvents.end(),
      [](const auto& a, const auto& b) { 
        return a.samplePosition < b.samplePosition;  // Must return bool
      });  }

  void scheduleMidiMessage(int lpindex, const juce::MidiMessage& msg, 
    double timeSeconds) 
  {
    int64_t sample = currentSamplePosition + 
      static_cast<int64_t>(timeSeconds * sampleRate);

    std::lock_guard<std::mutex> lock(schedulerMutex);
    scheduledEvents.push_back({msg, sample, lpindex});

    std::sort(scheduledEvents.begin() + nextEventIndex, scheduledEvents.end(),
      [](const auto& a, const auto& b) { 
        return a.samplePosition < b.samplePosition; 
      });
  }

  void scheduleCC(int lpindex, int controller, int value, 
    double timeSeconds, int channel = 1) 
  {
    auto msg = juce::MidiMessage::controllerEvent(channel, controller, value);
    scheduleMidiMessage(lpindex, msg, timeSeconds);
  }

  void schedulePitchBend(int lpindex, int value, double timeSeconds, int channel = 1) 
  {
    auto msg = juce::MidiMessage::pitchWheel(channel, value);
    scheduleMidiMessage(lpindex, msg, timeSeconds);
  }

  void processBlock(juce::MidiBuffer& midiBuffer, int blockSize) 
  {
    midiBuffer.clear();
    int64_t blockEnd = currentSamplePosition + blockSize;

    // Process events without locking (only reading at nextEventIndex)
    while (nextEventIndex < scheduledEvents.size()) 
    {
      const auto& event = scheduledEvents[nextEventIndex];

      // Stop if we've reached events beyond this block
      if (event.samplePosition >= blockEnd) {
        break;
      }

      // Add event if it's within this block
      if (event.samplePosition >= currentSamplePosition) {
        int sampleOffset = static_cast<int>(event.samplePosition - currentSamplePosition);
        midiBuffer.addEvent(event.message, sampleOffset);
      }

      nextEventIndex++;
    }

    // Advance timeline
    currentSamplePosition += blockSize;
  }

  void getEventsForPlugin(int lpindex, juce::MidiBuffer& buffer, int blockSize) 
  {
    buffer.clear();
    int64_t blockEnd = currentSamplePosition + blockSize;

    size_t index = nextEventIndex;
    while (index < scheduledEvents.size()) 
    {
      const auto& event = scheduledEvents[index];
      if (event.samplePosition >= blockEnd) break;

      if (event.lpindex == lpindex && 
        event.samplePosition >= currentSamplePosition) 
      {
        int offset = static_cast<int>(event.samplePosition - currentSamplePosition);
        buffer.addEvent(event.message, offset);
      }
      index++;
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

class MidiSourceNode : public juce::AudioProcessor
{
private:
  MidiScheduler* scheduler;
  int targetlpindex;

public:
  MidiSourceNode(MidiScheduler* s, int lpindex)
    : AudioProcessor(BusesProperties()  // No audio buses needed
      .withInput("Input", juce::AudioChannelSet::stereo(), false)
      .withOutput("Output", juce::AudioChannelSet::stereo(), false)),
    scheduler(s), 
    targetlpindex(lpindex) {}

  // Main processing - just outputs MIDI for this plugin
  void processBlock(juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midiMessages) override
  {
    midiMessages.clear();
    scheduler->getEventsForPlugin(targetlpindex, midiMessages, buffer.getNumSamples());
  }

  // Required AudioProcessor methods
  const juce::String getName() const override { return "MIDI Source " + juce::String(targetlpindex); }
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

// Audio file player node - plays back audio files through the graph
class AudioFilePlayerNode : public juce::AudioProcessor
{
private:
  juce::AudioBuffer<float> audioBuffer;
  int64_t playbackPosition = 0;
  int64_t startSamplePosition = 0;
  bool isScheduled = false;
  bool isPlaying = false;
  std::string loadedFilename;

public:
  AudioFilePlayerNode()
    : AudioProcessor(BusesProperties()
      .withOutput("Output", juce::AudioChannelSet::stereo(), true))
  {
  }

  // Load an audio file
  bool loadAudioFile(const std::string& filename)
  {
    juce::File audioFile = juce::File::getCurrentWorkingDirectory().getChildFile(filename);

    if (!audioFile.existsAsFile())
    {
      std::cout << "ERROR: Audio file does not exist: " << filename << std::endl;
      return false;
    }

    juce::AudioFormatManager formatManager;
    formatManager.registerBasicFormats();

    std::unique_ptr<juce::AudioFormatReader> reader(formatManager.createReaderFor(audioFile));

    if (reader == nullptr)
    {
      std::cout << "ERROR: Failed to create reader for: " << filename << std::endl;
      return false;
    }

    // Load the audio into our buffer
    audioBuffer.setSize(reader->numChannels, (int)reader->lengthInSamples);
    reader->read(&audioBuffer, 0, (int)reader->lengthInSamples, 0, true, true);

    loadedFilename = filename;
    playbackPosition = 0;
    isPlaying = false;

    std::cout << "Audio file loaded into player node: " << filename
              << ", channels=" << reader->numChannels
              << ", samples=" << reader->lengthInSamples << std::endl;

    return true;
  }

  // Schedule playback to start at a specific sample position
  void schedulePlayback(int64_t startSample, int64_t fileStartPosition = 0)
  {
    startSamplePosition = startSample;
    playbackPosition = juce::jmin(fileStartPosition, (int64_t)audioBuffer.getNumSamples());
    isScheduled = true;
    isPlaying = false;
    std::cout << "Playback scheduled for sample " << startSample
              << ", starting at file position " << playbackPosition << std::endl;
  }

  // Start playing immediately
  void startPlayback(int64_t fileStartPosition = 0)
  {
    playbackPosition = juce::jmin(fileStartPosition, (int64_t)audioBuffer.getNumSamples());
    isPlaying = true;
    isScheduled = false;
    std::cout << "Playback started immediately at file position " << playbackPosition << std::endl;
  }

  // Stop playback
  void stopPlayback()
  {
    isPlaying = false;
    isScheduled = false;
    playbackPosition = 0;
    std::cout << "Playback stopped" << std::endl;
  }

  bool isCurrentlyPlaying() const { return isPlaying; }
  std::string getLoadedFilename() const { return loadedFilename; }

  void processBlock(juce::AudioBuffer<float>& buffer, juce::MidiBuffer& midiMessages) override
  {
    buffer.clear();

    // Check if scheduled playback should start
    if (isScheduled && !isPlaying && currentSamplePosition >= startSamplePosition)
    {
      isPlaying = true;
      isScheduled = false;
      std::cout << "Started scheduled playback at sample " << currentSamplePosition << std::endl;
    }

    if (!isPlaying || audioBuffer.getNumSamples() == 0)
      return;

    int numSamples = buffer.getNumSamples();
    int samplesToPlay = juce::jmin(numSamples,
      (int)(audioBuffer.getNumSamples() - playbackPosition));

    if (samplesToPlay > 0)
    {
      // Copy audio to output
      for (int ch = 0; ch < buffer.getNumChannels() && ch < audioBuffer.getNumChannels(); ++ch)
      {
        buffer.copyFrom(ch, 0, audioBuffer, ch, (int)playbackPosition, samplesToPlay);
      }

      playbackPosition += samplesToPlay;
    }

    // Check if finished
    if (playbackPosition >= audioBuffer.getNumSamples())
    {
      std::cout << "Playback finished" << std::endl;
      isPlaying = false;
      playbackPosition = 0;
    }
  }

  const juce::String getName() const override { return "Audio File Player"; }
  void prepareToPlay(double sampleRate, int samplesPerBlock) override {}
  void releaseResources() override {}

  bool acceptsMidi() const override { return false; }
  bool producesMidi() const override { return false; }

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

// Forward declaration
class CompletePluginHost;

// Audio callback wrapper for recording
class RecordingAudioCallback : public juce::AudioIODeviceCallback
{
private:
  juce::AudioIODeviceCallback* wrappedCallback;  // The actual callback (graphPlayer)
  CompletePluginHost* host;

public:
  RecordingAudioCallback(juce::AudioIODeviceCallback* callback, CompletePluginHost* h)
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

struct ScheduledParameterChange 
{
  int lpindex;
  int parameterIndex;
  float value;
  uint64_t atBlock;  // Which audio block to execute on
  bool executed = false;
};

class BlockLevelScheduler 
{
private:
  CompletePluginHost* host = nullptr;
  vector<ScheduledParameterChange> scheduledChanges;
  uint64_t currentBlock = 0;
  int lastChangeIndex = 0;

public:
  void setHost(CompletePluginHost* h) { host = h; }
  void setBlockSize(int size) { blockSize = size; }
  // Python calls this to schedule changes
  void scheduleParameterChange(int lpindex, int paramIndex, float value, uint64_t atBlock) 
  {
    ScheduledParameterChange change;
    change.lpindex = lpindex;
    change.parameterIndex = paramIndex;
    change.value = value;
    change.atBlock = atBlock;

    scheduledChanges.push_back(change);

    // Sort by block number
    sort(scheduledChanges.begin(), scheduledChanges.end(),
      [](const auto& a, const auto& b) { return a.atBlock < b.atBlock; });
  }

  // Called at the START of each audio block, before processing
  void processScheduledChanges() ;
  void incrementBlock() { currentBlock++; }
  uint64_t getCurrentBlock() const { return currentBlock; }

  void clearSchedule()
  {
    scheduledChanges.clear();
    lastChangeIndex = 0;
  }
};

struct ParameterChangeEvent
{
  int lpindex;
  int parameterIndex;
  float value;
  uint64_t atBlock;  // Block number when event occurred
};

struct MidiNoteEvent
{
  int noteNumber;
  int velocity;  // 0-127, or 0 for note off
  int channel;
  bool isNoteOn;
  uint64_t samplePosition;  // Sample position when event occurred
};

struct MidiCCEvent
{
  int controller;
  int value;  // 0-127
  int channel;
  uint64_t atBlock;  // Block number when event occurred
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

LockFreeParameterQueue parameterQueue;
LockFreeMidiQueue<MidiNoteEvent> midiNoteQueue;
LockFreeMidiQueue<MidiCCEvent> midiCCQueue;
LockFreeMidiQueue<MidiNoteEvent> virtualKeyboardNoteQueue;
LockFreeMidiQueue<MidiCCEvent> virtualKeyboardCCQueue;

class ParameterChangeListener : public juce::AudioProcessorListener 
{
public:
  void audioProcessorParameterChanged(juce::AudioProcessor* processor, int paramIndex, float value) override;
  void audioProcessorChanged(juce::AudioProcessor* processor, const juce::AudioProcessorListener::ChangeDetails& details) override 
  {
  }
};

ParameterChangeListener paramListener;

// Forward declare CompletePluginHost for the virtual keyboard listener
class CompletePluginHost;

// Virtual keyboard listener to route MIDI from the on-screen keyboard
class VirtualKeyboardListener : public juce::MidiKeyboardStateListener
{
public:
  VirtualKeyboardListener(CompletePluginHost* host) : hostApp(host) {}

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
  CompletePluginHost* hostApp;
  void routeToHost(const juce::MidiMessage& message);
};

class CompletePluginHost : public juce::Timer, public juce::MidiInputCallback
{
public:
  CompletePluginHost()
  {
    bool running = true;
    // Initialize format manager with plugin formats - be more explicit
    cout << "Initializing plugin formats..." << endl;

    // Add formats individually to make sure they're loaded

    formatManager.addFormat(new juce::VST3PluginFormat()); 
    cout << "Added VST3 format" << endl;
    processorGraph = std::make_unique<juce::AudioProcessorGraph>();

#ifdef _WIN32
    // Windows named pipe
    cout << "creating pipe" << endl;
    string commandPipeName = "\\\\.\\pipe\\" + pipeName + "_commands";  // Python -> C++
    

    string notificationPipeName = "\\\\.\\pipe\\" + pipeName + "_notifications";  // C++ -> Python
    hCommandPipe = CreateNamedPipeA(
      commandPipeName.c_str(),
      PIPE_ACCESS_DUPLEX,
      //PIPE_TYPE_MESSAGE
      //PIPE_READMODE_MESSAGE
      PIPE_TYPE_BYTE | PIPE_WAIT,
      PIPE_UNLIMITED_INSTANCES,
      4096,
      4096,
      0,
      NULL
      );
    if (hCommandPipe == INVALID_HANDLE_VALUE)
    {
      cout << "Failed to create named pipe" << endl;
      for (int i = 0; i < formatManager.getNumFormats(); ++i)
        throw std::runtime_error("Failed to create named pipe");
    }

    cout << "created pipe " << commandPipeName << endl;    

    hNotificationPipe = CreateNamedPipeA(
      notificationPipeName.c_str(),
      PIPE_ACCESS_OUTBOUND,
      //PIPE_TYPE_MESSAGE
      //PIPE_READMODE_MESSAGE
      PIPE_TYPE_BYTE | PIPE_WAIT,
      PIPE_UNLIMITED_INSTANCES,
      4096,
      4096,
      0,
      NULL
    );
    if (hNotificationPipe == INVALID_HANDLE_VALUE)
    {
      cout << "Failed to create named pipe" << endl;
      for (int i = 0; i < formatManager.getNumFormats(); ++i)
        throw std::runtime_error("Failed to create named pipe");
    }
    notificationPipeReady = true;
    cout << "created pipe " << notificationPipeName << endl;
#else
    // Unix named pipes (FIFOs) - create two separate pipes
    string commandPipePath = "/tmp/" + pipeName + "_commands";
    string notificationPipePath = "/tmp/" + pipeName + "_notifications";

    // Create the FIFO files
    if (mkfifo(commandPipePath.c_str(), 0666) == -1 && errno != EEXIST) {
      throw runtime_error("Failed to create command FIFO: " + string(strerror(errno)));
    }

    if (mkfifo(notificationPipePath.c_str(), 0666) == -1 && errno != EEXIST) {
      throw runtime_error("Failed to create notification FIFO: " + string(strerror(errno)));
    }

    // Open command pipe for reading and writing (bidirectional)
    commandPipe_fd = open(commandPipePath.c_str(), O_RDWR);
    if (commandPipe_fd < 0) {
      throw runtime_error("Failed to open command FIFO: " + string(strerror(errno)));
    }

    // Open notification pipe for writing only (C++ -> Python)
    notificationPipe_fd = open(notificationPipePath.c_str(), O_WRONLY);
    if (notificationPipe_fd < 0) {
      throw runtime_error("Failed to open notification FIFO: " + string(strerror(errno)));
    }
#endif

#if JUCE_PLUGINHOST_AU && (JUCE_MAC || JUCE_IOS) //todo: what about Linux?
    formatManager.addFormat(new juce::AudioUnitPluginFormat());
#endif
    cout << "startTimer(" << updateRate << ")" << endl;
    startTimer(updateRate);
    scheduler.setHost(this);

    // Initialize virtual keyboard listener
    virtualKeyboardListener = std::make_unique<VirtualKeyboardListener>(this);
    virtualKeyboardState.addListener(virtualKeyboardListener.get());
  }

  BlockLevelScheduler scheduler;
  int inputIndex = -2;
  int outputIndex = -1;
  bool audioInitialized = false;

  // Keyboard routing (for physical MIDI keyboard)
  int keyboardRoutedPlugin = -1;  // -1 means no routing
  bool useKeyboardVelocity = true;
  float fixedVelocity = 1.0f;  // Used when useKeyboardVelocity is false

  // Virtual keyboard routing (separate from physical keyboard)
  int virtualKeyboardRoutedPlugin = -1;  // -1 means no routing
  bool useVirtualKeyboardVelocity = true;
  float virtualFixedVelocity = 1.0f;

  // CC to parameter mapping
  struct CCMapping
  {
    int lpindex;
    int parameterIndex;
    int ccController;
    int midiChannel;  // -1 means any channel
  };
  std::vector<CCMapping> ccMappings;

  ~CompletePluginHost()
  
  {
    cout << "DESTRUCTOR CALLED - shutting down" << endl;
    stopTimer();
    shutdownAudio();
    processorGraph = nullptr;
#ifdef _WIN32
    CloseHandle(hCommandPipe);
    CloseHandle(hNotificationPipe);
#else
    close(commandPipe_fd);
    close(notificationPipe_fd);
    unlink(commandPipePath.c_str());
    unlink(notificationPipePath.c_str())
#endif
  }

  unordered_map<int, juce::AudioProcessorGraph::NodeID> loadedPlugins; 
  atomic<bool> running = true;

#define WRITEALLC(...) writeAllc(__VA_ARGS__)
#define WRITEALLN(...) writeAlln(__VA_ARGS__)

  void timerCallback() override {
    // DON'T call processParameterNotifications() here anymore!

    // Just handle UI updates
    for (auto it = pluginWindows.begin(); it != pluginWindows.end();) {
      if (!it->second || !it->second->isVisible()) {
        it = pluginWindows.erase(it);
      } else {
        ++it;
      }
    }
  }

  // Add this method to queue notifications for the command thread
  void queueParameterNotification(const ParameterChangeEvent& event) 
  {
    cout << "queing parameter notification" << endl;

    lock_guard<std::mutex> lock(notificationMutex);
    pendingNotifications.push(event);
  }

  // Call this from command thread instead
  void sendQueuedParameterNotifications() {
    std::lock_guard<std::mutex> lock(notificationMutex);
    while (!pendingNotifications.empty())
    {
      const auto& event = pendingNotifications.front();
      cout << "sending parameter notification" << " lpindex: " << event.lpindex << " parameterIndex: " << event.parameterIndex << " value: " << event.value << " atBlock: " << event.atBlock << endl;
      WRITEALLN(param_changed, event.lpindex, event.parameterIndex, event.value, event.atBlock);
#ifdef _WIN32
      if (GetLastError() == ERROR_BROKEN_PIPE) {
        // Pipe disconnected
        notificationPipeReady = false;
      }
#else
      if (errno == EPIPE) {
        // Pipe disconnected (broken pipe)
        notificationPipeReady = false;
      }
#endif
      if (notificationPipeReady)
        pendingNotifications.pop();
      else
        cout << "notificationPipeReady = false" << endl; //could probably confuse this with an error generated by the command pipe failing!
    }
  }

  void sendQueuedMidiNotifications() {
    // Send MIDI note events
    MidiNoteEvent noteEvent;
    while (midiNoteQueue.pop(noteEvent))
    {
      if (!notificationPipeReady) break;
      WRITEALLN(midi_note_event, noteEvent.noteNumber, noteEvent.velocity,
                noteEvent.channel, uint32_t(noteEvent.isNoteOn), noteEvent.samplePosition);
#ifdef _WIN32
      if (GetLastError() == ERROR_BROKEN_PIPE) {
        notificationPipeReady = false;
        break;
      }
#else
      if (errno == EPIPE) {
        notificationPipeReady = false;
        break;
      }
#endif
    }

    // Send MIDI CC events
    MidiCCEvent ccEvent;
    while (midiCCQueue.pop(ccEvent))
    {
      if (!notificationPipeReady) break;
      WRITEALLN(midi_cc_event, ccEvent.controller, ccEvent.value, ccEvent.channel, ccEvent.atBlock);
#ifdef _WIN32
      if (GetLastError() == ERROR_BROKEN_PIPE) {
        notificationPipeReady = false;
        break;
      }
#else
      if (errno == EPIPE) {
        notificationPipeReady = false;
        break;
      }
#endif
    }

    // Send virtual keyboard note events (separate from physical keyboard)
    MidiNoteEvent virtualNoteEvent;
    while (virtualKeyboardNoteQueue.pop(virtualNoteEvent))
    {
      if (!notificationPipeReady) break;
      WRITEALLN(virtual_keyboard_note_event, virtualNoteEvent.noteNumber, virtualNoteEvent.velocity,
                virtualNoteEvent.channel, uint32_t(virtualNoteEvent.isNoteOn), virtualNoteEvent.samplePosition);
#ifdef _WIN32
      if (GetLastError() == ERROR_BROKEN_PIPE) {
        notificationPipeReady = false;
        break;
      }
#else
      if (errno == EPIPE) {
        notificationPipeReady = false;
        break;
      }
#endif
    }

    // Send virtual keyboard CC events (for future use)
    MidiCCEvent virtualCCEvent;
    while (virtualKeyboardCCQueue.pop(virtualCCEvent))
    {
      if (!notificationPipeReady) break;
      WRITEALLN(virtual_keyboard_cc_event, virtualCCEvent.controller, virtualCCEvent.value,
                virtualCCEvent.channel, virtualCCEvent.atBlock);
#ifdef _WIN32
      if (GetLastError() == ERROR_BROKEN_PIPE) {
        notificationPipeReady = false;
        break;
      }
#else
      if (errno == EPIPE) {
        notificationPipeReady = false;
        break;
      }
#endif
    }
  }

  void setupAudioIO()
  {
    // Add audio output node (required for hearing anything)
    audioOutputNode = processorGraph->addNode(
      std::make_unique<juce::AudioProcessorGraph::AudioGraphIOProcessor>(
        juce::AudioProcessorGraph::AudioGraphIOProcessor::audioOutputNode))->nodeID;

    // Add audio input node (optional, for processing external audio)
    audioInputNode = processorGraph->addNode(
      std::make_unique<juce::AudioProcessorGraph::AudioGraphIOProcessor>(
        juce::AudioProcessorGraph::AudioGraphIOProcessor::audioInputNode))->nodeID;

    loadedPlugins[outputIndex] = audioOutputNode;  // Special ID for audio output
    loadedPlugins[inputIndex] = audioInputNode;   // Special ID for audio input
  }

  void testFunction() {
    std::cout << "Test function called successfully" << std::endl;
  }
    
  int findlpindex(juce::AudioProcessor* processor)
  {
    auto it = processorTolpindex.find(processor);
    if (it != processorTolpindex.end()) {
      return it->second;
    }
    return -1;  // Not found
  }

  void initialise(const juce::String& commandLine) {
    std::cout << "initialise: start, app=" << app << std::endl;

    std::cout << "initialise: creating thread, app=" << app << std::endl;
    commandThread = thread([this]() { 
      
      cout << "Thread lambda: started, global app=" << app << std::endl;
      processCommands(); 
      std::cout << "Thread lambda: ended, global app=" << app << std::endl;
      });

    std::cout << "initialise: thread created, app=" << app << std::endl;

    // Check if something here is setting app to null
    std::cout << "initialise: end, app=" << app << std::endl;

    notificationThread = thread([this]() { notificationLoop(); });
  }



  /*void initialise(const juce::String& commandLine)
  {
    // Start command processing thread
    commandThread = thread([this]() { processCommands(); });
  }
  */

  void waitForConnection() 
  {
    std::cout << "Entering waitForConnection()" << std::endl;
#ifdef _WIN32
    if (hCommandPipe == INVALID_HANDLE_VALUE) {
      std::cerr << "ERROR: Invalid pipe handle!" << std::endl;
      return;
    }
    std::cout << "Pipe created, waiting for connection in command thread..." << std::endl;
#else
    std::cout << "FIFO created, waiting for connection in command thread..." << std::endl;
#endif
    // Don't connect here - let processCommands handle it
  }

  void setPluginParameter(int lpindex, int parameterIndex, float value) 
  {
    auto it = loadedPlugins.begin();
    advance(it, lpindex);  // Move iterator to lpindex position
    if (it != loadedPlugins.end()) 
    {
      auto node = processorGraph->getNodeForId(it->second);
      if (node && node->getProcessor()) 
      {
        auto* processor = node->getProcessor();
        if (parameterIndex >= 0 && parameterIndex < processor->getParameters().size()) 
        {
          suppressNotifications = true;  // Prevent recording our own changes
          processor->getParameters()[parameterIndex]->setValue(value);
          suppressNotifications = false;
        }
      }
    }
  }

  void shutdown()
  {
    running = false;
    commandCv.notify_all();

    if (commandThread.joinable())
      commandThread.join();
    if (notificationThread.joinable()) 
      notificationThread.join();

    // Close all plugin windows
    for (auto& pair : pluginWindows)
    {
      if (pair.second)
        pair.second->closeButtonPressed();
    }
    pluginWindows.clear();
  }

  // Public method to handle virtual keyboard MIDI (called by VirtualKeyboardListener)
  void handleVirtualKeyboardMessage(const juce::MidiMessage& message)
  {
    bool shouldRouteToPlugin = false;
    juce::MidiMessage routedMessage = message;

    // Send MIDI note events to VIRTUAL KEYBOARD notification queue (separate from physical keyboard)
    if (message.isNoteOn() || message.isNoteOff())
    {
      MidiNoteEvent noteEvent;
      noteEvent.noteNumber = message.getNoteNumber();
      noteEvent.velocity = message.getVelocity();
      noteEvent.channel = message.getChannel();
      noteEvent.isNoteOn = message.isNoteOn();
      noteEvent.samplePosition = currentSamplePosition;
      virtualKeyboardNoteQueue.push(noteEvent);  // Push to virtual keyboard queue, not regular MIDI queue
    }

    // Handle virtual keyboard routing for notes
    if (virtualKeyboardRoutedPlugin >= 0 && (message.isNoteOn() || message.isNoteOff()))
    {
      shouldRouteToPlugin = true;

      // Apply velocity handling for note on messages
      if (message.isNoteOn() && !useVirtualKeyboardVelocity)
      {
        routedMessage = juce::MidiMessage::noteOn(
          message.getChannel(),
          message.getNoteNumber(),
          static_cast<juce::uint8>(virtualFixedVelocity * 127.0f)
        );
      }
    }

    // Handle pitch bend routing (when virtual keyboard is routed)
    if (virtualKeyboardRoutedPlugin >= 0 && message.isPitchWheel())
    {
      shouldRouteToPlugin = true;
    }

    // Handle aftertouch routing (when virtual keyboard is routed)
    if (virtualKeyboardRoutedPlugin >= 0 && (message.isAftertouch() || message.isChannelPressure()))
    {
      shouldRouteToPlugin = true;
    }

    // Route to the specific plugin if virtual keyboard routing is active
    if (shouldRouteToPlugin)
    {
      if (midiCollector)
        midiCollector->addMessageToQueue(routedMessage);
    }
  }

  // Toggle MIDI keyboard routing for a plugin
  void toggleMidiKeyboardRouting(int lpindex)
  {
    cout << "toggleMidiKeyboardRouting called with lpindex=" << lpindex << ", current keyboardRoutedPlugin=" << keyboardRoutedPlugin << endl;
    if (keyboardRoutedPlugin == lpindex)
    {
      // Unroute if already routed to this plugin
      keyboardRoutedPlugin = -3;
      cout << "MIDI keyboard unrouted from plugin " << lpindex << endl;
    }
    else
    {
      // Route to this plugin
      keyboardRoutedPlugin = lpindex;
      cout << "MIDI keyboard routed to plugin " << lpindex << endl;
    }

    cout << "About to send WRITEALLN notification: midi_keyboard_routed, lpindex=" << keyboardRoutedPlugin << ", samplePos=" << currentSamplePosition << endl;
    // Send notification to Python client
    WRITEALLN(midi_keyboard_routed, keyboardRoutedPlugin, currentSamplePosition);
    cout << "WRITEALLN notification sent successfully" << endl;

    cout << "About to update all routing indicators" << endl;
    // Update all plugin windows to reflect the routing change
    updateAllRoutingIndicators();
    cout << "toggleMidiKeyboardRouting completed" << endl;
  }

  // Toggle virtual keyboard routing for a plugin
  void toggleVirtualKeyboardRouting(int lpindex)
  {
    cout << "toggleVirtualKeyboardRouting called with lpindex=" << lpindex << ", current virtualKeyboardRoutedPlugin=" << virtualKeyboardRoutedPlugin << endl;
    if (virtualKeyboardRoutedPlugin == lpindex)
    {
      // Unroute if already routed to this plugin
      virtualKeyboardRoutedPlugin = -1;
      cout << "Virtual keyboard unrouted from plugin " << lpindex << endl;

      // Hide the virtual keyboard window when unrouted
      if (virtualKeyboardWindow)
      {
        virtualKeyboardWindow->setVisible(false);
        cout << "Virtual keyboard window hidden after unrouting" << endl;
      }
    }
    else
    {
      // Route to this plugin
      virtualKeyboardRoutedPlugin = lpindex;
      cout << "Virtual keyboard routed to plugin " << lpindex << endl;

      // Show the virtual keyboard window when routed
      if (!virtualKeyboardWindow)
      {
        virtualKeyboardWindow = std::make_unique<VirtualKeyboardWindow>(virtualKeyboardState);
        virtualKeyboardWindow->setVisible(true);
        cout << "Virtual keyboard window created and shown" << endl;
      }
      else
      {
        virtualKeyboardWindow->setVisible(true);
        virtualKeyboardWindow->toFront(true);
        cout << "Virtual keyboard window shown and brought to front" << endl;
      }
    }

    cout << "About to send WRITEALLN notification: virtual_keyboard_routed, lpindex=" << virtualKeyboardRoutedPlugin << ", samplePos=" << currentSamplePosition << endl;
    // Send notification to Python client
    WRITEALLN(virtual_keyboard_routed, virtualKeyboardRoutedPlugin, currentSamplePosition);
    cout << "WRITEALLN notification sent successfully" << endl;

    cout << "About to update all routing indicators" << endl;
    // Update all plugin windows to reflect the routing change
    updateAllRoutingIndicators();
    cout << "toggleVirtualKeyboardRouting completed" << endl;
  }

  // Update routing indicators on all plugin windows
  void updateAllRoutingIndicators()
  {
    cout << "updateAllRoutingIndicators called, pluginWindows.size()=" << pluginWindows.size() << endl;
    juce::MessageManager::callAsync([this]() {
      cout << "updateAllRoutingIndicators async callback executing" << endl;
      int count = 0;
      for (auto& pair : pluginWindows)
      {
        cout << "  Updating window for lpindex=" << pair.first << ", window ptr=" << (void*)pair.second.get() << endl;
        if (pair.second)
        {
          pair.second->updateRoutingIndicators(keyboardRoutedPlugin, virtualKeyboardRoutedPlugin);
          count++;
        }
      }
      cout << "  Updated " << count << " plugin windows" << endl;
    });
    cout << "updateAllRoutingIndicators callAsync scheduled" << endl;
  }

  // Find next available recording filename
  std::string findNextRecordingFilename()
  {
    int index = 1;
    while (true)
    {
      std::string filename = "record_" + std::to_string(index) + ".wav";
      juce::File file = juce::File::getCurrentWorkingDirectory().getChildFile(filename);
      if (!file.existsAsFile())
      {
        cout << "Next available recording filename: " << filename << endl;
        return filename;
      }
      index++;
    }
  }

  // Toggle recording on/off
  void toggleRecording()
  {
    if (isRecording)
    {
      stopRecording();
    }
    else
    {
      startRecording();
    }
  }

  // Start recording
  void startRecording()
  {
    if (isRecording)
    {
      cout << "Already recording" << endl;
      return;
    }

    currentRecordingFilename = findNextRecordingFilename();
    currentRecordingFile = juce::File::getCurrentWorkingDirectory().getChildFile(currentRecordingFilename);

    // Create WAV file writer
    juce::WavAudioFormat wavFormat;
    std::unique_ptr<juce::FileOutputStream> outputStream(currentRecordingFile.createOutputStream());

    if (outputStream != nullptr)
    {
      audioWriter.reset(wavFormat.createWriterFor(
        outputStream.get(),
        sampleRate,
        2,  // stereo
        16, // 16-bit
        {},
        0));

      if (audioWriter != nullptr)
      {
        outputStream.release(); // Writer now owns the stream
        isRecording = true;
        recordingStartSample = currentSamplePosition;

        cout << "Recording started: file=" << currentRecordingFilename
             << ", startSample=" << recordingStartSample
             << ", sampleRate=" << sampleRate << endl;

        // Send notification
        WRITEALLN(recording_started, currentRecordingFilename, recordingStartSample);
      }
      else
      {
        cout << "ERROR: Failed to create audio writer" << endl;
      }
    }
    else
    {
      cout << "ERROR: Failed to create output stream for " << currentRecordingFilename << endl;
    }
  }

  // Stop recording
  void stopRecording()
  {
    if (!isRecording)
    {
      cout << "Not currently recording" << endl;
      return;
    }

    audioWriter.reset();
    isRecording = false;

    cout << "Recording stopped: file=" << currentRecordingFilename
         << ", duration=" << (currentSamplePosition - recordingStartSample) << " samples" << endl;

    // Send notification
    WRITEALLN(recording_stopped, currentRecordingFilename, currentSamplePosition);
  }

  // Toggle monitoring on/off
  void toggleMonitoring()
  {
    isMonitoring = !isMonitoring;
    cout << "Monitoring " << (isMonitoring ? "enabled" : "disabled") << endl;

    // Send notification
    WRITEALLN(monitoring_changed, isMonitoring, currentSamplePosition);
  }

  // Capture audio for recording (called from audio callback)
  void captureAudioForRecording(float* const* outputChannelData, int numChannels, int numSamples)
  {
    if (!isRecording || !audioWriter)
      return;

    // Create a temporary buffer to hold the audio
    juce::AudioBuffer<float> tempBuffer(numChannels, numSamples);

    // Copy from the output channels
    for (int ch = 0; ch < numChannels && ch < 2; ++ch)
    {
      tempBuffer.copyFrom(ch, 0, outputChannelData[ch], numSamples);
    }

    // Write to file
    audioWriter->writeFromAudioSampleBuffer(tempBuffer, 0, numSamples);
  }

  // Ordered note playback functions
  void scheduleOrderedNote(int orderNumber, int noteNumber, int velocity, int channel, int duration)
  {
    OrderedNote note;
    note.orderNumber = orderNumber;
    note.noteNumber = noteNumber;
    note.velocity = velocity;
    note.channel = channel;
    note.duration = duration;

    orderedNotes.push_back(note);
    cout << "Scheduled ordered note: order=" << orderNumber
         << ", note=" << noteNumber << ", velocity=" << velocity << endl;
  }

  void sortOrderedNotes()
  {
    // Sort by order number
    std::sort(orderedNotes.begin(), orderedNotes.end(),
      [](const OrderedNote& a, const OrderedNote& b) { return a.orderNumber < b.orderNumber; });
    cout << "Sorted " << orderedNotes.size() << " ordered notes" << endl;
  }

  void clearOrderedNotes()
  {
    orderedNotes.clear();
    currentOrderIndex = 0;
    cout << "Cleared ordered notes" << endl;
  }

  void startOrderedPlayback(bool useKeyboardVelocity = true, bool useKeyboardDuration = true)
  {
    if (orderedNotes.empty())
    {
      cout << "WARNING: No ordered notes to play" << endl;
      return;
    }

    useKeyboardVelocityForOrdered = useKeyboardVelocity;
    useKeyboardDurationForOrdered = useKeyboardDuration;
    activeOrderedNotes.clear();

    if (isPlaying)
    {
      orderedPlaybackActive = true;
      orderedPlaybackScheduled = false;
      currentOrderIndex = 0;
      cout << "Started ordered playback immediately (keyboard velocity: "
           << useKeyboardVelocity << ", keyboard duration: " << useKeyboardDuration << ")" << endl;
      WRITEALLN(ordered_playback_started, currentSamplePosition);
    }
    else
    {
      orderedPlaybackScheduled = true;
      orderedPlaybackActive = false;
      currentOrderIndex = 0;
      cout << "Ordered playback scheduled to start with audio playback (keyboard velocity: "
           << useKeyboardVelocity << ", keyboard duration: " << useKeyboardDuration << ")" << endl;
      WRITEALLN(ordered_playback_started, int64_t(0));
    }
  }

  void stopOrderedPlayback()
  {
    orderedPlaybackActive = false;
    orderedPlaybackScheduled = false;
    cout << "Stopped ordered playback at index " << currentOrderIndex << endl;
    WRITEALLN(ordered_playback_stopped, currentSamplePosition);
  }

  void triggerNextOrderedNotes(int keyboardVelocity)
  {
    if (!orderedPlaybackActive || currentOrderIndex >= orderedNotes.size())
      return;

    // Get the current order number
    int currentOrder = orderedNotes[currentOrderIndex].orderNumber;

    // Play all notes with the same order number
    vector<int> triggeredNotes;
    while (currentOrderIndex < orderedNotes.size() &&
           orderedNotes[currentOrderIndex].orderNumber == currentOrder)
    {
      const auto& note = orderedNotes[currentOrderIndex];

      // Use keyboard velocity or stored velocity
      int velocity = useKeyboardVelocityForOrdered ? keyboardVelocity : note.velocity;

      // Schedule the note on
      juce::MidiMessage noteOn = juce::MidiMessage::noteOn(note.channel, note.noteNumber, (uint8_t)velocity);
      scheduler.scheduleMidiEvent(noteOn, currentSamplePosition);

      // Handle duration
      if (useKeyboardDurationForOrdered)
      {
        // Track this note to turn off on keyboard release
        ActiveOrderedNote activeNote;
        activeNote.noteNumber = note.noteNumber;
        activeNote.channel = note.channel;
        activeNote.orderNumber = currentOrder;
        activeOrderedNotes.push_back(activeNote);
      }
      else
      {
        // Use stored duration - schedule note off immediately
        juce::MidiMessage noteOff = juce::MidiMessage::noteOff(note.channel, note.noteNumber);
        scheduler.scheduleMidiEvent(noteOff, currentSamplePosition + note.duration);
      }

      triggeredNotes.push_back(note.noteNumber);
      cout << "Triggered ordered note: order=" << currentOrder
           << ", note=" << note.noteNumber
           << ", velocity=" << velocity
           << ", duration=" << (useKeyboardDurationForOrdered ? "keyboard" : std::to_string(note.duration)) << endl;

      currentOrderIndex++;
    }

    // Send notification with all triggered notes
    WRITEALLN(ordered_note_triggered, currentOrder, currentOrderIndex, (int)orderedNotes.size());

    // Check if we've finished the sequence
    if (currentOrderIndex >= orderedNotes.size())
    {
      cout << "Ordered playback sequence completed" << endl;
      orderedPlaybackActive = false;
      WRITEALLN(ordered_playback_stopped, currentSamplePosition);
    }
  }

  void handleOrderedNoteOff()
  {
    // Turn off all currently active ordered notes when keyboard key is released
    if (!useKeyboardDurationForOrdered || activeOrderedNotes.empty())
      return;

    for (const auto& activeNote : activeOrderedNotes)
    {
      juce::MidiMessage noteOff = juce::MidiMessage::noteOff(activeNote.channel, activeNote.noteNumber);
      scheduler.scheduleMidiEvent(noteOff, currentSamplePosition);
      cout << "Released ordered note: note=" << activeNote.noteNumber << endl;
    }

    activeOrderedNotes.clear();
  }

  void handleIncomingMidiMessage(juce::MidiInput* source,
    const juce::MidiMessage& message) override
  {
    bool shouldRouteToPlugin = false;
    juce::MidiMessage routedMessage = message;

    MidiNoteEvent noteEvent;
    noteEvent.noteNumber = message.getNoteNumber();
    noteEvent.velocity = routedMessage.getVelocity();
    noteEvent.channel = message.getChannel();
    noteEvent.isNoteOn = message.isNoteOn();
    noteEvent.samplePosition = currentSamplePosition;

    midiNoteQueue.push(noteEvent);

    // Check if ordered playback is active
    if (orderedPlaybackActive)
    {
      if (message.isNoteOn())
      {
        // Consume the next ordered note(s) instead of routing the actual key pressed
        triggerNextOrderedNotes(message.getVelocity());
        return;  // Don't route the actual MIDI message
      }
      else if (message.isNoteOff())
      {
        // Handle note-off for keyboard-controlled duration
        handleOrderedNoteOff();
        return;  // Don't route the actual MIDI message
      }
    }

    // Handle keyboard routing for notes
    if (keyboardRoutedPlugin >= 0 && (message.isNoteOn() || message.isNoteOff()))
    {
      shouldRouteToPlugin = true;

      // Apply velocity handling for note on messages
      if (message.isNoteOn() && !useKeyboardVelocity)
      {
        routedMessage = juce::MidiMessage::noteOn(
          message.getChannel(),
          message.getNoteNumber(),
          static_cast<juce::uint8>(fixedVelocity * 127.0f)
        );
      }

      // Queue notification event

    }

    // Handle pitch bend routing (when keyboard is routed)
    if (keyboardRoutedPlugin >= 0 && message.isPitchWheel())
    {
      shouldRouteToPlugin = true;
    }

    // Handle aftertouch routing (when keyboard is routed)
    if (keyboardRoutedPlugin >= 0 && (message.isAftertouch() || message.isChannelPressure()))
    {
      shouldRouteToPlugin = true;
    }

    // Route to the specific plugin if keyboard routing is active
    if (shouldRouteToPlugin)
    {
      if (midiCollector)
        midiCollector->addMessageToQueue(routedMessage);
    }

    // Handle CC to parameter mapping
    if (message.isController())
    {
      int cc = message.getControllerNumber();
      int value = message.getControllerValue();
      int channel = message.getChannel();

      // Queue CC notification event
      MidiCCEvent ccEvent;
      ccEvent.controller = cc;
      ccEvent.value = value;
      ccEvent.channel = channel;
      ccEvent.atBlock = scheduler.getCurrentBlock() + 1;  // Effect takes place in next block
      midiCCQueue.push(ccEvent);

      // Apply CC to parameter mappings
      for (const auto& mapping : ccMappings)
      {
        if (mapping.ccController == cc &&
            (mapping.midiChannel == -1 || mapping.midiChannel == channel))
        {
          auto it = loadedPlugins.find(mapping.lpindex);
          if (it != loadedPlugins.end())
          {
            auto node = processorGraph->getNodeForId(it->second);
            if (node && node->getProcessor())
            {
              auto* processor = node->getProcessor();
              const auto& params = processor->getParameters();

              if (mapping.parameterIndex >= 0 && mapping.parameterIndex < params.size())
              {
                auto* param = params[mapping.parameterIndex];

                // Normalize CC value (0-127) to 0.0-1.0
                float normalizedValue = value / 127.0f;

                // If it's a RangedAudioParameter, we can use the range
                if (auto* rangedParam = dynamic_cast<juce::RangedAudioParameter*>(param))
                {
                  auto range = rangedParam->getNormalisableRange();
                  // Convert normalized value to actual parameter value using the range
                  float actualValue = range.convertFrom0to1(normalizedValue);
                  // Set the parameter (setValue expects normalized 0-1 value)
                  param->setValue(normalizedValue);
                }
                else
                {
                  // For non-ranged parameters, just use the normalized value
                  param->setValue(normalizedValue);
                }
              }
            }
          }
        }
      }
    }

    // Always route to midiCollector for general MIDI handling
    if (midiCollector)
      midiCollector->addMessageToQueue(message);
  }

  void processScheduledEvents()  // Called from audio thread
  {
    if (isPlaying)
    {
      scheduler.processScheduledChanges();

      // Check if we've reached the end
      if (scheduler.getCurrentBlock() >= playbackEndBlock)
      {
        stopPlayback();
      }

      scheduler.incrementBlock();
    }
  }

  private:

  bool offlineMode = true;
  uint64_t playbackEndBlock = 0;
  queue<ParameterChangeEvent> pendingNotifications;
  mutex notificationMutex;

  int write4c(void* buff, int n)
  {
#ifdef _WIN32
    DWORD bytesWritten;
    if (WriteFile(hCommandPipe, buff, n, &bytesWritten, NULL)) {
      return bytesWritten;
    }
    return -1;  // Error
#else
    return write(commandPipe_fd, buff, n);
#endif
  }

  int write4n(void* buff, int n)
  {
#ifdef _WIN32
    DWORD bytesWritten;
    if (WriteFile(hNotificationPipe, buff, n, &bytesWritten, NULL)) {
      return bytesWritten;
    }
    return -1;  // Error
#else
    return write(notificationPipe_fd, buff, n);
#endif
  }

  void write2c_string(const string& s)
  {
    int l1 = static_cast<int>(s.length());
    int l2 = 4 + l1;  // 4 bytes for length + string content
    int tosend = l2;

    char* buff = new char[l2];

    // Copy length as first 4 bytes
    memcpy(buff, &l1, 4);
    // Copy string content
    memcpy(buff + 4, s.c_str(), l1);

    // Send all data, handling partial writes
    char* current_pos = buff;
    while (tosend > 0) {
      int byteswritten = write4c(current_pos, tosend);
      if (byteswritten > 0) {
        tosend -= byteswritten;
        current_pos += byteswritten;
      }
      else 
      {
#ifdef _WIN32
        throw runtime_error("Error writing to pipe (Windows error: " + to_string(GetLastError()) + ")");
#else
        throw runtime_error("Error writing to pipe (errno: " + to_string(errno) + ")";)
#endif
          break;  // Exit on error
      }
    }
    delete[] buff;
  }

 
  // Write binary data for non-string types
  template<typename T>
  void write2c_binary(T n)
  {
    int tosend = sizeof(T);
    char* current_pos = reinterpret_cast<char*>(&n);

    while (tosend > 0) {
      int byteswritten = write4c(current_pos, tosend);
      if (byteswritten > 0) {
        tosend -= byteswritten;
        current_pos += byteswritten;
      }
      else 
      {
#ifdef _WIN32
        throw runtime_error("Error writing to pipe (Windows error: " + to_string(GetLastError()) + ")");
#else
        throw runtime_error("Error writing to pipe (errno: " + to_string(errno) + ")";)
#endif
          break;  // Exit on error
      }
    }
  }

  // Write binary data for non-string types
  template<typename T>
  void write2n_binary(T n)
  {
    int tosend = sizeof(T);
    char* current_pos = reinterpret_cast<char*>(&n);

    while (tosend > 0) {
      int byteswritten = write4n(current_pos, tosend);
      if (byteswritten > 0) {
        tosend -= byteswritten;
        current_pos += byteswritten;
      }
      else 
      {
#ifdef _WIN32
        throw runtime_error("Error writing to pipe (Windows error: " + to_string(GetLastError()) + ")");
#else
        throw runtime_error("Error writing to pipe (errno: " + to_string(errno) + ")";)
#endif
          break;  // Exit on error
      }
    }
  }
    
  
  // Overloaded write2 functions
  inline void write2c(const string& s)
  {
    write2c_string(s);
  }

  template<typename T>
  inline void write2c(T n)
  {
    write2c_binary(n);
  }

  template<typename T>
  inline void write2n(T n)
  {
    write2n_binary(n);
  }
    
  template<typename... Args>
  void writeAllc(Args&&... args) 
  {
    ((write2c(std::forward<Args>(args))), ...);  // C++17 fold expression
  }

  template<typename... Args>
  void writeAlln(Args&&... args) 
  {
    ((write2n(std::forward<Args>(args))), ...);  // C++17 fold expression
  }

  // Template function to read any type from pipe
  template<typename T>
  T readFromPipe() 
  {
    T value;
#ifdef _WIN32
    char* buffer = reinterpret_cast<char*>(&value);
    int totalRead = 0;
    while (totalRead < sizeof(T)) {
      DWORD bytesRead;
      if (!ReadFile(hCommandPipe, buffer + totalRead, 
        sizeof(T) - totalRead, &bytesRead, NULL)) {
        throw runtime_error("ReadFile failed: " + to_string(GetLastError()));
      }
      if (bytesRead == 0) {
        throw runtime_error("Pipe closed");
      }
      totalRead += bytesRead;
    }
    return value;
#else
    if (read(pipe_fd, &value, sizeof(T)) == sizeof(T)) {
      return value;
    }
    throw runtime_error("couldn't read from pipe");
#endif
  }

  // Specialization for strings (reads length-prefixed strings)
  template<>
  string readFromPipe<string>() {
    // Read 4-byte length first
    uint32_t length = readFromPipe<uint32_t>();
    if (length <= 0 || length > 10000) {  // Sanity check
      throw runtime_error("invalid string length");
    }

    char* buffer = new char[length + 1];  // +1 for null terminator
    int totalRead = 0;

#ifdef _WIN32
    while (totalRead < length) {
      DWORD bytesRead;
      if (!ReadFile(hCommandPipe, buffer + totalRead, length - totalRead, &bytesRead, NULL)) {
        delete[] buffer;
        throw runtime_error("ReadFile failed: " + to_string(GetLastError()));
      }
      if (bytesRead == 0) {
        delete[] buffer;
        throw runtime_error("pipe closed");
      }
      totalRead += bytesRead;
    }
#else
    while (totalRead < length) {
      ssize_t bytesRead = read(commandPipe_fd, buffer + totalRead, length - totalRead);
      if (bytesRead < 0) {
        delete[] buffer;
        throw runtime_error("read failed: " + string(strerror(errno)));
      }
      if (bytesRead == 0) {
        delete[] buffer;
        throw runtime_error("pipe closed");
      }
      totalRead += bytesRead;
    }
#endif

    // Create string with explicit length to handle embedded nulls
    string result(buffer, length);
    delete[] buffer;
    return result;
  }

#define READFROMPIPE(type) readFromPipe<type>()

  void renderToFile(uint64_t endBlock, string outputFile)
  {
    juce::File file(outputFile);
    juce::WavAudioFormat wavFormat;

    audioFileWriter.reset(wavFormat.createWriterFor(
      new juce::FileOutputStream(file),
      sampleRate,
      2,  // stereo
      16, // bit depth
      {},
      0
    ));

    const int samplesPerBlock = 512;
    juce::AudioBuffer<float> buffer(2, samplesPerBlock);
    juce::MidiBuffer midiBuffer;

    // Calculate total samples needed
    uint64_t totalBlocks = endBlock;

    for (uint64_t block = 0; block < totalBlocks; ++block)
    {
      buffer.clear();
      midiBuffer.clear();

      // Process scheduled changes
      scheduler.processScheduledChanges();

      // Process scheduled MIDI
      midiScheduler->processBlock(midiBuffer, samplesPerBlock);

      // Process the graph
      processorGraph->processBlock(buffer, midiBuffer);

      // Write to file
      if (audioFileWriter)
      {
        audioFileWriter->writeFromAudioSampleBuffer(buffer, 0, samplesPerBlock);
      }

      scheduler.incrementBlock();
    }

    // Flush and close file
    audioFileWriter.reset();
    cout << "Offline rendering complete" << endl;
  }

  unordered_map<int, juce::AudioProcessorGraph::NodeID> midiSourceNodes;  // lpindex -> MIDI source node
  unique_ptr<MidiScheduler> midiScheduler;
  juce::AudioPluginFormatManager formatManager;
  unordered_map<int, juce::AudioProcessorGraph::NodeID> midiRouterNodes;
  juce::AudioProcessorGraph::NodeID audioOutputNode;
  juce::AudioProcessorGraph::NodeID audioInputNode;
  std::unique_ptr<juce::AudioFormatWriter> audioFileWriter;

  void notificationLoop() {
#ifdef _WIN32
    // Wait for Python to connect to notification pipe
    cout << "Waiting for notification pipe connection..." << endl;
    if (!ConnectNamedPipe(hNotificationPipe, NULL) && GetLastError() != ERROR_PIPE_CONNECTED) {
      cerr << "Failed to connect notification pipe" << endl;
      return;
    }
    cout << "Notification pipe connected" << endl;
    notificationPipeReady = true;
#endif

    while (running) {
      sendQueuedParameterNotifications();
      sendQueuedMidiNotifications();
      std::this_thread::sleep_for(std::chrono::milliseconds(5));
    }
  }

  void shutdownAudio()
  {
    if (recordingCallback)
      deviceManager.removeAudioCallback(recordingCallback.get());
    deviceManager.closeAudioDevice();
    graphPlayer.setProcessor(nullptr);
    recordingCallback.reset();
  }

 
  void processCommands() {
    std::cout << "Thread: processCommands started" << endl;

    while (running) {
      try {
        // Wait for pipe to be ready
        cout << "Thread: Waiting for pipe connection..." << endl;
        commandPipeReady = false;

#ifdef _WIN32
        // Disconnect any existing client
        if (hCommandPipe != INVALID_HANDLE_VALUE) {
          DisconnectNamedPipe(hCommandPipe);
        }

        // Wait for new connection
        cout << "Waiting for client to connect..." << endl;
        if (ConnectNamedPipe(hCommandPipe, NULL) || GetLastError() == ERROR_PIPE_CONNECTED) {
          cout << "Client connected" << endl;
          commandPipeReady = true;
        } else {
          cerr << "ConnectNamedPipe failed: " << GetLastError() << endl;
          this_thread::sleep_for(chrono::seconds(1));
          continue;
        }
#else
        // Linux - reopen the FIFO if needed
        if (commandPipe_fd >= 0) {
          close(commandPipe_fd);
        }

        string commandPipePath = "/tmp/" + pipeName + "_commands";
        commandPipe_fd = open(commandPipePath.c_str(), O_RDWR);
        if (commandPipe_fd < 0) {
          cerr << "Failed to open FIFO: " << strerror(errno) << endl;
          this_thread::sleep_for(chrono::seconds(1));
          continue;
        }
        commandPipeReady = true;
#endif

        // Process commands until disconnection
        cout << "Thread: Starting command loop" << endl;
        while (running && commandPipeReady) {
          try {
            char command = READFROMPIPE(char);
            processCommand(command);
          } catch (const exception& e) {
            // Pipe disconnected or read error
            cout << "Read error: " << e.what() << endl;
            commandPipeReady = false;
            break;  // Exit inner loop to reconnect
          }
        }

        cout << "Exited command loop. running=" << running 
          << " commandPipeReady=" << commandPipeReady << endl;

#ifdef _WIN32
        // Disconnect on Windows
        if (hCommandPipe != INVALID_HANDLE_VALUE) {
          DisconnectNamedPipe(hCommandPipe);
        }
#else
        // Close on Linux
        if (commandPipe_fd >= 0) {
          close(commandPipe_fd);
          commandPipe_fd = -1;
        }
#endif

        // Small delay before attempting reconnect
        if (running) {
          cout << "Waiting before reconnect attempt..." << endl;
          this_thread::sleep_for(chrono::seconds(1));
        }

      } catch (const exception& e) {
        cout << "Exception in processCommands: " << e.what() << endl;
        commandPipeReady = false;
        this_thread::sleep_for(chrono::seconds(1));
      } catch (...) {
        cout << "Unknown exception in processCommands" << endl;
        commandPipeReady = false;
        this_thread::sleep_for(chrono::seconds(1));
      }
    }

    cout << "Thread: processCommands ending" << endl;
  }
  struct paramInfo
  {
    uint32_t originalIndex;
    string name; 
    float minValue; //todo: I'm not really sure which ones of these should be integers.
    float maxValue;
    float interval;
    float skewFactor;
    float defaultValue;
    float value;
    uint32_t numSteps;
    uint32_t isDiscrete;
    uint32_t isBoolean;
    uint32_t isOrientationInverted;
    uint32_t isAutomatable;
    uint32_t isMetaParameter;
  };

  struct getParamsInfoR {
    uint32_t success = false;
    uint32_t originalNumParams = 0;
    vector<paramInfo> validParams;
    string errmsg;
  };

  getParamsInfoR getParamsInfo(int lpindex) 
  {
    getParamsInfoR resp;
    vector<paramInfo> validParams;

    auto it = loadedPlugins.find(lpindex);
    if (it != loadedPlugins.end()) {
      auto node = processorGraph->getNodeForId(it->second);
      cout << "Node pointer: " << node << endl;
      if (node && node->getProcessor()) {
        auto* processor = node->getProcessor();
        cout << "Processor pointer: " << processor << endl;
        cout << "Processor name: " << processor->getName().toStdString() << endl; //debug
        const auto& params = processor->getParameters();

        cout << "Params vector address: " << &params << endl;
        cout << "Params size: " << processor->getNumParameters() << endl;

        resp.originalNumParams = processor->getNumParameters();
        resp.success = true;

        for (int i = 0; i < resp.originalNumParams; i++) 
        {
          paramInfo paramR;
          auto* param = params[i];
          paramR.originalIndex = i;  // Store the actual index
          paramR.name = param->getName(1000).toStdString();
          paramR.defaultValue = param->getDefaultValue();
          paramR.numSteps = param->getNumSteps();
          paramR.isDiscrete = param->isDiscrete();
          paramR.isBoolean = param->isBoolean();
          paramR.isOrientationInverted = param->isOrientationInverted();
          paramR.isAutomatable = param->isAutomatable();
          paramR.isMetaParameter = param->isMetaParameter();
          paramR.value = param->getValue();

          // If you have RangedAudioParameter (more specific type)
          if (auto* rangedParam = dynamic_cast<juce::RangedAudioParameter*>(param)) 
          {
            auto range = rangedParam->getNormalisableRange();
            paramR.minValue = range.start;
            paramR.maxValue = range.end;
            paramR.interval = range.interval;
            paramR.skewFactor = range.skew;
          }
          // For AudioParameterFloat, AudioParameterInt, etc.

          bool isValid = true;
          if (paramR.minValue == paramR.maxValue) isValid = false;
          if (paramR.name.find("MIDI") != string::npos) isValid = false;
          if (paramR.numSteps == INT_MAX) isValid = false;

          // If you have RangedAudioParameter (more specific type)
          if (auto* rangedParam = dynamic_cast<juce::RangedAudioParameter*>(param)) 
          {
            auto range = rangedParam->getNormalisableRange();
            paramR.minValue = range.start;
            paramR.maxValue = range.end;
            paramR.interval = range.interval;
            paramR.skewFactor = range.skew;
          }

          if (isValid) 
          {

            cout << "paremeterinfo " << i << ": " << endl;
            cout << "originalIndex: " << paramR.originalIndex << " name: " << paramR.name << " defaultValue: " << paramR.defaultValue << " numSteps: " << paramR.numSteps 
              << " isDiscrete: " << paramR.isDiscrete << " isBoolean: " << paramR.isBoolean << " isOrientationInverted: " << paramR.isOrientationInverted << " isAutomatable: " 
              << paramR.isAutomatable << " isMetaParameter: " << paramR.isMetaParameter << " value: " << paramR.value << " minValue: " << paramR.minValue << " maxValue: " 
              << paramR.maxValue << " interval: " << paramR.interval << " skewFactor: " << paramR.skewFactor << " isValid: " << isValid << endl;


            validParams.push_back(paramR);
          }
        }
      }
    } 
    else 
    {
      resp.errmsg = "Plugin not loaded";
    }
    resp.validParams = validParams;
    return resp;
  }

  struct busR
  {
    uint32_t numChannels;
    vector<string> channelTypes;
    uint32_t isEnabled = true;
    string mainBusLayout;
  };

  struct getChannnelsInfoR {
    uint32_t success = false;
    uint32_t acceptsMidi = false;
    uint32_t producesMidi = false;
    vector<busR> inputBuses;
    vector<busR> outputBuses;
    string errmsg;
  };
  getChannnelsInfoR getChannelsInfo(int lpindex) 
  {
    getChannnelsInfoR resp;
   
    cout << "Looking for plugin with lpindex: " << lpindex << endl;
    auto it = loadedPlugins.find(lpindex);

    if (it != loadedPlugins.end()) {
      cout << "Found plugin in map" << endl;
      auto node = processorGraph->getNodeForId(it->second);
      cout << "Got node pointer: " << node << endl;

      if (node && node->getProcessor()) {
        cout << "node->getProcessor()" << endl;
        auto* processor = node->getProcessor();
        cout << "processor pointer = " << processor << endl;

        // MIDI capabilities
        cout << "processor->acceptsMidi()" << endl;
        resp.acceptsMidi = processor->acceptsMidi();
        cout << "acceptsMidi = " << resp.acceptsMidi << endl;

        cout << "processor->producesMidi()" << endl;
        resp.producesMidi = processor->producesMidi();
        cout << "producesMidi = " << resp.producesMidi << endl;

        cout << "processor->getBusesLayout()" << endl;
        auto layout = processor->getBusesLayout();

        // Input buses
        cout << "processor->getBusCount(true)" << endl;
        int inputBusCount = processor->getBusCount(true);
        cout << "Input bus count: " << inputBusCount << endl;

        for (int i = 0; i < inputBusCount; ++i) 
        {
          busR bus2;
          vector<string> channelTypes;

          cout << "processor->getBus(true, " << i << ")" << endl;
          auto* bus = processor->getBus(true, i);
          cout << "Bus pointer: " << bus << endl;

          cout << "bus->getNumberOfChannels()" << endl;
          int numChannels = bus->getNumberOfChannels();
          cout << "Number of channels: " << numChannels << endl;

          cout << "bus->getName()" << endl;
          string busName = bus->getName().toStdString();
          cout << "Bus name: " << busName << endl;

          cout << "bus->isEnabled()" << endl;
          bool isEnabled = bus->isEnabled();
          cout << "Is enabled: " << isEnabled << endl;

          cout << "bus->getCurrentLayout()" << endl;
          auto& inputBus = bus->getCurrentLayout();

          for (int chan = 0; chan < numChannels; chan++)
          {
            cout << "inputBus.getTypeOfChannel(" << chan << ")" << endl;
            auto channelType = inputBus.getTypeOfChannel(chan);

            cout << "getChannelTypeName()" << endl;
            string channelName = juce::AudioChannelSet::getChannelTypeName(channelType).toStdString();
            cout << "Channel " << chan << " name: " << channelName << endl;

            channelTypes.push_back(channelName);
          }

          bus2.channelTypes = channelTypes;
          bus2.numChannels = numChannels;
          bus2.isEnabled = isEnabled;

          cout << "inputBus.getDescription()" << endl;
          bus2.mainBusLayout = inputBus.getDescription().toStdString();
          cout << "Main bus layout: " << bus2.mainBusLayout << endl;

          resp.inputBuses.push_back(bus2);
        }

        // Output buses
        cout << "processor->getBusCount(false)" << endl;
        int outputBusCount = processor->getBusCount(false);
        cout << "Output bus count: " << outputBusCount << endl;

        for (int i = 0; i < outputBusCount; ++i) 
        {
          busR bus2;
          vector<string> channelTypes;

          cout << "processor->getBus(false, " << i << ")" << endl;
          auto* bus = processor->getBus(false, i);
          cout << "Bus pointer: " << bus << endl;

          cout << "bus->getNumberOfChannels()" << endl;
          int numChannels = bus->getNumberOfChannels();
          cout << "Number of channels: " << numChannels << endl;

          cout << "bus->getName()" << endl;
          string busName = bus->getName().toStdString();
          cout << "Bus name: " << busName << endl;

          cout << "bus->isEnabled()" << endl;
          bool isEnabled = bus->isEnabled();
          cout << "Is enabled: " << isEnabled << endl;

          cout << "bus->getCurrentLayout() for output bus " << i << endl;
          auto& outputBus = bus->getCurrentLayout();  // Fixed: use bus, not processor->getBus(false, 0)

          for (int chan = 0; chan < numChannels; chan++)
          {
            cout << "outputBus.getTypeOfChannel(" << chan << ")" << endl;
            auto channelType = outputBus.getTypeOfChannel(chan);

            cout << "getChannelTypeName()" << endl;
            string channelName = juce::AudioChannelSet::getChannelTypeName(channelType).toStdString();
            cout << "Channel " << chan << " name: " << channelName << endl;

            channelTypes.push_back(channelName);
          }

          bus2.channelTypes = channelTypes;
          bus2.numChannels = numChannels;
          bus2.isEnabled = isEnabled;

          cout << "outputBus.getDescription()" << endl;
          bus2.mainBusLayout = outputBus.getDescription().toStdString();
          cout << "Main bus layout: " << bus2.mainBusLayout << endl;

          resp.outputBuses.push_back(bus2);
        }

        cout << "Setting success = true" << endl;
        resp.success = true;
      }
      else {
        cout << "Node or processor is null" << endl;
        resp.errmsg = "Node or processor not found";
      }
    } 
    else 
    {
      cout << "Plugin not found in loadedPlugins map" << endl;
      resp.errmsg = "Plugin not loaded";
      resp.success = false;
    }

    cout << "Returning response with success=" << resp.success << endl;
    return resp;
  }
  uint32_t scanPluginDirectory(const juce::File& directory)
  {
    if (!directory.exists()) {
      std::cout << "directory doesn't exist: " << directory.getFullPathName() << std::endl;
      return 0;
    }

    juce::Array<juce::File> pluginFiles;

    // Now search for VST3s
    auto vst3Dirs = directory.findChildFiles(juce::File::findDirectories, true, "*.vst3");
    auto vst3Files = directory.findChildFiles(juce::File::findFiles, true, "*.vst3");
    auto vstFiles = directory.findChildFiles(juce::File::findFiles, true, "*.vst");
    auto dllFiles = directory.findChildFiles(juce::File::findFiles, true, "*.dll");

    pluginFiles.addArray(vst3Dirs);
    pluginFiles.addArray(vst3Files);
    pluginFiles.addArray(vstFiles);
    pluginFiles.addArray(dllFiles); //todo: remember which plugins wouldn't load, so user doesn't have to close a bunch of activation windows

    juce::OwnedArray<juce::PluginDescription> totalDescriptions;
    uint32_t num_found = 0;
    for (const auto& pluginFile : pluginFiles)
    {
      string pathname = pluginFile.getFullPathName().toStdString();
      if (!badPaths.count(pathname))
      {

        for (auto* format : formatManager.getFormats())
        {
          if (format->fileMightContainThisPluginType(pluginFile.getFullPathName()))
          {
            juce::OwnedArray<juce::PluginDescription> descriptions;
            format->findAllTypesForFile(descriptions, pathname);
            for (auto description : descriptions)
            {
              num_found++;
              availablePlugin a;
              a.desc = *description;
              a.path = pathname;
              availablePlugins.push_back(a);
            }
            if (descriptions.size() == 0)
            {
              badPaths.insert(pathname);
            }
          }
        }
      }
    }
    return num_found;
  }

  uint32_t scanPluginDirectories()
  {
    vector<string> directories;
    int n = READFROMPIPE(uint32_t);
    uint32_t num_found = 0;
    for (int x = 0; x < n; x++)
    {
      string d = READFROMPIPE(string);
      directories.push_back(d);
    }
    n = READFROMPIPE(uint32_t);
    for (int x = 0; x < n; x++)
    {
      badPaths.insert(READFROMPIPE(string));
    }
    for (auto directory : directories)
    {
      cout << "scanning directory: " << directory << endl;
      num_found += scanPluginDirectory(juce::File(directory));
    }
    cout << "num_found: " << num_found << endl;
    return num_found;
  }

  void listAvailablePlugins()
  {
    WRITEALLC(uint32_t(availablePlugins.size()));
    for (auto plugin : availablePlugins)
    {
      WRITEALLC(uint32_t(plugin.desc.isInstrument), uint32_t(plugin.desc.uniqueId), uint32_t(plugin.desc.numInputChannels), uint32_t(plugin.desc.numOutputChannels), plugin.desc.name.toStdString(),
        plugin.desc.descriptiveName.toStdString(), plugin.desc.pluginFormatName.toStdString(), plugin.desc.category.toStdString(), plugin.desc.manufacturerName.toStdString(), plugin.desc.version.toStdString(),
        plugin.desc.fileOrIdentifier.toStdString(), plugin.desc.lastFileModTime.toString(true, true).toStdString(), plugin.path);
    }
  }

  void getPluginInfo()
  {
    int lpindex = READFROMPIPE(uint32_t);
    auto plugin = availablePlugins[lpindex];
    WRITEALLC(uint32_t(plugin.desc.isInstrument), uint32_t(plugin.desc.uniqueId), uint32_t(plugin.desc.numInputChannels), uint32_t(plugin.desc.numOutputChannels), plugin.desc.name.toStdString(),
        plugin.desc.descriptiveName.toStdString(), plugin.desc.pluginFormatName.toStdString(), plugin.desc.category.toStdString(), plugin.desc.manufacturerName.toStdString(), plugin.desc.version.toStdString(),
        plugin.desc.fileOrIdentifier.toStdString(), plugin.desc.lastFileModTime.toString(true, true).toStdString(), plugin.path);
  }

  void listBadPaths()
  {
    cout << "badPaths.size()" << badPaths.size() << endl;
    WRITEALLC(uint32_t(badPaths.size()));
    for (string badpath : badPaths)
    {
      WRITEALLC(badpath);
    }
  }
  
  struct loadPluginByIndexR {uint32_t success = true; string name; int32_t lpindex = -3; /*-1 and -2 are taken*/ uint32_t uid = -1; string errmsg;};
  loadPluginByIndexR loadPluginByIndex(int apindex) 
  {
    loadPluginByIndexR resp;
    if (apindex >= 0 && apindex < availablePlugins.size()) 
    {
      const availablePlugin& pluginInfo = availablePlugins[apindex];
      juce::String errorMessage;
      auto plugin = formatManager.createPluginInstance(
        pluginInfo.desc, sampleRate, blockSize, errorMessage);

      if (plugin != nullptr) 
      {
        if (realtime) 
        {
          cout << "adding listener" << endl;
          plugin->addListener(&paramListener);
        }
        juce::AudioProcessor* processorPtr = plugin.get();
        auto node = processorGraph->addNode(move(plugin));

        if (node != nullptr) 
        {
          int lpindex = nextlpindex++;
          loadedPlugins[lpindex] = node->nodeID;
          processorTolpindex[processorPtr] = lpindex;
          resp.lpindex = lpindex;
          resp.name = pluginInfo.desc.name.toStdString();
          resp.uid = node->nodeID.uid;
          resp.success = true;

          // Create MIDI source node for this plugin
          if (midiScheduler)  // Make sure scheduler exists
          {
            auto midiSource = std::make_unique<MidiSourceNode>(
              midiScheduler.get(), lpindex);
            auto sourceNode = processorGraph->addNode(std::move(midiSource));

            if (sourceNode)
            {
              // Store the source node
              midiSourceNodes[lpindex] = sourceNode->nodeID;

              // Connect MIDI source -> plugin
              processorGraph->addConnection({
                {sourceNode->nodeID, juce::AudioProcessorGraph::midiChannelIndex},
                {node->nodeID, juce::AudioProcessorGraph::midiChannelIndex}
                });
            }
          }

          return resp;
        }
        else
        {
          resp.success = false;
          resp.errmsg = "Couldn't add node to processor graph";
        }
      }
      else
      {
        resp.errmsg = errorMessage.toStdString();
        resp.success = false;
        badPaths.insert(pluginInfo.path);
      }
    }
    else
    {
      resp.success = false;
      resp.errmsg = "Index out of range";
    }
    return resp;
  }
  struct loadPluginR {uint32_t success = false; string errmsg; string name; int32_t lpindex = -3; uint32_t uid;}; //todo: change uint to int for lpindex in pipe communication

  loadPluginR loadPlugin(const string& path)
  {
    loadPluginR resp;

    // Acquire lock to run on message thread
    const juce::MessageManagerLock mml;

    // Check if we got the lock
    if (!mml.lockWasGained()) {
      resp.success = false;
      resp.errmsg = "Could not acquire message manager lock";
      return resp;
    }

    juce::OwnedArray<juce::PluginDescription> descriptions;

    for (auto* format : formatManager.getFormats())
    {
      if (format->fileMightContainThisPluginType(path))
      {
        format->findAllTypesForFile(descriptions, path);
        break;
      }
    }

    if (descriptions.size() > 0)
    {
      const juce::PluginDescription& desc = *descriptions[0];
      juce::String errorMessage;

      auto instance = formatManager.createPluginInstance(
        desc,
        processorGraph->getSampleRate(),
        processorGraph->getBlockSize(),
        errorMessage
      );

      if (instance)
      {
        auto* rawPointer = instance.release();
        auto node = processorGraph->addNode(
          unique_ptr<juce::AudioProcessor>(rawPointer)
        );

        if (node)
        {
          uint32_t lpindex = nextlpindex++;
          loadedPlugins[lpindex] = node->nodeID;
          processorTolpindex[rawPointer] = lpindex;  // Track reverse mapping

          resp.success = true;
          resp.name = desc.name.toStdString();
          resp.uid = node->nodeID.uid;
          resp.lpindex = lpindex;
        }
        else
        {
          resp.success = false;
          resp.errmsg = "Failed to add node to graph";
        }
      }
      else
      {
        resp.success = false;
        resp.errmsg = errorMessage.toStdString();
        badPaths.insert(path);
      }
    }
    else
    {
      resp.success = false;
      resp.errmsg = "Plugin not found at path: " + path;
    }

    return resp;
  }

  // Usable area (excluding taskbar/menu bar)
  juce::Rectangle<int> screenBounds = 
  juce::Desktop::getInstance().getDisplays().displays[0].userArea;

  int screenWidth = screenBounds.getWidth();
  int screenHeight = screenBounds.getHeight(); 

  struct showPluginUIR { uint32_t success = false; string errmsg; };
  showPluginUIR showPluginUI(int lpindex)
  {
    showPluginUIR resp;
    auto it = loadedPlugins.find(lpindex);
    if (it != loadedPlugins.end())
    {
      auto node = processorGraph->getNodeForId(it->second);
      if (node)
      {
        juce::MessageManager::callAsync([this, lpindex, node]() {
          cout << "showPluginUI async callback executing for lpindex=" << lpindex << endl;
          if (node->getProcessor()->hasEditor())
          {
            cout << "  Processor has editor, creating editor..." << endl;
            juce::AudioProcessorEditor* editor = node->getProcessor()->createEditor();

            if (editor)
            {
              cout << "  Editor created, creating PluginWindow..." << endl;
              auto window = make_unique<PluginWindow>(node->getProcessor()->getName(), editor, node->getProcessor(), lpindex, this);
              cout << "  PluginWindow created, setting visible..." << endl;
              window->setVisible(true);

              // Position the window using tiling layout
              int windowWidth = window->getWidth();
              int windowHeight = window->getHeight();

              // Get screen dimensions
              juce::Rectangle<int> screenBounds = juce::Desktop::getInstance().getDisplays().getPrimaryDisplay()->userArea;
              int screenWidth = screenBounds.getWidth();
              int screenHeight = screenBounds.getHeight();
              int margin = 10;

              cout << "  Positioning window at x=" << (nextWindowX + columnOffset) << ", y=" << nextWindowY << endl;
              window->setTopLeftPosition(nextWindowX + columnOffset, nextWindowY);

              // Update position for next window
              nextWindowX += windowWidth + margin;
              currentRowHeight = juce::jmax(currentRowHeight, windowHeight);

              // Check if we need to wrap to next row
              if (nextWindowX + columnOffset + 300 > screenWidth)  // Assume avg window width ~300, adjust as needed
              {
                cout << "  Wrapping to next row" << endl;
                nextWindowX = 10;
                nextWindowY += currentRowHeight + margin;
                currentRowHeight = 0;

                // Check if we've filled the screen vertically
                if (nextWindowY + 200 > screenHeight)  // Assume avg window height ~200
                {
                  cout << "  Screen filled vertically, starting new layer with offset" << endl;
                  nextWindowY = 10;  // Start from top again
                  columnOffset += 30;  // Offset by 30 pixels to show overlap
                }
              }

              cout << "  Window visible, initializing routing indicators..." << endl;
              // Initialize routing indicators
              window->updateRoutingIndicators(keyboardRoutedPlugin, virtualKeyboardRoutedPlugin);
              cout << "  Adding window to pluginWindows map..." << endl;
              pluginWindows[lpindex] = std::move(window);
              cout << "  showPluginUI async callback completed for lpindex=" << lpindex << endl;
            }
            else
            {
              cout << "  ERROR: Failed to create editor for lpindex=" << lpindex << endl;
            }
          }
          else
          {
            cout << "  Processor does not have editor for lpindex=" << lpindex << endl;
          }

        });
        resp.success = true;
      }
      else
      {
        resp.success = false;
        resp.errmsg = "Plugin node not found";
      }
    }
    else
    {
      resp.success = false;
      resp.errmsg = "Plugin not loaded: " + to_string(lpindex);
    }
    return resp;
  }

  uint32_t hidePluginUI(int lpindex)
  {
    auto it = pluginWindows.find(lpindex);
    if (it != pluginWindows.end())
    {
      juce::MessageManager::callAsync
      (
        [this, lpindex]() 
        {
        pluginWindows.erase(lpindex);
        }
      );

      return true;
    }
    else
    {
      return false;
    }
  }

  // Virtual keyboard show/hide functions
  uint32_t showVirtualKeyboard()
  {
    juce::MessageManager::callAsync([this]() {
      if (!virtualKeyboardWindow)
      {
        virtualKeyboardWindow = std::make_unique<VirtualKeyboardWindow>(virtualKeyboardState);
        virtualKeyboardWindow->setVisible(true);
        cout << "Virtual keyboard shown" << endl;
      }
      else
      {
        virtualKeyboardWindow->setVisible(true);
        virtualKeyboardWindow->toFront(true);
      }
    });
    return 1;  // Success
  }

  uint32_t hideVirtualKeyboard()
  {
    juce::MessageManager::callAsync([this]() {
      if (virtualKeyboardWindow)
      {
        virtualKeyboardWindow->setVisible(false);
        cout << "Virtual keyboard hidden" << endl;
      }
    });
    return 1;  // Success
  }

  struct setParameterR { uint32_t success = false; string errmsg; };
  setParameterR setParameter(int id, int paramIndex, float value)
  {
    setParameterR resp;
    auto it = loadedPlugins.find(id);
    if (it != loadedPlugins.end())
    {
      auto node = processorGraph->getNodeForId(it->second);
      if (node && node->getProcessor())
      {
        auto* processor = node->getProcessor();
        if (paramIndex >= 0 && paramIndex < processor->getParameters().size())
        {
          processor->getParameters()[paramIndex]->setValue(value);
          resp.success = true;
        }
        else
        {
          resp.success = false;
          resp.errmsg = "Invalid parameter index";
        }
      }
      else
      {
        resp.success = false;
        resp.errmsg = "Plugin node not found";
      }
    }
    else
    {
      resp.success = false;
      resp.errmsg = "Plugin not loaded: " + id;
    }
    return resp;
  }

  struct getParameterR {uint32_t success; float value; string errmsg;};
  getParameterR getParameter(int id, int paramIndex)
  {
    getParameterR resp;

    auto it = loadedPlugins.find(id);
    if (it != loadedPlugins.end())
    {
      auto node = processorGraph->getNodeForId(it->second);
      if (node && node->getProcessor())
      {
        auto* processor = node->getProcessor();
        if (paramIndex >= 0 && paramIndex < processor->getParameters().size())
        {
          float value = processor->getParameters()[paramIndex]->getValue();
          resp.success = true;
          resp.value = value;
        }
        else
        {
          resp.success = false;
          resp.errmsg = "Invalid parameter index";
        }
      }
    }
    else
    {
      resp.success = false;
      resp.errmsg = "Plugin not loaded: " + id;
    }
    return resp;
  }

  uint32_t connectAudio(const int sourceId, int sourceChannel,
    const int destId, int destChannel)
  {

    auto sourceIt = loadedPlugins.find(sourceId);
    auto destIt = loadedPlugins.find(destId);
    if (sourceIt != loadedPlugins.end() && destIt != loadedPlugins.end())
    {
      processorGraph->addConnection({
        {sourceIt->second, sourceChannel},
        {destIt->second, destChannel}
        });
      return true;
    }
    return false;
  }

  uint32_t connectMidi(int sourceId, int destId)
  {
    auto sourceIt = loadedPlugins.find(sourceId);
    auto destIt = loadedPlugins.find(destId);

    if (sourceIt != loadedPlugins.end() && destIt != loadedPlugins.end())
    {
      processorGraph->addConnection({
        {sourceIt->second, juce::AudioProcessorGraph::midiChannelIndex},
        {destIt->second, juce::AudioProcessorGraph::midiChannelIndex}
        });
      return true;
    }

    return false;
  }
  void startPlayback(uint64_t endBlock, bool toFile, const std::string& fileName = "")
  {
    realtime = !toFile;
    if (!audioInitialized) 
    {
      // Initialize audio system on first playback
      if (toFile) 
      {
        // No hardware needed for file rendering
        processorGraph->prepareToPlay(48000, 512);
        setupAudioIO();
        midiScheduler = std::make_unique<MidiScheduler>(48000);
      }
      else 
      {
        
        if (realtime) 
        {
          for (const auto& pair : loadedPlugins) 
          {
            int lpindex = pair.first;
            juce::AudioProcessorGraph::NodeID nodeId = pair.second;

            // Get the actual processor from the graph
            auto node = processorGraph->getNodeForId(nodeId);
            if (node && node->getProcessor()) {
              node->getProcessor()->addListener(&paramListener);
            }
          }
        }

        // Initialize hardware for real-time
        auto setup = deviceManager.getAudioDeviceSetup();
        setup.sampleRate = 48000;
        setup.bufferSize = 512;

        juce::String error = deviceManager.initialise(2, 2, nullptr, true, {}, &setup);

        if (error.isNotEmpty()) {
          cerr << "Audio initialization error: " << error.toStdString() << endl;
        }

        // Setup graph and audio I/O (only once)
        graphPlayer.setProcessor(processorGraph.get());

        // Create recording callback wrapper
        recordingCallback = std::make_unique<RecordingAudioCallback>(&graphPlayer, this);
        deviceManager.addAudioCallback(recordingCallback.get());

        setupAudioIO();

        midiScheduler = std::make_unique<MidiScheduler>(setup.sampleRate);

        // Setup MIDI collection
        midiCollector = make_unique<juce::MidiMessageCollector>();
        midiCollector->reset(setup.sampleRate);

        // Setup MIDI inputs
        auto midiInputs = juce::MidiInput::getAvailableDevices();
        for (const auto& input : midiInputs) {
          deviceManager.setMidiInputDeviceEnabled(input.identifier, true);
          deviceManager.addMidiInputDeviceCallback(input.identifier, this);
        }
      }
      audioInitialized = true;
    }

    // Now start actual playback
    if (toFile)
    {
      renderToFile(endBlock, fileName);
    }
    else
    {
      isPlaying = true;
      playbackEndBlock = endBlock;

      // Activate scheduled ordered playback if it was scheduled
      if (orderedPlaybackScheduled)
      {
        orderedPlaybackActive = true;
        orderedPlaybackScheduled = false;
        cout << "Activated scheduled ordered playback" << endl;
      }
    }
  }

  void stopPlayback()
  {
  }

  void removePlugin(int lpindex) 
  {
    // Remove MIDI source node first
    auto sourceIt = midiSourceNodes.find(lpindex);
    if (sourceIt != midiSourceNodes.end()) 
    {
      processorGraph->removeNode(sourceIt->second);
      midiSourceNodes.erase(sourceIt);
    }

    // Then remove the plugin itself
    auto it = loadedPlugins.find(lpindex);
    if (it != loadedPlugins.end()) 
    {
      auto node = processorGraph->getNodeForId(it->second);
      if (node) 
      {
        processorTolpindex.erase(node->getProcessor());
      }
      processorGraph->removeNode(it->second);
      loadedPlugins.erase(it);
    }
  }
  
  void clearAllPlugins() {
    processorTolpindex.clear();
    loadedPlugins.clear();
    pluginWindows.clear();
    processorGraph->clear();
  }

  void processCommand(char command)
  {
    auto commandtype = (recv_cmd)command;
    switch (commandtype)
    {
      case load_plugin:
      {
        auto response = loadPlugin(READFROMPIPE(string));
        WRITEALLC(response.success, response.name, response.lpindex, response.uid, response.errmsg);
        break;
      }
      case remove_plugin:
      {
        removePlugin(READFROMPIPE(uint32_t));
        break;
      }
      case load_plugin_by_index:
      {
        int index = READFROMPIPE(uint32_t);
        auto response = loadPluginByIndex(index);
        WRITEALLC(response.success, response.name, response.lpindex, response.uid, response.errmsg);

        cout << "load_plugin_by_index:" << endl << "index: " << index << " success: " << response.success << " name: " << response.name 
          << " lpindex: " << response.lpindex << " uid: " << response.uid << " errmsg: " << response.errmsg << endl;

        break;
      }
      case scan_plugins:
      {
        WRITEALLC(scanPluginDirectories());
        break;
      }
      case list_plugins:
      {
        listAvailablePlugins();
        break;
      }

      case get_plugin_info:
      {
        getPluginInfo();
        break;
      }

      case show_plugin_ui:
      {
        uint32_t a = READFROMPIPE(uint32_t);
        auto response = showPluginUI(a);
        cout << "show plugin ui:" << endl;
        cout << "success: " << response.success << " errmsg: " << response.errmsg << endl;
        WRITEALLC(response.success, response.errmsg);
        break;
      }
      case hide_plugin_ui:
      {
        WRITEALLC(hidePluginUI(READFROMPIPE(uint32_t)));
        break;
      }
      case set_parameter: //probably won't be used.
      {
        auto response = setParameter(READFROMPIPE(uint32_t), READFROMPIPE(uint32_t), READFROMPIPE(float));
        WRITEALLC(response.success, response.errmsg);
        break;
        break;
      }
      case get_parameter: //probably won't be used.
      {
        auto response = getParameter(READFROMPIPE(uint32_t), READFROMPIPE(uint32_t));
        WRITEALLC(response.success, response.value, response.errmsg);
        if(response.success) WRITEALLC(response.value);
        else WRITEALLC(response.errmsg);
        break;
      }
      case connect_audio:
      {
        WRITEALLC(connectAudio(READFROMPIPE(uint32_t), READFROMPIPE(uint32_t), READFROMPIPE(uint32_t), READFROMPIPE(uint32_t)));
        break;
      }
      case connect_midi:
      {
        WRITEALLC(connectMidi(READFROMPIPE(uint32_t), READFROMPIPE(uint32_t)));
        break;
      }
      case start_playback:
      {
        uint64_t lastBlock = READFROMPIPE(uint64_t);
        bool toFile = READFROMPIPE(uint32_t);
        std::string fileName = "";
        if (toFile) {
          fileName = READFROMPIPE(string);
        }
        startPlayback(lastBlock, toFile, fileName);
        WRITEALLC(uint32_t(1));
        break;
      }
      case cmd_shutdown:
      {
        clearAllPlugins();
        running = false;
        break;
      }
      case list_bad_paths:
      {
        listBadPaths();
        break;
      }
      case get_params_info:
      {
        uint32_t lpindex = READFROMPIPE(uint32_t); //debug
        getParamsInfoR resp = getParamsInfo(lpindex); 

        cout << "lpindex: " << lpindex << endl; 

        cout << "success: " << resp.success << " originalNumParams: " << resp.originalNumParams << " errmsg: " << resp.errmsg << endl;

        WRITEALLC(resp.success, uint32_t(resp.validParams.size()), resp.errmsg);
        
        cout << "WRITEALL(resp.success, resp.validParams.size(), resp.errmsg);" << endl;

        for (auto param : resp.validParams)
        {
          
          WRITEALLC(param.originalIndex, param.name, param.minValue, param.maxValue, param.interval, param.defaultValue, param.skewFactor, param.value, param.numSteps, param.isDiscrete, param.isBoolean, 
            param.isOrientationInverted, param.isAutomatable, param.isMetaParameter);

          cout << "name: " << param.name << " minValue: " << param.minValue << " maxValue: " << param.maxValue << endl;
        }
        break;
      }
      case get_channels_info:
      {
      
        cout << "getChannnelsInfoR resp = getChannelsInfo(READFROMPIPE(uint32_t));" << endl;

        getChannnelsInfoR resp = getChannelsInfo(READFROMPIPE(uint32_t));

        cout << "WRITEALL(resp.success, resp.acceptsMidi, resp.producesMidi);" << endl;

        WRITEALLC(resp.success, resp.acceptsMidi, resp.producesMidi);

        cout << "WRITEALL(uint32_t(resp.inputBuses.size()));" << endl;

        WRITEALLC(uint32_t(resp.inputBuses.size()));
        for (auto bus : resp.inputBuses)
        {
          WRITEALLC(bus.numChannels, uint32_t(bus.channelTypes.size()));
          for (string channelType : bus.channelTypes)
          {
            WRITEALLC(channelType);
          }
          WRITEALLC(bus.isEnabled, bus.mainBusLayout);
        }

        cout << "WRITEALL(uint32_t(resp.outputBuses.size()));" << endl;

        WRITEALLC(uint32_t(resp.outputBuses.size()));
        for (auto bus : resp.outputBuses)
        {
          WRITEALLC(bus.numChannels, uint32_t(bus.channelTypes.size()));
          for (string channelType : bus.channelTypes)
          {
            WRITEALLC(channelType);
          }
          WRITEALLC(bus.isEnabled, bus.mainBusLayout);
        }

        cout << "WRITEALL(resp.errmsg);" << endl;

        WRITEALLC(resp.errmsg);
        break;
      }
      case schedule_midi_note:
      {
        int lpindex = READFROMPIPE(uint32_t);
        int note = READFROMPIPE(uint32_t);
        float velocity = READFROMPIPE(float);
        double startTime = READFROMPIPE(double);
        double duration = READFROMPIPE(double);
        int channel = READFROMPIPE(uint32_t);
        midiScheduler->scheduleNote(lpindex, note, velocity, startTime, duration, channel);
        break;
      }
      case schedule_midi_cc:
      {
        int lpindex = READFROMPIPE(uint32_t);
        int controller = READFROMPIPE(uint32_t);
        int value = READFROMPIPE(uint32_t);
        double time = READFROMPIPE(double);
        int channel = READFROMPIPE(uint32_t);
        midiScheduler->scheduleCC(lpindex, controller, value, time, channel);
        break;
      }
      case clear_midi_schedule:
      {
        midiScheduler->clearSchedule();
        break;
      }
      case schedule_param_change:
      {
        int lpindex = READFROMPIPE(uint32_t);
        int parameterIndex = READFROMPIPE(uint32_t);
        float value = READFROMPIPE(float);
        uint64_t atBlock = READFROMPIPE(uint64_t);
        scheduler.scheduleParameterChange(lpindex, parameterIndex, value, atBlock);
        break;
      }
      case route_keyboard_input:
      {
        int lpindex = READFROMPIPE(uint32_t);
        uint32_t useVelocity = READFROMPIPE(uint32_t);
        float fixedVel = READFROMPIPE(float);

        keyboardRoutedPlugin = lpindex;
        useKeyboardVelocity = (useVelocity != 0);
        fixedVelocity = fixedVel;

        cout << "Keyboard routed to plugin " << lpindex
             << ", useVelocity=" << useKeyboardVelocity
             << ", fixedVelocity=" << fixedVelocity << endl;

        WRITEALLC(uint32_t(1));  // Success
        break;
      }
      case unroute_keyboard_input:
      {
        keyboardRoutedPlugin = -1;
        cout << "Keyboard input unrouted" << endl;
        WRITEALLC(uint32_t(1));  // Success
        break;
      }
      case route_cc_to_param:
      {
        int lpindex = READFROMPIPE(uint32_t);
        int parameterIndex = READFROMPIPE(uint32_t);
        int ccController = READFROMPIPE(uint32_t);
        int midiChannel = READFROMPIPE(int32_t);  // -1 for any channel

        CCMapping mapping;
        mapping.lpindex = lpindex;
        mapping.parameterIndex = parameterIndex;
        mapping.ccController = ccController;
        mapping.midiChannel = midiChannel;

        ccMappings.push_back(mapping);

        cout << "CC " << ccController << " (channel " << midiChannel
             << ") routed to plugin " << lpindex << " param " << parameterIndex << endl;

        WRITEALLC(uint32_t(1));  // Success
        break;
      }
      case unroute_cc_to_param:
      {
        int lpindex = READFROMPIPE(uint32_t);
        int parameterIndex = READFROMPIPE(uint32_t);
        int ccController = READFROMPIPE(uint32_t);

        // Remove matching mapping
        ccMappings.erase(
          std::remove_if(ccMappings.begin(), ccMappings.end(),
            [lpindex, parameterIndex, ccController](const CCMapping& m) {
              return m.lpindex == lpindex &&
                     m.parameterIndex == parameterIndex &&
                     m.ccController == ccController;
            }),
          ccMappings.end()
        );

        cout << "Unrouted CC " << ccController << " from plugin " << lpindex
             << " param " << parameterIndex << endl;

        WRITEALLC(uint32_t(1));  // Success
        break;
      }
      case show_virtual_keyboard:
      {
        uint32_t result = showVirtualKeyboard();
        WRITEALLC(result);
        break;
      }
      case hide_virtual_keyboard:
      {
        uint32_t result = hideVirtualKeyboard();
        WRITEALLC(result);
        break;
      }
      case route_virtual_keyboard:
      {
        int lpindex = READFROMPIPE(uint32_t);
        uint32_t useVelocity = READFROMPIPE(uint32_t);
        float fixedVel = READFROMPIPE(float);

        virtualKeyboardRoutedPlugin = lpindex;
        useVirtualKeyboardVelocity = (useVelocity != 0);
        virtualFixedVelocity = fixedVel;

        cout << "Virtual keyboard routed to plugin " << lpindex
             << ", useVelocity=" << useVirtualKeyboardVelocity
             << ", fixedVelocity=" << virtualFixedVelocity << endl;

        WRITEALLC(uint32_t(1));  // Success
        break;
      }
      case unroute_virtual_keyboard:
      {
        virtualKeyboardRoutedPlugin = -1;
        cout << "Virtual keyboard input unrouted" << endl;
        WRITEALLC(uint32_t(1));  // Success
        break;
      }
      case toggle_recording:
      {
        toggleRecording();
        WRITEALLC(uint32_t(isRecording ? 1 : 0));  // Return current recording state
        break;
      }
      case toggle_monitoring:
      {
        toggleMonitoring();
        WRITEALLC(uint32_t(isMonitoring ? 1 : 0));  // Return current monitoring state
        break;
      }
      case load_audio_file:
      {
        std::string filename = READFROMPIPE(string);

        // Create a new audio file player node
        auto playerNode = std::make_unique<AudioFilePlayerNode>();

        if (playerNode->loadAudioFile(filename))
        {
          int playerId = nextAudioPlayerId++;

          // Add node to processor graph
          auto nodeId = processorGraph->addNode(std::move(playerNode));

          if (nodeId != nullptr)
          {
            audioFilePlayerNodes[playerId] = nodeId->nodeID;

            cout << "Audio file loaded into graph: " << filename
                 << ", playerId=" << playerId
                 << ", nodeId=" << nodeId->nodeID.uid << endl;

            WRITEALLC(playerId);  // Return player ID
            WRITEALLN(audio_file_loaded, filename, playerId);
          }
          else
          {
            cout << "ERROR: Failed to add audio player node to graph" << endl;
            WRITEALLC(-1);  // Error
          }
        }
        else
        {
          cout << "ERROR: Failed to load audio file: " << filename << endl;
          WRITEALLC(-1);  // Error
        }
        break;
      }
      case control_audio_playback:
      {
        int playerId = READFROMPIPE(uint32_t);
        uint8_t action = READFROMPIPE(uint8_t);  // 0=stop, 1=start, 2=schedule
        int64_t startSample = 0;
        int64_t fileStartPosition = 0;

        if (action == 1)  // start immediately
        {
          fileStartPosition = READFROMPIPE(uint64_t);
        }
        else if (action == 2)  // schedule
        {
          startSample = READFROMPIPE(uint64_t);
          fileStartPosition = READFROMPIPE(uint64_t);
        }

        auto it = audioFilePlayerNodes.find(playerId);
        if (it != audioFilePlayerNodes.end())
        {
          // Get the node from the graph
          auto node = processorGraph->getNodeForId(it->second);
          if (node != nullptr)
          {
            auto* playerProcessor = dynamic_cast<AudioFilePlayerNode*>(node->getProcessor());
            if (playerProcessor != nullptr)
            {
              if (action == 0)  // stop
              {
                playerProcessor->stopPlayback();
                cout << "Stopped audio playback: playerId=" << playerId << endl;
                WRITEALLC(uint32_t(1));  // Success
                WRITEALLN(audio_playback_stopped, playerId, currentSamplePosition);
              }
              else if (action == 1)  // start immediately
              {
                playerProcessor->startPlayback(fileStartPosition);
                cout << "Started audio playback: playerId=" << playerId
                     << ", fileStartPosition=" << fileStartPosition << endl;
                WRITEALLC(uint32_t(1));  // Success
                WRITEALLN(audio_playback_started, playerId, currentSamplePosition);
              }
              else if (action == 2)  // schedule
              {
                playerProcessor->schedulePlayback(startSample, fileStartPosition);
                cout << "Scheduled audio playback: playerId=" << playerId
                     << ", startSample=" << startSample
                     << ", fileStartPosition=" << fileStartPosition << endl;
                WRITEALLC(uint32_t(1));  // Success
                WRITEALLN(audio_playback_started, playerId, startSample);
              }
              else
              {
                cout << "ERROR: Unknown playback action: " << (int)action << endl;
                WRITEALLC(uint32_t(0));  // Error
              }
            }
            else
            {
              cout << "ERROR: Node is not an AudioFilePlayerNode" << endl;
              WRITEALLC(uint32_t(0));  // Error
            }
          }
          else
          {
            cout << "ERROR: Node not found in graph" << endl;
            WRITEALLC(uint32_t(0));  // Error
          }
        }
        else
        {
          cout << "ERROR: Player ID not found: " << playerId << endl;
          WRITEALLC(uint32_t(0));  // Error
        }
        break;
      }
      case schedule_ordered_notes:
      {
        uint32_t count = READFROMPIPE(uint32_t);

        for (uint32_t i = 0; i < count; ++i)
        {
          int orderNumber = READFROMPIPE(uint32_t);
          int noteNumber = READFROMPIPE(uint32_t);
          int velocity = READFROMPIPE(uint32_t);
          int channel = READFROMPIPE(uint32_t);
          int duration = READFROMPIPE(uint32_t);

          scheduleOrderedNote(orderNumber, noteNumber, velocity, channel, duration);
        }

        // Sort the notes by order number
        sortOrderedNotes();

        WRITEALLC(uint32_t(count));  // Return count of notes added
        break;
      }
      case start_ordered_playback:
      {
        uint32_t useKeyboardVelocity = READFROMPIPE(uint32_t);
        uint32_t useKeyboardDuration = READFROMPIPE(uint32_t);
        startOrderedPlayback(useKeyboardVelocity != 0, useKeyboardDuration != 0);
        WRITEALLC(uint32_t(1));  // Success
        break;
      }
      case stop_ordered_playback:
      {
        stopOrderedPlayback();
        WRITEALLC(uint32_t(1));  // Success
        break;
      }
      case clear_ordered_notes:
      {
        clearOrderedNotes();
        WRITEALLC(uint32_t(1));  // Success
        break;
      }
      case clear_midi_cc_schedule:
      {
        midiScheduler->clearCCSchedule();
        break;
      }
      case clear_param_schedule:
      {
        scheduler.clearSchedule();
        break;
      }
      case clear_all_plugins:
      {
        clearAllPlugins();
        break;
      }
      default:
      {
        cout << "command not recognized: " << commandtype << endl;
      }
    }
  }

  // Custom button component for routing indicators
  class RoutingButton : public juce::Component
  {
  public:
    enum RoutingType { MidiKeyboard, VirtualKeyboard };

    RoutingButton(RoutingType type, int pluginlpindex, CompletePluginHost* hostApp)
      : routingType(type), lpindex(pluginlpindex), host(hostApp), isActive(false)
    {
      cout << "RoutingButton constructor: type=" << type << ", lpindex=" << pluginlpindex << endl;
      setSize(40, 40);
      setMouseCursor(juce::MouseCursor::PointingHandCursor);
    }

    void paint(juce::Graphics& g) override
    {
      cout << "RoutingButton::paint called for lpindex=" << lpindex << ", type=" << routingType << ", isActive=" << isActive << endl;
      auto bounds = getLocalBounds().reduced(4);

      // Background - more opaque and visible
      if (isActive)
      {
        g.setColour(juce::Colours::lightgreen.withAlpha(0.8f));
        g.fillRoundedRectangle(bounds.toFloat(), 4.0f);
      }
      else
      {
        // Light background for inactive buttons too
        g.setColour(juce::Colour(60, 60, 70)); // Dark blue-grey
        g.fillRoundedRectangle(bounds.toFloat(), 4.0f);
      }

      // Border - brighter colors
      g.setColour(isActive ? juce::Colours::lime : juce::Colours::lightgrey);
      g.drawRoundedRectangle(bounds.toFloat(), 4.0f, 2.0f);

      // Draw miniature keyboard graphic
      if (routingType == MidiKeyboard)
      {
        drawMidiKeyboardIcon(g, bounds.reduced(6));
      }
      else
      {
        drawVirtualKeyboardIcon(g, bounds.reduced(6));
      }
      cout << "RoutingButton::paint completed for lpindex=" << lpindex << endl;
    }

    void mouseDown(const juce::MouseEvent& event) override
    {
      cout << "RoutingButton::mouseDown for lpindex=" << lpindex << ", type=" << routingType << endl;
      if (event.mods.isLeftButtonDown())
      {
        toggleRouting();
      }
    }

    void setActive(bool active)
    {
      if (isActive != active)
      {
        isActive = active;
        repaint();
      }
    }

    bool getIsActive() const { return isActive; }
    int getlpindex() const { return lpindex; }
    RoutingType getRoutingType() const { return routingType; }

  private:
    void drawMidiKeyboardIcon(juce::Graphics& g, juce::Rectangle<int> area)
    {
      // Draw a simplified MIDI keyboard icon with rectangular keys
      g.setColour(isActive ? juce::Colours::white : juce::Colours::lightgrey);

      int numKeys = 7; // 7 white keys
      float keyWidth = area.getWidth() / (float)numKeys;

      // Draw white keys
      for (int i = 0; i < numKeys; ++i)
      {
        float x = (float)area.getX() + i * keyWidth;
        g.drawRect(x, (float)area.getY(), keyWidth, (float)area.getHeight(), 1.5f);
      }

      // Draw black keys (smaller, on top)
      g.setColour(isActive ? juce::Colours::yellow : juce::Colours::silver);
      int blackKeyPattern[] = {1, 1, 0, 1, 1, 1}; // Pattern for black keys
      float blackKeyHeight = area.getHeight() * 0.6f;
      float blackKeyWidth = keyWidth * 0.6f;

      for (int i = 0; i < 6; ++i)
      {
        if (blackKeyPattern[i])
        {
          float x = (float)area.getX() + (i + 0.7f) * keyWidth;
          g.fillRect(x, (float)area.getY(), blackKeyWidth, blackKeyHeight);
        }
      }
    }

    void drawVirtualKeyboardIcon(juce::Graphics& g, juce::Rectangle<int> area)
    {
      // Draw a computer keyboard icon (more rectangular/digital looking)
      g.setColour(isActive ? juce::Colours::white : juce::Colours::lightgrey);

      // Draw outer frame (computer keyboard outline)
      g.drawRoundedRectangle(area.toFloat(), 2.0f, 1.5f);

      // Draw 3 rows of small rectangular keys inside
      auto innerArea = area.reduced(3);
      int rowHeight = innerArea.getHeight() / 3;

      for (int row = 0; row < 3; ++row)
      {
        int numKeysInRow = (row == 1) ? 5 : 4; // Middle row has more keys
        float keyWidth = innerArea.getWidth() / (float)(numKeysInRow + 0.5f);
        float yPos = innerArea.getY() + row * rowHeight + 1;
        float xOffset = (row == 1) ? 0 : keyWidth * 0.25f;

        for (int i = 0; i < numKeysInRow; ++i)
        {
          float xPos = innerArea.getX() + xOffset + i * keyWidth + 1;
          float w = keyWidth - 2;
          float h = rowHeight - 2;
          g.drawRect(xPos, yPos, w, h, 1.0f);
        }
      }
    }

    void toggleRouting()
    {
      cout << "RoutingButton::toggleRouting called for lpindex=" << lpindex << ", type=" << routingType << endl;
      if (host)
      {
        cout << "  host pointer is valid" << endl;
        if (routingType == MidiKeyboard)
        {
          cout << "  calling toggleMidiKeyboardRouting" << endl;
          host->toggleMidiKeyboardRouting(lpindex);
        }
        else
        {
          cout << "  calling toggleVirtualKeyboardRouting" << endl;
          host->toggleVirtualKeyboardRouting(lpindex);
        }
        cout << "  toggleRouting completed" << endl;
      }
      else
      {
        cout << "  ERROR: host pointer is null!" << endl;
      }
    }

    RoutingType routingType;
    int lpindex;
    CompletePluginHost* host;
    bool isActive;
  };

  // Wrapper component that contains the editor and routing buttons
  class EditorWithButtonsComponent : public juce::Component
  {
  public:
    EditorWithButtonsComponent(juce::AudioProcessorEditor* editor,
                                std::unique_ptr<RoutingButton> midiBtn,
                                std::unique_ptr<RoutingButton> virtualBtn)
      : editor(editor),
        midiKeyboardButton(std::move(midiBtn)),
        virtualKeyboardButton(std::move(virtualBtn))
    {
      cout << "EditorWithButtonsComponent constructor" << endl;

      // Make this component opaque with white background
      setOpaque(true);

      // Paint all children on top of our background
      setBufferedToImage(false);

      // Add the editor - but set it to be non-opaque so our background shows through
      if (editor)
      {
        addAndMakeVisible(editor);
        cout << "  Editor is opaque: " << editor->isOpaque() << endl;

        // CRITICAL: Force the editor to not be opaque so our background paints behind it
        editor->setOpaque(false);
        cout << "  Forced editor to be non-opaque. Now editor is opaque: " << editor->isOpaque() << endl;
      }

      // Add the buttons
      if (midiKeyboardButton)
      {
        addAndMakeVisible(midiKeyboardButton.get());
      }
      if (virtualKeyboardButton)
      {
        addAndMakeVisible(virtualKeyboardButton.get());
      }

      cout << "EditorWithButtonsComponent constructor completed" << endl;
    }

    ~EditorWithButtonsComponent()
    {
      // Editor will be deleted by the window when it takes ownership
      // Just release it from our raw pointer without deleting
    }

    void resized() override
    {
      cout << "EditorWithButtonsComponent::resized() - bounds: " << getBounds().toString() << endl;

      auto bounds = getLocalBounds();

      // Check if we have buttons
      bool hasButtons = (midiKeyboardButton != nullptr && virtualKeyboardButton != nullptr);
      int buttonHeight = hasButtons ? 50 : 0; // Height of the button strip at the bottom
      int buttonSize = 40;
      int margin = 5;

      // Editor takes the top portion (or all space if no buttons)
      if (editor)
      {
        auto editorBounds = bounds.removeFromTop(bounds.getHeight() - buttonHeight);
        cout << "  Setting editor bounds: " << editorBounds.toString() << endl;
        editor->setBounds(editorBounds);
      }

      // Position buttons only if they exist
      if (hasButtons)
      {
        // Button strip at the bottom
        auto buttonStrip = bounds; // Remaining area at bottom

        cout << "  Button strip area: " << buttonStrip.toString() << endl;

        // Center the buttons horizontally in the button strip
        int totalButtonWidth = (buttonSize * 2) + margin;
        int startX = buttonStrip.getX() + (buttonStrip.getWidth() - totalButtonWidth) / 2;
        int buttonY = buttonStrip.getY() + (buttonStrip.getHeight() - buttonSize) / 2;

        cout << "  Calculated button position: startX=" << startX << ", buttonY=" << buttonY << endl;

        if (midiKeyboardButton)
        {
          auto midiBounds = juce::Rectangle<int>(startX, buttonY, buttonSize, buttonSize);
          cout << "  Setting midiKeyboardButton bounds: " << midiBounds.toString() << endl;
          midiKeyboardButton->setBounds(midiBounds);
        }

        if (virtualKeyboardButton)
        {
          auto virtualBounds = juce::Rectangle<int>(startX + buttonSize + margin, buttonY, buttonSize, buttonSize);
          cout << "  Setting virtualKeyboardButton bounds: " << virtualBounds.toString() << endl;
          virtualKeyboardButton->setBounds(virtualBounds);
        }
      }

      cout << "EditorWithButtonsComponent::resized() completed" << endl;
    }

    void paint(juce::Graphics& g) override
    {
      // Our component is opaque, so we must completely fill the background with a solid colour
      // Paint black background for ALL areas
      g.fillAll(juce::Colours::black);
    }

    std::unique_ptr<RoutingButton>& getMidiButton() { return midiKeyboardButton; }
    std::unique_ptr<RoutingButton>& getVirtualButton() { return virtualKeyboardButton; }

  private:
    juce::AudioProcessorEditor* editor;
    std::unique_ptr<RoutingButton> midiKeyboardButton;
    std::unique_ptr<RoutingButton> virtualKeyboardButton;
  };

  // Plugin window class
  class PluginWindow : public juce::DocumentWindow
  {
  public:
    PluginWindow(const juce::String& name,
      juce::AudioProcessorEditor* editor,
      juce::AudioProcessor* processor, int lpind,
      CompletePluginHost* hostApp)
      : DocumentWindow(name,
        juce::Colours::black,  // Black background for the entire window
        DocumentWindow::allButtons),
        lpindex(lpind),
        processor(processor),
        host(hostApp)
    {
      cout << "PluginWindow constructor called for lpindex=" << lpind << ", host=" << (void*)hostApp << endl;

      // DON'T use native title bar - use JUCE rendering for full control
      setUsingNativeTitleBar(false);

      // Set the background color to black for the entire window area
      setBackgroundColour(juce::Colours::black);

      // Make sure the window is opaque (not transparent)
      setOpaque(true);

      // Check if this processor is an instrument
      // Instruments typically: accept MIDI AND have no audio inputs (or very few) AND produce audio output
      bool acceptsMidi = processor->acceptsMidi();
      bool hasNoAudioInput = (processor->getTotalNumInputChannels() == 0);
      bool producesAudio = (processor->getTotalNumOutputChannels() > 0);
      bool isInstrument = acceptsMidi && hasNoAudioInput && producesAudio;

      cout << "  Processor: acceptsMidi=" << acceptsMidi
           << ", audioInputs=" << processor->getTotalNumInputChannels()
           << ", audioOutputs=" << processor->getTotalNumOutputChannels()
           << ", isInstrument=" << isInstrument << endl;

      int buttonStripHeight = 0;
      std::unique_ptr<RoutingButton> midiBtn = nullptr;
      std::unique_ptr<RoutingButton> virtualBtn = nullptr;

      // Only create routing buttons for instruments
      if (isInstrument)
      {
        cout << "  Creating routing buttons for instrument..." << endl;
        midiBtn = std::make_unique<RoutingButton>(
          RoutingButton::MidiKeyboard, lpind, hostApp);
        virtualBtn = std::make_unique<RoutingButton>(
          RoutingButton::VirtualKeyboard, lpind, hostApp);
        buttonStripHeight = 50;
        cout << "  Routing buttons created" << endl;
      }
      else
      {
        cout << "  Skipping routing buttons (not an instrument)" << endl;
      }

      cout << "  Creating wrapper component with editor and buttons..." << endl;
      // Create wrapper component that contains the editor and buttons
      auto wrapper = std::make_unique<EditorWithButtonsComponent>(
        editor, std::move(midiBtn), std::move(virtualBtn));

      // Get references to the buttons before moving the wrapper (only if they exist)
      if (isInstrument)
      {
        midiKeyboardButton = wrapper->getMidiButton().get();
        virtualKeyboardButton = wrapper->getVirtualButton().get();
      }
      else
      {
        midiKeyboardButton = nullptr;
        virtualKeyboardButton = nullptr;
      }

      // Calculate the size: editor size + button strip height (0 for non-instruments)
      int editorWidth = editor->getWidth();
      int editorHeight = editor->getHeight();

      wrapper->setSize(editorWidth, editorHeight + buttonStripHeight);
      cout << "  Wrapper component created with size: " << editorWidth << "x" << (editorHeight + buttonStripHeight) << endl;

      // Set the wrapper as the window's content
      setContentOwned(wrapper.release(), true);
      setResizable(editor->isResizable(), false);

      // Don't center - let the caller position the window
      // (The tiling layout in showPluginUI will position it)

      // Make the window visible (like the GUI template does)
      setVisible(true);

      cout << "  Window dimensions: " << getWidth() << "x" << getHeight() << endl;
      cout << "  Window background colour: " << getBackgroundColour().toString() << endl;
      cout << "  Content component size: " << getContentComponent()->getWidth() << "x" << getContentComponent()->getHeight() << endl;
      cout << "  Window is visible: " << isVisible() << endl;

      cout << "PluginWindow constructor completed for lpindex=" << lpind << endl;
    }

    int getlpindex() const { return lpindex; }

    void closeButtonPressed() override
    {
      setVisible(false);
    }

    void paint(juce::Graphics& g) override
    {
      // Paint black background behind everything
      g.fillAll(juce::Colours::black);
    }

    void updateRoutingIndicators(int activeMidiKeyboardPlugin, int activeVirtualKeyboardPlugin)
    {
      cout << "PluginWindow::updateRoutingIndicators called for lpindex=" << lpindex
           << ", activeMidi=" << activeMidiKeyboardPlugin
           << ", activeVirtual=" << activeVirtualKeyboardPlugin << endl;
      if (midiKeyboardButton && virtualKeyboardButton)
      {
        midiKeyboardButton->setActive(activeMidiKeyboardPlugin == lpindex);
        virtualKeyboardButton->setActive(activeVirtualKeyboardPlugin == lpindex);
        cout << "  Indicators updated" << endl;
      }
      else
      {
        cout << "  ERROR: Button pointers are null!" << endl;
      }
    }

  private:

    juce::AudioProcessor* processor;
    int lpindex;
    CompletePluginHost* host;
    RoutingButton* midiKeyboardButton;  // Raw pointer - owned by the wrapper component
    RoutingButton* virtualKeyboardButton;  // Raw pointer - owned by the wrapper component

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(PluginWindow)
  };

  // Virtual Keyboard Window
  class VirtualKeyboardWindow : public juce::DocumentWindow
  {
  public:
    VirtualKeyboardWindow(juce::MidiKeyboardState& state)
      : DocumentWindow("Virtual MIDI Keyboard",
        juce::Desktop::getInstance().getDefaultLookAndFeel()
        .findColour(ResizableWindow::backgroundColourId),
        DocumentWindow::allButtons),
        keyboardState(state)
    {
      setUsingNativeTitleBar(true);

      // Create the keyboard component (2 octaves, horizontal orientation)
      keyboardComponent = std::make_unique<juce::MidiKeyboardComponent>(
        keyboardState,
        juce::MidiKeyboardComponent::horizontalKeyboard);

      // Set keyboard properties
      keyboardComponent->setKeyWidth(40.0f);
      keyboardComponent->setLowestVisibleKey(36);  // C2

      // Set the keyboard as the window content
      setContentNonOwned(keyboardComponent.get(), true);

      // Size the window appropriately (2 octaves = 24 keys)
      int keyboardWidth = 24 * 40 + 50;  // approximate width for 2 octaves
      int keyboardHeight = 120;

      setResizable(true, false);
      setResizeLimits(400, 80, 2000, 200);

      // Position at bottom-center of screen (like a real keyboard)
      juce::Rectangle<int> screenBounds = juce::Desktop::getInstance().getDisplays().getPrimaryDisplay()->userArea;
      int screenWidth = screenBounds.getWidth();
      int screenHeight = screenBounds.getHeight();

      int xPos = (screenWidth - keyboardWidth) / 2;  // Center horizontally
      int yPos = screenHeight - keyboardHeight - 50;  // 50 pixels from bottom

      setBounds(xPos, yPos, keyboardWidth, keyboardHeight);

      cout << "Virtual keyboard positioned at bottom-center: x=" << xPos << ", y=" << yPos << endl;
    }

    void closeButtonPressed() override
    {
      setVisible(false);
    }

  private:
    juce::MidiKeyboardState& keyboardState;
    std::unique_ptr<juce::MidiKeyboardComponent> keyboardComponent;

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(VirtualKeyboardWindow)
  };

private:
  // Core components
#ifdef _WIN32
  HANDLE hCommandPipe;
  HANDLE hNotificationPipe;
#else
  int commandPipe_fd;
  int notificationPipe_fd;
#endif
  bool commandPipeReady = false;
  bool notificationPipeReady = false;
  long int currentBlock = 0;
  bool realtime = false;
  bool threadStarted = false;
  bool isPlaying = false;
  juce::AudioDeviceManager deviceManager;
  juce::KnownPluginList knownPluginList;
  unique_ptr<juce::AudioProcessorGraph> processorGraph;
  juce::AudioProcessorPlayer graphPlayer;
  unique_ptr<juce::MidiMessageCollector> midiCollector;
  unique_ptr<RecordingAudioCallback> recordingCallback;

  // map<> is always O(log n), unordered_map<> is normally O(1) but can get up to O(n)
  unordered_map<juce::AudioProcessor*, int> processorTolpindex;
  unordered_map<int, unique_ptr<PluginWindow>> pluginWindows;       // int ID -> Window
  int nextlpindex = 0;  // Auto-increment ID counter

  // Window positioning state for tiling layout
  int nextWindowX = 10;  // Start 10 pixels from left edge
  int nextWindowY = 10;  // Start 10 pixels from top edge
  int currentRowHeight = 0;  // Track tallest window in current row
  int columnOffset = 0;  // Horizontal offset for when screen fills vertically

  // Virtual keyboard
  juce::MidiKeyboardState virtualKeyboardState;
  unique_ptr<VirtualKeyboardWindow> virtualKeyboardWindow;
  unique_ptr<VirtualKeyboardListener> virtualKeyboardListener;

  // Audio recording
  bool isRecording = false;
  bool isMonitoring = false;  // Whether to play back during recording
  std::unique_ptr<juce::AudioFormatWriter> audioWriter;
  juce::File currentRecordingFile;
  int64_t recordingStartSample = 0;
  std::string currentRecordingFilename;

  // Audio file playback (using processor graph nodes)
  unordered_map<int, juce::AudioProcessorGraph::NodeID> audioFilePlayerNodes;  // player ID -> node ID
  int nextAudioPlayerId = 0;

  // Ordered note playback - triggered by any MIDI keyboard key press
  struct OrderedNote
  {
    int orderNumber;      // Notes with same number play simultaneously
    int noteNumber;       // MIDI note number
    int velocity;         // MIDI velocity
    int channel;          // MIDI channel
    int duration;         // Duration in samples
  };
  vector<OrderedNote> orderedNotes;  // Sorted by orderNumber
  int currentOrderIndex = 0;         // Current position in orderedNotes
  bool orderedPlaybackActive = false;
  bool orderedPlaybackScheduled = false;  // If true, will activate when playback starts
  bool useKeyboardVelocityForOrdered = true;  // If true, use MIDI keyboard velocity; if false, use stored velocity
  bool useKeyboardDurationForOrdered = true;  // If true, use MIDI keyboard note-off timing; if false, use stored duration

  // Track currently held notes for keyboard-controlled duration
  struct ActiveOrderedNote {
    int noteNumber;
    int channel;
    int orderNumber;
  };
  vector<ActiveOrderedNote> activeOrderedNotes;

  struct availablePlugin
  {
    string path;
    juce::PluginDescription desc;
  };
  vector<availablePlugin> availablePlugins;
  set<string> badPaths;

  // Communication
  thread commandThread;
  thread notificationThread;
  //queue<Command> commandQueue;
  mutex commandMutex;
  condition_variable commandCv;

  JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(CompletePluginHost)
};

// RecordingAudioCallback implementation
void RecordingAudioCallback::audioDeviceIOCallbackWithContext(
  const float* const* inputChannelData,
  int numInputChannels,
  float* const* outputChannelData,
  int numOutputChannels,
  int numSamples,
  const juce::AudioIODeviceCallbackContext& context)
{
  // Call the wrapped callback first (this processes the audio from plugins and graph nodes)
  wrappedCallback->audioDeviceIOCallbackWithContext(inputChannelData, numInputChannels,
                                                    outputChannelData, numOutputChannels,
                                                    numSamples, context);

  // Capture the final output for recording if enabled
  if (host)
  {
    host->captureAudioForRecording(outputChannelData, numOutputChannels, numSamples);
  }
}

// VirtualKeyboardListener implementation
void VirtualKeyboardListener::routeToHost(const juce::MidiMessage& message)
{
  if (hostApp)
  {
    hostApp->handleVirtualKeyboardMessage(message);
  }
}

void audioProcessorParameterChanged(juce::AudioProcessor* processor, int paramIndex, float value)
{
  cout << "audioProcessorParameterChanged" << endl;
  if (!suppressNotifications)
  {
    cout << "!suppressNotifications" << endl;
    ParameterChangeEvent event{app->findlpindex(processor), paramIndex, value, app->scheduler.getCurrentBlock()};
    parameterQueue.push(event);  // Lock-free, real-time safe
  }
}

void ParameterChangeListener::audioProcessorParameterChanged(juce::AudioProcessor* processor, int paramIndex, float value) {
  if (!suppressNotifications && app) {
    int lpindex = app->findlpindex(processor);
    if (lpindex != -1) {
      ParameterChangeEvent event{lpindex, paramIndex, value, app->scheduler.getCurrentBlock() + 1};  // Effect takes place in next block
      app->queueParameterNotification(event);  // Queue instead of direct pipe write
    }
  }
}
// Entry point
#ifdef _WIN32
// Use extern "C" to ensure proper linkage

bool isAllWhitespace(const std::string& str) 
{
  return str.empty() || str.find_first_not_of(" \t\n\r\f\v") == std::string::npos;
}

extern "C" int WINAPI WinMain(HINSTANCE hInstance, HINSTANCE hPrevInstance, LPSTR lpCmdLine, int nCmdShow)
{
  try {
    if (!isAllWhitespace(lpCmdLine))
    {
      pipeName = lpCmdLine;
    }    
    // Allocate a console window for this GUI application
    AllocConsole();

    // Redirect stdout, stdin, stderr to console
    FILE* pCout;
    freopen_s(&pCout, "CONOUT$", "w", stdout);
    freopen_s(&pCout, "CONOUT$", "w", stderr);
    freopen_s(&pCout, "CONIN$", "r", stdin);

    freopen_s(&pCout, "debug_log.txt", "w", stdout);
    freopen_s(&pCout, "CONOUT$", "w", stderr);


    std::cout.clear();
    std::cerr.clear();
    std::cin.clear();
 
  // Suppress unused parameter warnings
    (void)hInstance;
    (void)hPrevInstance;
    (void)nCmdShow;
    
#ifdef _WIN64
    cout << "compiled as 64-bit, so will only detect 64-bit plugins" << std::endl; //todo: notify of plugins found that are the wrong number of bits
#else
    cout << "compiled as 32-bit, so will only detect 32-bit plugins" << std::endl;
#endif
    cout << "formats supported:" << endl;
    juce::AudioFormatManager formatManager;
    for (auto* format : formatManager) 
    {  
      std::cout << format->getFormatName().toStdString() << std::endl;
    }
    cout << "using pipe: " << pipeName << endl;
    juce::initialiseJuce_GUI();
    app = new CompletePluginHost();
    app->testFunction();
    app->waitForConnection();
    app->initialise(juce::String(lpCmdLine));
    juce::MessageManager::getInstance()->runDispatchLoop();
    app->shutdown();
    juce::shutdownJuce_GUI();
    cout << endl << "press enter to close..." << endl;
    cin.get();
  } 
  catch (const exception& e) 
  {
    cout << "exception: " << e.what() << std::endl;
    cout << endl << "press enter to close..." << endl;
    cin.get();
  }
  return 0;
}
#else
// For Mac/Linux, use standard main
int main(int argc, char* argv[])
{
    juce::initialiseJuce_GUI();
    
    juce::String commandLine;
    if (argc > 1)
    {
        for (int i = 1; i < argc; ++i)
        {
            if (i > 1) commandLine << " ";
            commandLine << argv[i];
        }
    }
    
    CompletePluginHost app;
    app->initialise(commandLine);
    
    juce::MessageManager::getInstance()->runDispatchLoop();
    
    app->shutdown();
    juce::shutdownJuce_GUI();
    
    return 0;
}
#endif


void BlockLevelScheduler::processScheduledChanges() 
{
  if (!host) return;

  while (lastChangeIndex < scheduledChanges.size() && 
    currentBlock == scheduledChanges[lastChangeIndex].atBlock)
  {
    ScheduledParameterChange& change = scheduledChanges[lastChangeIndex];
    host->setPluginParameter(change.lpindex, change.parameterIndex, change.value);
    change.executed = true;
    lastChangeIndex++;
  }
}