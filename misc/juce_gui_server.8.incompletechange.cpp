  //todo: record midi input to instruments
//todo: record audio
//todo: find out how Popsicle works and see about making this work that way instead of using a pipe
// 
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

enum cmd {
  load_plugin, load_plugin_by_index, scan_plugins, list_plugins, get_plugin_info, show_plugin_ui, hide_plugin_ui, set_parameter, get_parameter, connect_audio, connect_midi, start_playback,
  stop_playback, cmd_shutdown, remove_plugin, list_bad_paths, get_params_info, get_channels_info, schedule_midi_note, schedule_midi_cc, clear_midi_schedule, schedule_param_change
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
  vector<ScheduledParameterChange> scheduledChanges;
  uint64_t currentBlock = 0;
  int lastChangeIndex = 0;

public:
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
  uint64_t blockNumber;
  chrono::steady_clock::time_point timestamp; 
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

LockFreeParameterQueue parameterQueue;

class ParameterChangeListener : public juce::AudioProcessorListener 
{
public:
  void audioProcessorParameterChanged(juce::AudioProcessor* processor, int paramIndex, float value) override;
  void audioProcessorChanged(juce::AudioProcessor* processor, const juce::AudioProcessorListener::ChangeDetails& details) override 
  {
  }
};

ParameterChangeListener paramListener;

class CompletePluginHost : public juce::Timer, public juce::MidiInputCallback
{
public:
  CompletePluginHost()
    : deviceManager(),
    formatManager(),
    knownPluginList(),
    processorGraph(new juce::AudioProcessorGraph()),
    graphPlayer()
  {
    bool running = true;
    // Initialize format manager with plugin formats - be more explicit
    cout << "Initializing plugin formats..." << endl;

    // Add formats individually to make sure they're loaded

    formatManager.addFormat(new juce::VST3PluginFormat());
    cout << "Added VST3 format" << endl;

#ifdef _WIN32
    // Windows named pipe
    cout << "creating pipe" << endl;
    string pipePath = "\\\\.\\pipe\\" + pipeName;
    hPipe = CreateNamedPipeA(
      pipePath.c_str(),
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
    if (hPipe == INVALID_HANDLE_VALUE)
    {
      cout << "Failed to create named pipe" << endl;
      for (int i = 0; i < formatManager.getNumFormats(); ++i)
        throw std::runtime_error("Failed to create named pipe");
    }
#else
    // Unix named pipe (FIFO)
    string pipePath = "/tmp/" + pipeName;
    if (mkfifo(pipeName.c_str(), 0666) == -1 && errno != EEXIST) {
      throw runtime_error("Failed to create FIFO");
    }
    int pipe_fd = open(pipePath.c_str(), O_RDWR);
#endif

#if JUCE_PLUGINHOST_AU && (JUCE_MAC || JUCE_IOS)
    formatManager.addFormat(new juce::AudioUnitPluginFormat());
#endif
    initializeAudio();
    cout << "startTimer(50)" << endl;
    startTimer(updateRate);  
  }

  BlockLevelScheduler scheduler;

  ~CompletePluginHost()
  {
    stopTimer();
    shutdownAudio();
    processorGraph = nullptr;
#ifdef _WIN32
    CloseHandle(hPipe);
#else
    close(fd);
    unlink(pipePath.c_str());
#endif
  }
  unordered_map<int, juce::AudioProcessorGraph::NodeID> loadedPlugins;  // int ID -> NodeID
  atomic<bool> running = true;
  void testFunction() {
    std::cout << "Test function called successfully" << std::endl;
  }
  void initializeAudio()
  {
    // Setup audio device
    auto setup = deviceManager.getAudioDeviceSetup();
    setup.sampleRate = 48000;
    setup.bufferSize = 512;

    juce::String error = deviceManager.initialise(
      2,     // max input channels
      2,     // max output channels
      nullptr,  // no saved state
      true,     // select default device
      {},       // preferred device
      &setup    // preferred setup
    );

    if (error.isNotEmpty())
    {
      cerr << "Audio initialization error: " << error.toStdString() << endl;
    }

    // Initialize the MIDI scheduler
    midiScheduler = std::make_unique<MidiScheduler>(setup.sampleRate);

    // Setup graph player with the original processor graph
    graphPlayer.setProcessor(processorGraph.get());
    deviceManager.addAudioCallback(&graphPlayer);

    // Setup MIDI collector for hardware MIDI input
    midiCollector = make_unique<juce::MidiMessageCollector>();
    midiCollector->reset(setup.sampleRate);

    // Open MIDI inputs
    auto midiInputs = juce::MidiInput::getAvailableDevices();
    for (const auto& input : midiInputs)
    {
      deviceManager.setMidiInputDeviceEnabled(input.identifier, true);
      deviceManager.addMidiInputDeviceCallback(input.identifier, this);
    }
  }


  void timerCallback() override 
  {
    // Process parameter notifications first
    processParameterNotifications();

    // Then handle UI updates
    for (auto it = pluginWindows.begin(); it != pluginWindows.end();) 
    {
      if (!it->second || !it->second->isVisible()) 
      {
        it = pluginWindows.erase(it);

        int lpindex = it->second->getPluginId();
        rects.erase( //todo: make rects a map instead of this?
          std::remove_if(rects.begin(), rects.end(),
            [lpindex](const rect& r) { 
              return r.lpindex == lpindex; 
            }),
          rects.end()
        );

      } 
      else 
      {
        ++it;
      }
    }
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
    if (hPipe == INVALID_HANDLE_VALUE) {
      std::cerr << "ERROR: Invalid pipe handle!" << std::endl;
      return;
    }
    std::cout << "Pipe created, waiting for connection in command thread..." << std::endl;
#else
    std::cout << "FIFO created, waiting for connection in command thread..." << std::endl;
#endif
    // Don't connect here - let processCommands handle it
  }

  void setPluginParameter(int lpindex, int parameterIndex, float value) {
    auto it = loadedPlugins.begin();
    advance(it, lpindex);  // Move iterator to lpindex position
    if (it != loadedPlugins.end()) {
      auto node = processorGraph->getNodeForId(it->second);
      if (node && node->getProcessor()) {
        auto* processor = node->getProcessor();
        if (parameterIndex >= 0 && parameterIndex < processor->getParameters().size()) {
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

    // Close all plugin windows
    for (auto& pair : pluginWindows)
    {
      if (pair.second)
        pair.second->closeButtonPressed();
    }
    pluginWindows.clear();
  }

  void handleIncomingMidiMessage(juce::MidiInput* source,
    const juce::MidiMessage& message) override
  {
    // Route MIDI to the processor graph
    if (midiCollector)
      midiCollector->addMessageToQueue(message);
  }

private:
  std::unique_ptr<MidiScheduler> midiScheduler;
  juce::AudioPluginFormatManager formatManager;
  std::unordered_map<int, juce::AudioProcessorGraph::NodeID> midiRouterNodes;

  void shutdownAudio()
  {
    deviceManager.removeAudioCallback(&graphPlayer);
    deviceManager.closeAudioDevice();
    graphPlayer.setProcessor(nullptr);
  }

  int write4(void* buff, int n)
  {
#ifdef _WIN32
    DWORD bytesWritten;
    if (WriteFile(hPipe, buff, n, &bytesWritten, NULL)) {
      return bytesWritten;
    }
    return -1;  // Error
#else
    return write(pipe_fd, buff, n);
#endif
  }

  // Write string with length prefix

  class WriteBuffer {
  public:
    std::vector<char> data;

    void append(const void* ptr, size_t size) {
      const char* bytes = static_cast<const char*>(ptr);
      data.insert(data.end(), bytes, bytes + size);
    }

    const char* getBuffer() const { return data.data(); }
    size_t getSize() const { return data.size(); }
  };

  WriteBuffer* currentWriteBuffer = nullptr;

  int write2_string_with_buffer(const string& s)
  {
    int l1 = static_cast<int>(s.length());
    int l2 = 4 + l1;
    int tosend = l2;
    int totalWritten = 0;

    char* buff = new char[l2];
    memcpy(buff, &l1, 4);
    memcpy(buff + 4, s.c_str(), l1);

    // Store in buffer if one is active
    if (currentWriteBuffer) {
      currentWriteBuffer->append(buff, l2);
    }

    char* current_pos = buff;
    while (tosend > 0) {
      int byteswritten = write4(current_pos, tosend);
      if (byteswritten > 0) {
        tosend -= byteswritten;
        current_pos += byteswritten;
        totalWritten += byteswritten;
      }
      else {
        delete[] buff;
        throw runtime_error("Error writing to pipe");
      }
    }
    delete[] buff;
    return totalWritten;
  }
  
  void write2_string(const string& s)
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
      int byteswritten = write4(current_pos, tosend);
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

  template<typename T>
  int write2_binary_with_buffer(T n)
  {
    // Store in buffer if one is active
    if (currentWriteBuffer) {
      currentWriteBuffer->append(&n, sizeof(T));
    }

    int tosend = sizeof(T);
    int totalWritten = 0;
    char* current_pos = reinterpret_cast<char*>(&n);

    while (tosend > 0) {
      int byteswritten = write4(current_pos, tosend);
      if (byteswritten > 0) {
        tosend -= byteswritten;
        current_pos += byteswritten;
        totalWritten += byteswritten;
      }
      else {
        throw runtime_error("Error writing to pipe");
      }
    }
    return totalWritten;
  }

  // Write binary data for non-string types
  template<typename T>
  void write2_binary(T n)
  {
    int tosend = sizeof(T);
    char* current_pos = reinterpret_cast<char*>(&n);

    while (tosend > 0) {
      int byteswritten = write4(current_pos, tosend);
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

  template<typename... Args>
  WriteBuffer writeAllWithBuffer(Args&&... args) {
    WriteBuffer buffer;
    currentWriteBuffer = &buffer;

    // Call write2 for each argument
    ((write2(std::forward<Args>(args))), ...);

    currentWriteBuffer = nullptr;
    return buffer;
  }

  // Overloaded write2 functions
  inline void write2(const string& s)
  {
    write2_string(s);
  }

  inline void write2(bool b)
  {
    uint8_t byte = b ? 1 : 0;
    write2_binary(byte);
  }

  template<typename T>
  inline void write2(T n)
  {
    write2_binary(n);
  }

  template<typename... Args>
  void writeAll(Args&&... args) {
    ((write2(std::forward<Args>(args))), ...);  // C++17 fold expression
  }

#define WRITEALL(...) writeAll(__VA_ARGS__)
#define WRITEALL_BUFFER(...) writeAllWithBuffer(__VA_ARGS__) //debug. //warning: doesn't treat bools the same way as WRITEALL, i didn't finish making the change because i probably won't use this.
  
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
      if (!ReadFile(hPipe, buffer + totalRead, 
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
      if (!ReadFile(hPipe, buffer + totalRead, length - totalRead, &bytesRead, NULL)) {
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
      ssize_t bytesRead = read(pipe_fd, buffer + totalRead, length - totalRead);
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

  void processCommands() {
    std::cout << "Thread: processCommands started" << std::endl;

    while (running) {
      try {
        // Wait for pipe to be ready
        std::cout << "Thread: Waiting for pipe connection..." << std::endl;

        // Reset pipe state
        pipeReady = false;

#ifdef _WIN32
        // Disconnect any existing client
        if (hPipe != INVALID_HANDLE_VALUE) {
          DisconnectNamedPipe(hPipe);
        }

        // Wait for new connection
        std::cout << "Waiting for client to connect..." << std::endl;
        if (ConnectNamedPipe(hPipe, NULL) || GetLastError() == ERROR_PIPE_CONNECTED) {
          std::cout << "Client connected" << std::endl;
          pipeReady = true;
        } else {
          std::cerr << "ConnectNamedPipe failed: " << GetLastError() << std::endl;
          std::this_thread::sleep_for(std::chrono::seconds(1));
          continue;
        }
#else
        // Linux - reopen the FIFO if needed
        if (pipe_fd >= 0) {
          close(pipe_fd);
        }

        string pipePath = "/tmp/" + pipeName;
        pipe_fd = open(pipePath.c_str(), O_RDWR);
        if (pipe_fd < 0) {
          std::cerr << "Failed to open FIFO: " << strerror(errno) << std::endl;
          std::this_thread::sleep_for(std::chrono::seconds(1));
          continue;
        }
        pipeReady = true;
#endif

        // Process commands until disconnection
        std::cout << "Thread: Starting command loop" << std::endl;
        while (running && pipeReady) {
          char command = READFROMPIPE(char);
          processCommand(command);
        }

      } catch (const std::exception& e) {
        std::cout << "Thread: Disconnected: '" << e.what() << "'" << std::endl;
        std::cout << "Thread: Will wait for reconnection..." << std::endl;
        pipeReady = false;

        // Wait a bit before trying to reconnect
        std::this_thread::sleep_for(std::chrono::seconds(1));

      } catch (...) {
        std::cout << "Thread: Unknown exception, attempting reconnect..." << std::endl;
        pipeReady = false;
        std::this_thread::sleep_for(std::chrono::seconds(1));
      }
    }

    std::cout << "Thread: processCommands ending" << std::endl;
  }
  struct paramInfo
  {
    string name; 
    float minValue; //todo: I'm not really sure which ones of these should be integers.
    float maxValue;
    float interval;
    float skewFactor;
    float defaultValue;
    float value;
    uint32_t numSteps;
    bool isDiscrete;
    bool isBoolean;
    bool isOrientationInverted;
    bool isAutomatable;
    bool isMetaParameter;
  };

  struct getPluginInfoR {
    bool failure = false;
    int32_t numParams = 0;
    vector<paramInfo> params;
    string errmsg;
  };

  getPluginInfoR getParamsInfo(int id) 
  {
    getPluginInfoR resp;
    vector<paramInfo> paramsR;
    
    auto it = loadedPlugins.find(id);
    if (it != loadedPlugins.end()) {
      auto node = processorGraph->getNodeForId(it->second);
      if (node && node->getProcessor()) {
        auto* processor = node->getProcessor();
        const auto& params = processor->getParameters();

        resp.numParams = params.size();
        resp.success = true;

        for (auto* param : params) 
        {
          // Get parameter name
          paramInfo paramR;
          paramR.name = param->getName(1000).toStdString(); //todo: what is the 100 for?
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
          paramsR.push_back(paramR);
        }
      }
    } 
    else 
    {
      resp.errmsg = "Plugin not loaded";
    }
    resp.params = paramsR;
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

  getChannnelsInfoR getChannelsInfo(int id) 
  {
    getChannnelsInfoR resp;
    vector<busR> buses;

    auto it = loadedPlugins.find(id);
    if (it != loadedPlugins.end()) {
      auto node = processorGraph->getNodeForId(it->second);
      if (node && node->getProcessor()) {
        auto* processor = node->getProcessor();

        // Basic channel counts

        // MIDI capabilities
        resp.acceptsMidi = processor->acceptsMidi();
        resp.producesMidi = processor->producesMidi();

        // Bus layout info
        auto layout = processor->getBusesLayout();

        // For each input/output bus:
        vector<busR> inputChannels;
        for (int i = 0; i < processor->getBusCount(true); ++i) 
        {
          busR bus2;
          vector<string> channelTypes;
          auto* bus = processor->getBus(true, i);  // true = input
          int numChannels = bus->getNumberOfChannels();
          string busName = bus->getName().toStdString();
          bool isEnabled = bus->isEnabled();
          auto& inputBus = processor->getBus(true, i)->getCurrentLayout();
          for (int chan = 0; chan < numChannels; chan++)
          {
            auto channelType = inputBus.getTypeOfChannel(chan);
            string channelName = juce::AudioChannelSet::getChannelTypeName(channelType).toStdString();
            channelTypes.push_back(channelName);
          }
          bus2.channelTypes = channelTypes;
          bus2.numChannels = numChannels;
          bus2.isEnabled = isEnabled;
          bus2.mainBusLayout = inputBus.getDescription().toStdString();
          resp.inputBuses.push_back(bus2);
        }

        for (int i = 0; i < processor->getBusCount(false); ++i) 
        {
          busR bus2;
          vector<string> channelTypes;
          auto* bus = processor->getBus(false, i); 
          int numChannels = bus->getNumberOfChannels();
          string busName = bus->getName().toStdString();
          bool isEnabled = bus->isEnabled();
          auto& inputBus = processor->getBus(false, 0)->getCurrentLayout();
          for (int chan = 0; chan < numChannels; chan++)
          {
            auto channelType = inputBus.getTypeOfChannel(chan);
            string channelName = juce::AudioChannelSet::getChannelTypeName(channelType).toStdString();
            channelTypes.push_back(channelName);
          }
          bus2.channelTypes = channelTypes;
          bus2.numChannels = numChannels;
          bus2.isEnabled = isEnabled;
          bus2.mainBusLayout = inputBus.getDescription().toStdString();
          resp.outputBuses.push_back(bus2);
        }
        resp.success = true;
      }
    } 
    else 
    {
      resp.errmsg = "Plugin not loaded";
    }
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
    WRITEALL(uint32_t(availablePlugins.size()));
    for (auto plugin : availablePlugins)
    {
      WRITEALL(uint32_t(plugin.desc.isInstrument), uint32_t(plugin.desc.uniqueId), uint32_t(plugin.desc.numInputChannels), uint32_t(plugin.desc.numOutputChannels), plugin.desc.name.toStdString(),
        plugin.desc.descriptiveName.toStdString(), plugin.desc.pluginFormatName.toStdString(), plugin.desc.category.toStdString(), plugin.desc.manufacturerName.toStdString(), plugin.desc.version.toStdString(),
        plugin.desc.fileOrIdentifier.toStdString(), plugin.desc.lastFileModTime.toString(true, true).toStdString(), plugin.path);
    }
  }

  void listBadPaths()
  {
    cout << "badPaths.size()" << badPaths.size() << endl;
    WRITEALL(uint32_t(badPaths.size()));
    for (string badpath : badPaths)
    {
      WRITEALL(badpath);
    }
  }
  
  struct loadPluginByIndexR {uint32_t success = true; string name; uint32_t lpindex = -1; uint32_t uid; string errmsg;};
  loadPluginByIndexR loadPluginByIndex(int apindex) {  // Return the assigned plugin ID
    loadPluginByIndexR resp;
    if (apindex >= 0 && apindex < availablePlugins.size()) 
    {
      const availablePlugin& pluginInfo = availablePlugins[apindex];

      juce::String errorMessage;
      auto plugin = formatManager.createPluginInstance(
        pluginInfo.desc, sampleRate, blockSize, errorMessage);

      if (plugin != nullptr) 
      {
        if (realtime) plugin->addListener(&paramListener);
        juce::AudioProcessor* processorPtr = plugin.get();
        auto node = processorGraph->addNode(move(plugin));
        if (node != nullptr) {
          int lpindex = nextlpindex++;
          loadedPlugins[lpindex] = node->nodeID;
          processorTolpindex[processorPtr] = lpindex;
          resp.lpindex = lpindex;  // Return the ID Python should use
          resp.name = pluginInfo.desc.name.toStdString();
          resp.uid = node->nodeID.uid;
          resp.success = true;
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

  void startPlayback()
  {
  }

  void stopPlayback()
  {
  }

  void removePlugin(int lpindex) {
    auto it = loadedPlugins.find(lpindex);
    if (it != loadedPlugins.end()) {
      auto node = processorGraph->getNodeForId(it->second);
      if (node) {
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
    auto commandtype = (cmd)command;
    switch (commandtype)
    {
      case load_plugin:
      {
        auto response = loadPlugin(READFROMPIPE(string));
        WRITEALL(response.success, response.name, response.lpindex, response.uid, response.errmsg);
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
        WRITEALL(response.success, response.name, response.lpindex, response.uid, response.errmsg);
        break;
      }
      case scan_plugins:
      {
        WRITEALL(scanPluginDirectories());
        break;
      }
      case list_plugins:
      {
        listAvailablePlugins();
        break;
      }
      case show_plugin_ui:
      {
        auto response = showPluginUI(READFROMPIPE(uint32_t));
        WRITEALL(response.success, response.errmsg);
        break;
      }
      case hide_plugin_ui:
      {
        WRITEALL(hidePluginUI(READFROMPIPE(uint32_t)));
        break;
      }
      case set_parameter: //probably won't be used.
      {
        auto response = setParameter(READFROMPIPE(uint32_t), READFROMPIPE(uint32_t), READFROMPIPE(float));
        WRITEALL(response.success, response.errmsg);
        break;
        break;
      }
      case get_parameter: //probably won't be used.
      {
        auto response = getParameter(READFROMPIPE(uint32_t), READFROMPIPE(uint32_t));
        WRITEALL(response.success, response.value, response.errmsg);
        if(response.success) WRITEALL(response.value);
        else WRITEALL(response.errmsg);
        break;
      }
      case connect_audio:
      {
        WRITEALL(connectAudio(READFROMPIPE(uint32_t), READFROMPIPE(uint32_t), READFROMPIPE(uint32_t), READFROMPIPE(uint32_t)));
        break;
      }
      case connect_midi:
      {
        WRITEALL(connectMidi(READFROMPIPE(uint32_t), READFROMPIPE(uint32_t)));
        break;
      }
      case start_playback:
      {
        startPlayback();
        break;
      }
      case stop_playback:
      {
        stopPlayback();
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
        getPluginInfoR resp = getParamsInfo(READFROMPIPE(uint32_t));
        WRITEALL(resp.success, resp.numParams, resp.errmsg);
        for (auto param : resp.params)
        {
          WRITEALL(param.name, param.minValue, param.maxValue, param.interval,param.defaultValue, param.skewFactor, param.value, param.numSteps, param.isDiscrete, param.isBoolean, 
            param.isOrientationInverted, param.isAutomatable, param.isMetaParameter);
        }
        break;
      }
      case get_channels_info:
      {
        getChannnelsInfoR resp = getChannelsInfo(READFROMPIPE(uint32_t));
        WRITEALL(resp.success, resp.acceptsMidi, resp.producesMidi);
        WRITEALL(uint32_t(resp.inputBuses.size()));
        for (auto bus : resp.inputBuses)
        {
          WRITEALL(bus.numChannels, uint32_t(bus.channelTypes.size()));
          for (string channelType : bus.channelTypes)
          {
            WRITEALL(channelType);
          }
          WRITEALL(bus.isEnabled, bus.mainBusLayout);
        }

        WRITEALL(uint32_t(resp.outputBuses.size()));
        for (auto bus : resp.inputBuses)
        {
          WRITEALL(bus.numChannels, uint32_t(bus.channelTypes.size()));
          for (string channelType : bus.channelTypes)
          {
            WRITEALL(channelType);
          }
          WRITEALL(bus.isEnabled, bus.mainBusLayout);
        }
        WRITEALL(resp.errmsg);
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

private:
  // Core components
#ifdef _WIN32
  HANDLE hPipe;
#else
  int pipe_fd;
#endif
  bool pipeReady = false;
  long int currentBlock = 0;
  bool realtime = false;
  bool threadStarted = false;
  juce::AudioDeviceManager deviceManager;
  juce::KnownPluginList knownPluginList;
  unique_ptr<juce::AudioProcessorGraph> processorGraph;
  juce::AudioProcessorPlayer graphPlayer;
  unique_ptr<juce::MidiMessageCollector> midiCollector;

  // map<> is always O(log n), unordered_map<> is normally O(1) but can get up to O(n)
  unordered_map<juce::AudioProcessor*, int> processorTolpindex;
  unordered_map<int, unique_ptr<PluginWindow>> pluginWindows;       // int ID -> Window
  int nextlpindex = 0;  // Auto-increment ID counter

  struct availablePlugin
  {
    string path;
    juce::PluginDescription desc;
  };
  vector<availablePlugin> availablePlugins;
  set<string> badPaths;

  // Communication
  thread commandThread;
  //queue<Command> commandQueue;
  mutex commandMutex;
  condition_variable commandCv;

  void processParameterNotifications()
  {
    ParameterChangeEvent event;
    while (parameterQueue.pop(event)) 
    {
      // Send to Python via pipe (safe here - not in audio thread)
      WRITEALL("PARAM_CHANGED", event.lpindex, event.parameterIndex, event.value);
    }
  }

  JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(CompletePluginHost)
};

void audioProcessorParameterChanged(juce::AudioProcessor* processor, int paramIndex, float value) {
  if (!suppressNotifications) {
    ParameterChangeEvent event{app->findlpindex(processor), paramIndex, value, app->scheduler.getCurrentBlock()};
    parameterQueue.push(event);  // Lock-free, real-time safe
  }
}

void ParameterChangeListener::audioProcessorParameterChanged(juce::AudioProcessor* processor, int paramIndex, float value)  
{
  ParameterChangeEvent changeevent;
  if (!suppressNotifications) 
  {
    int lpindex = app->findlpindex(processor);
    if (lpindex != -1) 
    {
      changeevent.blockNumber = app->scheduler.getCurrentBlock();
      changeevent.parameterIndex = paramIndex;
      changeevent.value = value;
      changeevent.lpindex = lpindex;
      changeevent.timestamp = chrono::steady_clock::now();
      parameterQueue.push(changeevent);
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
  ScheduledParameterChange change;
  while (currentBlock == ((change = scheduledChanges[lastChangeIndex]).atBlock))
  {
    lastChangeIndex++;
    app->setPluginParameter(change.lpindex, change.parameterIndex, change.value);
    change.executed = true;
    cout << "Executed parameter change at block " << currentBlock //debug
      << ": plugin " << change.lpindex
      << ", param " << change.parameterIndex
      << " = " << change.value << endl;
  }
}
