#pragma once
#include "Common.h"
#include "audio/Events.h"
#include "audio/Schedulers.h"
#include "audio/ParameterChangeListener.h"

// Forward decl
class PluginHostService;

// Bundles config/state that previously lived in globals.
// A single instance is created in main() and passed by reference.
struct ServerConfig
{
    int sampleRate = 44100;
    int blockSize = 64;
    bool suppressNotifications = false;
    std::string pipeName = "juceclientserver";
    int updateRate = 50;
};

struct ServerQueues
{
    LockFreeParameterQueue parameterQueue;
    LockFreeMidiQueue<MidiNoteEvent> midiNoteQueue;
    LockFreeMidiQueue<MidiCCEvent> midiCCQueue;
    LockFreeMidiQueue<MidiNoteEvent> virtualKeyboardNoteQueue;
    LockFreeMidiQueue<MidiCCEvent> virtualKeyboardCCQueue;
};

struct ServerState
{
    ServerConfig config{};
    ServerQueues queues{};
    std::atomic<int64_t> currentSamplePosition { 0 };
};
