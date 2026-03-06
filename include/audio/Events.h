#pragma once
#include "Common.h"

// --------------------
// Event structs
// --------------------
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
  int value;
  int channel;
  int64_t atSample;  // timeline sample
};

struct ParameterChangeEvent
{
  int key;
  int parameterIndex;
  float value;
  int64_t atSample; // timeline sample
};

struct ScheduledParameterChange
{
  int key;
  int parameterIndex;
  float value;
  int64_t atSample;
};
