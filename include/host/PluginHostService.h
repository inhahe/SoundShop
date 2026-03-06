#pragma once
#include "Common.h"
#include "ServerState.h"
#include "services/IpcService.h"
#include "services/AudioService.h"
#include "services/UiService.h"
#include "audio/Events.h"
#include "audio/MidiScheduler.h"
#include "audio/MidiSourceNode.h"
#include "audio/AudioFilePlayerNode.h"
#include "audio/RecordingAudioCallback.h"
#include "audio/Schedulers.h"
#include "gui/VirtualKeyboardListener.h"
#include "gui/RoutingButton.h"
#include "gui/EditorWithButtonsComponent.h"
#include "gui/VirtualKeyboardWindow.h"

class PluginHostService : public juce::Timer, public juce::MidiInputCallback
{
  friend class IpcService;
  friend class UiService;

public:

  void setIpc(IpcService* ipc) { ipc_ = ipc; }
  void setAudio(AudioService* audio) { audio_ = audio; }
  void setUi(UiService* ui) { ui_ = ui; }


// Former globals, now stored in a single ServerState owned by main().
ServerState& state;
int& sampleRate;
int& blockSize;
bool& suppressNotifications;
std::string& pipeName;
int& updateRate;
std::atomic<int64_t>& currentSamplePosition;

LockFreeParameterQueue& parameterQueue;
LockFreeMidiQueue<MidiNoteEvent>& midiNoteQueue;
LockFreeMidiQueue<MidiCCEvent>& midiCCQueue;
LockFreeMidiQueue<MidiNoteEvent>& virtualKeyboardNoteQueue;
LockFreeMidiQueue<MidiCCEvent>& virtualKeyboardCCQueue;

  SampleParamScheduler paramScheduler;
  ParameterChangeListener paramListener;

  explicit PluginHostService(ServerState& s)
    : state(s)
    , sampleRate(s.config.sampleRate)
    , blockSize(s.config.blockSize)
    , suppressNotifications(s.config.suppressNotifications)
    , pipeName(s.config.pipeName)
    , updateRate(s.config.updateRate)
    , currentSamplePosition(s.currentSamplePosition)
    , parameterQueue(s.queues.parameterQueue)
    , midiNoteQueue(s.queues.midiNoteQueue)
    , midiCCQueue(s.queues.midiCCQueue)
    , virtualKeyboardNoteQueue(s.queues.virtualKeyboardNoteQueue)
    , virtualKeyboardCCQueue(s.queues.virtualKeyboardCCQueue)
  {
    paramScheduler.setHost(this);
    paramListener.setContext(this, &state);
    paramScheduler.setAutomationQuantum(blockSize); // or 1 for full sample accuracy
    // Initialize format manager with plugin formats - be more explicit
    //cout << "Initializing plugin formats..." << endl;

    // Add formats individually to make sure they're loaded

    formatManager.addFormat(new juce::VST3PluginFormat()); 
    //cout << "Added VST3 format" << endl;
    processorGraph = std::make_unique<juce::AudioProcessorGraph>();

// IPC transport (named pipes/FIFOs) is owned by IpcService.

#if JUCE_PLUGINHOST_AU && (JUCE_MAC || JUCE_IOS) //todo: what about Linux?
    formatManager.addFormat(new juce::AudioUnitPluginFormat());
#endif
    //cout << "startTimer(" << updateRate << ")" << endl;
    startTimer(updateRate);

    // Initialize virtual keyboard listener
    virtualKeyboardListener = std::make_unique<VirtualKeyboardListener>(this);
    virtualKeyboardState.addListener(virtualKeyboardListener.get());
  }

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
    int key;
    int parameterIndex;
    int ccController;
    int midiChannel;  // -1 means any channel
  };
  std::vector<CCMapping> ccMappings;

  ~PluginHostService()
  {
    //cout << "DESTRUCTOR CALLED - shutting down" << endl;
    stopTimer();
    shutdownAudio();
    processorGraph = nullptr;
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
    //cout << "queing parameter notification" << endl;

    lock_guard<std::mutex> lock(notificationMutex);
    pendingNotifications.push(event);
  }

  // Call this from command thread instead
void sendQueuedParameterNotifications()
{
  ParameterChangeEvent ev;
  while (parameterQueue.pop(ev))
  {
    if (!ipc_ || !ipc_->isNotificationReady()) break;

    WRITEALLN(param_changed,
              ev.key,
              ev.parameterIndex,
              ev.value,
              (uint64_t)ev.atSample);

#ifdef _WIN32
    if (GetLastError() == ERROR_BROKEN_PIPE) { break; }
#else
    if (errno == EPIPE) { break; }
#endif
  }
}
  void sendQueuedMidiNotifications() {
    // Send MIDI note events
    MidiNoteEvent noteEvent;
    while (midiNoteQueue.pop(noteEvent))
    {
      if (!ipc_ || !ipc_->isNotificationReady()) break;
      WRITEALLN(midi_note_event, noteEvent.noteNumber, noteEvent.velocity,
                noteEvent.channel, uint32_t(noteEvent.isNoteOn), noteEvent.samplePosition);
#ifdef _WIN32
      if (GetLastError() == ERROR_BROKEN_PIPE) {
        break;
      }
#else
      if (errno == EPIPE) {
        break;
      }
#endif
    }

    // Send MIDI CC events
    MidiCCEvent ccEvent;
    while (midiCCQueue.pop(ccEvent))
    {
      if (!ipc_ || !ipc_->isNotificationReady()) break;
      WRITEALLN(midi_cc_event,
          ccEvent.controller,
          ccEvent.value,
          ccEvent.channel,
          (uint64_t)ccEvent.atSample);

#ifdef _WIN32
      if (GetLastError() == ERROR_BROKEN_PIPE) {
        break;
      }
#else
      if (errno == EPIPE) {
        break;
      }
#endif
    }

    // Send virtual keyboard note events (separate from physical keyboard)
    MidiNoteEvent virtualNoteEvent;
    while (virtualKeyboardNoteQueue.pop(virtualNoteEvent))
    {
      if (!ipc_ || !ipc_->isNotificationReady()) break;
      WRITEALLN(virtual_keyboard_note_event, virtualNoteEvent.noteNumber, virtualNoteEvent.velocity,
                virtualNoteEvent.channel, uint32_t(virtualNoteEvent.isNoteOn), virtualNoteEvent.samplePosition);
#ifdef _WIN32
      if (GetLastError() == ERROR_BROKEN_PIPE) {
        break;
      }
#else
      if (errno == EPIPE) {
        break;
      }
#endif
    }

    // Send virtual keyboard CC events (for future use)
    MidiCCEvent virtualCCEvent;
    while (virtualKeyboardCCQueue.pop(virtualCCEvent))
    {
      if (!ipc_ || !ipc_->isNotificationReady()) break;
      WRITEALLN(virtual_keyboard_cc_event,
                virtualCCEvent.controller,
                virtualCCEvent.value,
                virtualCCEvent.channel,
                (uint64_t)virtualCCEvent.atSample);
#ifdef _WIN32
      if (GetLastError() == ERROR_BROKEN_PIPE) {
        break;
      }
#else
      if (errno == EPIPE) {
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
    //std::cout << "Test function called successfully" << std::endl;
  }
    
  int findkey(juce::AudioProcessor* processor)
  {
    auto it = processorToKey.find(processor);
    if (it != processorToKey.end()) {
      return it->second;
    }
    return -1;  // Not found
  }

    void initialise(const juce::String& /*commandLine*/) {
    // IPC threads are started by IpcService (ServerServices::start).
  }




  void setPluginParameter(int key, int parameterIndex, float value) 
  {
    auto it = loadedPlugins.begin();
    advance(it, key);  // Move iterator to key position
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
      if (audio_ && audio_->midiCollector())
        audio_->midiCollector()->addMessageToQueue(routedMessage);
    }
  }

  // Toggle MIDI keyboard routing for a plugin
  void toggleMidiKeyboardRouting(int key)
  {
    //cout << "toggleMidiKeyboardRouting called with key=" << key << ", current keyboardRoutedPlugin=" << keyboardRoutedPlugin << endl;
    if (keyboardRoutedPlugin == key)
    {
      // Unroute if already routed to this plugin
      keyboardRoutedPlugin = -3;
      //cout << "MIDI keyboard unrouted from plugin " << key << endl;
    }
    else
    {
      // Route to this plugin
      keyboardRoutedPlugin = key;
      //cout << "MIDI keyboard routed to plugin " << key << endl;
    }

    //cout << "About to send WRITEALLN notification: midi_keyboard_routed, key=" << keyboardRoutedPlugin << ", samplePos=" << currentSamplePosition << endl;
    // Send notification to Python client
    WRITEALLN(midi_keyboard_routed, keyboardRoutedPlugin, currentSamplePosition.load());
    //cout << "WRITEALLN notification sent successfully" << endl;

    //cout << "About to update all routing indicators" << endl;
    // Update all plugin windows to reflect the routing change
    updateAllRoutingIndicators();
    //cout << "toggleMidiKeyboardRouting completed" << endl;
  }

  // Toggle virtual keyboard routing for a plugin
  void toggleVirtualKeyboardRouting(int key)
  {
    //cout << "toggleVirtualKeyboardRouting called with key=" << key << ", current virtualKeyboardRoutedPlugin=" << virtualKeyboardRoutedPlugin << endl;
    if (virtualKeyboardRoutedPlugin == key)
    {
      // Unroute if already routed to this plugin
      virtualKeyboardRoutedPlugin = -1;
      //cout << "Virtual keyboard unrouted from plugin " << key << endl;

      // Hide the virtual keyboard window when unrouted
      if (virtualKeyboardWindow)
      {
        virtualKeyboardWindow->setVisible(false);
        //cout << "Virtual keyboard window hidden after unrouting" << endl;
      }
    }
    else
    {
      // Route to this plugin
      virtualKeyboardRoutedPlugin = key;
      //cout << "Virtual keyboard routed to plugin " << key << endl;

      // Show the virtual keyboard window when routed
      if (!virtualKeyboardWindow)
      {
        virtualKeyboardWindow = std::make_unique<VirtualKeyboardWindow>(virtualKeyboardState);
        virtualKeyboardWindow->setVisible(true);
        //cout << "Virtual keyboard window created and shown" << endl;
      }
      else
      {
        virtualKeyboardWindow->setVisible(true);
        virtualKeyboardWindow->toFront(true);
        //cout << "Virtual keyboard window shown and brought to front" << endl;
      }
    }

    //cout << "About to send WRITEALLN notification: virtual_keyboard_routed, key=" << virtualKeyboardRoutedPlugin << ", samplePos=" << currentSamplePosition << endl;
    // Send notification to Python client
    WRITEALLN(virtual_keyboard_routed, virtualKeyboardRoutedPlugin, currentSamplePosition.load());
    //cout << "WRITEALLN notification sent successfully" << endl;

    //cout << "About to update all routing indicators" << endl;
    // Update all plugin windows to reflect the routing change
    updateAllRoutingIndicators();
    //cout << "toggleVirtualKeyboardRouting completed" << endl;
  }

  // Update routing indicators on all plugin windows
  void updateAllRoutingIndicators()
  {
    //cout << "updateAllRoutingIndicators called, pluginWindows.size()=" << pluginWindows.size() << endl;
    juce::MessageManager::callAsync([this]() {
      //cout << "updateAllRoutingIndicators async callback executing" << endl;
      int count = 0;
      for (auto& pair : pluginWindows)
      {
        //cout << "  Updating window for key=" << pair.first << ", window ptr=" << (void*)pair.second.get() << endl;
        if (pair.second)
        {
          pair.second->updateRoutingIndicators(keyboardRoutedPlugin, virtualKeyboardRoutedPlugin);
          count++;
        }
      }
      //cout << "  Updated " << count << " plugin windows" << endl;
    });
    //cout << "updateAllRoutingIndicators callAsync scheduled" << endl;
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
        //cout << "Next available recording filename: " << filename << endl;
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
      //cout << "Already recording" << endl;
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

        //cout << "Recording started: file=" << currentRecordingFilename
             //<< ", startSample=" << recordingStartSample
             //<< ", sampleRate=" << sampleRate << endl;

        // Send notification
        WRITEALLN(recording_started, currentRecordingFilename, recordingStartSample);
      }
      else
      {
        //cout << "ERROR: Failed to create audio writer" << endl;
      }
    }
    else
    {
      //cout << "ERROR: Failed to create output stream for " << currentRecordingFilename << endl;
    }
  }

  // Stop recording
  void stopRecording()
  {
    if (!isRecording)
    {
      //cout << "Not currently recording" << endl;
      return;
    }

    audioWriter.reset();
    isRecording = false;

    //cout << "Recording stopped: file=" << currentRecordingFilename
         //<< ", duration=" << (currentSamplePosition - recordingStartSample) << " samples" << endl;

    // Send notification
    WRITEALLN(recording_stopped, currentRecordingFilename, currentSamplePosition.load());
  }

  // Toggle monitoring on/off
  void toggleMonitoring()
  {
    isMonitoring = !isMonitoring;
    //cout << "Monitoring " << (isMonitoring ? "enabled" : "disabled") << endl;

    // Send notification
    WRITEALLN(monitoring_changed, uint32_t(isMonitoring), currentSamplePosition.load());
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

  void updateAudioFilePlayerTimeline()
  {
    const int64_t t = paramScheduler.getTimelineSample();

    for (auto& kv : audioFilePlayerNodes)
    {
      auto node = processorGraph->getNodeForId(kv.second);
      if (!node) continue;

      if (auto* p = dynamic_cast<AudioFilePlayerNode*>(node->getProcessor()))
        p->setTimelineSamplePosition(t);
    }
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
    //cout << "Scheduled ordered note: order=" << orderNumber
         //<< ", note=" << noteNumber << ", velocity=" << velocity << endl;
  }

  void sortOrderedNotes()
  {
    // Sort by order number
    std::sort(orderedNotes.begin(), orderedNotes.end(),
      [](const OrderedNote& a, const OrderedNote& b) { return a.orderNumber < b.orderNumber; });
    //cout << "Sorted " << orderedNotes.size() << " ordered notes" << endl;
  }

  void clearOrderedNotes()
  {
    orderedNotes.clear();
    currentOrderIndex = 0;
    //cout << "Cleared ordered notes" << endl;
  }

  void startOrderedPlayback(bool useKeyboardVelocity = true, bool useKeyboardDuration = true)
  {
    if (orderedNotes.empty())
    {
      //cout << "WARNING: No ordered notes to play" << endl;
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
      //cout << "Started ordered playback immediately (keyboard velocity: "
           //<< useKeyboardVelocity << ", keyboard duration: " << useKeyboardDuration << ")" << endl;
      WRITEALLN(ordered_playback_started, currentSamplePosition.load());
    }
    else
    {
      orderedPlaybackScheduled = true;
      orderedPlaybackActive = false;
      currentOrderIndex = 0;
      //cout << "Ordered playback scheduled to start with audio playback (keyboard velocity: "
           //<< useKeyboardVelocity << ", keyboard duration: " << useKeyboardDuration << ")" << endl;
      WRITEALLN(ordered_playback_started, int64_t(0));
    }
  }

  void stopOrderedPlayback()
  {
    orderedPlaybackActive = false;
    orderedPlaybackScheduled = false;
    //cout << "Stopped ordered playback at index " << currentOrderIndex << endl;
    WRITEALLN(ordered_playback_stopped, currentSamplePosition.load());
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
      midiScheduler->scheduleMidiMessage(keyboardRoutedPlugin, noteOn, 0.0);

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
        // Use stored duration - schedule note off after duration
        juce::MidiMessage noteOff = juce::MidiMessage::noteOff(note.channel, note.noteNumber);
        double durationSeconds = note.duration / (double)sampleRate;
        midiScheduler->scheduleMidiMessage(keyboardRoutedPlugin, noteOff, durationSeconds);
      }

      triggeredNotes.push_back(note.noteNumber);
//      //cout << "Triggered ordered note: order=" << currentOrder
//           << ", note=" << note.noteNumber
//           << ", velocity=" << velocity
//           << ", duration=" << (useKeyboardDurationForOrdered ? "keyboard" : std::to_string(note.duration)) << endl;

      currentOrderIndex++;
    }

    // Send notification with all triggered notes
    WRITEALLN(ordered_note_triggered, currentOrder, currentOrderIndex, (int)orderedNotes.size());

    // Check if we've finished the sequence
    if (currentOrderIndex >= orderedNotes.size())
    {
      ////cout << "Ordered playback sequence completed" << endl;
      orderedPlaybackActive = false;
      WRITEALLN(ordered_playback_stopped, currentSamplePosition.load());
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
      midiScheduler->scheduleMidiMessage(keyboardRoutedPlugin, noteOff, 0.0);
      ////cout << "Released ordered note: note=" << activeNote.noteNumber << endl;
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
      if (audio_ && audio_->midiCollector())
        audio_->midiCollector()->addMessageToQueue(routedMessage);
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
      ccEvent.atSample = currentSamplePosition;
      midiCCQueue.push(ccEvent);

      // Apply CC to parameter mappings
      for (const auto& mapping : ccMappings)
      {
        if (mapping.ccController == cc &&
            (mapping.midiChannel == -1 || mapping.midiChannel == channel))
        {
          auto it = loadedPlugins.find(mapping.key);
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
    if (audio_ && audio_->midiCollector())
      audio_->midiCollector()->addMessageToQueue(message);
  }

  void processScheduledEvents(int numSamples) // pass numSamples in!
  {
    if (!isPlaying) return;

    // 1) apply scheduled parameter changes that fall inside this block
    paramScheduler.processBlock(numSamples);

    // 2) update timeline
    paramScheduler.advance(numSamples);

    // 3) stop condition in SAMPLES (not blocks)
    if (paramScheduler.getTimelineSample() >= playbackEndSample)
      stopPlayback();
  }
  private:

  IpcService* ipc_ = nullptr;
  AudioService* audio_ = nullptr;
  UiService* ui_ = nullptr;


  bool offlineMode = true;
  uint64_t playbackEndSample = 0;
  queue<ParameterChangeEvent> pendingNotifications;
  mutex notificationMutex;

  
int write4c(void* buff, int n)
{
  if (!ipc_) return -1;
  return ipc_->writeCommand(buff, (size_t)n) ? n : -1;
}

int write4n(void* buff, int n)
{
  if (!ipc_) return -1;
  return ipc_->writeNotification(buff, (size_t)n) ? n : -1;
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
        throw runtime_error("Error writing to pipe (errno: " + to_string(errno) + ")");
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
        throw runtime_error("Error writing to pipe (errno: " + to_string(errno) + ")")
#endif
          break;  // Exit on error
      }
    }
  }

  void write2n_string(const string& s)
  {
    int l1 = static_cast<int>(s.length());
    int l2 = 4 + l1;
    int tosend = l2;

    char* buff = new char[l2];
    memcpy(buff, &l1, 4);
    memcpy(buff + 4, s.c_str(), l1);

    char* current_pos = buff;
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
        throw runtime_error("Error writing to pipe (errno: " + to_string(errno) + ")");
#endif
          break;
      }
    }
    delete[] buff;
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
        throw runtime_error("Error writing to pipe (errno: " + to_string(errno) + ")")
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

  inline void write2n(const string& s)
  {
    write2n_string(s);
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
    if (!ipc_ || !ipc_->readCommandExact(&value, sizeof(T)))
      throw runtime_error("Pipe read failed");
    return value;
  }

  // Specialization for strings (reads length-prefixed strings)
template<>
std::string readFromPipe<std::string>() {
  uint32_t length = readFromPipe<uint32_t>();
  if (length > 10'000'000)
    throw runtime_error("invalid string length");

  std::string s;
  s.resize(length);
  if (length)
  {
    if (!ipc_ || !ipc_->readCommandExact(s.data(), length))
      throw runtime_error("Pipe read failed");
  }
  return s;
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

    const int samplesPerBlock = blockSize;
    juce::AudioBuffer<float> buffer(2, samplesPerBlock);
    juce::MidiBuffer midiBuffer;

    // Calculate total samples needed
    uint64_t totalBlocks = endBlock;

    for (uint64_t block = 0; block < totalBlocks; ++block)
    {
        buffer.clear();
        midiBuffer.clear();

        int64_t blockStart = paramScheduler.getTimelineSample();
        paramScheduler.processBlock(samplesPerBlock);

        midiScheduler->processBlock(midiBuffer, blockStart, samplesPerBlock);
        processorGraph->processBlock(buffer, midiBuffer);
        // Write
        if (audioFileWriter)
            audioFileWriter->writeFromAudioSampleBuffer(buffer, 0, samplesPerBlock);

        // Advance time
        paramScheduler.advance(samplesPerBlock);
        currentSamplePosition = paramScheduler.getTimelineSample();
        updateAudioFilePlayerTimeline();
    }
    // Flush and close file
    audioFileWriter.reset();
    ////cout << "Offline rendering complete" << endl;
  }

  unordered_map<int, juce::AudioProcessorGraph::NodeID> midiSourceNodes;  // key -> MIDI source node
  unique_ptr<MidiScheduler> midiScheduler;
  juce::AudioPluginFormatManager formatManager;
  unordered_map<int, juce::AudioProcessorGraph::NodeID> midiRouterNodes;
  juce::AudioProcessorGraph::NodeID audioOutputNode;
  juce::AudioProcessorGraph::NodeID audioInputNode;
  std::unique_ptr<juce::AudioFormatWriter> audioFileWriter;

  // notificationLoop() removed - now owned by IpcService

  void shutdownAudio()
  {
    if (audio_)
      audio_->shutdown();
  }

 
  // processCommands() removed - now owned by IpcService
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

  getParamsInfoR getParamsInfo(int key) 
  {
    getParamsInfoR resp;
    vector<paramInfo> validParams;

    auto it = loadedPlugins.find(key);
    if (it != loadedPlugins.end()) {
      auto node = processorGraph->getNodeForId(it->second);
      ////cout << "Node pointer: " << node << endl;
      if (node && node->getProcessor()) {
        auto* processor = node->getProcessor();
        ////cout << "Processor pointer: " << processor << endl;
        ////cout << "Processor name: " << processor->getName().toStdString() << endl; //debug
        const auto& params = processor->getParameters();

        ////cout << "Params vector address: " << &params << endl;
        ////cout << "Params size: " << processor->getNumParameters() << endl;

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

            ////cout << "paremeterinfo " << i << ": " << endl;
            ////cout << "originalIndex: " << paramR.originalIndex << " name: " << paramR.name << " defaultValue: " << paramR.defaultValue << " numSteps: " << paramR.numSteps 
            //  << " isDiscrete: " << paramR.isDiscrete << " isBoolean: " << paramR.isBoolean << " isOrientationInverted: " << paramR.isOrientationInverted << " isAutomatable: " 
            //  << paramR.isAutomatable << " isMetaParameter: " << paramR.isMetaParameter << " value: " << paramR.value << " minValue: " << paramR.minValue << " maxValue: " 
            //  << paramR.maxValue << " interval: " << paramR.interval << " skewFactor: " << paramR.skewFactor << " isValid: " << isValid << endl;


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
  getChannnelsInfoR getChannelsInfo(int key) 
  {
    getChannnelsInfoR resp;
   
    ////cout << "Looking for plugin with key: " << key << endl;
    auto it = loadedPlugins.find(key);

    if (it != loadedPlugins.end()) {
      ////cout << "Found plugin in map" << endl;
      auto node = processorGraph->getNodeForId(it->second);
      //cout << "Got node pointer: " << node << endl;

      if (node && node->getProcessor()) {
        ////cout << "node->getProcessor()" << endl;
        auto* processor = node->getProcessor();
        ////cout << "processor pointer = " << processor << endl;

        // MIDI capabilities
        ////cout << "processor->acceptsMidi()" << endl;
        resp.acceptsMidi = processor->acceptsMidi();
        ////cout << "acceptsMidi = " << resp.acceptsMidi << endl;

        //cout << "processor->producesMidi()" << endl;
        resp.producesMidi = processor->producesMidi();
        ////cout << "producesMidi = " << resp.producesMidi << endl;

        //cout << "processor->getBusesLayout()" << endl;
        auto layout = processor->getBusesLayout();

        // Input buses
        ////cout << "processor->getBusCount(true)" << endl;
        int inputBusCount = processor->getBusCount(true);
        ////cout << "Input bus count: " << inputBusCount << endl;

        for (int i = 0; i < inputBusCount; ++i) 
        {
          busR bus2;
          vector<string> channelTypes;

          ////cout << "processor->getBus(true, " << i << ")" << endl;
          auto* bus = processor->getBus(true, i);
          ////cout << "Bus pointer: " << bus << endl;

          //cout << "bus->getNumberOfChannels()" << endl;
          int numChannels = bus->getNumberOfChannels();
          //cout << "Number of channels: " << numChannels << endl;

          //cout << "bus->getName()" << endl;
          string busName = bus->getName().toStdString();
          //cout << "Bus name: " << busName << endl;

          //cout << "bus->isEnabled()" << endl;
          bool isEnabled = bus->isEnabled();
          //cout << "Is enabled: " << isEnabled << endl;

          //cout << "bus->getCurrentLayout()" << endl;
          auto& inputBus = bus->getCurrentLayout();

          for (int chan = 0; chan < numChannels; chan++)
          {
            //cout << "inputBus.getTypeOfChannel(" << chan << ")" << endl;
            auto channelType = inputBus.getTypeOfChannel(chan);

            //cout << "getChannelTypeName()" << endl;
            string channelName = juce::AudioChannelSet::getChannelTypeName(channelType).toStdString();
            //cout << "Channel " << chan << " name: " << channelName << endl;

            channelTypes.push_back(channelName);
          }

          bus2.channelTypes = channelTypes;
          bus2.numChannels = numChannels;
          bus2.isEnabled = isEnabled;

          //cout << "inputBus.getDescription()" << endl;
          bus2.mainBusLayout = inputBus.getDescription().toStdString();
          //cout << "Main bus layout: " << bus2.mainBusLayout << endl;

          resp.inputBuses.push_back(bus2);
        }

        // Output buses
        //cout << "processor->getBusCount(false)" << endl;
        int outputBusCount = processor->getBusCount(false);
        //cout << "Output bus count: " << outputBusCount << endl;

        for (int i = 0; i < outputBusCount; ++i) 
        {
          busR bus2;
          vector<string> channelTypes;

          //cout << "processor->getBus(false, " << i << ")" << endl;
          auto* bus = processor->getBus(false, i);
          //cout << "Bus pointer: " << bus << endl;

          //cout << "bus->getNumberOfChannels()" << endl;
          int numChannels = bus->getNumberOfChannels();
          //cout << "Number of channels: " << numChannels << endl;

          //cout << "bus->getName()" << endl;
          string busName = bus->getName().toStdString();
          //cout << "Bus name: " << busName << endl;

          //cout << "bus->isEnabled()" << endl;
          bool isEnabled = bus->isEnabled();
          //cout << "Is enabled: " << isEnabled << endl;

          //cout << "bus->getCurrentLayout() for output bus " << i << endl;
          auto& outputBus = bus->getCurrentLayout();  // Fixed: use bus, not processor->getBus(false, 0)

          for (int chan = 0; chan < numChannels; chan++)
          {
            //cout << "outputBus.getTypeOfChannel(" << chan << ")" << endl;
            auto channelType = outputBus.getTypeOfChannel(chan);

            //cout << "getChannelTypeName()" << endl;
            string channelName = juce::AudioChannelSet::getChannelTypeName(channelType).toStdString();
            //cout << "Channel " << chan << " name: " << channelName << endl;

            channelTypes.push_back(channelName);
          }

          bus2.channelTypes = channelTypes;
          bus2.numChannels = numChannels;
          bus2.isEnabled = isEnabled;

          //cout << "outputBus.getDescription()" << endl;
          bus2.mainBusLayout = outputBus.getDescription().toStdString();
          //cout << "Main bus layout: " << bus2.mainBusLayout << endl;

          resp.outputBuses.push_back(bus2);
        }

        //cout << "Setting success = true" << endl;
        resp.success = true;
      }
      else {
        //cout << "Node or processor is null" << endl;
        resp.errmsg = "Node or processor not found";
      }
    } 
    else 
    {
      //cout << "Plugin not found in loadedPlugins map" << endl;
      resp.errmsg = "Plugin not loaded";
      resp.success = false;
    }

    //cout << "Returning response with success=" << resp.success << endl;
    return resp;
  }
  uint32_t scanPluginDirectory(const juce::File& directory)
  {
    if (!directory.exists()) {
      //std::cout << "directory doesn't exist: " << directory.getFullPathName() << std::endl;
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

#if JUCE_MAC || JUCE_IOS
    auto auComponents = directory.findChildFiles(juce::File::findDirectories, true, "*.component");
    auto auAppex = directory.findChildFiles(juce::File::findDirectories, true, "*.appex");
    pluginFiles.addArray(auComponents);
    pluginFiles.addArray(auAppex);
#endif

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
      //cout << "scanning directory: " << directory << endl;
      num_found += scanPluginDirectory(juce::File(directory));
    }
    //cout << "num_found: " << num_found << endl;
    return num_found;
  }

  void listAvailablePlugins()
  {
    WRITEALLC(uint32_t(availablePlugins.size()));
    for (auto plugin : availablePlugins) //do i need to use loadedPlugin->fillInPluginDescription?
    {
      WRITEALLC(uint32_t(plugin.desc.isInstrument), uint32_t(plugin.desc.uniqueId), uint32_t(plugin.desc.numInputChannels), uint32_t(plugin.desc.numOutputChannels), plugin.desc.name.toStdString(),
        plugin.desc.descriptiveName.toStdString(), plugin.desc.pluginFormatName.toStdString(), plugin.desc.category.toStdString(), plugin.desc.manufacturerName.toStdString(), plugin.desc.version.toStdString(),
        plugin.desc.fileOrIdentifier.toStdString(), plugin.desc.lastFileModTime.toString(true, true).toStdString(), plugin.path);
    }
  }

  void getPluginInfo()
  {
    int key = READFROMPIPE(uint32_t);
    auto plugin = availablePlugins[key];
    WRITEALLC(uint32_t(plugin.desc.isInstrument), uint32_t(plugin.desc.uniqueId), uint32_t(plugin.desc.numInputChannels), uint32_t(plugin.desc.numOutputChannels), plugin.desc.name.toStdString(),
        plugin.desc.descriptiveName.toStdString(), plugin.desc.pluginFormatName.toStdString(), plugin.desc.category.toStdString(), plugin.desc.manufacturerName.toStdString(), plugin.desc.version.toStdString(),
        plugin.desc.fileOrIdentifier.toStdString(), plugin.desc.lastFileModTime.toString(true, true).toStdString(), plugin.path);
  }

  void listBadPaths()
  {
    //cout << "badPaths.size()" << badPaths.size() << endl;
    WRITEALLC(uint32_t(badPaths.size()));
    for (string badpath : badPaths)
    {
      WRITEALLC(badpath);
    }
  }
  
  struct loadPluginByUidR {uint32_t success = true; string name; string errmsg;};
  loadPluginByUidR loadPluginByUid(int uid, int key) 
  {
    loadPluginByUidR resp;
    bool found = false;
    for (auto availablePlugin : availablePlugins)
    {
      if (availablePlugin.desc.uniqueId == uid)
      {
        found = true;
        juce::String errorMessage;
        auto plugin = formatManager.createPluginInstance(
          availablePlugin.desc, sampleRate, blockSize, errorMessage);

        if (plugin != nullptr)
        {
          if (realtime)
          {
            //cout << "adding listener" << endl;
            plugin->addListener(&paramListener);
          }
          juce::AudioProcessor* processorPtr = plugin.get();
          auto node = processorGraph->addNode(move(plugin));

          if (node != nullptr)
          {
            loadedPlugins[key] = node->nodeID;
            processorToKey[processorPtr] = key;
            resp.name = availablePlugin.desc.name.toStdString();
            resp.success = true;

            // Create MIDI source node for this plugin
            if (midiScheduler)  // Make sure scheduler exists
            {
              auto midiSource = std::make_unique<MidiSourceNode>(
                midiScheduler.get(), key);
              auto sourceNode = processorGraph->addNode(std::move(midiSource));

              if (sourceNode)
              {
                // Store the source node
                midiSourceNodes[key] = sourceNode->nodeID;

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
          badPaths.insert(availablePlugin.path);
        }
        break;
      }
    }
    if (!found)
    {
      resp.success = false;
      resp.errmsg = "uid not found";
      return resp;
    }
  }
    
  struct loadPluginR {uint32_t success = false; string errmsg; string name; uint32_t uid = -3;}; //todo: change uint to int for key in pipe communication

  loadPluginR loadPlugin(const string& path, int key)
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
          loadedPlugins[key] = node->nodeID;
          processorToKey[rawPointer] = key;  // Track reverse mapping

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

  struct showPluginUIR { uint32_t success = false; string errmsg; };
  showPluginUIR showPluginUI(int key)
  {
    showPluginUIR resp;
    auto it = loadedPlugins.find(key);
    if (it != loadedPlugins.end())
    {
      auto node = processorGraph->getNodeForId(it->second);
      if (node)
      {
        juce::MessageManager::callAsync([this, key, node]() {
          //cout << "showPluginUI async callback executing for key=" << key << endl;
          if (node->getProcessor()->hasEditor())
          {
            //cout << "  Processor has editor, creating editor..." << endl;
            juce::AudioProcessorEditor* editor = node->getProcessor()->createEditor();

            if (editor)
            {
              //cout << "  Editor created, creating PluginWindow..." << endl;
              auto window = make_unique<PluginWindow>(node->getProcessor()->getName(), editor, node->getProcessor(), key, this);
              //cout << "  PluginWindow created, setting visible..." << endl;
              window->setVisible(true);

              // Position the window using tiling layout
              int windowWidth = window->getWidth();
              int windowHeight = window->getHeight();

              // Get screen dimensions
              juce::Rectangle<int> screenBounds = juce::Desktop::getInstance().getDisplays().getPrimaryDisplay()->userArea;
              int screenWidth = screenBounds.getWidth();
              int screenHeight = screenBounds.getHeight();
              int margin = 10;

              //cout << "  Positioning window at x=" << (nextWindowX + columnOffset) << ", y=" << nextWindowY << endl;
              window->setTopLeftPosition(nextWindowX + columnOffset, nextWindowY);

              // Update position for next window
              nextWindowX += windowWidth + margin;
              currentRowHeight = juce::jmax(currentRowHeight, windowHeight);

              // Check if we need to wrap to next row
              if (nextWindowX + columnOffset + 300 > screenWidth)  // Assume avg window width ~300, adjust as needed
              {
                //cout << "  Wrapping to next row" << endl;
                nextWindowX = 10;
                nextWindowY += currentRowHeight + margin;
                currentRowHeight = 0;

                // Check if we've filled the screen vertically
                if (nextWindowY + 200 > screenHeight)  // Assume avg window height ~200
                {
                  //cout << "  Screen filled vertically, starting new layer with offset" << endl;
                  nextWindowY = 10;  // Start from top again
                  columnOffset += 30;  // Offset by 30 pixels to show overlap
                }
              }

              //cout << "  Window visible, initializing routing indicators..." << endl;
              // Initialize routing indicators
              window->updateRoutingIndicators(keyboardRoutedPlugin, virtualKeyboardRoutedPlugin);
              //cout << "  Adding window to pluginWindows map..." << endl;
              pluginWindows[key] = std::move(window);
              //cout << "  showPluginUI async callback completed for key=" << key << endl;
            }
            else
            {
              //cout << "  ERROR: Failed to create editor for key=" << key << endl;
            }
          }
          else
          {
            //cout << "  Processor does not have editor for key=" << key << endl;
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
      resp.errmsg = "Plugin not loaded: " + to_string(key);
    }
    return resp;
  }

  uint32_t hidePluginUI(int key)
  {
    auto it = pluginWindows.find(key);
    if (it != pluginWindows.end())
    {
      juce::MessageManager::callAsync
      (
        [this, key]() 
        {
        pluginWindows.erase(key);
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
        //cout << "Virtual keyboard shown" << endl;
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
        //cout << "Virtual keyboard hidden" << endl;
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
        processorGraph->prepareToPlay(sampleRate, blockSize);
        setupAudioIO();
        midiScheduler = std::make_unique<MidiScheduler>(sampleRate);
      }
      else 
      {
        
        if (realtime) 
        {
          for (const auto& pair : loadedPlugins) 
          {
            int key = pair.first;
            juce::AudioProcessorGraph::NodeID nodeId = pair.second;

            // Get the actual processor from the graph
            auto node = processorGraph->getNodeForId(nodeId);
            if (node && node->getProcessor()) {
              node->getProcessor()->addListener(&paramListener);
            }
          }
        }

        // Initialize hardware for real-time
        if (!audio_)
          throw std::runtime_error("AudioService not set (ServerServices wiring error)");

        auto cfg = audio_->ensureRealtimeInitialised(*processorGraph);
        auto setup = audio_->deviceManager().getAudioDeviceSetup();

        // Keep state config in sync with the actual device
        sampleRate = (int) setup.sampleRate;
        blockSize = (int) setup.bufferSize;

        setupAudioIO();
        midiScheduler = std::make_unique<MidiScheduler>(setup.sampleRate);
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
      playbackEndSample = endBlock;

      // Activate scheduled ordered playback if it was scheduled
      if (orderedPlaybackScheduled)
      {
        orderedPlaybackActive = true;
        orderedPlaybackScheduled = false;
        //cout << "Activated scheduled ordered playback" << endl;
      }
    }
  }

  void stopPlayback()
  {
    if (isPlaying)
    {
      isPlaying = false;
      orderedPlaybackActive = false;

      // Send stop_playback notification to Python
      if (ipc_ && ipc_->isNotificationReady())
      {
        WRITEALLN(send_cmd::stop_playback);
      }
    }
  }

  void removePlugin(int key) 
  {
    // Remove MIDI source node first
    auto sourceIt = midiSourceNodes.find(key);
    if (sourceIt != midiSourceNodes.end()) 
    {
      processorGraph->removeNode(sourceIt->second);
      midiSourceNodes.erase(sourceIt);
    }

    // Then remove the plugin itself
    auto it = loadedPlugins.find(key);
    if (it != loadedPlugins.end()) 
    {
      auto node = processorGraph->getNodeForId(it->second);
      if (node) 
      {
        processorToKey.erase(node->getProcessor());
      }
      processorGraph->removeNode(it->second);
      loadedPlugins.erase(it);
    }
  }
  
  void clearAllPlugins() {
    processorToKey.clear();
    loadedPlugins.clear();
    pluginWindows.clear();
    processorGraph->clear();
  }

  void processCommand(char command)
  {
    auto commandtype = (recv_cmd)command;
    // Command dispatch table (replaces the large switch).
const auto idx = static_cast<size_t>(commandtype);
const auto& tbl = commandHandlers();
if (idx < tbl.size() && tbl[idx] != nullptr)
{
  (this->*tbl[idx])();
  return;
}
// Unknown/unhandled command: ignore safely.

  }

  // --- Command dispatcher helpers ---
  using CommandHandler = void (PluginHostService::*)();

static const std::array<CommandHandler, static_cast<size_t>(stop_playback_cmd) + 1>& commandHandlers()
{
  static const std::array<CommandHandler, static_cast<size_t>(stop_playback_cmd) + 1> tbl = {
    &PluginHostService::cmd_load_plugin,         // 0: load_plugin
    &PluginHostService::cmd_load_plugin_by_uid,  // 1: load_plugin_by_uid
    &PluginHostService::cmd_scan_plugins,         // 2: scan_plugins
    &PluginHostService::cmd_list_plugins,         // 3: list_plugins
    &PluginHostService::cmd_get_plugin_info,      // 4: get_plugin_info
    &PluginHostService::cmd_show_plugin_ui,       // 5: show_plugin_ui
    &PluginHostService::cmd_hide_plugin_ui,       // 6: hide_plugin_ui
    &PluginHostService::cmd_set_parameter,        // 7: set_parameter
    &PluginHostService::cmd_get_parameter,        // 8: get_parameter
    &PluginHostService::cmd_connect_audio,        // 9: connect_audio
    &PluginHostService::cmd_connect_midi,         // 10: connect_midi
    &PluginHostService::cmd_start_playback,       // 11: start_playback
    &PluginHostService::cmd_cmd_shutdown,         // 12: cmd_shutdown
    &PluginHostService::cmd_remove_plugin,        // 13: remove_plugin
    &PluginHostService::cmd_list_bad_paths,       // 14: list_bad_paths
    &PluginHostService::cmd_get_params_info, // get_params_info
    &PluginHostService::cmd_get_channels_info, // get_channels_info
    &PluginHostService::cmd_schedule_midi_note, // schedule_midi_note
    &PluginHostService::cmd_schedule_midi_cc, // schedule_midi_cc
    &PluginHostService::cmd_clear_midi_schedule, // clear_midi_schedule
    &PluginHostService::cmd_schedule_param_change, // schedule_param_change
    &PluginHostService::cmd_route_keyboard_input, // route_keyboard_input
    &PluginHostService::cmd_unroute_keyboard_input, // unroute_keyboard_input
    &PluginHostService::cmd_route_cc_to_param, // route_cc_to_param
    &PluginHostService::cmd_unroute_cc_to_param, // unroute_cc_to_param
    &PluginHostService::cmd_show_virtual_keyboard, // show_virtual_keyboard
    &PluginHostService::cmd_hide_virtual_keyboard, // hide_virtual_keyboard
    &PluginHostService::cmd_route_virtual_keyboard, // route_virtual_keyboard
    &PluginHostService::cmd_unroute_virtual_keyboard, // unroute_virtual_keyboard
    &PluginHostService::cmd_toggle_recording, // toggle_recording
    &PluginHostService::cmd_toggle_monitoring, // toggle_monitoring
    &PluginHostService::cmd_load_audio_file, // load_audio_file
    &PluginHostService::cmd_control_audio_playback, // control_audio_playback
    &PluginHostService::cmd_schedule_ordered_notes, // schedule_ordered_notes
    &PluginHostService::cmd_start_ordered_playback, // start_ordered_playback
    &PluginHostService::cmd_stop_ordered_playback, // stop_ordered_playback
    &PluginHostService::cmd_clear_ordered_notes, // clear_ordered_notes
    &PluginHostService::cmd_clear_midi_cc_schedule, // clear_midi_cc_schedule
    &PluginHostService::cmd_clear_param_schedule, // clear_param_schedule
    &PluginHostService::cmd_clear_all_plugins, // clear_all_plugins
    &PluginHostService::cmd_stop_playback_cmd, // stop_playback_cmd
  };
  return tbl;
}

  void cmd_load_plugin()
  {
    string path = READFROMPIPE(string);
    int key = READFROMPIPE(uint32_t);
    auto response = loadPlugin(path, key);

    WRITEALLC(response.success, response.name, response.uid, response.errmsg); //todo: return everything in plugin.desc? todo: change return values in client
  }

  void cmd_remove_plugin()
  {
    removePlugin(READFROMPIPE(uint32_t));
  }

  void cmd_load_plugin_by_uid()
  {
    int uid = READFROMPIPE(uint32_t); 
    int key = READFROMPIPE(uint32_t);
    auto response = loadPluginByUid(uid, key);
    WRITEALLC(response.success, response.name, response.errmsg); //todo: change this in juce_client.py too

    //cout << "load_plugin_by_uid:" << endl << " success: " << response.success << " name: " << response.name 
      //<< " key: " << key << " uid: " << uid << " errmsg: " << response.errmsg << endl;
  }

  void cmd_scan_plugins()
  {
    WRITEALLC(scanPluginDirectories());
  }

  void cmd_list_plugins()
  {
    listAvailablePlugins();
  }

  void cmd_get_plugin_info()
  {
    getPluginInfo();
  }

  void cmd_show_plugin_ui()
  {
    uint32_t a = READFROMPIPE(uint32_t);
    auto response = showPluginUI(a);
    //cout << "show plugin ui:" << endl;
    //cout << "success: " << response.success << " errmsg: " << response.errmsg << endl;
    WRITEALLC(response.success, response.errmsg);
  }

  void cmd_hide_plugin_ui()
  {
    WRITEALLC(hidePluginUI(READFROMPIPE(uint32_t)));
  }

  void cmd_set_parameter()
  {
    //probably won't be used.
          {
            auto response = setParameter(READFROMPIPE(uint32_t), READFROMPIPE(uint32_t), READFROMPIPE(float));
            WRITEALLC(response.success, response.errmsg);
          }
  }

  void cmd_get_parameter()
  {
    //probably won't be used.
          {
            auto response = getParameter(READFROMPIPE(uint32_t), READFROMPIPE(uint32_t));
            WRITEALLC(response.success, response.value, response.errmsg);
            if(response.success) WRITEALLC(response.value);
            else WRITEALLC(response.errmsg);
          }
  }

  void cmd_connect_audio()
  {
    WRITEALLC(connectAudio(READFROMPIPE(uint32_t), READFROMPIPE(uint32_t), READFROMPIPE(uint32_t), READFROMPIPE(uint32_t)));
  }

  void cmd_connect_midi()
  {
    WRITEALLC(connectMidi(READFROMPIPE(uint32_t), READFROMPIPE(uint32_t)));
  }

  void cmd_start_playback()
  {
    uint64_t lastBlock = READFROMPIPE(uint64_t);
    bool toFile = READFROMPIPE(uint32_t);
    std::string fileName = "";
    if (toFile) {
      fileName = READFROMPIPE(string);
    }
    startPlayback(lastBlock, toFile, fileName);
    WRITEALLC(uint32_t(1));
  }

  void cmd_cmd_shutdown()
  {
    clearAllPlugins();
    running = false;
  }

  void cmd_list_bad_paths()
  {
    listBadPaths();
  }

  void cmd_get_params_info()
  {
    uint32_t key = READFROMPIPE(uint32_t); //debug
    getParamsInfoR resp = getParamsInfo(key); 

    //cout << "key: " << key << endl; 

    //cout << "success: " << resp.success << " originalNumParams: " << resp.originalNumParams << " errmsg: " << resp.errmsg << endl;

    WRITEALLC(resp.success, uint32_t(resp.validParams.size()), resp.errmsg);

    //cout << "WRITEALL(resp.success, resp.validParams.size(), resp.errmsg);" << endl;

    for (auto param : resp.validParams)
    {

      WRITEALLC(param.originalIndex, param.name, param.minValue, param.maxValue, param.interval, param.defaultValue, param.skewFactor, param.value, param.numSteps, param.isDiscrete, param.isBoolean, 
        param.isOrientationInverted, param.isAutomatable, param.isMetaParameter);

      //cout << "name: " << param.name << " minValue: " << param.minValue << " maxValue: " << param.maxValue << endl;
    }
  }

  void cmd_get_channels_info()
  {
    //cout << "getChannnelsInfoR resp = getChannelsInfo(READFROMPIPE(uint32_t));" << endl;

    getChannnelsInfoR resp = getChannelsInfo(READFROMPIPE(uint32_t));

    //cout << "WRITEALL(resp.success, resp.acceptsMidi, resp.producesMidi);" << endl;

    WRITEALLC(resp.success, resp.acceptsMidi, resp.producesMidi);

    //cout << "WRITEALL(uint32_t(resp.inputBuses.size()));" << endl;

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

    //cout << "WRITEALL(uint32_t(resp.outputBuses.size()));" << endl;

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

    //cout << "WRITEALL(resp.errmsg);" << endl;

    WRITEALLC(resp.errmsg);
  }

  void cmd_schedule_midi_note()
  {
    int key = READFROMPIPE(uint32_t);
    int note = READFROMPIPE(uint32_t);
    float velocity = READFROMPIPE(float);
    double startTime = READFROMPIPE(double);
    double duration = READFROMPIPE(double);
    int channel = READFROMPIPE(uint32_t);
    midiScheduler->scheduleNote(key, note, velocity, startTime, duration, channel);
  }

  void cmd_schedule_midi_cc()
  {
    int key = READFROMPIPE(uint32_t);
    int controller = READFROMPIPE(uint32_t);
    int value = READFROMPIPE(uint32_t);
    double time = READFROMPIPE(double);
    int channel = READFROMPIPE(uint32_t);
    midiScheduler->scheduleCC(key, controller, value, time, channel);
  }

  void cmd_clear_midi_schedule()
  {
    midiScheduler->clearSchedule();
  }

  void cmd_schedule_param_change()
  {
    int key = READFROMPIPE(uint32_t);
    int parameterIndex = READFROMPIPE(uint32_t);
    float value = READFROMPIPE(float);
    uint64_t atSample = READFROMPIPE(uint64_t);

    paramScheduler.scheduleAtSample(key, parameterIndex, value, (int64_t)atSample);
  }

  void cmd_route_keyboard_input()
  {
    int key = READFROMPIPE(uint32_t);
    uint32_t useVelocity = READFROMPIPE(uint32_t);
    float fixedVel = READFROMPIPE(float);

    keyboardRoutedPlugin = key;
    useKeyboardVelocity = (useVelocity != 0);
    fixedVelocity = fixedVel;

    //cout << "Keyboard routed to plugin " << key
         //<< ", useVelocity=" << useKeyboardVelocity
         //<< ", fixedVelocity=" << fixedVelocity << endl;

    WRITEALLC(uint32_t(1));  // Success
  }

  void cmd_unroute_keyboard_input()
  {
    keyboardRoutedPlugin = -1;
    //cout << "Keyboard input unrouted" << endl;
    WRITEALLC(uint32_t(1));  // Success
  }

  void cmd_route_cc_to_param()
  {
    int key = READFROMPIPE(uint32_t);
    int parameterIndex = READFROMPIPE(uint32_t);
    int ccController = READFROMPIPE(uint32_t);
    int midiChannel = READFROMPIPE(int32_t);  // -1 for any channel

    CCMapping mapping;
    mapping.key = key;
    mapping.parameterIndex = parameterIndex;
    mapping.ccController = ccController;
    mapping.midiChannel = midiChannel;

    ccMappings.push_back(mapping);

    //cout << "CC " << ccController << " (channel " << midiChannel
         //<< ") routed to plugin " << key << " param " << parameterIndex << endl;

    WRITEALLC(uint32_t(1));  // Success
  }

  void cmd_unroute_cc_to_param()
  {
    int key = READFROMPIPE(uint32_t);
    int parameterIndex = READFROMPIPE(uint32_t);
    int ccController = READFROMPIPE(uint32_t);

    // Remove matching mapping
    ccMappings.erase(
      std::remove_if(ccMappings.begin(), ccMappings.end(),
        [key, parameterIndex, ccController](const CCMapping& m) {
          return m.key == key &&
                 m.parameterIndex == parameterIndex &&
                 m.ccController == ccController;
        }),
      ccMappings.end()
    );

    //cout << "Unrouted CC " << ccController << " from plugin " << key
         //<< " param " << parameterIndex << endl;

    WRITEALLC(uint32_t(1));  // Success
  }

  void cmd_show_virtual_keyboard()
  {
    uint32_t result = showVirtualKeyboard();
    WRITEALLC(result);
  }

  void cmd_hide_virtual_keyboard()
  {
    uint32_t result = hideVirtualKeyboard();
    WRITEALLC(result);
  }

  void cmd_route_virtual_keyboard()
  {
    int key = READFROMPIPE(uint32_t);
    uint32_t useVelocity = READFROMPIPE(uint32_t);
    float fixedVel = READFROMPIPE(float);

    virtualKeyboardRoutedPlugin = key;
    useVirtualKeyboardVelocity = (useVelocity != 0);
    virtualFixedVelocity = fixedVel;

    //cout << "Virtual keyboard routed to plugin " << key
         //<< ", useVelocity=" << useVirtualKeyboardVelocity
         //<< ", fixedVelocity=" << virtualFixedVelocity << endl;

    WRITEALLC(uint32_t(1));  // Success
  }

  void cmd_unroute_virtual_keyboard()
  {
    virtualKeyboardRoutedPlugin = -1;
    //cout << "Virtual keyboard input unrouted" << endl;
    WRITEALLC(uint32_t(1));  // Success
  }

  void cmd_toggle_recording()
  {
    toggleRecording();
    WRITEALLC(uint32_t(isRecording ? 1 : 0));  // Return current recording state
  }

  void cmd_toggle_monitoring()
  {
    toggleMonitoring();
    WRITEALLC(uint32_t(isMonitoring ? 1 : 0));  // Return current monitoring state
  }

  void cmd_load_audio_file()
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

        //cout << "Audio file loaded into graph: " << filename
             //<< ", playerId=" << playerId
             //<< ", nodeId=" << nodeId->nodeID.uid << endl;

        WRITEALLC(playerId);  // Return player ID
        WRITEALLN(audio_file_loaded, filename, playerId);
      }
      else
      {
        //cout << "ERROR: Failed to add audio player node to graph" << endl;
        WRITEALLC(-1);  // Error
      }
    }
    else
    {
      //cout << "ERROR: Failed to load audio file: " << filename << endl;
      WRITEALLC(-1);  // Error
    }
  }

  void cmd_control_audio_playback()
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
            //cout << "Stopped audio playback: playerId=" << playerId << endl;
            WRITEALLC(uint32_t(1));  // Success
            WRITEALLN(audio_playback_stopped, playerId, currentSamplePosition.load());
          }
          else if (action == 1)  // start immediately
          {
            playerProcessor->startPlayback(fileStartPosition);
            //cout << "Started audio playback: playerId=" << playerId
                 //<< ", fileStartPosition=" << fileStartPosition << endl;
            WRITEALLC(uint32_t(1));  // Success
            WRITEALLN(audio_playback_started, playerId, currentSamplePosition.load());
          }
          else if (action == 2)  // schedule
          {
            playerProcessor->schedulePlayback(startSample, fileStartPosition);
            //cout << "Scheduled audio playback: playerId=" << playerId
                 //<< ", startSample=" << startSample
                 //<< ", fileStartPosition=" << fileStartPosition << endl;
            WRITEALLC(uint32_t(1));  // Success
            WRITEALLN(audio_playback_started, playerId, startSample);
          }
          else
          {
            //cout << "ERROR: Unknown playback action: " << (int)action << endl;
            WRITEALLC(uint32_t(0));  // Error
          }
        }
        else
        {
          //cout << "ERROR: Node is not an AudioFilePlayerNode" << endl;
          WRITEALLC(uint32_t(0));  // Error
        }
      }
      else
      {
        //cout << "ERROR: Node not found in graph" << endl;
        WRITEALLC(uint32_t(0));  // Error
      }
    }
    else
    {
      //cout << "ERROR: Player ID not found: " << playerId << endl;
      WRITEALLC(uint32_t(0));  // Error
    }
  }

  void cmd_schedule_ordered_notes()
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
  }

  void cmd_start_ordered_playback()
  {
    uint32_t useKeyboardVelocity = READFROMPIPE(uint32_t);
    uint32_t useKeyboardDuration = READFROMPIPE(uint32_t);
    startOrderedPlayback(useKeyboardVelocity != 0, useKeyboardDuration != 0);
    WRITEALLC(uint32_t(1));  // Success
  }

  void cmd_stop_ordered_playback()
  {
    stopOrderedPlayback();
    WRITEALLC(uint32_t(1));  // Success
  }

  void cmd_clear_ordered_notes()
  {
    clearOrderedNotes();
    WRITEALLC(uint32_t(1));  // Success
  }

  void cmd_clear_midi_cc_schedule()
  {
    midiScheduler->clearCCSchedule();
  }

  void cmd_clear_param_schedule()
  {
    paramScheduler.clear();
  }

  void cmd_clear_all_plugins()
  {
    clearAllPlugins();
  }

  void cmd_stop_playback_cmd()
  {
    stopPlayback();
    WRITEALLC(uint32_t, 1); // success
  }

  // --- End command dispatcher helpers ---


  // Custom button component for routing indicators
  

// (moved to gui/RoutingButton.h)



  // Wrapper component that contains the editor and routing buttons
  

// (moved to gui/EditorWithButtonsComponent.h)



  // Plugin window class
  class PluginWindow : public juce::DocumentWindow
  {
  public:
    PluginWindow(const juce::String& name,
      juce::AudioProcessorEditor* editor,
      juce::AudioProcessor* processor, int lpind,
      PluginHostService* hostApp)
      : DocumentWindow(name,
        juce::Colours::black,  // Black background for the entire window
        DocumentWindow::allButtons),
        key(lpind),
        processor(processor),
        host(hostApp)
    {
      //cout << "PluginWindow constructor called for key=" << lpind << ", host=" << (void*)hostApp << endl;

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

      //cout << "  Processor: acceptsMidi=" << acceptsMidi
           //<< ", audioInputs=" << processor->getTotalNumInputChannels()
           //<< ", audioOutputs=" << processor->getTotalNumOutputChannels()
           //<< ", isInstrument=" << isInstrument << endl;

      int buttonStripHeight = 0;
      std::unique_ptr<RoutingButton> midiBtn = nullptr;
      std::unique_ptr<RoutingButton> virtualBtn = nullptr;

      // Only create routing buttons for instruments
      if (isInstrument)
      {
        //cout << "  Creating routing buttons for instrument..." << endl;
        midiBtn = std::make_unique<RoutingButton>(
          RoutingButton::MidiKeyboard, lpind, hostApp);
        virtualBtn = std::make_unique<RoutingButton>(
          RoutingButton::VirtualKeyboard, lpind, hostApp);
        buttonStripHeight = 50;
        //cout << "  Routing buttons created" << endl;
      }
      else
      {
        //cout << "  Skipping routing buttons (not an instrument)" << endl;
      }

      //cout << "  Creating wrapper component with editor and buttons..." << endl;
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
      //cout << "  Wrapper component created with size: " << editorWidth << "x" << (editorHeight + buttonStripHeight) << endl;

      // Set the wrapper as the window's content
      setContentOwned(wrapper.release(), true);
      setResizable(editor->isResizable(), false);

      // Don't center - let the caller position the window
      // (The tiling layout in showPluginUI will position it)

      // Make the window visible (like the GUI template does)
      setVisible(true);

      //cout << "  Window dimensions: " << getWidth() << "x" << getHeight() << endl;
      //cout << "  Window background colour: " << getBackgroundColour().toString() << endl;
      //cout << "  Content component size: " << getContentComponent()->getWidth() << "x" << getContentComponent()->getHeight() << endl;
      //cout << "  Window is visible: " << isVisible() << endl;

      //cout << "PluginWindow constructor completed for key=" << lpind << endl;
    }

    int getkey() const { return key; }

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
      //cout << "PluginWindow::updateRoutingIndicators called for key=" << key
           //<< ", activeMidi=" << activeMidiKeyboardPlugin
           //<< ", activeVirtual=" << activeVirtualKeyboardPlugin << endl;
      if (midiKeyboardButton && virtualKeyboardButton)
      {
        midiKeyboardButton->setActive(activeMidiKeyboardPlugin == key);
        virtualKeyboardButton->setActive(activeVirtualKeyboardPlugin == key);
        //cout << "  Indicators updated" << endl;
      }
      else
      {
        //cout << "  ERROR: Button pointers are null!" << endl;
      }
    }

  private:

    juce::AudioProcessor* processor;
    int key;
    PluginHostService* host;
    RoutingButton* midiKeyboardButton;  // Raw pointer - owned by the wrapper component
    RoutingButton* virtualKeyboardButton;  // Raw pointer - owned by the wrapper component

    JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(PluginWindow)
  };

  // Virtual Keyboard Window
  

// (moved to gui/VirtualKeyboardWindow.h)



private:
  // Core components
  long int currentBlock = 0;
  bool realtime = false;
  bool threadStarted = false;
  std::atomic<bool> isPlaying { false };
  juce::KnownPluginList knownPluginList;
  unique_ptr<juce::AudioProcessorGraph> processorGraph;

  // map<> is always O(log n), unordered_map<> is normally O(1) but can get up to O(n)
  unordered_map<juce::AudioProcessor*, int> processorToKey;
  unordered_map<int, unique_ptr<PluginWindow>> pluginWindows;       // int ID -> Window
  
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
  std::atomic<bool> orderedPlaybackActive { false };
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

  mutex commandMutex;
  condition_variable commandCv;

  JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR(PluginHostService)
};

// RoutingButton method implementation (needs full PluginHostService definition)
inline void RoutingButton::toggleRouting()
{
  //cout << "RoutingButton::toggleRouting called for key=" << key << ", type=" << routingType << endl;
  if (host)
  {
    //cout << "  host pointer is valid" << endl;
    if (routingType == MidiKeyboard)
    {
      //cout << "  calling toggleMidiKeyboardRouting" << endl;
      host->toggleMidiKeyboardRouting(key);
    }
    else
    {
      //cout << "  calling toggleVirtualKeyboardRouting" << endl;
      host->toggleVirtualKeyboardRouting(key);
    }
    //cout << "  toggleRouting completed" << endl;
  }
  else
  {
    //cout << "  ERROR: host pointer is null!" << endl;
  }
}

// SampleParamScheduler method implementation (needs full PluginHostService definition)
inline void SampleParamScheduler::processBlock(int numSamples)
{
    if (!host) return;

    std::lock_guard<std::mutex> lock(mtx);

    const int64_t blockStart = timelineSample;
    const int64_t blockEnd   = blockStart + numSamples;

    // Skip past events strictly before this block.
    while (nextIndex < (int)events.size() && events[nextIndex].atSample < blockStart)
        ++nextIndex;

    // Apply all events that fall inside this block window (block-quantized).
    int i = nextIndex;
    while (i < (int)events.size() && events[i].atSample < blockEnd)
    {
        auto& e = events[i];
        host->setPluginParameter(e.key, e.parameterIndex, e.value);
        ++i;
    }
    nextIndex = i;

    // NOTE: timeline is advanced by the caller via advance(), not here.
}

// ParamScheduler method implementation (needs full PluginHostService definition)
inline void ParamScheduler::processBlock(int numSamples)
{
    if (!host) return;

    const int64_t blockStart = timelineSample;
    const int64_t blockEnd   = blockStart + numSamples;

    while (nextIndex < (int)events.size() && events[nextIndex].atSample < blockStart)
      ++nextIndex;

    int i = nextIndex;
    while (i < (int)events.size() && events[i].atSample < blockEnd)
    {
      auto& e = events[i];
      host->setPluginParameter(e.key, e.parameterIndex, e.value);
      ++i;
    }
    nextIndex = i;

    timelineSample = blockEnd; // IMPORTANT
}
