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
  show_virtual_keyboard, hide_virtual_keyboard, route_virtual_keyboard, unroute_virtual_keyboard
};

enum send_cmd : uint8_t
{
  param_changed, param_changes_end, stop_playback, midi_note_event, midi_cc_event
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

  unordered_map<int, juce::AudioProcessorGraph::NodeID> loadedPlugins;  // int ID -> NodeID
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
      ___WRITEALLN(midi_note_event, noteEvent.noteNumber, noteEvent.velocity,
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

    // Send MIDI note events to notification queue
    if (message.isNoteOn() || message.isNoteOff())
    {
      MidiNoteEvent noteEvent;
      noteEvent.noteNumber = message.getNoteNumber();
      noteEvent.velocity = message.getVelocity();
      noteEvent.channel = message.getChannel();
      noteEvent.isNoteOn = message.isNoteOn();
      noteEvent.samplePosition = currentSamplePosition;
      midiNoteQueue.push(noteEvent);
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
    deviceManager.removeAudioCallback(&graphPlayer);
    deviceManager.closeAudioDevice();
    graphPlayer.setProcessor(nullptr);
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

  void listBadPaths()
  {
    cout << "badPaths.size()" << badPaths.size() << endl;
    WRITEALLC(uint32_t(badPaths.size()));
    for (string badpath : badPaths)
    {
      WRITEALLC(badpath);
    }
  }
  
  struct loadPluginByIndexR {uint32_t success = true; string name; uint32_t lpindex = -1; uint32_t uid = -1; string errmsg;};
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
  struct loadPluginR {uint32_t success = false; string errmsg; string name; uint32_t lpindex = -1; uint32_t uid;};

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

  struct rect {int lpindex; int x; int y; int w; int h;};
  int lastxpos = 0;
  vector<rect> rects;
  float httolerance = .5;
  
  int getmaxy(int x, int width)
  {
    int maxy=0;
    for(int i = 0; i < rects.size(); i++)
    {
      if (rects[i].x < x+width && rects[i].x+rects[i].w < x)
      {
        if (rects[i].y > maxy)
        {
          maxy=rects[i].y;
        }
      }
    }
    return maxy;
  }

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
          if (node->getProcessor()->hasEditor())
          {
            juce::AudioProcessorEditor* editor = node->getProcessor()->createEditor();

            if (editor)
            {
              auto window = make_unique<PluginWindow>(node->getProcessor()->getName(), editor, node->getProcessor(), lpindex);
              window->setVisible(true);
              pluginWindows[lpindex] = std::move(window);
            }

/*
            if (editor)
            {
              int width = editor->getWidth();
              int height = editor->getHeight();
              int x = rects[rects.size()-1].x;
              int y = rects[rects.size()-1].y;
              int w = rects[rects.size()-1].w;
              int maxy = 0;

              int newx, newy;
              if (getmaxy(x, width)+height-y>height/httolerance && x+width<screenWidth)
              {
                newx = 0; //debug: untested
                newy = getmaxy(0, width);
              }
              else
              {  
                newx = lastxpos;
                newy = getmaxy(lastxpos, width);
                lastxpos += width;
              }
              if (newy+height > screenHeight) 
              {
                newx = 0;
                newy = 0;
                rects.clear();
              }
              auto window = make_unique<PluginWindow>(node->getProcessor()->getName(), editor, node->getProcessor(), lpindex);
              window->setTopLeftPosition(newx, newy);
              window->setVisible(true);
              pluginWindows[lpindex] = move(window); 
              rects.push_back({newx, newy, width, height}); //todo: remove rect when window closes
            }

            */

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
      
      rects.erase( //todo: make rects a map instead of this?
        std::remove_if(rects.begin(), rects.end(),
          [lpindex](const rect& r) { 
            return r.lpindex == lpindex; 
          }),
        rects.end()
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
            int pluginId = pair.first;
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
        deviceManager.addAudioCallback(&graphPlayer);
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
      default:
      {
        cout << "command not recognized: " << commandtype << endl;
      }
    }
  }

  // Plugin window class
  class PluginWindow : public juce::DocumentWindow
  {
  public:
    PluginWindow(const juce::String& name,
      juce::AudioProcessorEditor* editor,
      juce::AudioProcessor* processor, int lpind)
      : DocumentWindow(name,
        juce::Desktop::getInstance().getDefaultLookAndFeel()
        .findColour(ResizableWindow::backgroundColourId),
        DocumentWindow::allButtons),
        lpindex(lpind),
        processor(processor)
    {
      setUsingNativeTitleBar(true);
      setContentOwned(editor, true);
      setResizable(editor->isResizable(), false); //true to enable a resizer on the bottom right corner
     //todo: how to show plugins on multiple monitors. juce supports getting info on multiple displays.

      centreWithSize(getWidth(), getHeight());


      juce::Rectangle<int> screenBounds =
        juce::Desktop::getInstance().getDisplays().displays[0].userArea;

      int screenWidth = screenBounds.getWidth();
      int screenHeight = screenBounds.getHeight();
    }

    int getPluginId() const { return lpindex; }

    void closeButtonPressed() override
    {
      setVisible(false);
    }

  private:
    juce::AudioProcessor* processor;
    int lpindex;
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
      centreWithSize(keyboardWidth, keyboardHeight);
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

  // map<> is always O(log n), unordered_map<> is normally O(1) but can get up to O(n)
  unordered_map<juce::AudioProcessor*, int> processorTolpindex;
  unordered_map<int, unique_ptr<PluginWindow>> pluginWindows;       // int ID -> Window
  int nextlpindex = 0;  // Auto-increment ID counter

  // Virtual keyboard
  juce::MidiKeyboardState virtualKeyboardState;
  unique_ptr<VirtualKeyboardWindow> virtualKeyboardWindow;
  unique_ptr<VirtualKeyboardListener> virtualKeyboardListener;

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
   /* freopen_s(&pCout, "CONIN$", "r", stdin);

    freopen_s(&pCout, "debug_log.txt", "w", stdout);
    freopen_s(&pCout, "CONOUT$", "w", stderr);*/


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