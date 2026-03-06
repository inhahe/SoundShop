#!/usr/bin/env python3
from __future__ import annotations

import time, platform, sys, json, os, threading, bisect, math, hashlib, struct, importlib
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Union, Sequence, Tuple, Set, List, Protocol, Any, Iterable
from fractions import Fraction
from collections import defaultdict
from math import ceil

import yaml

from notes_manipulation import *

class send_cmd:
  load_plugin, load_plugin_by_index, scan_plugins, list_plugins, get_plugin_info, show_plugin_ui, hide_plugin_ui, \
  set_parameter, get_parameter, connect_audio, connect_midi, start_playback, cmd_shutdown, remove_plugin, \
  list_bad_paths, get_params_info, get_channels_info, schedule_midi_note, schedule_midi_cc, clear_midi_schedule, \
  schedule_param_change, route_keyboard_input, unroute_keyboard_input, route_cc_to_param, unroute_cc_to_param, \
  show_virtual_keyboard, hide_virtual_keyboard, route_virtual_keyboard, unroute_virtual_keyboard, \
  toggle_recording, toggle_monitoring, \
  load_audio_file, schedule_audio_region, control_audio_playback, load_audio_file_into_player, \
  schedule_ordered_notes, start_ordered_playback, stop_ordered_playback, clear_ordered_notes, \
  clear_midi_cc_schedule, clear_param_schedule, clear_all_plugins = range(41)

class recv_cmd:
  param_change, param_changes_end, stop_playback, midi_note_event, midi_cc_event, \
  virtual_keyboard_note_event, virtual_keyboard_cc_event, \
  midi_keyboard_routed, virtual_keyboard_routed, \
  recording_started, recording_stopped, monitoring_changed, \
  audio_file_loaded, audio_playback_started, audio_playback_stopped, \
  ordered_note_triggered, ordered_playback_started, ordered_playback_stopped = range(18)

pipe_name = "juceclientserver"

sampleRate = 44100
blockSize = 64
bpm = None #set this 

inputIndex = -2
outputIndex = -1
leftChannel = 0
rightChannel = 1

if platform.system() == "Windows": #VST3 only
  defaultDirs = ( 
      "C:\\Program Files\\Steinberg\\VSTPlugins",
      "C:\\Program Files\\Common Files\\VST3",
      "C:\\Program Files\\Vstplugins",
      "C:\\Program Files (x86)\\Steinberg\\VSTPlugins",
      "C:\\Program Files (x86)\\VstPlugins",
      "C:\\VstPlugins"
      )
elif platform.system() == "Linux":
  defaultDirs = (
    "/usr/lib/vst", "/usr/local/lib/vst", "~/.vst",
    "/usr/lib/vst3", "/usr/local/lib/vst3", "~/.vst3",
    "/usr/lib/ladspa", "/usr/local/lib/ladspa", "~/.ladspa",
    "/usr/lib/lv2", "/usr/local/lib/lv2", "~/.lv2"
  )
elif platform.system() == "Darwin":
  defaultDirs = (
    "/Library/Audio/Plug-Ins/VST",
    "/Library/Audio/Plug-Ins/VST3",
    "/Library/Audio/Plug-Ins/Components", #AU
    "~/Library/Audio/Plug-Ins/VST",
    "~/Library/Audio/Plug-Ins/VST3"
  )

class dummy:
  pass

param_changes = []

def read_exact(pipe_handle, n):
  """Read exactly n bytes, blocking until all are received"""
  data = b''
  while len(data) < n:
    chunk = pipe_handle.read(n - len(data))
    if not chunk:  # EOF/pipe closed
      raise IOError("pipe closed before all data received. data: "+repr(data))
    data += chunk
  return data

class JuceAudioClient:
    def __init__(self, pipe_name=pipe_name, server_exe_path=None, pluginDirectories = None, badPluginPaths = None):
        self.pipe_name = pipe_name
        self.commands_pipe_path = f"\\\\.\\pipe\\{pipe_name}_commands"
        self.commands_pipe_handle = None
        self.notifications_pipe_path = f"\\\\.\\pipe\\{pipe_name}_notifications"
        self.notifications_pipe_handle = None
        self.commands_connected = False
        self.notifications_connected = False
        self.server_process = None
        self.server_exe_path = server_exe_path or os.path.join(os.path.dirname(os.path.abspath(__file__)), "juce_gui_server.exe") #todo: make this work on mac and linux
        self.availablePlugins = None
        self.pluginDirectories = pluginDirectories
        self.badPluginPaths = badPluginPaths

    def start_server(self):
        """Launch the JUCE server process if not already running"""
        import subprocess

        if not os.path.exists(self.server_exe_path):
            print(f"Server executable not found at: {self.server_exe_path}")
            return False

        try:
            print(f"Launching server: {self.server_exe_path} {self.pipe_name}")
            self.server_process = subprocess.Popen(
                [self.server_exe_path, self.pipe_name],
                creationflags=subprocess.CREATE_NEW_CONSOLE if platform.system() == "Windows" else 0
            )
            # Give the server a moment to create pipes
            time.sleep(1)
            return True
        except Exception as e:
            print(f"Failed to launch server: {e}")
            return False

    def connect(self, auto_start=True, max_retries=5, retry_delay=1.0):
        """Connect to the JUCE server, optionally starting it if not running"""
        for attempt in range(max_retries):
            try:
                print(f"Connecting to {self.pipe_name}... (attempt {attempt + 1}/{max_retries})")
                self.commands_pipe_handle = open(self.commands_pipe_path, 'w+b', buffering=0)
                self.notifications_pipe_handle = open(self.notifications_pipe_path, 'rb', buffering=0)
                self.commands_connected = True
                self.notifications_connected = True
                return True
                print("Connected successfully!")
            except Exception as e:
                print(f"Connection attempt {attempt + 1} failed: {e}")

                # On first failure, try to start the server
                if attempt == 0 and auto_start:
                    if self.start_server():
                        # Continue retrying after starting server
                        time.sleep(retry_delay)
                        continue
                    else:
                        print("Failed to start server automatically")
                        return False

                # Retry with delay
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                else:
                    print(f"Connection failed after {max_retries} attempts")
                    return False

        return False
    
    def disconnect(self, shutdown_server=False):
        """Disconnect from the server"""
        if self.commands_connected and self.commands_pipe_handle:
            try:
                if shutdown_server:
                    # Send shutdown command before disconnecting
                    try:
                        self.sendcmd(send_cmd.cmd_shutdown)
                        self.commands_pipe_handle.flush()
                        time.sleep(0.5)  # Give server time to shutdown
                    except:
                        pass

                self.commands_pipe_handle.close()
                print("Disconnected from server")
            except:
                pass
            self.commands_connected = False

        if self.notifications_connected and self.notifications_pipe_handle:
            try:
                self.notifications_pipe_handle.close()
            except:
                pass
            self.notifications_connected = False

        # Wait for server process to terminate if we started it
        if self.server_process:
            try:
                self.server_process.wait(timeout=3)
                print("Server process terminated")
            except:
                # Force kill if it doesn't terminate gracefully
                try:
                    self.server_process.terminate()
                except:
                    pass
            self.server_process = None
    
    def sendinfo(self, pattern, *args):
      if not self.commands_connected:
        raise IOError
      else:
        self.commands_pipe_handle.write(struct.pack("<"+pattern, *args))

    def readinfoc(self, pattern):
      if not self.commands_connected:
        raise IOError
      else:
        return struct.unpack("<"+pattern, read_exact(self.commands_pipe_handle, struct.calcsize("<"+pattern)))

    def readinfo1c(self, pattern):
      if not self.commands_connected:
        raise IOError
      else:
        return struct.unpack("<"+pattern, read_exact(self.commands_pipe_handle, struct.calcsize("<"+pattern)))[0]

    def readinfon(self, pattern):
      if not self.notifications_connected:
        raise IOError
      else:
        return struct.unpack("<"+pattern, read_exact(self.notifications_pipe_handle, struct.calcsize("<"+pattern)))

    def readinfo1n(self, pattern):
      if not self.notifications_connected:
        raise IOError
      else:
        return struct.unpack("<"+pattern, read_exact(self.notifications_pipe_handle, struct.calcsize("<"+pattern)))[0]

    def readstr1(self):
      if not self.commands_connected:
        raise IOError
      else:
        size = self.readinfo1c("I")
        return read_exact(self.commands_pipe_handle, size).decode("utf-8", errors="ignore") #debug, there should never be decoding errors so we shouldn't have errors="ignore"

    def readstrs(self, num):
      return tuple(self.readstr1() for _ in range(num))

    def sendstr(self, s):
      if not self.commands_connected:      
        raise IOError
      else:
        self.commands_pipe_handle.write(struct.pack("I", len(s)) + s.encode("utf-8"))

    def sendstrs(self, ss):
      for s in ss:
        self.sendstr(s)
        
    def sendcmd(self, command):
      self.sendinfo("B", command)
          
    def loadplugin(self, path, key): 
      #success, uid, name, errmsg
      self.sendcmd(send_cmd.load_plugin)
      self.sendstr(path)
      self.sendinfo("II", key)
      self.commands_pipe_handle.flush()
      return self.readinfoc("I") + self.readstrs(2) 
                                   
    def loadpluginbyuid(self, uid, key):
      #success, name, pluginId, uid, errmsg
      self.sendcmd(send_cmd.load_plugin_by_index)
      self.sendinfo("II", uid, key)
      self.commands_pipe_handle.flush()
      success, name, errmsg = self.readinfoc("I") + self.readstrs(2)
      if not success:
        raise ValueError("loading plugin failed. errmsg: " + errmsg) #todo: i forget, is 0 success or is 1 success?
      return Processor(uid=uid, key=key)

    def scanplugins(self, directories=None, badpaths=None):
      #num_found
      directories = directories or self.pluginDirectories
      badpaths = badpaths or self.badPluginPaths
      assert directories #todo: i forget, does scanplugins in the server have default directories?
      self.sendcmd(send_cmd.scan_plugins)
      self.sendinfo("I", len(directories))
      self.sendstrs(directories)
      self.sendinfo("I", len(badpaths))
      self.sendstrs(badpaths)
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I")

    def listplugins(self):
      self.sendcmd(send_cmd.list_plugins)
      self.commands_pipe_handle.flush()
      size = self.readinfo1c("I")
      plugins = []
      for x in range(size):
        p = dummy()
        p.isInstrument, p.pluginId, p.numInputChannels, p.numOutputChannels = self.readinfoc("IIII") #todo: we could use "?" for booleans and cast to uint8_t in c++
        p.name, p.descriptiveName, p.pluginFormatName, p.category, p.manufacturerName, p.version, p.fileOrIdentifier, p.lastFileModTime, p.path = self.readstrs(9)
        plugins.append(p)
      self.availablePlugins = plugins
      return plugins

    def listbadpaths(self):
      badpaths = []
      self.sendcmd(send_cmd.list_bad_paths)
      self.commands_pipe_handle.flush()
      size = self.readinfo1c("I")
      for x in range(size):
        badpaths.append(self.readstr1())
      return badpaths
    
    def getPluginInfo(self, pluginId):
       self.sendcmd(send_cmd.get_plugin_info)
       self.sendinfo("I", pluginId)       
       p = dummy()
       p.isInstrument, p.pluginId, p.numInputChannels, p.numOutputChannels = self.readinfoc("IIII") #todo: we could use "?" for booleans and cast to uint8_t in c++
       p.name, p.descriptiveName, p.pluginFormatName, p.category, p.manufacturerName, p.version, p.fileOrIdentifier, p.lastFileModTime, p.path = self.readstrs(9)
       return p

    def getParamsInfo(self, pluginId):
      self.sendcmd(send_cmd.get_params_info)
      self.sendinfo("I", pluginId)
      self.commands_pipe_handle.flush()
      success, numParams = self.readinfoc("II")
      print(f"{success=}{numParams=}")
      errmsg = self.readstr1()
      params = []
      for x in range(numParams):
        p = dummy()
        p.originalIndex = self.readinfo1c("I")
        p.name = self.readstr1()
        a = read_exact(self.commands_pipe_handle, struct.calcsize("<"+"ffffffIIIIII"))
        p.minValue, p.maxValue, p.interval, p.defaultValue, p.skewFactor, p.value, p.numSteps, p.isDiscrete, p.isBoolean, p.isOrientationInverted, \
          p.isAutomatable, p.isMetaParameter = struct.unpack("<ffffffIIIIII", a) #debug
        print("parameters data:")
        print(f"{p.originalIndex=} {p.name=} {p.minValue=} {p.maxValue=} {p.defaultvalue=} {p.skewFactor=} {p.value=} {p.numStemsp=} {p.isDiscrete=}"
        " {p.isBoolean=} {p.isOrientationInverted=} {p.isAutomatable=} {p.isMetaParameter=}")
      return success, numParams, params, errmsg
    
    def getChannelsInfo(self, pluginId):
      self.sendcmd(send_cmd.get_channels_info)
      self.commands_pipe_handle.flush()
      self.sendinfo("I", pluginId)
      success, acceptsMidi, producesMidi = self.readinfoc("III")
      print(f"getChannelsInfo: {pluginId=} {success=} {acceptsMidi=} {producesMidi=}")
      inputBuses = []
      for x in range(self.readinfo1c("I")):
        b = dummy()
        b.numChannels = self.readinfo1c("I")
        b.channelTypes = self.readstrs(self.readinfo1c("I"))
        b.isEnabled = self.readinfo1c("I")
        b.mainBusLayout = self.readstr1()
        inputBuses.append(b)
      print("finished reading input buses")
      outputBuses = []
      a = self.readinfo1c("I")
      print(f"number of output buses for pluginId {pluginId}: {a}")
      for x in range(a):
        print(f"reading input channel info for channel for bus {x} pluginId {pluginId}")
        b = dummy()
        b.numChannels = self.readinfo1c("I")
        print(f"{b.numChannels=}")
        a = self.readinfo1c("I")
        print(f"channel types size: {a}")
        b.channelTypes = self.readstrs(a)
        print(f"{b.channelTypes=}")
        b.isEnabled = self.readinfo1c("I")
        print(f"{b.isEnabled=}")
        b.mainBusLayout = self.readstr1()
        print(f"{b.mainBusLayout=}")
        outputBuses.append(b)
      print("finished reading output buses")
      errmsg = self.readstr1()  
      return success, acceptsMidi, producesMidi, inputBuses, outputBuses, errmsg

    def showpluginui(self, pluginId):
      #success, errmsg
      self.sendcmd(send_cmd.show_plugin_ui)
      self.sendinfo("I", pluginId)
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I"), self.readstr1()

    def schedulemidinote(self, *args):
      #in: pluginId, note, velocity, startTime, duration, channel
      self.sendcmd(send_cmd.schedule_midi_note)
      self.sendinfo("IIfddI", *args)
      self.commands_pipe_handle.flush()

    def schedulemidicc(self, *args): #todo: add to song
      #in: pluginId, controller, value, time, channel
      self.sendcmd(send_cmd.schedule_midi_cc)
      self.senfindo("IIId", *args)
      self.commands_pipe_handle_flush()
    
    def schedulemidiccs(self, arg):
      for e in arg:
        self.sendcmd(send_cmd.schedule_midi_cc)
        self.senfindo("IIId", *e)
      self.commands_pipe_handle_flush()
      
    def clearmidischedule(self): #todo: remove from song?
      self.sendcmd(send_cmd.clear_midi_schedule)
      self.commands_pipe_handle_flush()

    def clearmidiccschedule(self):
      """Clear only scheduled MIDI CC events, keeping notes intact"""
      self.sendcmd(send_cmd.clear_midi_cc_schedule)
      self.commands_pipe_handle_flush()

    def clearparamschedule(self):
      """Clear all scheduled parameter changes"""
      self.sendcmd(send_cmd.clear_param_schedule)
      self.commands_pipe_handle_flush()

    def clearallplugins(self):
      """Clear all loaded plugins from the processor graph"""
      self.sendcmd(send_cmd.clear_all_plugins)
      self.commands_pipe_handle_flush()

    def scheduleparamchange(self, *args): #todo: add to song
      #in: pluginId, parameterIndex, value, atSample)
      self.sendcmd(send_cmd.schedule_param_change)
      self.sendinfo("IIfQ", *args)
      self.commands_pipe_handle_flush()

    def scheduleparamchanges(self, arg):
      for e in arg:
        self.sendcmd(send_cmd.schedule_param_change)
        self.sendinfo("IIfQ", *e)
      self.commands_pipe_handle_flush()

    def schedulemidinotes(self, arg):
      for e in arg:
        self.sendcmd(send_cmd.schedule_midi_cc)
        self.sendinfo("IIfddI", *e)
      self.commands_pipe_handle.flush()

    def connectaudio(self, sourcePluginId, sourcechan, destPluginId, destchan, gain_db=None, group_id=None):
      self.sendcmd(send_cmd.connect_audio)
      self.sendinfo("IIII", sourcePluginId, sourcechan, destPluginId, destchan)

    def startplayback(self, endsample, tofile, filename):
      self.sendcmd(send_cmd.start_playback)
      self.sendinfo("QI", endsample, tofile)
      if tofile:
        self.sendstr(filename)
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I")

    def scheduleaudioregion(self, pluginId, startSample, fileOffset, lengthSamples, gainLinear, fadeInSamples, fadeOutSamples):
      self.sendcmd() # todo

    def loadAudioFileIntoPlayer(self, pluginId, filename):
      self.sendcmd() # todo

    def routekeyboardinput(self, pluginId, use_velocity=True, fixed_velocity=1.0):
      """Route MIDI keyboard input to a specific plugin

      Args:
        pluginId: Plugin unique id to route to
        use_velocity: If True, use actual key velocity; if False, use fixed_velocity
        fixed_velocity: Fixed velocity value (0.0-1.0) when use_velocity is False
      """
      self.sendcmd(send_cmd.route_keyboard_input)
      self.sendinfo("IIf", pluginId, int(use_velocity), fixed_velocity)
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I")

    def unroutekeyboardinput(self):
      """Disable keyboard routing"""
      self.sendcmd(send_cmd.unroute_keyboard_input)
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I")

    def showvirtualkeyboard(self):
      """Show the virtual MIDI keyboard window

      Returns:
        success: 1 if successful, 0 otherwise
      """
      self.sendcmd(send_cmd.show_virtual_keyboard)
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I")

    def hidevirtualkeyboard(self):
      """Hide the virtual MIDI keyboard window

      Returns:
        success: 1 if successful, 0 otherwise
      """
      self.sendcmd(send_cmd.hide_virtual_keyboard)
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I")

    def routevirtualkeyboard(self, pluginId, use_velocity=True, fixed_velocity=1.0):
      """Route virtual keyboard input to a specific plugin

      Args:
        pluginId: Plugin ID to route to
        use_velocity: If True, use actual key velocity; if False, use fixed_velocity
        fixed_velocity: Fixed velocity value (0.0-1.0) when use_velocity is False
      """
      self.sendcmd(send_cmd.route_virtual_keyboard)
      self.sendinfo("IIf", pluginId, int(use_velocity), fixed_velocity)
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I")

    def unroutevirtualkeyboard(self):
      """Disable virtual keyboard routing"""
      self.sendcmd(send_cmd.unroute_virtual_keyboard)
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I")

    def scheduleorderednotes(self, notes):
      """Schedule notes to be triggered in order by any MIDI key press

      Args:
        notes: List of tuples (order_number, note_number, velocity, channel, duration)
               Notes with same order_number will be triggered simultaneously
      """
      self.sendcmd(send_cmd.schedule_ordered_notes)
      self.sendinfo("I", len(notes))
      for order, note, velocity, channel, duration in notes:
        self.sendinfo("IIIII", order, note, velocity, channel, duration)
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I")  # Returns count of notes added

    def startorderedplayback(self, use_keyboard_velocity=True, use_keyboard_duration=True):
      """Start ordered note playback mode

      Args:
        use_keyboard_velocity: If True, use actual MIDI keyboard velocity; if False, use stored velocity
        use_keyboard_duration: If True, use actual MIDI keyboard note-off timing; if False, use stored duration

      If audio playback is running, activates immediately.
      If not running, will activate when playback starts.
      """
      self.sendcmd(send_cmd.start_ordered_playback)
      self.sendinfo("II", int(use_keyboard_velocity), int(use_keyboard_duration))
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I")

    def stoporderedplayback(self):
      """Stop ordered note playback mode"""
      self.sendcmd(send_cmd.stop_ordered_playback)
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I")

    def clearorderednotes(self):
      """Clear all scheduled ordered notes"""
      self.sendcmd(send_cmd.clear_ordered_notes)
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I")

class ParamSchedule(list):
  def __init__(self, *args):
    super().__init__(*args)

class AudioTrack:
  def __init__(self, filePath=None, processor=None):
    self.filePath = filePath
    self.processor = processor

Number = Union[int, float]

@dataclass
class CanonStats:
    visited: int = 0
    replaced: int = 0
    created: int = 0

@dataclass
class CanonResult:
    root: Signal
    replaced: int

def table_fingerprint(table: Sequence[float]) -> str:
    """
    Stable fingerprint for a float table (cross-platform).
    Hashes little-endian float64 bytes for each element.
    """
    h = hashlib.sha1()
    for x in table:
        h.update(struct.pack("<d", float(x)))  # explicit little-endian float64
    return h.hexdigest()

def _node_key(node: Signal, child_ids: Tuple[int, ...]) -> Tuple[Any, ...]:
    """
    Produce a hashable structural key for a node.
    child_ids are already canonical ids of children.
    """
    t = type(node)

    # Leaves
    if isinstance(node, Const):
        return (t, node.value)
    if isinstance(node, TimeFn):
        # Can't reliably hash arbitrary callables; treat as unique by identity.
        return (t, id(node.fn))
    if isinstance(node, BeatFn):
        return (t, id(node.fn))
    if isinstance(node, RefSignal):
        # reference key is stable; after binding, still keep key
        return (t, node.key)

    # Binary ops
    if isinstance(node, Add) or isinstance(node, Mul):
        # commutative: sort child ids to canonicalize (a+b == b+a)
        a, b = sorted(child_ids)
        return (t, a, b)
    if isinstance(node, Sub):
        return (t, child_ids[0], child_ids[1])
    if isinstance(node, Neg):
        return (t, child_ids[0])

    # Mod nodes
    if isinstance(node, Clamp):
        return (t, child_ids[0], node.lo, node.hi)
    if isinstance(node, Rectify):
        return (t, child_ids[0])
    if isinstance(node, Power):
        return (t, child_ids[0], node.gamma, node.unipolar)
    if isinstance(node, MapRange):
        return (t, child_ids[0], node.in_min, node.in_max, node.out_min, node.out_max, node.clamp)
    if isinstance(node, Mix):
        return (t, child_ids[0], child_ids[1], child_ids[2])

    if isinstance(node, WavetableOverTime):
        return (
            t,
            ("tbl", node._table_fp),
            node.duration,
            node.phase0,
            node.interp,
            node.loop,
        )
    # don't subject smooth and wavetableosc to the global canonicalizer (callapses identical routes to the same route) because they have internal state: _smooth_cache and _phase_cache, respectively.
    # If freq is constant and you treat phase as purely a function of time (no cached state needed), then it’s safe to CSE because there’s no “history”.
    # In your oscillator, constant freq path is exact and doesn’t need phase cache. So you can do:
    if isinstance(node, WavetableOsc):
        # Policy B: CSE only if constant freq (stateless)
        if isinstance(node.freq, Const):
            return (
                t,
                ("tbl", node._table_fp),
                ("ConstFreq", node.freq.value),
                node.phase0,
                node.interp,
                node.loop,
            )
        return (t, id(node))

    if isinstance(node, Smooth):
        # IMPORTANT: smooth has internal cache/state per instance; two Smooth nodes with same input
        # are *functionally* same in forward rendering, but deduping them can change cache behavior for random access.
        # Safer to treat Smooth as unique by identity unless you decide otherwise.
        return (t, id(node))

    # Fallback: unique
    return (t, id(node))

def canonicalize(root: Signal) -> CanonResult:
    """
    Returns a structurally deduped version of the signal DAG.
    Only dedupes node types with stable structural keys.
    """
    memo: Dict[int, Signal] = {}           # original id -> canonical node
    intern: Dict[Tuple[Any, ...], Signal] = {}  # key -> canonical node
    replaced = 0

    def rec(n: Signal) -> Signal:
        nonlocal replaced
        if n.id in memo:
            return memo[n.id]

        # canonicalize children first
        ch = tuple(rec(c) for c in n.children())
        child_ids = tuple(c.id for c in ch)
        key = _node_key(n, child_ids)

        # if we can intern this node, return existing canonical instance
        existing = intern.get(key)
        if existing is not None:
            memo[n.id] = existing
            replaced += 1
            return existing

        # otherwise we need a node instance representing (n with canonical children)
        # For safety, we only rebuild nodes for the types we know how to reconstruct cleanly.
        new_node = _rebuild_node(n, ch)

        intern[key] = new_node
        memo[n.id] = new_node
        return new_node

    return CanonResult(root=rec(root), replaced=replaced)

# ----------------------------
# Render context + block cache
# ----------------------------

@dataclass(frozen=True)
class MidiNoteEvent:
    sample: int
    on: bool
    channel: int
    pitch: int
    velocity: int
    note_id: int  # optional stable id

@dataclass(frozen=True)
class MidiCCEvent:
    sample: int
    channel: int
    cc: int
    value: int  # 0..127

@dataclass(frozen=True)
class ParamEvent:
    sample: int
    node_id: int     # plugin node in graph
    param_id: int    # VST3 param id (or your own)
    value: float     # normalized or real, your choice

@dataclass(frozen=True)
class EvalContext:
    sample_rate: int
    block_size: int = 128
    # Optional: if you want beat-aware signals later
    beat_at_sample: Optional[Callable[[int], float]] = None

@dataclass(frozen=True)
class PluginNode:
    node_id: int
    vst3_uid: str            # or (plugin_path, class_id)
    name: str
    kind: str                # "instrument" | "effect"
    # plus initial param state, program, etc.

@dataclass(frozen=True)
class PluginEdge:
    src_node: int
    src_port: str            # "audio.out:0", "midi.out", etc.
    dst_node: int
    dst_port: str
    kind: str                # "audio" | "midi"
    gain: float = 1.0        # optional

@dataclass
class RenderPlan:
    nodes: list[PluginNode]
    edges: list[PluginEdge]
    notes: list[MidiNoteEvent]
    ccs: list[MidiCCEvent]
    params: list[ParamEvent]

class BlockCache:
    """
    Cache values per (signal_id, block_index).
    The assumption is you evaluate signals at block boundaries or treat values as piecewise-constant per block.
    """
    def __init__(self):
        self._cache: Dict[tuple[int, int], float] = {}

    def get(self, signal_id: int, block_index: int) -> Optional[float]:
        return self._cache.get((signal_id, block_index))

    def set(self, signal_id: int, block_index: int, value: float) -> None:
        self._cache[(signal_id, block_index)] = value


def _rebuild_node(n: Signal, ch: tuple[Signal, ...]) -> Signal:
    # folds identically defined nodes into one to save computation time

    # Leaves: return the same node (safe) except we might want new Consts
    if isinstance(n, Const):
        return Const(n.value)
    if isinstance(n, TimeFn):
        return n
    if isinstance(n, BeatFn):
        return n
    if isinstance(n, RefSignal):
        # keep same ref node instance (it may already be bound)
        return n

    # Ops

    if isinstance(n, Add):
        if isinstance(ch[0], Const) and isinstance(ch[1], Const):
            return Const(ch[0].value + ch[1].value)
        return Add(ch[0], ch[1])
    if isinstance(n, Sub):
        return Sub(ch[0], ch[1])
    # --- Mul(Const, Const) ---
    if isinstance(n, Mul):
        if isinstance(ch[0], Const) and isinstance(ch[1], Const):
            return Const(ch[0].value * ch[1].value)
        return Mul(ch[0], ch[1])
    # --- Neg(Const) ---
    if isinstance(n, Neg):
        if isinstance(ch[0], Const):
            return Const(-ch[0].value)
        return Neg(ch[0])
    # --- Clamp(Const) ---
    if isinstance(n, Clamp):
        if isinstance(ch[0], Const):
            v = ch[0].value
            if v < n.lo:
                v = n.lo
            elif v > n.hi:
                v = n.hi
            return Const(v)
        return Clamp(ch[0], n.lo, n.hi)
    # Modifiers
    if isinstance(n, Rectify):
        return Rectify(ch[0])
    if isinstance(n, Power):
        return Power(ch[0], n.gamma, unipolar=n.unipolar)
        # --- MapRange(Const) ---
    if isinstance(n, MapRange):
        if isinstance(ch[0], Const):
            x = ch[0].value
            y = n.out_min + (x - n.in_min) * n.scale
            if n.clamp:
                lo = min(n.out_min, n.out_max)
                hi = max(n.out_min, n.out_max)
                if y < lo:
                    y = lo
                elif y > hi:
                    y = hi
            return Const(y)
        # IMPORTANT: rebuild MapRange so its 'scale' is consistent with ctor
        return MapRange(ch[0], n.in_min, n.in_max, n.out_min, n.out_max, clamp=n.clamp)
    if isinstance(n, Mix):
        return Mix(ch[0], ch[1], ch[2])

    # Wavetables
    if isinstance(n, WavetableOverTime):
        return WavetableOverTime(
            n.table,
            duration_seconds=n.duration,
            phase0=n.phase0,
            interp=n.interp,
            loop=n.loop,
        )
    if isinstance(n, WavetableOsc):
        # Only safe to rebuild (and thus dedupe) if freq is Const
        if isinstance(n.freq, Const):
            return WavetableOsc(
                n.table,
                ch[0],  # freq signal (will be Const)
                phase0=n.phase0,
                interp=n.interp,
                loop=n.loop,
                cache_every_blocks=n._cache_every_blocks,
            )
        return n  # keep identity for variable freq

    # Stateful nodes: keep as-is
    if isinstance(n, Smooth):
        return n

    return n

# ----------------------------
# Signal base class
# ----------------------------

class GlobalCanonicalizer:
    """
    Cross-track CSE: one interning table shared across all roots.
    """
    def __init__(self):
        self.intern: Dict[Tuple[Any, ...], Signal] = {}
        self.memo: Dict[int, Signal] = {}   # original node id -> canonical node
        self.stats = CanonStats()

    def canonicalize(self, root: Signal) -> Signal:
        def rec(n: Signal) -> Signal:
            nid = n.id
            if nid in self.memo:
                return self.memo[nid]

            self.stats.visited += 1

            # Canonicalize children first
            ch = tuple(rec(c) for c in n.children())
            child_ids = tuple(c.id for c in ch)

            key = _node_key(n, child_ids)

            existing = self.intern.get(key)
            if existing is not None:
                self.memo[nid] = existing
                self.stats.replaced += 1
                return existing

            # Build a node that points to canonical children (plus constant folding)
            new_node = _rebuild_node(n, ch)

            self.intern[key] = new_node
            self.memo[nid] = new_node
            self.stats.created += 1
            return new_node

        return rec(root)

class Signal:
    """
    A signal is a pure function sampled at (sample, ctx).
    By default we cache at block granularity: block = sample // ctx.block_size.
    """

    _next_id = 1

    def __init__(self):
        self._id = Signal._next_id
        Signal._next_id += 1

    @property
    def requires_beat(self) -> bool:
        return False

    @property
    def id(self) -> int:
        return self._id

    def at(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache] = None) -> float:
        """
        Evaluate signal at 'sample'. Default caching is per-block.
        """
        if sample < 0:
            raise ValueError("sample must be >= 0")

        block = sample // ctx.block_size
        if cache is not None:
            hit = cache.get(self._id, block)
            if hit is not None:
                return hit

        v = float(self._eval(sample, ctx, cache))
        if cache is not None:
            cache.set(self._id, block, v)
        return v

    def children(self) -> tuple["Signal", ...]:
        # Leaf signals override with ().
        return ()

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        raise NotImplementedError

    # ---- operator overloads ----

    def __add__(self, other: Union[Signal, Number]) -> Signal:
        return Add(self, as_signal(other))

    def __radd__(self, other: Number) -> Signal:
        return Add(as_signal(other), self)

    def __sub__(self, other: Union[Signal, Number]) -> Signal:
        return Sub(self, as_signal(other))

    def __rsub__(self, other: Number) -> Signal:
        return Sub(as_signal(other), self)

    def __mul__(self, other: Union[Signal, Number]) -> Signal:
        return Mul(self, as_signal(other))

    def __rmul__(self, other: Number) -> Signal:
        return Mul(as_signal(other), self)

    def __neg__(self) -> Signal:
        return Neg(self)

def as_signal(x: Union[Signal, Number]) -> Signal:
    return x if isinstance(x, Signal) else Const(float(x))


# ----------------------------
# Concrete signals
# ----------------------------

class Const(Signal):
    def __init__(self, value: float):
        super().__init__()
        self.value = float(value)

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        return self.value

    def children(self) -> tuple["Signal", ...]:
        return ()

class TimeFn(Signal):
    """
    Wrap an arbitrary user function f(t_seconds) -> float
    """
    def __init__(self, fn: Callable[[float], float]):
        super().__init__()
        self.fn = fn

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        t = sample / ctx.sample_rate
        v = float(self.fn(t))
        if not math.isfinite(v):
            raise ValueError("Signal function returned non-finite value")
        return v

    def children(self) -> tuple["Signal", ...]:
        return ()

class BeatFn(Signal):
    """
    Wrap f(beat) -> float, using ctx.beat_at_sample(sample) supplied by your scheduler/tempo map.
    """
    def __init__(self, fn: Callable[[float], float]):
        super().__init__()
        self.fn = fn

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        if ctx.beat_at_sample is None:
            raise ValueError("BeatFn requires ctx.beat_at_sample")
        b = float(ctx.beat_at_sample(sample))
        v = float(self.fn(b))
        if not math.isfinite(v):
            raise ValueError("Signal function returned non-finite value")
        return v

    @property
    def requires_beat(self) -> bool:
        return True

    def children(self) -> tuple["Signal", ...]:
        return ()

# ----------------------------
# Expression nodes
# ----------------------------

class BinaryOp(Signal):
    def __init__(self, a: Signal, b: Signal):
        super().__init__()
        self.a = a
        self.b = b

    @property
    def requires_beat(self) -> bool:
        return self.a.requires_beat or self.b.requires_beat

    def children(self) -> tuple["Signal", ...]:
        return (self.a, self.b)

class Add(BinaryOp):
    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        return self.a.at(sample, ctx, cache) + self.b.at(sample, ctx, cache)

class Sub(BinaryOp):
    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        return self.a.at(sample, ctx, cache) - self.b.at(sample, ctx, cache)

class Mul(BinaryOp):
    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        return self.a.at(sample, ctx, cache) * self.b.at(sample, ctx, cache)

class Neg(Signal):
    def __init__(self, x: Signal):
        super().__init__()
        self.x = x

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        return -self.x.at(sample, ctx, cache)

    @property
    def requires_beat(self) -> bool:
        return self.x.requires_beat

    def children(self) -> tuple["Signal", ...]:
        return (self.x,)
# ----------------------------
# Example: define a shared curve and use it for multiple params
# ----------------------------

if __name__ == "__main__":
    ctx = EvalContext(sample_rate=48000, block_size=128)
    cache = BlockCache()

    # shared arbitrary curve: "vibe"
    vibe = TimeFn(lambda t: math.sin(2 * math.pi * 5.0 * t))  # 5 Hz sine

    base_tempo = Const(120.0)
    tempo = base_tempo + 20.0 * vibe
    microtune = 7.0 * vibe
    cutoff = 2000.0 + 500.0 * vibe

    s = 1024  # sample index

    # These will share cached vibe evaluation at the same block.
    print("tempo:", tempo.at(s, ctx, cache))
    print("microtune:", microtune.at(s, ctx, cache))
    print("cutoff:", cutoff.at(s, ctx, cache))

class InterpMode:
    NEAREST = "nearest"
    LINEAR = "linear"

def _wrap01(x: float) -> float:
    return x - math.floor(x)

def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)

def _wavetable_lookup(table: Sequence[float], phase01: float, interp: str) -> float:
    n = len(table)
    if n == 0:
        raise ValueError("Wavetable cannot be empty")
    if n == 1:
        return float(table[0])

    pos = phase01 * n
    if interp == InterpMode.NEAREST:
        i = int(pos) % n
        return float(table[i])

    if interp == InterpMode.LINEAR:
        i0 = int(pos)
        frac = pos - i0
        i0 = i0 % n
        i1 = (i0 + 1) % n
        return float(table[i0]) * (1.0 - frac) + float(table[i1]) * frac

    raise ValueError(f"Unknown interp mode: {interp}")

@dataclass
class PhaseCache:
    """
    Stores phase at block boundaries for a specific oscillator.
    Keyed by block_index -> phase (float cycles, not wrapped).
    """
    phase_by_block: Dict[int, float] = field(default_factory=dict)

    def get_nearest_leq(self, block_index: int) -> Tuple[int, float]:
        """
        Return (b, phase_b) for the greatest cached b <= block_index.
        """
        if not self.phase_by_block:
            raise ValueError("PhaseCache is empty (seed block 0 first)")
        # dict isn't ordered; do a small search. If you want faster, store sorted keys too.
        # Typically cache is dense, so this is fine; you can optimize later.
        candidates = [b for b in self.phase_by_block.keys() if b <= block_index]
        if not candidates:
            raise ValueError("No cached phase <= requested block")
        b = max(candidates)
        return b, self.phase_by_block[b]

    def set(self, block_index: int, phase: float) -> None:
        self.phase_by_block[block_index] = phase

class WavetableOsc(Signal):
    """
    Wavetable oscillator with optional time-varying frequency.
    Phase units: cycles (1.0 = one full wrap).
    """
    def __init__(
        self,
        table: Sequence[float],
        freq_hz: Union[Signal, Number],
        *,
        phase0: float = 0.0,
        interp: str = InterpMode.LINEAR,
        loop: bool = True,
        cache_every_blocks: int = 64,  # drop phase anchors every N blocks
    ):
        super().__init__()
        self.table = list(map(float, table))
        if not self.table:
            raise ValueError("WavetableOsc table cannot be empty")

        self._table_fp = table_fingerprint(self.table)

        self.freq = as_signal(freq_hz)
        self.phase0 = float(phase0)
        self.interp = interp
        self.loop = loop

        self._phase_cache = PhaseCache()
        self._phase_cache.set(0, self.phase0)
        self._cache_every_blocks = int(cache_every_blocks)

    def _phase_at_block_start(self, block: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        # Constant freq: exact
        if isinstance(self.freq, Const):
            tb = (block * ctx.block_size) / ctx.sample_rate
            return self.phase0 + tb * self.freq.value

        # Variable freq: use phase cache anchors
        # Find nearest cached block <= block
        b0, phase0 = self._phase_cache.get_nearest_leq(block)
        phase = phase0

        dt = ctx.block_size / ctx.sample_rate

        # Integrate b0 .. block-1
        for bi in range(b0, block):
            # midpoint sample for this block
            s_mid = bi * ctx.block_size + (ctx.block_size // 2)
            f_mid = float(self.freq.at(s_mid, ctx, cache))
            phase += f_mid * dt

            # periodically cache
            if self._cache_every_blocks > 0 and ((bi + 1) % self._cache_every_blocks == 0):
                self._phase_cache.set(bi + 1, phase)

        # Always cache the requested block (helps random access)
        self._phase_cache.set(block, phase)
        return phase

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        block = sample // ctx.block_size
        phase = self._phase_at_block_start(block, ctx, cache)
        phase01 = _wrap01(phase) if self.loop else _clamp01(phase)
        return _wavetable_lookup(self.table, phase01, self.interp)

    def children(self) -> tuple["Signal", ...]:
        return (self.freq,)

    """
    Treat the table as a curve over normalized time u in [0,1] (or looping).
    Useful for arbitrary automation/LFO shapes without trig.

    Examples:
      - one-shot envelope-like curve: loop=False, duration_seconds=2.0
      - looping LFO: loop=True, period_seconds=0.5
    """

class WavetableOverTime(Signal):
    """
    Treat the table as a curve over normalized time u in [0,1] (or looping).
    Useful for arbitrary automation/LFO shapes without trig.

    Examples:
      - one-shot envelope-like curve: loop=False, duration_seconds=2.0
      - looping LFO: loop=True, period_seconds=0.5
    """

    def __init__(
        self,
        table: Sequence[float],
        *,
        duration_seconds: float,
        phase0: float = 0.0,
        interp: str = InterpMode.LINEAR,
        loop: bool = False,
    ):
        super().__init__()
        self.table = list(map(float, table))
        if len(self.table) == 0:
            raise ValueError("WavetableOverTime table cannot be empty")
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be > 0")
        self._table_fp = table_fingerprint(self.table)
        self.duration = float(duration_seconds)
        self.phase0 = float(phase0)
        self.interp = interp
        self.loop = loop

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        t = sample / ctx.sample_rate
        u = (self.phase0 + (t / self.duration))
        u01 = _wrap01(u) if self.loop else _clamp01(u)
        return _wavetable_lookup(self.table, u01, self.interp)

    def children(self) -> tuple["Signal", ...]:
        return ()

class SignalCycleError(ValueError):
    pass

def detect_signal_cycle(root: Signal) -> None:
    """
    Raises SignalCycleError if a cycle is reachable from root.
    Otherwise returns None.
    """
    # 0 = unvisited, 1 = visiting (in stack), 2 = done
    color: Dict[int, int] = {}
    stack: List[int] = []

    def dfs(node: Signal):
        nid = node.id
        c = color.get(nid, 0)
        if c == 1:
            # cycle: node is already in the current recursion stack
            # Build a readable cycle path from stack
            cycle_start = stack.index(nid) if nid in stack else 0
            cycle = stack[cycle_start:] + [nid]
            raise SignalCycleError(f"Cycle detected in Signal graph (ids): {cycle}")
        if c == 2:
            return

        color[nid] = 1
        stack.append(nid)

        for ch in node.children():
            dfs(ch)

        stack.pop()
        color[nid] = 2

    dfs(root)

def signal_depends_on_beat(root: Signal) -> bool:
    """
    Returns True if any BeatFn node is reachable from root.
    """
    seen: Set[int] = set()
    stack: List[Signal] = [root]

    while stack:
        n = stack.pop()
        if n.id in seen:
            continue
        seen.add(n.id)
        if isinstance(n, BeatFn):
            return True
        stack.extend(n.children())
    return False

"""
Example usage
A) Classic LFO from a wavetable (looping)
# One cycle wavetable (e.g., triangle)
tri = [0.0, 0.5, 1.0, 0.5]  # toy example; real tables have 256/1024 samples

vibe = WavetableOsc(tri, freq_hz=5.0, interp=InterpMode.LINEAR, loop=True)

tempo = 120 + 20 * vibe
microtune = 7 * vibe
B) Arbitrary curve as a “shape over time” (one-shot or looping)
# A user-drawn automation curve over 2 seconds
curve = [0.0, 0.2, 0.9, 0.4, 0.8, 0.0]

vibe = WavetableOverTime(curve, duration_seconds=2.0, loop=False)
cutoff = 2000 + 500 * vibe
"""

# ----------------------------
# Note types
# ----------------------------

@dataclass(frozen=True)
class BeatNote:
    start: Fraction
    dur: Fraction
    pitch: int
    vel: int
    origin_id: int

@dataclass(frozen=True)
class ScheduledNote:
    start_sample: int
    dur_samples: int
    pitch: int
    vel: int
    origin_id: int

BeatArrangement = List[BeatNote]
ScheduledArrangement = List[ScheduledNote]


# ----------------------------
# Rounding / quantization
# ----------------------------

class RoundMode:
    CEIL = "ceil"
    FLOOR = "floor"
    NEAREST = "nearest"

def round_float_to_int(x: float, mode: str) -> int:
    if mode == RoundMode.CEIL:
        return int(math.ceil(x))
    if mode == RoundMode.FLOOR:
        return int(math.floor(x))
    if mode == RoundMode.NEAREST:
        return int(round(x))
    raise ValueError(f"Unknown round mode: {mode}")

def quantize_beat_float_to_fraction(
    beat: float,
    grid: Fraction,
    mode: str = RoundMode.NEAREST,
) -> Fraction:
    g = float(grid)
    if g <= 0:
        raise ValueError("grid must be > 0")
    q = beat / g
    if mode == RoundMode.CEIL:
        qi = math.ceil(q)
    elif mode == RoundMode.FLOOR:
        qi = math.floor(q)
    elif mode == RoundMode.NEAREST:
        qi = int(round(q))
    else:
        raise ValueError(f"Unknown round mode: {mode}")
    return grid * qi


# ----------------------------
# Anchor cache: beat <-> sample
# ----------------------------

@dataclass
class AnchorCache:
    beats: List[float] = field(default_factory=list)
    samples: List[int] = field(default_factory=list)

    def add_anchor(self, beat: float, sample: int) -> None:
        if self.beats:
            if beat <= self.beats[-1] or sample <= self.samples[-1]:
                raise ValueError("Anchors must be strictly increasing")
        self.beats.append(beat)
        self.samples.append(sample)

    def find_anchor_leq_beat(self, beat: float) -> Tuple[float, int]:
        if not self.beats:
            raise ValueError("No anchors")
        i = bisect.bisect_right(self.beats, beat) - 1
        i = max(i, 0)
        return self.beats[i], self.samples[i]

    def find_anchor_leq_sample(self, sample: int) -> Tuple[float, int]:
        if not self.samples:
            raise ValueError("No anchors")
        i = bisect.bisect_right(self.samples, sample) - 1
        i = max(i, 0)
        return self.beats[i], self.samples[i]


# ----------------------------
# Tempo segments
# ----------------------------

class TempoSegment(Protocol):
    b0: float
    b1: float
    t0: float
    def bpm(self, beat: float) -> float: ...
    def beat_to_time_seconds(self, beat: float) -> float: ...
    def time_seconds_to_beat(self, t: float) -> float: ...

@dataclass
class ConstBpmByBeat:
    b0: float
    b1: float
    bpm_value: float
    t0: float

    def bpm(self, beat: float) -> float:
        return self.bpm_value

    def beat_to_time_seconds(self, beat: float) -> float:
        spb = 60.0 / self.bpm_value
        return self.t0 + (beat - self.b0) * spb

    def time_seconds_to_beat(self, t: float) -> float:
        spb = 60.0 / self.bpm_value
        return self.b0 + (t - self.t0) / spb

@dataclass
class LinearBpmByBeat:
    """
    BPM(b) = m*b + c on [b0, b1)
    """
    b0: float
    b1: float
    m: float
    c: float
    t0: float

    def bpm(self, beat: float) -> float:
        return self.m * beat + self.c

    def beat_to_time_seconds(self, beat: float) -> float:
        if abs(self.m) < 1e-15:
            bpm0 = self.bpm(self.b0)
            spb = 60.0 / bpm0
            return self.t0 + (beat - self.b0) * spb

        num = self.m * beat + self.c
        den = self.m * self.b0 + self.c
        if num <= 0 or den <= 0:
            raise ValueError("BPM must stay > 0 throughout segment")
        return self.t0 + (60.0 / self.m) * math.log(num / den)

    def time_seconds_to_beat(self, t: float) -> float:
        if abs(self.m) < 1e-15:
            bpm0 = self.bpm(self.b0)
            spb = 60.0 / bpm0
            return self.b0 + (t - self.t0) / spb

        den = self.m * self.b0 + self.c
        if den <= 0:
            raise ValueError("BPM must stay > 0")
        factor = math.exp((self.m / 60.0) * (t - self.t0))
        return (den * factor - self.c) / self.m

@dataclass
class LambdaBpmByBeat:
    """
    Arbitrary BPM(b) supplied by user. beat_to_time/time_to_beat are handled by TempoMap numerically.
    """
    b0: float
    b1: float
    bpm_fn: Callable[[float], float]
    t0: float

    def bpm(self, beat: float) -> float:
        bpm = float(self.bpm_fn(beat))
        if bpm <= 0:
            raise ValueError("BPM must be > 0")
        return bpm

    def beat_to_time_seconds(self, beat: float) -> float:
        raise NotImplementedError("Use TempoMap cached methods")

    def time_seconds_to_beat(self, t: float) -> float:
        raise NotImplementedError("Use TempoMap cached methods")


# ----------------------------
# TempoMap (with inverse refinement)
# ----------------------------

@dataclass
class TempoMap:
    segments: List[TempoSegment]
    sample_rate: int
    anchors: AnchorCache = field(default_factory=AnchorCache)
    bpm_mod_signal: Optional[Signal] = None
    block_samples: int = 128
    anchor_every_samples: int = 8192
    round_mode: str = RoundMode.CEIL

    def __post_init__(self):
        self.segments.sort(key=lambda s: s.b0)

        if not self.segments:
            raise ValueError("TempoMap needs at least one segment")

        if self.bpm_mod_signal is not None:
            detect_signal_cycle(self.bpm_mod_signal)
            if signal_depends_on_beat(self.bpm_mod_signal):
                raise ValueError("Tempo modulation signal must not depend on beat (cycle risk).")

        self.anchors.add_anchor(self.segments[0].b0, 0)

    def _find_segment_for_beat(self, beat: float) -> TempoSegment:
        b0s = [s.b0 for s in self.segments]
        i = bisect.bisect_right(b0s, beat) - 1
        if i < 0:
            raise ValueError("beat before first segment")
        seg = self.segments[i]
        if not (seg.b0 <= beat < seg.b1):
            raise ValueError("beat not covered by any segment")
        return seg

    def _bpm_effective(self, beat_est: float, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        bpm = self._bpm_base(beat_est)
        if self.bpm_mod_signal is not None:
            bpm += float(self.bpm_mod_signal.at(sample, ctx, cache))
        if bpm <= 0 or not math.isfinite(bpm):
            raise ValueError("Effective BPM must be finite and > 0")
        return bpm

    def set_bpm_modulation(self, signal: Optional[Signal]) -> None:
        if signal is not None:
            detect_signal_cycle(signal)
            if signal_depends_on_beat(signal):
                raise ValueError(
                    "Tempo modulation signal must not depend on beat "
                    "(would create a dependency cycle). Use a time/sample-based signal instead."
                )
        self.bpm_mod_signal = signal
        # ---- Beat -> Sample ----

    def beat_to_sample(self, beat: Union[Fraction, float], *, ctx: Optional[EvalContext]=None, cache: Optional[BlockCache]=None) -> int:
        b = float(beat)

        # If no modulation, keep the existing fast path
        if self.bpm_mod_signal is None:
            seg = self._find_segment_for_beat(b)
            if not isinstance(seg, LambdaBpmByBeat):
                t = seg.beat_to_time_seconds(b)
                return round_float_to_int(t * self.sample_rate, self.round_mode)
            return self._beat_to_sample_lambda_cached(b, seg)

        # With modulation: must have ctx/cache for signal evaluation
        if ctx is None:
            ctx = EvalContext(sample_rate=self.sample_rate, block_size=self.block_samples)
        if self.bpm_mod_signal.requires_beat:
            raise ValueError("Tempo modulation must not depend on beat (cycle).")
        if cache is None:
            cache = BlockCache()

        return self._beat_to_sample_with_modulation(b, ctx, cache)

    def _bpm_base(self, beat: float) -> float:
        seg = self._find_segment_for_beat(beat)
        return seg.bpm(beat)

    def _beat_to_sample_lambda_cached(self, b_target: float, seg: LambdaBpmByBeat) -> int:
        b_anchor, s_anchor = self.anchors.find_anchor_leq_beat(b_target)
        if b_anchor < seg.b0:
            raise ValueError("Missing anchor at lambda segment boundary (build boundary anchors)")

        cur_b = b_anchor
        cur_s = s_anchor

        while cur_b < b_target:
            bpm_now = seg.bpm(cur_b)
            beats_per_sec = bpm_now / 60.0
            remaining_beats = b_target - cur_b
            max_block = int(math.ceil(remaining_beats * self.sample_rate / max(beats_per_sec, 1e-12)))
            block = max(1, min(self.block_samples, max_block))

            dt = block / self.sample_rate

            # midpoint
            db_euler = beats_per_sec * dt
            mid_b = cur_b + 0.5 * db_euler
            bpm_mid = seg.bpm(mid_b)
            db = (bpm_mid / 60.0) * dt

            cur_b += db
            cur_s += block

            if (cur_s - self.anchors.samples[-1]) >= self.anchor_every_samples:
                if cur_b <= self.anchors.beats[-1]:
                    raise ValueError("Non-monotonic beat progress during integration")
                self.anchors.add_anchor(cur_b, cur_s)

        return cur_s

    # ---- Sample -> Beat (fast + optional refinement) ----

    def sample_to_beat(
            self,
            sample: int,
            *,
            refine: bool = True,
            refine_iters: int = 20,
            ctx: Optional[EvalContext] = None,
            cache: Optional[BlockCache] = None,
    ) -> float:
        if sample < 0:
            raise ValueError("sample must be >= 0")

        if ctx is None:
            ctx = EvalContext(sample_rate=self.sample_rate, block_size=self.block_samples)
        if cache is None:
            cache = BlockCache()

        # If modulation exists, prevent beat-dependent modulation (cycle) defensively
        if self.bpm_mod_signal is not None and self.bpm_mod_signal.requires_beat:
            raise ValueError("Tempo modulation must not depend on beat (cycle).")

        b_anchor, s_anchor = self.anchors.find_anchor_leq_sample(sample)
        cur_b = b_anchor
        cur_s = s_anchor

        b_low = cur_b
        s_low = cur_s

        while cur_s < sample:
            remaining = sample - cur_s
            block = min(self.block_samples, remaining)
            dt = block / self.sample_rate

            # --- Modulation-aware midpoint integration in SAMPLE domain ---
            # evaluate bpm at current point (use block midpoint sample for control-rate modulation)
            s_mid = cur_s + (block // 2)

            bpm_now = self._bpm_effective(cur_b, s_mid, ctx, cache)
            beats_per_sec = bpm_now / 60.0

            db_euler = beats_per_sec * dt
            mid_b = cur_b + 0.5 * db_euler

            bpm_mid = self._bpm_effective(mid_b, s_mid, ctx, cache)
            db = (bpm_mid / 60.0) * dt
            # -------------------------------------------------------------

            b_low, s_low = cur_b, cur_s
            cur_b += db
            cur_s += block

            if (cur_s - self.anchors.samples[-1]) >= self.anchor_every_samples:
                if cur_b <= self.anchors.beats[-1]:
                    raise ValueError("Non-monotonic beat progress during integration")
                self.anchors.add_anchor(cur_b, cur_s)

        b_high = cur_b
        s_high = cur_s

        if not refine:
            return b_high

        # Refinement: binary search in beat space.
        # IMPORTANT: for modulation, beat_to_sample() must also be called with ctx/cache.
        lo, hi = b_low, b_high
        if s_low == sample:
            return lo
        if s_high == sample:
            return hi

        s_lo = self.beat_to_sample(lo, ctx=ctx, cache=cache)
        s_hi = self.beat_to_sample(hi, ctx=ctx, cache=cache)

        if not (s_lo <= sample <= s_hi):
            hi = hi + 1e-6
            s_hi = self.beat_to_sample(hi, ctx=ctx, cache=cache)
            if not (s_lo <= sample <= s_hi):
                return b_high

        for _ in range(refine_iters):
            mid = 0.5 * (lo + hi)
            s_mid2 = self.beat_to_sample(mid, ctx=ctx, cache=cache)
            if s_mid2 < sample:
                lo = mid
            else:
                hi = mid

        return hi

        #In your _beat_to_sample_with_modulation() you used the same s_mid for both bpm and bpm_mid. That’s fine (control-rate assumption). The patch above does the same: it samples modulation at s_mid and treats it constant across that block.
        #If later you want slightly better accuracy, you can sample modulation at cur_s + block//4 and cur_s + 3*block//4 (or similar), but not necessary now.

    def _beat_to_sample_with_modulation(self, b_target: float, ctx: EvalContext, cache: BlockCache) -> int:
        # Use your existing beat->sample anchors as starting point

        #"In _beat_to_sample_with_modulation() you compute bpm_mid using the same s_mid as the start bpm. Ideally midpoint integration should sample modulation at the midpoint sample too (or just accept “control-rate” tempo modulation). If you’re treating modulation as piecewise-constant per block, using the same s_mid is fine and consistent—just be aware it’s a deliberate approximation."

        b_anchor, s_anchor = self.anchors.find_anchor_leq_beat(b_target)
        cur_b = b_anchor
        cur_s = s_anchor

        # integrate in blocks until cur_b >= b_target
        while cur_b < b_target:
            block = ctx.block_size

            # sample modulation at block midpoint (or start)
            s_mid = cur_s + (block // 2)
            bpm_mod = float(self.bpm_mod_signal.at(s_mid, ctx, cache))
            bpm = self._bpm_base(cur_b) + bpm_mod
            if bpm <= 0 or not math.isfinite(bpm):
                raise ValueError("Effective BPM must be finite and > 0")

            dt = block / self.sample_rate

            # midpoint integration in beat-space
            db_euler = (bpm / 60.0) * dt
            mid_b = cur_b + 0.5 * db_euler
            bpm_mid = self._bpm_base(mid_b) + float(self.bpm_mod_signal.at(s_mid, ctx, cache))
            if bpm_mid <= 0 or not math.isfinite(bpm_mid):
                raise ValueError("Effective BPM must be finite and > 0")

            cur_b += (bpm_mid / 60.0) * dt
            cur_s += block

            if (cur_s - self.anchors.samples[-1]) >= self.anchor_every_samples:
                self.anchors.add_anchor(cur_b, cur_s)

        return cur_s

    def ensure_boundary_anchors(self, ctx: Optional[EvalContext] = None, cache: Optional[BlockCache] = None) -> None:
        if ctx is None:
            ctx = EvalContext(sample_rate=self.sample_rate, block_size=self.block_samples)
        if cache is None:
            cache = BlockCache()
        for seg in self.segments[1:]:
            _ = self.beat_to_sample(float(seg.b0), ctx=ctx, cache=cache)

# ----------------------------
# TempoMapBuilder (computes t0, validates segments, seeds anchors)
# ----------------------------

@dataclass
class TempoMapBuilder:
    """
    Build a beat-parameterized tempo map with guaranteed continuity.

    You add segments in increasing beat order. Builder computes each segment's t0
    so beat->time mapping is continuous.

    For analytic segments, segment end time is exact.
    For lambda segments, builder estimates end time by numeric integration in beat space,
    which is sufficient for continuity and scheduling.
    """
    sample_rate: int
    _segments: List[TempoSegment] = field(default_factory=list)
    _current_time: float = 0.0

    # numeric integration controls for lambda segment end-time estimation
    lambda_step_beats: float = 1.0 / 256  # smaller => more accurate

    def add_const(self, b0: float, b1: float, bpm: float) -> TempoMapBuilder:
        self._validate_new_segment_bounds(b0, b1)
        if bpm <= 0:
            raise ValueError("bpm must be > 0")
        seg = ConstBpmByBeat(b0=b0, b1=b1, bpm_value=bpm, t0=self._current_time)
        # advance current_time to end of segment exactly
        self._current_time = seg.beat_to_time_seconds(b1)
        self._segments.append(seg)
        return self

    def add_linear_by_beat(self, b0: float, b1: float, bpm0: float, bpm1: float) -> TempoMapBuilder:
        self._validate_new_segment_bounds(b0, b1)
        if bpm0 <= 0 or bpm1 <= 0:
            raise ValueError("BPM endpoints must be > 0")
        # BPM(b) = m*b + c. Solve for m,c from endpoints.
        m = (bpm1 - bpm0) / (b1 - b0)
        c = bpm0 - m * b0
        # Validate positivity across segment: linear -> min occurs at an endpoint
        if min(bpm0, bpm1) <= 0:
            raise ValueError("BPM must stay > 0 across segment")

        seg = LinearBpmByBeat(b0=b0, b1=b1, m=m, c=c, t0=self._current_time)
        self._current_time = seg.beat_to_time_seconds(b1)
        self._segments.append(seg)
        return self

    def add_lambda_by_beat(self, b0: float, b1: float, bpm_fn: Callable[[float], float]) -> TempoMapBuilder:
        self._validate_new_segment_bounds(b0, b1)
        seg = LambdaBpmByBeat(b0=b0, b1=b1, bpm_fn=bpm_fn, t0=self._current_time)

        # Validate bpm_fn > 0 across segment by sampling
        self._validate_lambda_positivity(seg)

        # Estimate end time by numeric integration of dt/db = 60/BPM(b)
        t_end = self._current_time + self._integrate_seconds_over_beats(seg, b0, b1)
        self._current_time = t_end
        self._segments.append(seg)
        return self

    def build(self, *, block_samples: int = 128, anchor_every_samples: int = 8192) -> TempoMap:
        tempo = TempoMap(
            segments=list(self._segments),
            sample_rate=self.sample_rate,
            block_samples=block_samples,
            anchor_every_samples=anchor_every_samples,
        )
        # Seed anchors at every segment boundary (important for lambda segments)
        tempo.ensure_boundary_anchors()
        return tempo

    # ---- internal helpers ----

    def _validate_new_segment_bounds(self, b0: float, b1: float) -> None:
        if b1 <= b0:
            raise ValueError("segment requires b1 > b0")
        if self._segments and b0 != self._segments[-1].b1:
            raise ValueError("segments must be contiguous (b0 must equal previous b1)")

    def _validate_lambda_positivity(self, seg: LambdaBpmByBeat) -> None:
        # sample a modest grid; you can make this stricter if you want
        steps = max(8, int((seg.b1 - seg.b0) / (self.lambda_step_beats * 16)))
        for i in range(steps + 1):
            b = seg.b0 + (seg.b1 - seg.b0) * (i / steps)
            bpm = float(seg.bpm_fn(b))
            if bpm <= 0 or not math.isfinite(bpm):
                raise ValueError("lambda tempo must be finite and > 0 across segment")

    def _integrate_seconds_over_beats(self, seg: LambdaBpmByBeat, b0: float, b1: float) -> float:
        # Simple midpoint integration in beat-domain:
        # seconds = ∫ 60/BPM(b) db
        h = self.lambda_step_beats
        total = 0.0
        b = b0
        while b < b1:
            step = min(h, b1 - b)
            mid = b + 0.5 * step
            bpm_mid = float(seg.bpm_fn(mid))
            total += (60.0 / bpm_mid) * step
            b += step
        return total


# ----------------------------
# Scheduling and retiming
# ----------------------------

def schedule_beats_to_samples(
    notes: BeatArrangement,
    tempo: TempoMap,
    *,
    round_mode: str = RoundMode.CEIL,
) -> ScheduledArrangement:
    tempo.round_mode = round_mode
    notes_sorted = sorted(notes, key=lambda n: (float(n.start), n.origin_id))
    out: List[ScheduledNote] = []
    for n in notes_sorted:
        s0 = tempo.beat_to_sample(n.start)
        s1 = tempo.beat_to_sample(n.start + n.dur)
        dur = max(0, s1 - s0)
        if dur == 0:
            continue
        out.append(ScheduledNote(s0, dur, n.pitch, n.vel, n.origin_id))
    return out


def retime_samples_to_beats(
    notes: ScheduledArrangement,
    tempo: TempoMap,
    *,
    grid: Fraction = Fraction(1, 16),
    quantize_mode: str = RoundMode.NEAREST,
    refine_inverse: bool = True,
    min_dur: Optional[Fraction] = Fraction(1, 256),
) -> BeatArrangement:
    out: List[BeatNote] = []
    notes_sorted = sorted(notes, key=lambda n: (n.start_sample, n.origin_id))

    # Reuse context/cache across all calls (important if bpm_mod_signal exists)
    ctx = EvalContext(sample_rate=tempo.sample_rate, block_size=tempo.block_samples)
    cache = BlockCache()

    for n in notes_sorted:
        b0 = tempo.sample_to_beat(n.start_sample, refine=refine_inverse, ctx=ctx, cache=cache)
        b1 = tempo.sample_to_beat(n.start_sample + n.dur_samples, refine=refine_inverse, ctx=ctx, cache=cache)

        qb0 = quantize_beat_float_to_fraction(b0, grid, quantize_mode)
        qb1 = quantize_beat_float_to_fraction(b1, grid, quantize_mode)

        dur = qb1 - qb0
        if dur <= 0:
            if min_dur is None or min_dur <= 0:
                continue
            dur = min_dur

        out.append(BeatNote(qb0, dur, n.pitch, n.vel, n.origin_id))

    out.sort(key=lambda n: (float(n.start), n.origin_id))
    return out

class SignalGraphError(ValueError):
    pass

@dataclass
class CompiledSignalGraph:
    """
    Immutable-ish compiled graph: topo order of all nodes reachable from roots.
    """
    roots: Dict[str, "Signal"]
    topo: List["Signal"]          # children come before parents
    nodes: Dict[int, "Signal"]    # id -> node

def compile_project_to_render_plan(project):
    pass # todo:
    #validate_ports(strict=...)(authoring ports exist)
    #For each track( in stable order):
    #  compile track graph to TrackIR(plugins + port mapping + event sources)
    #  Resolve project connect() edges by translating track ports → plugin ports
    #  Insert mixers if needed for fan-in
    #  Toposort plugin nodes(not tracks) for the renderer
    #Run scheduler:
    #  gather notes/cc/param signals, convert to sample times, emit event lists

class SignalEngine:
    def __init__(self, ctx: EvalContext, registry: SignalRegistry):
        self.ctx = ctx
        self.cache = BlockCache()
        self.registry = registry
        self._roots: Dict[str, Signal] = {}
        self._compiled: Optional[CompiledSignalGraph] = None
        self._last_prepared_block: Optional[int] = None
        self._snap_a_block: Optional[int] = None
        self._snap_a_vals: Optional[List[float]] = None
        self._snap_b_block: Optional[int] = None
        self._snap_b_vals: Optional[List[float]] = None

    def register_root(self, name: str, signal: Signal) -> None:
        if name in self._roots:
            raise SignalGraphError(f"Root '{name}' already registered")
        self._roots[name] = signal
        self._compiled = None

    def _resolve_refs(self, root: Signal) -> None:
        """
        Walk reachable nodes; bind any RefSignal using registry.
        """
        seen: Set[int] = set()
        stack: List[Signal] = [root]
        while stack:
            n = stack.pop()
            if n.id in seen:
                continue
            seen.add(n.id)

            if isinstance(n, RefSignal) and n._target is None:
                target = self.registry.get(n.key)
                n.bind(target)

            stack.extend(n.children())

    def _ensure_evaluator(self) -> None:
        _ = self.compiled  # ensure compiled
        if not hasattr(self, "_compiled_evaluator") or self._compiled_evaluator is None:
            # if you store it elsewhere, adjust accordingly
            self._compiled_evaluator = CompiledBlockEvaluator(self._compiled)

    def _snapshot_block(self, block_index: int) -> List[float]:
        """
        Returns a cached snapshot (copy) of evaluator.values for this block.
        Keeps up to two snapshots (simple 2-entry LRU).
        """
        self._ensure_evaluator()

        if self._snap_a_block == block_index and self._snap_a_vals is not None:
            return self._snap_a_vals
        if self._snap_b_block == block_index and self._snap_b_vals is not None:
            return self._snap_b_vals

        # Miss: evaluate block once, then copy values array
        self._compiled_evaluator.eval_block(block_index, self.ctx)
        vals = list(self._compiled_evaluator.values)  # snapshot copy

        # Insert/replace (2-entry LRU: push into A, demote old A into B)
        self._snap_b_block, self._snap_b_vals = self._snap_a_block, self._snap_a_vals
        self._snap_a_block, self._snap_a_vals = block_index, vals
        return vals

    def _sig_index(self, signal: Signal) -> int:
        self._ensure_evaluator()
        try:
            return self._compiled_evaluator.index[signal.id]
        except KeyError as e:
            raise SignalGraphError(
                "Signal not present in compiled graph (did you register it or a root that reaches it?)") from e

    def compile(self) -> CompiledSignalGraph:
        if not self._roots:
            raise SignalGraphError("No roots registered")

        # 0) Resolve RefSignals first so cross-track edges exist
        for sig in self._roots.values():
            self._resolve_refs(sig)

        # 1) Cross-track CSE (global canonicalization)
        canon = GlobalCanonicalizer()
        for name, sig in list(self._roots.items()):
            self._roots[name] = canon.canonicalize(sig)

        # 2) Cycle check on canonical roots
        for name, sig in self._roots.items():
            try:
                detect_signal_cycle(sig)
            except Exception as e:
                raise SignalGraphError(f"Cycle detected from root '{name}': {e}") from e

        # 3) Topological sort of all reachable nodes (canonical graph)
        visited: Set[int] = set()
        temp: Set[int] = set()
        topo: List[Signal] = []
        nodes: Dict[int, Signal] = {}

        def dfs(n: Signal) -> None:
            nid = n.id
            if nid in visited:
                return
            if nid in temp:
                raise SignalGraphError("Cycle detected during topo sort")
            temp.add(nid)
            for ch in n.children():
                dfs(ch)
            temp.remove(nid)
            visited.add(nid)
            topo.append(n)
            nodes[nid] = n

        for sig in self._roots.values():
            dfs(sig)

        self._compiled = CompiledSignalGraph(roots=dict(self._roots), topo=topo, nodes=nodes)
        self._compiled_evaluator = CompiledBlockEvaluator(self._compiled)
        self._last_prepared_block = None

        # Optional: keep stats for debugging/profiling
        self._last_cse_stats = canon.stats

        return self._compiled

    @property
    def compiled(self) -> CompiledSignalGraph:
        if self._compiled is None:
            return self.compile()
        return self._compiled

    def at(self, signal: Signal, sample: int) -> float:
        return signal.at(sample, self.ctx, self.cache)

    def prepare_range(self, start_sample: int, end_sample: int) -> None:
        """
        Optional: pre-warm blocks for an interval. Helpful for offline rendering.
        """
        if end_sample < start_sample:
            raise ValueError("end_sample must be >= start_sample")
        bs = self.ctx.block_size
        b0 = start_sample // bs
        b1 = end_sample // bs
        for b in range(b0, b1 + 1):
            self.prepare_block(b)

    def prepare_block(self, block_index: int) -> None: #optional prewarming of the cache
        _ = self._snapshot_block(block_index)  # fills snapshot cache

    def value_block(self, signal: Signal, block_index: int) -> float:
        vals = self._snapshot_block(block_index)
        idx = self._sig_index(signal)
        return float(vals[idx])

    def value(self, signal: Signal, sample: int) -> float:
        if sample < 0:
            raise ValueError("sample must be >= 0")

        bs = self.ctx.block_size
        b = sample // bs
        mid = b * bs + (bs // 2)

        # bracket by block midpoints for continuity
        if sample < mid:
            b0 = max(0, b - 1)
            b1 = b
            mid0 = b0 * bs + (bs // 2)
            mid1 = b1 * bs + (bs // 2)
        else:
            b0 = b
            b1 = b + 1
            mid0 = b0 * bs + (bs // 2)
            mid1 = b1 * bs + (bs // 2)

        vals0 = self._snapshot_block(b0)
        vals1 = self._snapshot_block(b1)
        idx = self._sig_index(signal)

        v0 = float(vals0[idx])
        v1 = float(vals1[idx])

        if mid1 == mid0:
            return v1

        t = (sample - mid0) / float(mid1 - mid0)
        if t <= 0.0:
            return v0
        if t >= 1.0:
            return v1
        return v0 * (1.0 - t) + v1 * t

class RefSignal(Signal):
    """
    A placeholder node that points at a named signal exported elsewhere.
    It becomes a real edge in the signal graph after resolution.
    """
    def __init__(self, key: str):
        super().__init__()
        self.key = key
        self._target: Optional[Signal] = None

    def bind(self, target: Signal) -> None:
        self._target = target

    @property
    def target(self) -> Signal:
        if self._target is None:
            raise ValueError(f"Unresolved RefSignal('{self.key}')")
        return self._target

    @property
    def requires_beat(self) -> bool:
        # Once bound, inherit
        return self._target.requires_beat if self._target is not None else False

    def children(self) -> tuple["Signal", ...]:
        # Once bound, the ref becomes an edge to its target.
        return (self._target,) if self._target is not None else ()

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        # Delegate evaluation to target
        return self.target.at(sample, ctx, cache)

class SignalRegistry:
    def __init__(self):
        self._exports: Dict[str, Signal] = {}

    def export(self, key: str, signal: Signal) -> None:
        if key in self._exports:
            raise ValueError(f"Signal '{key}' already exported")
        self._exports[key] = signal

    def get(self, key: str) -> Signal:
        try:
            return self._exports[key]
        except KeyError:
            raise KeyError(f"Unknown exported signal '{key}'") from None

    # --- new helpers ---

    def keys(self) -> List[str]:
        return sorted(self._exports.keys())

    def items(self) -> List[Tuple[str, Signal]]:
        return sorted(self._exports.items(), key=lambda kv: kv[0])

    def list(self, prefix: str = "") -> List[str]:
        """
        List exported keys, optionally filtered by prefix.
        Example prefixes:
          "A." or "A.out." or "Master.tempo."
        """
        if not prefix:
            return self.keys()
        return [k for k in self.keys() if k.startswith(prefix)]

    def describe(self, prefix: str = "") -> List[Tuple[str, int, str]]:
        """
        Debug view: (key, signal_id, signal_type)
        """
        out: List[Tuple[str, int, str]] = []
        for k in self.list(prefix):
            s = self._exports[k]
            out.append((k, s.id, type(s).__name__))
        return out

    def export(self, port: str, signal: Signal, *, category: str = "out") -> Signal:
        """
        Export under 'Track.category.port', default category='out'.
        """
        key = f"{self.name}.{category}.{port}"
        self.registry.export(key, signal)
        return signal

    def import_(self, key: str) -> RefSignal:
        return RefSignal(key)

    def sig(self, port: str, *, category: str = "out") -> RefSignal:
        """
        Reference a port on THIS track.
        """
        return RefSignal(f"{self.name}.{category}.{port}")

    def track(self, name: str) -> "Track":
        return Track(name, self.registry)

    def sig(self, key: str) -> RefSignal:
        """
        Lazy reference. Errors (unknown key) appear at engine.compile() time.
        """
        return RefSignal(key)

    def sig_checked(self, key: str) -> RefSignal:
        """
        Eager reference. Validates that the key exists right now.
        """
        self.registry.get(key)  # raises if unknown
        return RefSignal(key)

    def list_exports(self, prefix: str = "") -> List[str]:
        return self.registry.list(prefix)

    def describe_exports(self, prefix: str = "") -> List[Tuple[str, int, str]]:
        return self.registry.describe(prefix)

def sanity_check_tempo_map(
    tempo: TempoMap,
    *,
    start_sample: int = 0,
    end_sample: int = 48000 * 10,   # default: 10 seconds at 48kHz
    step_samples: int = 997,        # intentionally not a power of 2 (hits varied phases)
    refine_inverse: bool = True,
    max_err_samples: int = 256,     # tolerance for round-trip beat->sample after inversion
) -> None:
    """
    Practical checks:
      - sample_to_beat monotone
      - beat_to_sample(sample_to_beat(s)) close to s
      - local consistency

    Note:
      - Because beat_to_sample uses rounding (often CEIL), the round-trip won't be exact.
      - A bound like 64-256 samples is usually plenty with block_samples ~128.
    """
    if end_sample <= start_sample:
        raise ValueError("end_sample must be > start_sample")
    if step_samples <= 0:
        raise ValueError("step_samples must be > 0")

    last_beat = None
    worst_err = 0
    worst_s = None

    # We'll also check a local slope is positive by ensuring beat increases over steps.
    for s in range(start_sample, end_sample + 1, step_samples):
        b = tempo.sample_to_beat(s, refine=refine_inverse)
        if last_beat is not None and b < last_beat - 1e-12:
            raise AssertionError(
                f"Non-monotone sample_to_beat: at sample {s}, beat {b} < previous {last_beat}"
            )
        last_beat = b

        s2 = tempo.beat_to_sample(b)  # uses tempo.round_mode
        err = abs(s2 - s)
        if err > worst_err:
            worst_err = err
            worst_s = s

        if err > max_err_samples:
            raise AssertionError(
                f"Round-trip error too large: sample {s} -> beat {b:.9f} -> sample {s2}, "
                f"err={err} (max {max_err_samples}). Consider smaller block_samples, "
                f"or enable refine_inverse, or relax max_err_samples."
            )

    print(
        f"TempoMap sanity check OK: range [{start_sample}, {end_sample}] step {step_samples}. "
        f"Worst round-trip err={worst_err} samples at s={worst_s}."
    )

class UnaryOp(Signal):
    def __init__(self, x: Signal):
        super().__init__()
        self.x = x

    @property
    def requires_beat(self) -> bool:
        return self.x.requires_beat

    def children(self) -> tuple["Signal", ...]:
        return (self.x,)

class Clamp(UnaryOp):
    def __init__(self, x: Signal, lo: Number, hi: Number):
        super().__init__(x)
        self.lo = float(lo)
        self.hi = float(hi)
        if self.hi < self.lo:
            raise ValueError("Clamp requires hi >= lo")

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        v = float(self.x.at(sample, ctx, cache))
        if v < self.lo:
            return self.lo
        if v > self.hi:
            return self.hi
        return v
"""
Usage:
Usage:

vibe = proj.sig("A.mod.vibe")
safe = Clamp(vibe, -1.0, 1.0)
cutoff = 2000 + 500 * safe
"""

@dataclass
class SmoothCache:
    """
    Per Smooth node cache: block_index -> smoothed_value.
    Also stores the last computed block for faster forward stepping.
    """
    values: Dict[int, float] = field(default_factory=dict)

    def get(self, block: int) -> Optional[float]:
        return self.values.get(block)

    def set(self, block: int, value: float) -> None:
        self.values[block] = value

class Smooth(UnaryOp):
# This is the “DAW style” smoothing you typically see on modulation sources to avoid zipper noise.

    """
    One-pole smoothing at control rate (per block).

    time_constant_s:
      - larger => more smoothing (slower response)
      - 0 => no smoothing
    """
    def __init__(self, x: Signal, time_constant_s: float):
        super().__init__(x)
        if time_constant_s < 0:
            raise ValueError("time_constant_s must be >= 0")
        self.tau = float(time_constant_s)
        self._smooth_cache = SmoothCache()

    def _alpha(self, ctx: EvalContext) -> float:
        # discrete one-pole coefficient at control rate
        dt = ctx.block_size / ctx.sample_rate
        if self.tau <= 0:
            return 1.0
        # alpha = 1 - exp(-dt/tau)
        return 1.0 - math.exp(-dt / self.tau)

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        block = sample // ctx.block_size

        hit = self._smooth_cache.get(block)
        if hit is not None:
            return hit

        # Ensure previous block is computed (recurrence)
        if block == 0:
            # initialize from input at block 0 midpoint
            s_mid = (ctx.block_size // 2)
            x0 = float(self.x.at(s_mid, ctx, cache))
            self._smooth_cache.set(0, x0)
            return x0

        prev = self._smooth_cache.get(block - 1)
        if prev is None:
            # Compute previous block first (recursive fill)
            prev = self._eval((block - 1) * ctx.block_size, ctx, cache)

        # Current input sample at block midpoint
        s_mid = block * ctx.block_size + (ctx.block_size // 2)
        xk = float(self.x.at(s_mid, ctx, cache))

        a = self._alpha(ctx)
        yk = prev + a * (xk - prev)

        self._smooth_cache.set(block, yk)
        return yk
"""
Why Smooth has its own cache (not BlockCache)
BlockCache caches a signal’s value per block, but smoothing needs the previous output to compute the next output. That’s a recurrence, so it needs a per-node history cache.
This is still deterministic because:
the output at block k is fully determined by:
the input samples at each block midpoint up to k
the time constant
no hidden time progression outside the cache
"""

"""
Usage
raw = proj.sig("A.mod.vibe")
# Smooth over ~50 ms
smooth = Smooth(raw, time_constant_s=0.05)
# Then clamp to a safe range
mapped = Clamp(0.8 * smooth + 0.2, 0.0, 1.0)
cutoff = 500 + 3000 * mapped
"""

class Rectify(UnaryOp):
    """
    Full-wave rectifier: abs(x)
    Common for turning an LFO into a unipolar modulation source.
    """
    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        return abs(float(self.x.at(sample, ctx, cache)))
"""
Rectify(x) — full-wave rectifier (abs(x))
Usage:

raw = proj.sig("A.mod.vibe")          # e.g. -1..1
uni = Rectify(raw)                    # 0..1
cutoff = 500 + 3000 * Clamp(uni, 0, 1)
"""

class Power(UnaryOp):
    """
    Curve shaping: y = sign(x) * |x|**gamma by default.
    If unipolar=True, assumes x in [0,1] and does y = clamp(x,0,1)**gamma.

    Typical use:
      - gamma > 1: ease-in (less sensitive near 0)
      - gamma < 1: ease-out (more sensitive near 0)
    """
    def __init__(self, x: Signal, gamma: float, *, unipolar: bool = True):
        super().__init__(x)
        if gamma <= 0 or not math.isfinite(gamma):
            raise ValueError("gamma must be finite and > 0")
        self.gamma = float(gamma)
        self.unipolar = bool(unipolar)

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        v = float(self.x.at(sample, ctx, cache))

        if self.unipolar:
            # Most DAW "curve" mappings are for 0..1
            if v <= 0.0:
                return 0.0
            if v >= 1.0:
                return 1.0
            return v ** self.gamma

        # Bipolar shaping preserves sign
        if v == 0.0:
            return 0.0
        return math.copysign(abs(v) ** self.gamma, v)
"""
Power(x, gamma) — curve shaping (like “modulation curve” / exponential response)

Make a unipolar LFO that “hangs near 0” then rises sharply
raw = proj.sig("A.mod.vibe")
uni = Clamp(Rectify(raw), 0, 1)
shaped = Power(uni, gamma=3.0, unipolar=True)
cutoff = 500 + 3000 * shaped

Gentle bipolar softening (less extreme near ±1)
raw = proj.sig("A.mod.vibe")          # -1..1
soft = Power(raw, gamma=0.7, unipolar=False)
pan = 0.8 * soft
"""

class MapRange(UnaryOp):
    """
    Map input range to output range.

    y = out_min + (x - in_min) * (out_max - out_min) / (in_max - in_min)

    If clamp=True, output is clamped to [out_min, out_max].
    """

    def __init__(
        self,
        x: Signal,
        in_min: float,
        in_max: float,
        out_min: float,
        out_max: float,
        *,
        clamp: bool = False,
    ):
        super().__init__(x)

        if in_max == in_min:
            raise ValueError("in_max must differ from in_min")

        self.in_min = float(in_min)
        self.in_max = float(in_max)
        self.out_min = float(out_min)
        self.out_max = float(out_max)
        self.clamp = clamp

        self.scale = (self.out_max - self.out_min) / (self.in_max - self.in_min)

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        x = float(self.x.at(sample, ctx, cache))
        y = self.out_min + (x - self.in_min) * self.scale

        if self.clamp:
            lo = min(self.out_min, self.out_max)
            hi = max(self.out_min, self.out_max)
            if y < lo:
                return lo
            if y > hi:
                return hi

        return y

"""
Bipolar LFO → filter cutoff
vibe = proj.sig("A.mod.vibe")  # -1..1

cutoff = map_range(vibe, -1, 1, 200, 4000)

Unipolar envelope → gain
env = proj.sig("Drums.mod.env")  # 0..1

gain = map_range(env, 0, 1, -12, 0)
MIDI velocity → filter
velocity = proj.sig("Synth.note.velocity")  # 0..127

cutoff = map_range(velocity, 0, 127, 500, 6000)
Clamp input automatically
cutoff = map_range(vibe, -1, 1, 200, 4000, clamp=True)

Most modulation tasks are:

LFO → parameter
velocity → filter
envelope → gain
macro knob → multiple parameters

All of those are range mappings.
So this single node removes a lot of graph clutter.
"""

class Mix(Signal):
    """
    Linear interpolation between two signals.

    y = a*(1-m) + b*m
    """

    def __init__(self, a: Signal, b: Signal, amount: Union[Signal, Number]):
        super().__init__()
        self.a = a
        self.b = b
        self.amount = as_signal(amount)

    @property
    def requires_beat(self) -> bool:
        return (
            self.a.requires_beat
            or self.b.requires_beat
            or self.amount.requires_beat
        )

    def children(self) -> tuple["Signal", ...]:
        return (self.a, self.b, self.amount)

    def _eval(self, sample: int, ctx: EvalContext, cache: Optional[BlockCache]) -> float:
        a = float(self.a.at(sample, ctx, cache))
        b = float(self.b.at(sample, ctx, cache))
        m = float(self.amount.at(sample, ctx, cache))

        return a * (1.0 - m) + b * m
"""
Crossfade functions:
crossfade
morph
wet/dry
macro blending between two curves

Example usages
Crossfade two LFOs
I don't think this will actually work for my system because this entire file is scheduling-time and this would have to be done in real-time rendering time in C++
lfo1 = WavetableOsc(table1, freq_hz=1)
lfo2 = WavetableOsc(table2, freq_hz=1)
macro = proj.sig("A.macro.1")
vibe = mix(lfo1, lfo2, macro)

Blend automation and LFO
automation = proj.sig("TrackA.auto.cutoff")
lfo = proj.sig("TrackA.mod.vibe")
cutoff_mod = mix(automation, lfo, 0.3)
30% LFO added to automation.

Dry/Wet
dry = Const(0)
wet = proj.sig("FX.reverb.amount")
mix_amt = proj.sig("FX.macro.reverb")
reverb_amount = mix(dry, wet, mix_amt)
"""

class CompiledBlockEvaluator:
    """
    Fast control-rate evaluator: computes all nodes once per block (at block midpoint),
    stores results in a dense array, then any number of consumers read from the array.

    This DOES NOT change user-facing signal construction.
    """
    def __init__(self, graph: CompiledSignalGraph):
        self.graph = graph
        self.index: Dict[int, int] = {n.id: i for i, n in enumerate(graph.topo)}
        self.values: List[float] = [0.0] * len(graph.topo)
        self.last_block: Optional[int] = None

    def _mid_sample(self, block: int, ctx: EvalContext) -> int:
        return block * ctx.block_size + (ctx.block_size // 2)

    def eval_block(self, block: int, ctx: EvalContext) -> None:
        if self.last_block == block:
            return

        s_mid = self._mid_sample(block, ctx)

        # Evaluate nodes in topo order; children already computed in self.values.
        for node in self.graph.topo:
            i = self.index[node.id]

            # ----- Leaves -----
            if isinstance(node, Const):
                self.values[i] = node.value
                continue

            if isinstance(node, TimeFn):
                t = s_mid / ctx.sample_rate
                v = float(node.fn(t))
                if not math.isfinite(v):
                    raise ValueError("Signal function returned non-finite value")
                self.values[i] = v
                continue

            if isinstance(node, BeatFn):
                if ctx.beat_at_sample is None:
                    raise ValueError("BeatFn requires ctx.beat_at_sample")
                b = float(ctx.beat_at_sample(s_mid))
                v = float(node.fn(b))
                if not math.isfinite(v):
                    raise ValueError("Signal function returned non-finite value")
                self.values[i] = v
                continue

            if isinstance(node, WavetableOverTime):
                t = s_mid / ctx.sample_rate
                u = (node.phase0 + (t / node.duration))
                u01 = _wrap01(u) if node.loop else _clamp01(u)
                self.values[i] = _wavetable_lookup(node.table, u01, node.interp)
                continue

            # ----- Unary/Binary expressions -----
            if isinstance(node, Add):
                ia = self.index[node.a.id]; ib = self.index[node.b.id]
                self.values[i] = self.values[ia] + self.values[ib]
                continue

            if isinstance(node, Sub):
                ia = self.index[node.a.id]; ib = self.index[node.b.id]
                self.values[i] = self.values[ia] - self.values[ib]
                continue

            if isinstance(node, Mul):
                ia = self.index[node.a.id]; ib = self.index[node.b.id]
                self.values[i] = self.values[ia] * self.values[ib]
                continue

            if isinstance(node, Neg):
                ix = self.index[node.x.id]
                self.values[i] = -self.values[ix]
                continue

            if isinstance(node, Clamp):
                ix = self.index[node.x.id]
                v = self.values[ix]
                if v < node.lo:
                    v = node.lo
                elif v > node.hi:
                    v = node.hi
                self.values[i] = v
                continue

            if isinstance(node, Rectify):
                ix = self.index[node.x.id]
                self.values[i] = abs(self.values[ix])
                continue

            if isinstance(node, Power):
                ix = self.index[node.x.id]
                v = self.values[ix]
                if node.unipolar:
                    if v <= 0.0:
                        self.values[i] = 0.0
                    elif v >= 1.0:
                        self.values[i] = 1.0
                    else:
                        self.values[i] = v ** node.gamma
                else:
                    if v == 0.0:
                        self.values[i] = 0.0
                    else:
                        self.values[i] = math.copysign(abs(v) ** node.gamma, v)
                continue

            if isinstance(node, MapRange):
                ix = self.index[node.x.id]
                x = self.values[ix]
                y = node.out_min + (x - node.in_min) * node.scale
                if node.clamp:
                    lo = min(node.out_min, node.out_max)
                    hi = max(node.out_min, node.out_max)
                    if y < lo:
                        y = lo
                    elif y > hi:
                        y = hi
                self.values[i] = y
                continue

            if isinstance(node, Mix):
                ia = self.index[node.a.id]
                ib = self.index[node.b.id]
                im = self.index[node.amount.id]
                a = self.values[ia]
                b = self.values[ib]
                m = self.values[im]
                self.values[i] = a * (1.0 - m) + b * m
                continue

            # ----- Special stateful control-rate nodes -----
            if isinstance(node, Smooth):
                # Use block-indexed cache in the node
                hit = node._smooth_cache.get(block)
                if hit is not None:
                    self.values[i] = hit
                    continue

                if block == 0:
                    # init from input at block 0 midpoint (already computed)
                    ix = self.index[node.x.id]
                    x0 = float(self.values[ix])
                    node._smooth_cache.set(0, x0)
                    self.values[i] = x0
                    continue

                prev = node._smooth_cache.get(block - 1)
                if prev is None:
                    # compute previous block first by forcing recursion
                    # (safe because it's only one step backwards)
                    self.eval_block(block - 1, ctx)
                    prev = node._smooth_cache.get(block - 1)
                    if prev is None:
                        raise RuntimeError("Smooth cache failed to populate previous block")

                ix = self.index[node.x.id]
                xk = float(self.values[ix])
                a = node._alpha(ctx)
                yk = prev + a * (xk - prev)
                node._smooth_cache.set(block, yk)
                self.values[i] = yk
                continue

            if isinstance(node, WavetableOsc):
                # control-rate oscillator: one sample per block, with phase accumulation
                # We compute phase at this block start using the oscillator's own cache.
                phase = node._phase_at_block_start(block, ctx, cache=None)  # it calls freq.at; we want fast path
                # To avoid node.freq.at recursion, we approximate freq from the already-computed value:
                # If freq is a Const, node._phase_at_block_start is exact.
                # If not Const, integrate using already-evaluated freq at each needed block.
                if not isinstance(node.freq, Const):
                    # Recompute phase using evaluator values (fast) rather than freq.at()
                    b0, ph0 = node._phase_cache.get_nearest_leq(block)
                    ph = ph0
                    dt = ctx.block_size / ctx.sample_rate
                    for bi in range(b0, block):
                        # midpoint sample corresponds to that block; use evaluator value of freq at that block
                        # Ensure we've evaluated that block (we're in it); earlier blocks may not be.
                        if bi != block:
                            self.eval_block(bi, ctx)
                        f = self.value_of(node.freq)  # value at current evaluator's last_block
                        ph += float(f) * dt
                        if node._cache_every_blocks > 0 and ((bi + 1) % node._cache_every_blocks == 0):
                            node._phase_cache.set(bi + 1, ph)
                    node._phase_cache.set(block, ph)
                    phase = ph

                phase01 = _wrap01(phase) if node.loop else _clamp01(phase)
                self.values[i] = _wavetable_lookup(node.table, phase01, node.interp)
                continue

            if isinstance(node, RefSignal):
                # After resolution it has a target. Identity.
                it = self.index[node.target.id]
                self.values[i] = self.values[it]
                continue

            # ----- Fallback -----
            # Anything not specialized: use slow path at midpoint sample.
            self.values[i] = float(node.at(s_mid, ctx, cache=None))

        self.last_block = block

    def value_of(self, signal: Signal) -> float:
        return self.values[self.index[signal.id]]

    def root_value(self, root_name: str) -> float:
        sig = self.graph.roots[root_name]
        return self.value_of(sig)

TrackId = int
Port = str

@dataclass
class Track:
    id: TrackId
    name: str
    parent_id: Optional[TrackId] = None
    children: List[TrackId] = field(default_factory=list)
    graph: PortGraph = field(default_factory=SimplePortGraph)

@dataclass(frozen=True)
class Connection:
    id: int
    src: TrackId
    src_port: Port
    dst: TrackId
    dst_port: Port
    auto: bool = False

class Project:
    def __init__(self, *, auto_declare_ports: bool = True):
        self.auto_declare_ports = auto_declare_ports

        self._next_track_id = 1
        self._next_conn_id = 1

        self.tracks: Dict[TrackId, Track] = {}
        self.track_order: List[TrackId] = []

        self.connections: Dict[int, Connection] = {}
        self._out_index: Dict[Tuple[TrackId, Port], Set[int]] = {}
        self._in_index: Dict[Tuple[TrackId, Port], Set[int]] = {}

    def compile(
            self,
            *,
            strict_ports: bool = True,
            detect_cycles: bool = True,
            freeze_ports: bool = True,
            auto_create_missing_ports: bool = False,
    ) -> CompiledProject:
        """
        Compile project routing for rendering.

        Args:
          strict_ports:
              If True, missing ports referenced by connections raise PortValidationError.
          detect_cycles:
              If True, detect audio routing cycles and raise AudioGraphCycleError.
          freeze_ports:
              If True, disable auto port declaration before compiling (recommended).
          auto_create_missing_ports:
              If True, missing ports will be created during compile (equivalent to strict_ports=False
              for port creation), and port_report will record what got created.

        Typical safe usage:
          compile(strict_ports=True, freeze_ports=True, detect_cycles=True)

        Typical prototyping usage:
          compile(strict_ports=False, freeze_ports=False, auto_create_missing_ports=True)
        """
        was_auto = self.auto_declare_ports

        ports_frozen = False
        if freeze_ports:
            self.auto_declare_ports = False
            ports_frozen = True

        # 1) Validate ports (and optionally auto-create them)
        if auto_create_missing_ports:
            # This explicitly creates missing ports regardless of auto_declare_ports.
            port_report = validate_ports(self, strict=False)
            if strict_ports:
                # If they asked strict_ports but also auto_create_missing_ports, treat
                # strict_ports as "after creation, ensure nothing missing".
                port_report2 = validate_ports(self, strict=True)
                # if strict=True passes, port_report2 won't raise; we keep the created report
            else:
                # Non-strict: we're done
                pass
        else:
            # No auto-creation: just validate according to strict_ports
            port_report = validate_ports(self, strict=strict_ports)

        # 2) Compile audio routing (topo order + optional cycle error)
        audio = None
        if detect_cycles:
            renderPlan = compile_project_to_render_plan(project)
        else:
            # If cycles aren't checked, you can still produce an order attempt.
            # But most renderers need DAG. We'll still run it because it also gives the order.
            renderPlan = compile_project_to_render_plan(project)

        # NOTE: if you want to compile MIDI/control graphs too, add them here.

        # Leave ports frozen if freeze_ports=True (intended)
        # Restore if not freezing
        if not freeze_ports:
            self.auto_declare_ports = was_auto

        return CompiledProject(audio=audio, port_report=port_report, ports_frozen=ports_frozen)

    def add_track(
        self,
        name: str,
        *,
        parent: Optional[Union[Track, TrackId]] = None,
        auto_sum: bool = True,
        child_out_port: str = "out",
        parent_in_port: str = "in",
    ) -> Track:
        tid = self._next_track_id
        self._next_track_id += 1

        parent_id = parent.id if isinstance(parent, Track) else parent
        t = Track(id=tid, name=name, parent_id=parent_id)

        self.tracks[tid] = t
        self.track_order.append(tid)

        if parent_id is not None:
            if parent_id not in self.tracks:
                raise ValueError(f"Unknown parent track id {parent_id}")
            self.tracks[parent_id].children.append(tid)

            if auto_sum:
                # This will obey auto_declare_ports
                self.connect(tid, child_out_port, parent_id, parent_in_port, auto=True)

        return t

    def connect(
            self,
            src: Union[Track, TrackId],
            src_port: Port,
            dst: Union[Track, TrackId],
            dst_port: Port,
            *,
            auto: bool = False,
    ) -> int:
        src_id = src.id if isinstance(src, Track) else src
        dst_id = dst.id if isinstance(dst, Track) else dst

        if src_id not in self.tracks:
            raise ValueError(f"Unknown src track id {src_id}")
        if dst_id not in self.tracks:
            raise ValueError(f"Unknown dst track id {dst_id}")

        if self.auto_declare_ports:
            # Authoring mode: create ports if needed
            self.tracks[src_id].graph.ensure_output_port(src_port)
            self.tracks[dst_id].graph.ensure_input_port(dst_port)
        else:
            # Strict mode: refuse unknown ports
            self._check_port_exists(src_id, src_port, is_output=True)
            self._check_port_exists(dst_id, dst_port, is_output=False)

        # Create connection record
        cid = self._next_conn_id
        self._next_conn_id += 1

        c = Connection(
            id=cid,
            src=src_id,
            src_port=str(src_port),
            dst=dst_id,
            dst_port=str(dst_port),
            auto=auto,
        )
        self.connections[cid] = c

        self._out_index.setdefault((c.src, c.src_port), set()).add(cid)
        self._in_index.setdefault((c.dst, c.dst_port), set()).add(cid)

        return cid
        # ---------- connect / disconnect ----------

    def disconnect(
        self,
        *,
        conn_id: Optional[int] = None,
        src: Optional[Union[Track, TrackId]] = None,
        src_port: Optional[Port] = None,
        dst: Optional[Union[Track, TrackId]] = None,
        dst_port: Optional[Port] = None,
        auto_only: bool = False,
        all_matches: bool = True,
    ) -> int:
        """
        Disconnect by:
          - conn_id, OR
          - endpoint match (src/src_port/dst/dst_port). Any None is treated as a wildcard.

        auto_only=True removes only edges created automatically (auto=True).
        all_matches=True removes all matching edges; otherwise removes at most one.
        Returns number removed.
        """
        if conn_id is not None:
            return 1 if self._remove_conn(conn_id, auto_only=auto_only) else 0

        src_id = src.id if isinstance(src, Track) else src
        dst_id = dst.id if isinstance(dst, Track) else dst

        # Candidate set from indices if possible
        candidates: Optional[Set[int]] = None
        if src_id is not None and src_port is not None:
            candidates = set(self._out_index.get((src_id, str(src_port)), set()))
        if dst_id is not None and dst_port is not None:
            in_set = set(self._in_index.get((dst_id, str(dst_port)), set()))
            candidates = in_set if candidates is None else (candidates & in_set)
        if candidates is None:
            candidates = set(self.connections.keys())

        removed = 0
        for cid in list(candidates):
            c = self.connections.get(cid)
            if c is None:
                continue
            if auto_only and not c.auto:
                continue
            if src_id is not None and c.src != src_id:
                continue
            if src_port is not None and c.src_port != str(src_port):
                continue
            if dst_id is not None and c.dst != dst_id:
                continue
            if dst_port is not None and c.dst_port != str(dst_port):
                continue

            self._remove_conn(cid, auto_only=False)
            removed += 1
            if not all_matches:
                break

        return removed

    def _check_port_exists(
            self,
            track_id: int,
            port: str,
            *,
            is_output: bool,
    ) -> None:
        g = self.tracks[track_id].graph
        ports = g.output_ports() if is_output else g.input_ports()
        if port not in ports:
            direction = "output" if is_output else "input"
            tname = self.tracks[track_id].name
            raise PortValidationError(
                f"Track '{tname}' (id={track_id}) has no declared {direction} port '{port}'. "
                f"Either declare it in the track graph or enable auto_declare_ports."
            )

    def _remove_conn(self, conn_id: int, *, auto_only: bool) -> bool:
        c = self.connections.get(conn_id)
        if c is None:
            return False
        if auto_only and not c.auto:
            return False

        del self.connections[conn_id]

        out_key = (c.src, c.src_port)
        in_key = (c.dst, c.dst_port)

        s = self._out_index.get(out_key)
        if s is not None:
            s.discard(conn_id)
            if not s:
                del self._out_index[out_key]

        s = self._in_index.get(in_key)
        if s is not None:
            s.discard(conn_id)
            if not s:
                del self._in_index[in_key]

        return True

    # ---------- “automatic port exposure” ----------

    def exposed_ports(self, track: Union[Track, TrackId]) -> Dict[str, Set[str]]:
        """
        Returns ports that are currently “used” by connections:
          outputs: any src_port used by outgoing edges
          inputs : any dst_port used by incoming edges

        This matches your “don’t predeclare ports; anything referenced is exposed” preference.
        """
        tid = track.id if isinstance(track, Track) else track
        outs: Set[str] = set()
        ins: Set[str] = set()

        for (src_id, src_port), ids in self._out_index.items():
            if src_id == tid and ids:
                outs.add(src_port)

        for (dst_id, dst_port), ids in self._in_index.items():
            if dst_id == tid and ids:
                ins.add(dst_port)

        return {"outputs": outs, "inputs": ins}

class AudioGraphCycleError(ValueError):
    pass

@dataclass
class CompiledAudioOrder:
    order: List[int]  # track ids in render order
    incoming_by_track: Dict[int, List[int]]  # dst_track -> list of src_track ids
    outgoing_by_track: Dict[int, List[int]]  # src_track -> list of dst_track ids

@dataclass
class EmptyPortGraph:
    _ins: Set[str] = field(default_factory=set)
    _outs: Set[str] = field(default_factory=set)

    def input_ports(self) -> Set[str]:
        return set(self._ins)

    def output_ports(self) -> Set[str]:
        return set(self._outs)

    def ensure_input_port(self, name: str) -> None:
        self._ins.add(str(name))

    def ensure_output_port(self, name: str) -> None:
        self._outs.add(str(name))

def compile_audio_graph(project: Project) -> CompiledAudioOrder:
    """
    Build track dependency graph from project.connections and return a topological render order.

    Rules:
      - Any connection src -> dst creates dependency: dst depends on src.
      - Self-edge is an immediate cycle.
      - Cycles raise AudioGraphCycleError with a readable path.
    """
    # Build adjacency + indegree for tracks
    outgoing: Dict[int, Set[int]] = {tid: set() for tid in project.tracks.keys()}
    incoming: Dict[int, Set[int]] = {tid: set() for tid in project.tracks.keys()}

    # Add edges
    for c in project.connections.values():
        # If you later add midi/control edges, filter by kind here.
        src = c.src
        dst = c.dst
        if src == dst:
            raise AudioGraphCycleError(f"Self-cycle on track {project.tracks[src].name} (id={src})")
        outgoing[src].add(dst)
        incoming[dst].add(src)

    indegree: Dict[int, int] = {tid: len(incoming[tid]) for tid in project.tracks.keys()}

    # Kahn's algorithm with deterministic tie-break:
    # prefer project.track_order if provided, otherwise by id.
    order_hint = {tid: i for i, tid in enumerate(project.track_order)}

    ready: List[int] = [tid for tid, deg in indegree.items() if deg == 0]
    ready.sort(key=lambda tid: order_hint.get(tid, 10 ** 9))

    topo: List[int] = []
    while ready:
        n = ready.pop(0)
        topo.append(n)

        for m in sorted(outgoing[n], key=lambda tid: order_hint.get(tid, 10 ** 9)):
            indegree[m] -= 1
            if indegree[m] == 0:
                # insert while keeping order_hint sort; for speed use heapq if you care
                ready.append(m)
                ready.sort(key=lambda tid: order_hint.get(tid, 10 ** 9))

    if len(topo) != len(project.tracks):
        # There is a cycle. Produce a readable cycle path.
        cycle = _find_cycle_path(project, outgoing)
        raise AudioGraphCycleError(
            "Audio routing cycle detected: " + " -> ".join(
                f"{project.tracks[tid].name}(id={tid})" for tid in cycle
            )
        )

    # Convert sets to stable lists
    incoming_by_track = {tid: sorted(list(srcs), key=lambda t: order_hint.get(t, 10 ** 9))
                         for tid, srcs in incoming.items()}
    outgoing_by_track = {tid: sorted(list(dsts), key=lambda t: order_hint.get(t, 10 ** 9))
                         for tid, dsts in outgoing.items()}

    return CompiledAudioOrder(
        order=topo,
        incoming_by_track=incoming_by_track,
        outgoing_by_track=outgoing_by_track,
    )

def _find_cycle_path(project: Project, outgoing: Dict[int, Set[int]]) -> List[int]:
    """
    DFS to extract one cycle path (track ids). Returns something like [a, b, c, a].
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[int, int] = {tid: WHITE for tid in project.tracks.keys()}
    parent: Dict[int, Optional[int]] = {tid: None for tid in project.tracks.keys()}

    def dfs(u: int) -> Optional[List[int]]:
        color[u] = GRAY
        for v in outgoing[u]:
            if color[v] == WHITE:
                parent[v] = u
                res = dfs(v)
                if res is not None:
                    return res
            elif color[v] == GRAY:
                # Found a back-edge u -> v, extract cycle v..u..v
                return _extract_cycle(parent, start=v, end=u)
        color[u] = BLACK
        return None

    for tid in project.tracks.keys():
        if color[tid] == WHITE:
            res = dfs(tid)
            if res is not None:
                return res

    # Fallback (shouldn't happen if called only when a cycle exists)
    return []

def _extract_cycle(parent: Dict[int, Optional[int]], start: int, end: int) -> List[int]:
    """
    Given a back-edge end -> start where start is in the current stack,
    reconstruct cycle [start, ..., end, start].
    """
    path = [start]
    cur = end
    while cur != start and cur is not None:
        path.append(cur)
        cur = parent[cur]
    path.append(start)
    path.reverse()
    return path

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

class PortValidationError(ValueError):
    pass

@dataclass
class PortValidationReport:
    missing_inputs: Dict[int, List[str]]
    missing_outputs: Dict[int, List[str]]
    created_inputs: Dict[int, List[str]]
    created_outputs: Dict[int, List[str]]

def validate_ports(project: Project, *, strict: bool = True) -> PortValidationReport:
    """
    Checks that every connection references existing ports on src/dst tracks.

    strict=True  -> raise PortValidationError if anything is missing.
    strict=False -> auto-create missing ports by calling ensure_input_port/ensure_output_port.
                   (This does NOT guarantee the track's DSP graph actually produces meaningful audio;
                    it just ensures the wiring is consistent.)
    """
    missing_in: Dict[int, List[str]] = {}
    missing_out: Dict[int, List[str]] = {}
    created_in: Dict[int, List[str]] = {}
    created_out: Dict[int, List[str]] = {}

    def ensure_graph(tid: int) -> PortGraph:
        t = project.tracks[tid]
        if t.graph is None:
            # attach a placeholder graph so we have somewhere to create ports
            t.graph = SimplePortGraph()
        return t.graph  # type: ignore[return-value]

    # First pass: collect missing ports
    for c in project.connections.values():
        src = c.src
        dst = c.dst
        src_port = c.src_port
        dst_port = c.dst_port

        sg = ensure_graph(src)
        dg = ensure_graph(dst)

        outs = sg.output_ports()
        ins = dg.input_ports()

        if src_port not in outs:
            missing_out.setdefault(src, []).append(src_port)
        if dst_port not in ins:
            missing_in.setdefault(dst, []).append(dst_port)

    # Deduplicate lists while preserving stable order
    def dedupe(d: Dict[int, List[str]]) -> Dict[int, List[str]]:
        out: Dict[int, List[str]] = {}
        for tid, ports in d.items():
            seen = set()
            uniq = []
            for p in ports:
                if p not in seen:
                    seen.add(p)
                    uniq.append(p)
            out[tid] = uniq
        return out

    missing_out = dedupe(missing_out)
    missing_in = dedupe(missing_in)

    if strict:
        if missing_in or missing_out:
            lines: List[str] = ["Port validation failed. Missing ports referenced by connections:"]
            if missing_out:
                lines.append("  Missing OUTPUT ports:")
                for tid, ports in sorted(missing_out.items()):
                    tname = project.tracks[tid].name
                    lines.append(f"    - {tname}(id={tid}): {ports}")
            if missing_in:
                lines.append("  Missing INPUT ports:")
                for tid, ports in sorted(missing_in.items()):
                    tname = project.tracks[tid].name
                    lines.append(f"    - {tname}(id={tid}): {ports}")

            lines.append("Fix options:")
            lines.append("  - Add those ports to the track's internal graph, OR")
            lines.append("  - Change the connections, OR")
            lines.append("  - Run validate_ports(strict=False) to auto-create placeholder ports.")
            raise PortValidationError("\n".join(lines))

# Non-strict: auto-create missing ports
    for tid, ports in missing_out.items():
        g = ensure_graph(tid)
        for p in ports:
            g.ensure_output_port(p)
        created_out[tid] = list(ports)

    for tid, ports in missing_in.items():
        g = ensure_graph(tid)
        for p in ports:
            g.ensure_input_port(p)
        created_in[tid] = list(ports)

    return PortValidationReport(
        missing_inputs=missing_in,
        missing_outputs=missing_out,
        created_inputs=created_in,
        created_outputs=created_out,
    )

@dataclass
class CompiledProject:
    audio: Optional[CompiledAudioOrder] = None
    port_report: Optional[PortValidationReport] = None
    ports_frozen: bool = False

@dataclass
class TrackIR:
    node_uids: list[int]
    plugin_specs: dict[int, PluginSpec]          # node_uid -> plugin descriptor
    internal_audio_edges: list[tuple[int, int, int, int]]
    # (src_plugin_key, src_channel, dst_plugin_key, dst_channel)         # edges already resolved to node_uid+channel
    published_in: dict[str, list[Endpoint]]      # allow multi-channel bundles
    published_out: dict[str, list[Endpoint]]

"""
Example: if the track’s output port "out" corresponds to the last effect’s "audio.out:0", then:

published_out["out"] = (last_fx_node_id, "audio.out:0")
published_in["in"] = (first_fx_node_id, "audio.in:0")
"""

@dataclass(frozen=True)
class Endpoint:
    node_uid: int              # stable node UID you assign (not JUCE NodeID)
    kind: str                  # "audio" or "midi"
    channel: int = 0           # audio channel index; ignored for midi

@dataclass(frozen=True)
class PluginSpec:
    uid: int        # VST3 UID or your identifier
    key: int        # instance key (unique per instance)
    kind: str       # "instrument" | "effect" | "utility_gain" | "utility_mixer"

@dataclass(frozen=True)
class AudioEdge:
    src: Endpoint
    dst: Endpoint
    gain_db: float | None

def sample_to_block(sample: int, block_size: int) -> int:
    return sample // block_size  # floor

UID_GAIN = 0x4741494E  # "GAIN" placeholder; pick your real builtin uid

@dataclass(frozen=True)
class PluginSpec:
    uid: int
    key: int
    kind: str  # "instrument" | "effect" | "utility_gain" | ...

@dataclass
class ServerCommandPlan:
    plugins: Dict[int, PluginSpec]  # plugin_key -> spec
    audio_connections: List[Tuple[int, int, int, int]]         # (srcKey,srcCh,dstKey,dstCh)
    param_changes: List[Tuple[int, int, float, int]]           # (pluginKey,paramIndex,value,atSample)
    midi_notes: List[Tuple[int, int, int, int, int, int]]      # (midiIndex,note,vel,start,dur,ch)
    midi_ccs: List[Tuple[int, int, int, int, int]]             # (pluginKey,cc,value,time,ch)

# TrackIR expected shape:
#   ir.plugin_specs: dict[plugin_key, PluginSpec]
#   ir.internal_audio_edges: list[(src_key,src_ch,dst_key,dst_ch)]
#   ir.published_out: dict[str, list[(plugin_key,ch)]]
#   ir.published_in:  dict[str, list[(plugin_key,ch)]]

def compile_to_server_plan(project, track_irs, *, UID_GAIN: int, remap_gain_channels: bool = True) -> ServerCommandPlan:
    plugins: Dict[int, PluginSpec] = {}
    final_edges: List[Tuple[int, int, int, int]] = []
    gain_param_inits: List[Tuple[int, int, float, int]] = []

    # A) collect per-track plugin specs + internal wiring
    for tid, ir in track_irs.items():
        plugins.update(ir.plugin_specs)
        final_edges.extend(ir.internal_audio_edges)  # add ONCE

    # B) resolve project connections to per-channel edges WITH conn_id
    # project_audio_edges: list[(conn_id, src_key, src_ch, dst_key, dst_ch, gain_db)]
    project_audio_edges: List[Tuple[int, int, int, int, int, Optional[float]]] = []

    for conn_id, conn in project.connections.items():  # use dict key as conn_id if conn has no id
        src_bundle = track_irs[conn.src].published_out[conn.src_port]
        dst_bundle = track_irs[conn.dst].published_in[conn.dst_port]
        gain_db = getattr(conn, "gain_db", None)

        if len(src_bundle) != len(dst_bundle):
            raise ValueError(
                f"Bundle size mismatch: "
                f"{project.tracks[conn.src].name}.{conn.src_port} has {len(src_bundle)} chans, "
                f"{project.tracks[conn.dst].name}.{conn.dst_port} has {len(dst_bundle)} chans"
            )

        for (src_key, src_ch), (dst_key, dst_ch) in zip(src_bundle, dst_bundle):
            project_audio_edges.append((conn_id, src_key, src_ch, dst_key, dst_ch, gain_db))

    # C) group by conn_id and insert ONE gain node per connection if needed
    by_conn: Dict[int, List[Tuple[int, int, int, int, Optional[float]]]] = defaultdict(list)
    for (conn_id, src_key, src_ch, dst_key, dst_ch, gain_db) in project_audio_edges:
        by_conn[conn_id].append((src_key, src_ch, dst_key, dst_ch, gain_db))

    for conn_id, edges in by_conn.items():
        gain_db = edges[0][4]
        if any(e[4] != gain_db for e in edges):
            raise ValueError(f"Inconsistent gain_db within connection {conn_id}")

        if gain_db is None:
            # direct wires
            for (src_key, src_ch, dst_key, dst_ch, _) in edges:
                final_edges.append((src_key, src_ch, dst_key, dst_ch))
            continue

        # Create one gain node
        gain_key = project.allocate_plugin_key()
        plugins[gain_key] = PluginSpec(uid=UID_GAIN, key=gain_key, kind="utility_gain")

        if remap_gain_channels:
            # map whatever src channels are used onto 0..N-1 on the gain node
            channels = sorted({src_ch for (_, src_ch, _, _, _) in edges})
            ch_map = {ch: i for i, ch in enumerate(channels)}
        else:
            ch_map = None

        for (src_key, src_ch, dst_key, dst_ch, _) in edges:
            gch = ch_map[src_ch] if ch_map is not None else src_ch
            final_edges.append((src_key, src_ch, gain_key, gch))
            final_edges.append((gain_key, gch, dst_key, dst_ch))

        linear = 10 ** (float(gain_db) / 20.0)
        gain_param_inits.append((gain_key, 0, linear, 0))  # param 0 at block 0

    # D) schedule the rest of events elsewhere and merge with gain init params
    midi_notes, midi_ccs, param_changes = schedule_everything_else(project, track_irs)
    param_changes = gain_param_inits + param_changes

    return ServerCommandPlan(
        plugins=plugins,
        audio_connections=final_edges,
        param_changes=param_changes,
        midi_notes=midi_notes,
        midi_ccs=midi_ccs,
    )

def send_plan_to_server(server, plugins, final_edges, midi_notes, midi_ccs, param_changes):

    server.clearallplugins()

    # load plugins, capture plugin_key -> pluginId (server)
    key_to_id = {}
    for key in sorted(plugins.keys()):  # pick any stable order
        spec = plugins[key]
        plugin_id = server.loadPluginByUid(spec.uid, spec.key)
        key_to_id[key] = plugin_id

    # connect audio
    for (src_key, src_ch, dst_key, dst_ch) in final_edges:
        server.connectaudio(key_to_id[src_key], src_ch, key_to_id[dst_key], dst_ch)

    # clear schedules
    server.clearmidischedule()
    server.clearmidiccschedule()
    server.clearparamschedule()

    # params (block-based)
    for (plugin_key, param_idx, value, at_block) in param_changes:
        server.scheduleparamchange(key_to_id[plugin_key], param_idx, value, at_block)

    # midi cc (time unit whatever your server expects)
    for (plugin_key, cc, value, t, ch) in midi_ccs:
        server.schedulemidicc(key_to_id[plugin_key], cc, value, t, ch)

    # midi notes (your function takes index not pluginId)
    # so your scheduler must have already converted to index
    server.schedulemidinotes(midi_notes)

# todo:
#    load audio files into player nodes
#    schedule regions

def insert_gain_processors(project, UID_GAIN):
    """
    Returns:
        processors: list[Processor]
        connections: list[ProcessorConnection]
        gain_param_inits: list[(processorKey, paramIndex, value, atSample)]
    """

    processors = set()  # collect all processors involved
    for conn in project.processor_connections:
        processors.add(conn.processor1)
        processors.add(conn.processor2)

    # Group connections by group_id
    by_group = defaultdict(list)
    for conn in project.processor_connections:
        by_group[conn.group_id].append(conn)

    new_connections = []
    gain_param_inits = []

    for group_id, conns in by_group.items():

        gain_db = conns[0].gain_db
        if any(c.gain_db != gain_db for c in conns):
            raise ValueError(f"Inconsistent gain_db inside connection group {group_id}")

        if gain_db is None:
            # direct connections
            new_connections.extend(conns)
            continue

        # Create ONE gain processor
        gain_key = project.allocate_plugin_key()
        gain_proc = Processor(uid=UID_GAIN, key=gain_key, name="Gain")
        processors.add(gain_proc)

        # Optional: remap channel numbers to 0..N-1
        src_channels = sorted({c.channel1 for c in conns})
        ch_map = {ch: i for i, ch in enumerate(src_channels)}

        for c in conns:
            gch = ch_map[c.channel1]

            # src -> gain
            new_connections.append(
                ProcessorConnection(
                    c.processor1,
                    c.channel1,
                    gain_proc,
                    gch,
                    gain_db=None,
                    group_id=None
                )
            )

            # gain -> dst
            new_connections.append(
                ProcessorConnection(
                    gain_proc,
                    gch,
                    c.processor2,
                    c.channel2,
                    gain_db=None,
                    group_id=None
                )
            )

        linear = 10 ** (float(gain_db) / 20.0)
        gain_param_inits.append((gain_key, 0, linear, 0))  # param 0 at block 0

    return list(processors), new_connections, gain_param_inits

def insert_mixers_for_fanin(project, connections, processors, *, UID_MIXER: int):
    """
    Insert mixer nodes where multiple connections feed the same (dst_processor, dst_channel).

    Args:
        connections: list[ProcessorConnection] (already has gain nodes inserted if you want)
        processors:  set[Processor] (all processors currently in graph)
        UID_MIXER:   builtin uid for mixer processor

    Returns:
        new_processors: set[Processor] (including new mixers)
        new_connections: list[ProcessorConnection] (rewritten with mixers)
    """

    # Group incoming edges by destination endpoint
    incoming = defaultdict(list)  # (dst_proc_key, dst_ch) -> [ProcessorConnection,...]
    passthrough = []              # connections we won't touch (fan-in==1 or non-audio types later)

    for c in connections:
        incoming[(c.processor2.key, c.channel2)].append(c)

    new_connections = []
    new_processors = set(processors)

    for (dst_key, dst_ch), conns in incoming.items():
        if len(conns) <= 1:
            # No fan-in; keep as-is
            new_connections.extend(conns)
            continue

        # Fan-in detected: create a mixer node for this destination input channel
        mix_key = project.allocate_plugin_key()
        mix_proc = Processor(uid=UID_MIXER, key=mix_key, name=f"Mixer_to_{dst_key}_ch{dst_ch}")
        new_processors.add(mix_proc)

        # All sources now go into mixer input channel 0 (or dst_ch; your choice)
        # We'll use channel 0 on mixer for simplicity
        mix_in_ch = 0
        mix_out_ch = 0

        # Connect each source -> mixer
        for c in conns:
            new_connections.append(
                ProcessorConnection(
                    c.processor1, c.channel1,
                    mix_proc, mix_in_ch,
                    gain_db=None,
                    group_id=None
                )
            )

        # Connect mixer -> original destination
        # Use first conn's dst processor object (all conns share same destination)
        dst_proc = conns[0].processor2
        new_connections.append(
            ProcessorConnection(
                mix_proc, mix_out_ch,
                dst_proc, dst_ch,
                gain_db=None,
                group_id=None
            )
        )

    return new_processors, new_connections

def compile_graph(project, UID_GAIN: int, UID_MIXER: int):
    # Step 1: gain insertion
    processors, conns_after_gain, gain_param_inits = insert_gain_processors(project, UID_GAIN)

    # Step 2: fan-in mixer insertion
    processors2, conns_after_mix = insert_mixers_for_fanin(
        project,
        conns_after_gain,
        set(processors),
        UID_MIXER=UID_MIXER
    )

    return processors2, conns_after_mix, gain_param_inits

ParamChange = Tuple[int, int, float, int]  # (pluginKey, paramIndex, value, atSample)

def sample_signal_to_param_changes(
    *,
    plugin_key: int,
    param_index: int,
    signal,                 # Signal
    sample_rate: int,
    block_size: int,
    num_blocks: int,
    eval_ctx,               # EvalContext(sample_rate, block_size, beat_at_sample maybe)
    cache=None,
    epsilon: float = 0.0,   # set >0 to reduce spam: only emit if value changed by >= epsilon
) -> List[ParamChange]:
    out: List[ParamChange] = []
    last: Optional[float] = None

    for b in range(num_blocks):
        # choose block midpoint for nicer control-rate sampling
        s = b * block_size + (block_size // 2)
        v = float(signal.at(s, eval_ctx, cache))

        if last is None or (epsilon == 0.0 and v != last) or (epsilon > 0.0 and abs(v - last) >= epsilon):
            out.append((plugin_key, param_index, v, b))
            last = v

    return out

# ---- convenience type aliases ----
MidiNote = Tuple[int, int, int, int, int, int]   # (midiIndex, note, vel, start_sample, dur_samples, channel)
MidiCC   = Tuple[int, int, int, int, int]        # (pluginKey, controller, value, time_sample, channel)
ParamChange = Tuple[int, int, float, int]        # (pluginKey, paramIndex, value_float, atSample)

# ---- helper: sample a Signal into control-block param changes ----
def sample_signal_to_param_changes(
    *,
    plugin_key: int,
    param_index: int,
    signal,                 # Signal instance with .at(sample, ctx, cache)
    sample_rate: int,
    block_size: int,
    total_samples: int,
    eval_ctx,               # EvalContext(sample_rate, block_size, beat_at_sample maybe)
    cache=None,
    epsilon: float = 0.0,   # minimum delta to emit change (0.0 => emit every block when changed)
) -> List[ParamChange]:
    """
    Sample `signal` at control blocks and return list of (pluginKey, paramIndex, value, atSample).
    - uses block midpoint for sampling (b*block + block//2)
    - total_samples controls how many blocks (ceil)
    - epsilon can reduce spam by only emitting when the value changed by >= epsilon
    """
    if total_samples <= 0:
        return []

    num_blocks = int(ceil(total_samples / block_size))
    out: List[ParamChange] = []

    last_val: Optional[float] = None

    for b in range(num_blocks):
        sample_mid = b * block_size + (block_size // 2)
        if sample_mid >= total_samples:
            # clamp to last valid sample if last partial block extends beyond end
            sample_mid = max(0, total_samples - 1)

        v = float(signal.at(sample_mid, eval_ctx, cache))

        if last_val is None:
            out.append((plugin_key, param_index, v, b))
            last_val = v
            continue

        if epsilon <= 0.0:
            # exact change detection
            if v != last_val:
                out.append((plugin_key, param_index, v, b))
                last_val = v
        else:
            if abs(v - last_val) >= epsilon:
                out.append((plugin_key, param_index, v, b))
                last_val = v

    return out

# ---- the main function ----

def iter_project_midi_note_events(project, track_irs) -> Iterable[Tuple[int,int,int,int,int,int]]:
    """
    Yield (midiIndex, note, vel, start_sample, dur_samples, channel)
    - midiIndex: integer index used by your server to route to an instrument instance
    """

def iter_project_midi_cc_events(project, track_irs) -> Iterable[Tuple[int,int,int,int,int]]:
    """
    Yield (pluginKey, controller, value, time_sample, channel)
    - This is the CC stream destined to plugin instances.
    """

def iter_project_param_signal_lanes(project, track_irs) -> Iterable[Tuple[int,int,Signal,float?]]:
    """
    Yield (plugin_key, param_index, signal[, epsilon])
    - `signal` is a Signal object implementing `.at(sample, ctx, cache)`.
    - `epsilon` is optional float threshold for change emission (reduces redundant param sends).
    """

def schedule_everything_else(
    project,
    track_irs,
    *,
    sample_rate: int,
    block_size: int,
    total_samples: int,
    eval_ctx_factory=None,
    cache_factory=None,
) -> Tuple[List[MidiNote], List[MidiCC], List[ParamChange]]:
    """
    Convert project/track IR into:
      (midi_notes, midi_ccs, param_changes)

    - midi notes and midi CCs are taken from project via adapter iterators (see below).
    - param_changes are produced by sampling Signal objects at control-block rate.
    - This function DOES NOT turn MIDI CCs into param changes — those mappings are left for
      your realtime MIDI->param mapper using MidiToParamConnection.

    Required adapters (you must provide in your project or pass in globals):
      - iter_project_midi_note_events(project, track_irs) -> Iterable[MidiNote]
      - iter_project_midi_cc_events(project, track_irs) -> Iterable[MidiCC]
      - iter_project_param_signal_lanes(project, track_irs) -> Iterable[(plugin_key, param_index, signal, epsilon)]
        where `signal` implements `.at(sample, ctx, cache)`

    Parameters:
      - sample_rate, block_size, total_samples: scheduling/render parameters
      - eval_ctx_factory(optional): callable(sample_rate, block_size) -> EvalContext
      - cache_factory(optional): callable() -> BlockCache

    Returns:
      (midi_notes, midi_ccs, param_changes)
    """

    # ---- defaults for eval/context/caching ----
    if eval_ctx_factory is None:
        def eval_ctx_factory(sr, bs):
            # project may provide beat_at_sample; if so, pass it into ctx
            beat_fn = getattr(project, "beat_at_sample", None)
            return EvalContext(sample_rate=sr, block_size=bs, beat_at_sample=beat_fn)

    if cache_factory is None:
        def cache_factory():
            return BlockCache()

    eval_ctx = eval_ctx_factory(sample_rate, block_size)
    cache = cache_factory()

    # ---- 1) MIDI notes ----
    # Adapter: project must expose an iterator yielding MidiNote tuples
    midi_notes: List[MidiNote] = []
    for ev in iter_project_midi_note_events(project, track_irs):
        # Expect the iterator to yield (midiIndex, note, vel, start_sample, dur_samples, channel)
        midi_notes.append(ev)

    # ---- 2) MIDI CCs ----
    midi_ccs: List[MidiCC] = []
    for ev in iter_project_midi_cc_events(project, track_irs):
        # Expect (pluginKey, controller, value, time_sample, channel)
        midi_ccs.append(ev)

    # ---- 3) Parameter automation: sample signals per block ----
    param_changes: List[ParamChange] = []

    # Adapter: should yield tuples (plugin_key, param_index, signal, epsilon)
    # where epsilon is optional and controls change threshold (float), default 0.0
    for plugin_key, param_index, signal, *maybe_epsilon in iter_project_param_signal_lanes(project, track_irs):
        epsilon = float(maybe_epsilon[0]) if maybe_epsilon else 0.0
        lanes = sample_signal_to_param_changes(
            plugin_key=plugin_key,
            param_index=param_index,
            signal=signal,
            sample_rate=sample_rate,
            block_size=block_size,
            total_samples=total_samples,
            eval_ctx=eval_ctx,
            cache=cache,
            epsilon=epsilon,
        )
        param_changes.extend(lanes)

    # Sort outputs deterministically (optional but useful)
    midi_notes.sort(key=lambda x: (x[3], x[0], x[1]))   # start_sample, midiIndex, pitch
    midi_ccs.sort(key=lambda x: (x[3], x[0], x[1]))     # time_sample, pluginKey, controller
    param_changes.sort(key=lambda x: (x[3], x[0], x[1]))# atSample, pluginKey, paramIndex

    return midi_notes, midi_ccs, param_changes


class Track: # input node = "input", output node = "output", that way we can put tracks into a graph
             # if there are no effects, the notes graph ends with "output", because the output of the track is the notes.
             # if there are effects, the notes graph ends with "input", because that's the first effect in the effect graph.
             # but what if we want another track to output notes to a track's notes graph? then that won't work.
             # also, some tracks will have effects and no notes or instruments..
             # how about "notes in", "audio in", "notes out" and "audio out"?
  def __init__(self, tracks=None, audioTracks=None, midiSchedules=None, notes=None, notesGraph=None, processorGraph=None, midiCcSchedules=None,
               paramSchedules=None, sampleOffset=None, timeOffset=None, beatOffset=None):
    assert ((sampleOffset is not None) + (timeOffset is not None) + (beatOffset is not None)) <= 1
    self.audioTracks = audioTracks
    self.midiSchedules = midiSchedules
    self.notes = notes
    self.notesGraph=notesGraph
    self.midiCcSchedules = midiCcSchedules
    self.paramSchedules = paramSchedules
    self.processorGraph = processorGraph
    self.timeOffset = timeOffset
    self.beatOffset = beatOffset
    self.sampleOffset = sampleOffset
  def add(self, item):
    if type(item) is ParamSchedule:
      self.paramSchedule.extend(item)
    elif type(item) is MidiSchedule:
      self.midiSchedule.extend(item)
    elif type(item) is MidiCcSchedule:
      self.midiCcSchedule.extend(item)
    elif type(item) is Notes:
      self.notes.extend(item)
    elif type(item) is AudioTrack:
      self.audioTracks.append(item)
    elif type(item) is Processors:
      self.processors.extend(item)
    elif type(item) is Processor:
      self.processors.append(item)
  def remove(self, item):
    if type(item) is ParamSchedule:
      for item2 in item:
        self.paramSchedule.remove(item2)
    elif type(item) is MidiSchedule:
      for item2 in item:
        self.midiSchedule.remove(item2)
    elif type(item) is MidiCcSchedule:
      for item2 in item:
        self.midiCcSchedule.remove(item2)
    elif type(item) is Notes:
      for item2 in item:
        self.notes.remove(item2)
    elif type(item) is AudioTrack:
      self.audioTracks.remove(item)
    elif type(item) is Processors:
      for item2 in item:
        self.processors.remove(item2)
    elif type(item) is Processor:
      self.processors.remove(item)

class MidiSchedule(list):
  def __init__(self, midischedule=None, pluginId=None, channel=None):
    self.pluginId = pluginId
    self.channel = channel
    if type(midischedule) is Notes:
      assert self.pluginId is not None and self.channel is not None
      if midischedule.sampleOffset is not None:
        sampleOffset = midischedule.sampleOffset
      elif midischedule.timeOffset is not None:
        sampleOffset = midischedule.timeOffset * sampleRate
      elif midischedule.beatOffset is not None:
        sampleOffset = sampleRate*midischedule.beatOffset/bpm
      else:
        sampleOffset = 0
      loffset = sampleOffset
      for note in midischedule:
        if note.sampleOffset:
          noteoffset = note.sampleOffset + sampleOffset 
        elif note.timeOffset:
          noteoffset = note.timeOffset * sampleRate + sampleOffset
        elif note.beatOffset:
          noteoffset = sampleRate * note.beatOffset / bpm + sampleOffset
        elif note.sampleInterval:
          noteoffset = loffset + note.sampleInterval 
        elif note.timeInterval:
          noteoffset = loffset + note.timeInterval*sampleRate 
        elif note.beatInterval:
          noteoffset = loffset + sampleRate*note.beatInterval/bpm + sampleOffset
        self.append((self.pluginId, note.midi, note.velocity, noteoffset, note.duration, self.channel))
        loffset = noteoffset
    elif type(midischedule) is list:
      super().__init__(midischedule)

class Processor:
  def __init__(self, uid=None, key=None, pluginInfo=None, name=None):
    self.pluginInfo = pluginInfo
    self.uid = uid
    self.key = key
    self.name = name

class ProcessorConnection:
  def __init__(self, p1, ch1, p2, ch2, *, gain_db=None, group_id=None):
    self.processor1 = p1
    self.channel1 = ch1
    self.processor2 = p2
    self.channel2 = ch2
    self.gain_db = gain_db
    self.group_id = group_id

class ProcessorConnections(list): #should we do it this way, or sohuld each processor have a list of things it's connected to?
  def add(self, processorConnection):
    self.append(processorConnection)
  def remove(self, processorConnection):
    try: 
      self.remove(processorConnection)
    except ValueError:
      for x in self[:]:
        if x.processor1==processorConnection.processor1 and x.channel1==processorConnection.channel1 and x.processor2==processorConnection.processor2 and x.channel2==processorConnection.channel2:
          super().remove(x)

class MidiToParamConnection:
  def __init__(self, controller, channel, processorKey, param):
    self.controller = controller
    self.channel = channel
    self.processorKey = processorKey
    self.param = param

class MidiToParamConnections(list):
  def add(self, item):
    self.append(item)
  def remove(self, item):
    try:
      self.remove(item)
    except ValueError:
      for x in self[:]:
        if x.controller == item.controller and x.channel == item.channel and x.pluginKey == item.pluginKey and x.param == item.param:
          super().remove(x)

class Processors(dict):
  def __init__(self, *args):
    super().__init__(*args)

class MidiCcSchedule(list):
  def __init__(self, midischedule=None):
    super().__init__(midischedule)

# todo: detect cyclic graphs
# todo: support comping/slip editing - soundshop_misc\comp lanes.txt
# todo: apparently, we need midi graphs for manipulating notes, not just in-place changes. https://chatgpt.com/share/69a44037-0cd4-8011-a96a-39e082f55e1a
class Song:
  def __init__(self, filePath=None, client=None, tracks=None):
    if tracks is None:
      tracks = []
    self.filePath = filePath
    self.client = client
    self.tracks = tracks
    self.trackConnections = []
  def addtrackconnection(self, track1, track2):
    self.trackConnections.append((track1, track2))
  def send(self):
    self.client.clearparamschedule()
    for paramschedule in self.paramSchedules:
      self.client.scheduleparamchanges(paramschedule)
    self.client.clearmidiccschedule()
    for midiccschedule in self.midiCcSchedules:
      self.client.schedulemidiccs(self.midiccschedule)
    self.client.clearmidischedule()
    for midischedule in self.midiSchedules:
      self.client.schedulemidinotes(midischedule)
    self.client.clearorderednotes()
    self.client.scheduleorderednotes(self.orderednotesschedule)
    self.client.clearallplugins()
    if self.availablePlugins is None:
      self.scanplugins()
    for processor in self.processors:
      for index, availablePlugin in enumerate(self.client.availablePlugins):
        if availablePlugin.pluginInfo.uid==processor.pluginInfo.uid:
          self.client.loadpluginbyindex(index)
  def routecctoparam(self, pluginId, param_index, cc_controller, midi_channel=-1):
    """Map a MIDI CC controller to a plugin parameter

    Args:
      pluginId: Plugin ID
      param_index: Parameter index
      cc_controller: MIDI CC controller number (0-127)
      midi_channel: MIDI channel (1-16) or -1 for any channel
    """
    self.sendcmd(send_cmd.route_cc_to_param)
    self.sendinfo("IIIi", pluginId, param_index, cc_controller, midi_channel)
    self.commands_pipe_handle.flush()
    return self.readinfo1c("I")
  def unroutecctoparam(self, pluginId, param_index, cc_controller):
    """Remove a MIDI CC to parameter mapping"""
    self.sendcmd(send_cmd.unroute_cc_to_param)
    self.sendinfo("III", pluginId, param_index, cc_controller)
    self.commands_pipe_handle.flush()
    return self.readinfo1c("I")
  def save(self, filePath=None):
    filePath = filePath or self.filePath or "song"
    if os.path.exists(filePath):
      x = 0
      while 1:
        x += 1
        filePath2 = filePath+str(x)
        if not os.path.exists(filePath2):
          filePath = filePath2
          break
    open(filePath).write(yaml.dump(self))

  def connect_audio(self, p1, ch1, p2, ch2, *, gain_db=None, group_id=None):
    if group_id is None:
      group_id = self.allocate_connection_group_id()

    self.connections.append(
      ProcessorConnection(
        p1, ch1,
        p2, ch2,
        gain_db=gain_db,
        group_id=group_id
      )
    )
    return group_id

  def connect_stereo(
          self,
          proc1: Processor,
          proc2: Processor,
          *,
          gain_db=None
  ):
    gid = self.allocate_connection_group_id()

    for ch in (0, 1):
      self.connections.append(
        ProcessorConnection(
          proc1, ch,
          proc2, ch,
          gain_db=gain_db,
          group_id=gid
        )
      )

    return gid

   def disconnect_audio(self, group_id: int) -> int:
        """
        Remove all ProcessorConnections with this group_id.
        Returns the number removed.
        """
        removed = 0
        kept = []
        for c in self.connections:
          if c.group_id == group_id:
            removed += 1
          else:
            kept.append(c)
        self.connections[:] = kept
        return removed

# should adding and removing to a song automatically update the info on the server side? i think just have a send() method to update on the server side.
# remember to call MidiSchedule(notes) for all notes in notesSchedules and send them when updating the song on the server side
# todo: make lists of audiotracks
# todo: add support for multiple tracks in one song. according to chatgpt, each DAW track has its own set of effects and connections between them, but you can also route each track to whatever other effects..
# maybe instead of midSchedules, etc. we should have one schedule for each thing per track

def load_song(path):
  return yaml.load(open(path))

def main():
  if len(sys.argv)>1:
    pipename = sys.argv[1]
    print(f"using pipe name: {pipename}")
    client = JuceAudioClient(sys.argv[1])
  else:
    client = JuceAudioClient()

  # Connect will auto-start the server if needed
  if not client.connect(auto_start=True, max_retries=5, retry_delay=1.0):
    print("Failed to connect to server")
    print("Make sure juce_gui_server.exe is in the same directory or specify path")
    return
  print("Connected!")
  print("scanning directories")
  plugindirs = defaultDirs + (r"d:\music creation\free vsts", r"(C:\Users\inhah\AppData\Local\Orchestral Tools\SINE Player\Content")
  #plugindirs = (r"d:\music creation\free vsts",)
  jf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "badpaths.json")
  badpaths = json.loads(open(jf).read()) if os.path.exists(jf) else []
  numfound = client.scanplugins(plugindirs, badpaths)
  print(f"numfound: {numfound}")
  badpathsfound = client.listbadpaths()
  print(f"badpaths found: {len(badpathsfound)}")
  badpaths += badpathsfound
  badpaths = list(set(badpaths))
  open(jf, "w").write(json.dumps(badpaths))
  
  running = True
  def read_notifications_thread():
    global running
    while True:
      try:
        cmd = client.readinfo1n("B")
        if cmd==recv_cmd.param_change:
          pluginId, parameterIndex, value = client.readinfon("IIf")
          atBlock = client.readinfo1n("Q")
          print(f"parameter change: {pluginId=}, {parameterIndex=}, {value=}, atBlock={atBlock}")
          p_c = dummy()
          p_c.pluginId = pluginId
          p_c.parameterIndex = parameterIndex
          p_c.value = value
          p_c.atBlock = atBlock
          param_changes.append(p_c)
        elif cmd==recv_cmd.midi_note_event:
          noteNumber, velocity, channel, isNoteOn, samplePosition = client.readinfon("IIIIQ")
          event_type = "NOTE ON" if isNoteOn else "NOTE OFF"
          print(f"MIDI {event_type}: note={noteNumber}, velocity={velocity}, channel={channel}, sample={samplePosition}")
        elif cmd==recv_cmd.midi_cc_event:
          controller, value, channel, atBlock = client.readinfon("IIIQ")
          print(f"MIDI CC: controller={controller}, value={value}, channel={channel}, atBlock={atBlock}")
        elif cmd==recv_cmd.virtual_keyboard_note_event:
          noteNumber, velocity, channel, isNoteOn, samplePosition = client.readinfon("IIIIQ")
          event_type = "NOTE ON" if isNoteOn else "NOTE OFF"
          print(f"VIRTUAL KEYBOARD {event_type}: note={noteNumber}, velocity={velocity}, channel={channel}, sample={samplePosition}")
        elif cmd==recv_cmd.virtual_keyboard_cc_event:
          controller, value, channel, atBlock = client.readinfon("IIIQ")
          print(f"VIRTUAL KEYBOARD CC: controller={controller}, value={value}, channel={channel}, atBlock={atBlock}")
        elif cmd==recv_cmd.midi_keyboard_routed:
          pluginId, samplePosition = client.readinfon("iQ")
          if pluginId == -3: #todo: check this value
            print(f"MIDI keyboard unrouted at sample {samplePosition}")
          else:
            print(f"MIDI keyboard routed to plugin {pluginId} at sample {samplePosition}")
        elif cmd==recv_cmd.virtual_keyboard_routed:
          pluginId, samplePosition = client.readinfon("iQ")
          if pluginId == -3: #todo: check this value
            print(f"Virtual keyboard unrouted at sample {samplePosition}")
          else:
            print(f"Virtual keyboard routed to plugin {pluginId} at sample {samplePosition}")
        elif cmd==recv_cmd.stop_playback:
          pass
        elif cmd==recv_cmd.param_changes_end:
          pass
        elif cmd==recv_cmd.cmd_shutdown:
          running = False
          break
      except Exception as e:
        print(f"Notification thread error: {e}")
        break

  t = threading.Thread(target=read_notifications_thread)
  t.start()

  print("loading and showing 5 plugins")

  for x in range(10):
    print(f"plugin #{x}")
    client.loadpluginbyindex(x)
    client.showpluginui(x)
    p = client.getPluginInfo(x)
    print(f"{p.name=} {p.isInstrument=}")
    success, numParams, params, errmsg = client.getParamsInfo(x)
    for p in params:
      print()
      for a in "name originalIndex minValue maxValue interval skewFactor value numSteps isDiscrete isBoolean isOrientationInverted isAutomatable isMetaParameter".split():
        print(f"{x}: {a}: {getattr(p, a)}")

  processors, connections, gain_inits = insert_gain_processors(project, UID_GAIN)
  # todo: send processors, connections, gain_units to server

  client.startplayback(15*sampleRate//blockSize, False, "")
  x = input()
  client.commands_pipe_handle.flush()  
  client.disconnect()  

if __name__ == "__main__":
  main()

# ---- helpers for stable ids ----

def _stable_hash(obj: Any) -> str:
    """
    Deterministic hash of JSON-serializable obj (dict/list/str/float/int/bool/None).
    """
    payload = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]

# for saving signal graphs to file
@dataclass
class SignalGraphDump:
    nodes: Dict[str, Dict[str, Any]]      # node_id -> node_spec
    roots: Dict[str, str]                  # name -> node_id
    wavetables: Dict[str, list[float]]     # table_id -> samples

class SignalGraphSerializer:
    """
    Serializes Signal DAGs with sharing preserved.
    """

    def __init__(self):
        self.nodes: Dict[str, Dict[str, Any]] = {}
        self.memo_by_obj: Dict[int, str] = {}           # python object identity -> node_id
        self.wavetables: Dict[str, list[float]] = {}    # table_id -> samples

    def dump_named_roots(self, roots: Dict[str, "Signal"]) -> SignalGraphDump:
        out_roots: Dict[str, str] = {}
        for name, sig in roots.items():
            out_roots[name] = self._emit(sig)
        return SignalGraphDump(nodes=self.nodes, roots=out_roots, wavetables=self.wavetables)

    def _emit(self, sig: "Signal") -> str:
        # preserve sharing by python identity
        oid = id(sig)
        if oid in self.memo_by_obj:
            return self.memo_by_obj[oid]

        spec = self._node_spec(sig)               # may recurse for children
        node_id = "n" + _stable_hash(spec)        # content-based id => stable across runs if structure same

        # In the rare case of collision, deconflict
        if node_id in self.nodes and self.nodes[node_id] != spec:
            # extremely unlikely; append a salt
            salt = 1
            while True:
                node_id2 = node_id + "_" + str(salt)
                if node_id2 not in self.nodes:
                    node_id = node_id2
                    break
                salt += 1

        self.memo_by_obj[oid] = node_id
        self.nodes[node_id] = spec
        return node_id

    def _table_ref(self, table: list[float]) -> str:
        # store wavetable once; refer by a stable hash of samples
        tid = "wt" + _stable_hash([float(x) for x in table])
        if tid not in self.wavetables:
            self.wavetables[tid] = [float(x) for x in table]
        return tid

    def _node_spec(self, sig: "Signal") -> Dict[str, Any]:
        # Const
        if isinstance(sig, Const):
            return {"type": "const", "value": float(sig.value)}

        # Binary ops
        if isinstance(sig, Add):
            return {"type": "add", "a": self._emit(sig.a), "b": self._emit(sig.b)}
        if isinstance(sig, Sub):
            return {"type": "sub", "a": self._emit(sig.a), "b": self._emit(sig.b)}
        if isinstance(sig, Mul):
            return {"type": "mul", "a": self._emit(sig.a), "b": self._emit(sig.b)}
        if isinstance(sig, Neg):
            return {"type": "neg", "x": self._emit(sig.x)}

        # Wavetables
        if isinstance(sig, WavetableOverTime):
            return {
                "type": "wavetable_over_time",
                "table_ref": self._table_ref(sig.table),
                "duration_seconds": float(sig.duration),
                "phase0": float(sig.phase0),
                "interp": sig.interp,
                "loop": bool(sig.loop),
            }

        if isinstance(sig, WavetableOsc):
            return {
                "type": "wavetable_osc",
                "table_ref": self._table_ref(sig.table),
                "freq": self._emit(sig.freq),
                "phase0": float(sig.phase0),
                "interp": sig.interp,
                "loop": bool(sig.loop),
                "cache_every_blocks": int(sig._cache_every_blocks),
            }

        # BeatFn/TimeFn/lambdas are addressed below (see lambda section)
        if isinstance(sig, TimeFn):
            raise ValueError("TimeFn with lambda is not serializable as-is (see lambda strategies below).")
        if isinstance(sig, BeatFn):
            raise ValueError("BeatFn with lambda is not serializable as-is (see lambda strategies below).")

        raise TypeError(f"Unsupported Signal type for serialization: {type(sig).__name__}")

serializer = SignalGraphSerializer()
dump = serializer.dump_named_roots({"tempo": tempo_signal, "cutoff": cutoff_signal})

yaml_data = {
    "version": 1,
    "signals": {
        "nodes": dump.nodes,
        "roots": dump.roots,
    },
    "wavetables": dump.wavetables,
}
yaml.safe_dump(yaml_data, open("song.yaml", "w"), sort_keys=False)

import yaml

serializer = SignalGraphSerializer()
dump = serializer.dump_named_roots({"tempo": tempo_signal, "cutoff": cutoff_signal})

yaml_data = {
    "version": 1,
    "signals": {
        "nodes": dump.nodes,
        "roots": dump.roots,
    },
    "wavetables": dump.wavetables,
}
yaml.safe_dump(yaml_data, open("song.yaml", "w"), sort_keys=False)

class SignalGraphDeserializer:
  def __init__(self, nodes: Dict[str, Dict[str, Any]], wavetables: Dict[str, list[float]]):
    self.nodes = nodes
    self.wavetables = wavetables
    self.memo: Dict[str, Signal] = {}  # node_id -> Signal instance

  def build(self, root_id: str) -> "Signal":
    if root_id in self.memo:
      return self.memo[root_id]

    spec = self.nodes[root_id]
    t = spec["type"]

    if t == "const":
      sig = Const(spec["value"])

    elif t == "add":
      sig = Add(self.build(spec["a"]), self.build(spec["b"]))
    elif t == "sub":
      sig = Sub(self.build(spec["a"]), self.build(spec["b"]))
    elif t == "mul":
      sig = Mul(self.build(spec["a"]), self.build(spec["b"]))
    elif t == "neg":
      sig = Neg(self.build(spec["x"]))

    elif t == "wavetable_over_time":
      table = self.wavetables[spec["table_ref"]]
      sig = WavetableOverTime(
        table,
        duration_seconds=float(spec["duration_seconds"]),
        phase0=float(spec["phase0"]),
        interp=spec["interp"],
        loop=bool(spec["loop"]),
      )

    elif t == "wavetable_osc":
      table = self.wavetables[spec["table_ref"]]
      freq = self.build(spec["freq"])
      sig = WavetableOsc(
        table,
        freq_hz=freq,
        phase0=float(spec["phase0"]),
        interp=spec["interp"],
        loop=bool(spec["loop"]),
        cache_every_blocks=int(spec["cache_every_blocks"]),
      )

    else:
      raise ValueError(f"Unknown node type: {t}")

    self.memo[root_id] = sig
    return sig

def resolve_callable(path: str):
    mod_name, func_name = path.split(":")
    mod = importlib.import_module(mod_name)
    return getattr(mod, func_name)

@dataclass(frozen=True)
class AudioFileRef:
    id: str                  # stable id in song (uuid or user label)
    path: str                # file path (or uri)
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    length_samples: Optional[int] = None
    # (optional) hash for caching / change detection

@dataclass(frozen=True)
class Fade:
    in_samples: int = 0
    out_samples: int = 0
    curve: str = "linear"     # later: "exp", "log", etc.

@dataclass(frozen=True)
class AudioClip:
    """
    A clip is a region of an audio file placed on the timeline.
    Everything is non-destructive: you never change the file; you only change metadata.
    """
    id: str
    file_id: str             # AudioFileRef.id
    start_sample: int        # where this clip sits on the track timeline
    length_samples: int      # duration on the timeline
    file_offset_samples: int = 0  # “slip” offset into the source file
    gain_db: float = 0.0
    mute: bool = False
    fade: Fade = Fade()
    # (optional) stretch/warp info could go here later


@dataclass(frozen=True)
class AudioFileRef:
  id: str  # stable id in song (uuid or user label)
  path: str  # file path (or uri)
  sample_rate: Optional[int] = None
  channels: Optional[int] = None
  length_samples: Optional[int] = None
  # (optional) hash for caching / change detection


@dataclass(frozen=True)
class Fade:
  in_samples: int = 0
  out_samples: int = 0
  curve: str = "linear"  # later: "exp", "log", etc.


@dataclass(frozen=True)
class AudioClip:
  """
  A clip is a region of an audio file placed on the timeline.
  Everything is non-destructive: you never change the file; you only change metadata.
  """
  id: str
  file_id: str  # AudioFileRef.id
  start_sample: int  # where this clip sits on the track timeline
  length_samples: int  # duration on the timeline
  file_offset_samples: int = 0  # “slip” offset into the source file
  gain_db: float = 0.0
  mute: bool = False
  fade: Fade = Fade()
  # (optional) stretch/warp info could go here later

dataclass
class AudioLane:
    """
    A lane holds clips that must not overlap (or you define overlap rules).
    """
    id: str
    clips: List[AudioClip] = field(default_factory=list)

@dataclass(frozen=True)
class CompSegment:
    """
    Select audio from a specific lane for a time range on the track timeline.
    """
    start_sample: int
    end_sample: int          # exclusive
    take_lane_id: str
    # optional crossfade between adjacent segments:
    xfade_samples: int = 0

@dataclass
class Comp:
    """
    The comp is the authoritative “what you hear” result.
    Segments should be non-overlapping and cover only the parts you want audible.
    """
    segments: List[CompSegment] = field(default_factory=list)
    active: bool = True

@dataclass
class AudioTrack:
    id: str
    name: str
    lanes: List[AudioLane] = field(default_factory=list)

# If comp.active is true: render only what the comp selects (plus maybe non-take lanes depending on your policy)
# If no comp: render all “regular lanes” (and optionally take lanes if you want)

@dataclass
class AudioLane:
    id: str
    clips: List[AudioClip] = field(default_factory=list)
    time_offset_samples: int = 0  # positive = later, negative = earlier

# =========================
# 1) Audio file + clip model
# =========================

@dataclass(frozen=True)
class AudioFileRef:
    """
    A stable reference to an audio file used in the project.

    - id: stable project-local identifier (e.g. "vox_take1")
    - path: file path (you can later allow URIs)
    - Optional metadata fields are for convenience/caching/validation.
    """
    id: str
    path: str
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    length_samples: Optional[int] = None


@dataclass(frozen=True)
class Fade:
    """
    Fade lengths in samples. Curves can be extended later.
    """
    in_samples: int = 0
    out_samples: int = 0
    curve: str = "linear"


@dataclass(frozen=True)
class AudioClip:
    """
    A region of an audio file placed on the timeline (non-destructive).

    start_sample: where clip starts on the track timeline
    length_samples: duration on timeline
    file_offset_samples: where reading begins in the source file ("slip")
    gain_db: per-clip gain (optional)
    mute: if True, contributes no audio
    fade: fade-in/out applied to this clip
    """
    id: str
    file_id: str
    start_sample: int
    length_samples: int
    file_offset_samples: int = 0
    gain_db: float = 0.0
    mute: bool = False
    fade: Fade = Fade()

    @property
    def end_sample(self) -> int:
        return self.start_sample + self.length_samples


@dataclass
class AudioLane:
    """
    A lane is a list of clips. You can decide overlap policy; this model allows overlap,
    but validate() can forbid it if you want.

    time_offset_samples: shifts the whole lane earlier/later on the timeline.
      - positive -> the lane plays later
      - negative -> the lane plays earlier
    """
    id: str
    clips: List[AudioClip] = field(default_factory=list)
    time_offset_samples: int = 0


# =========================
# 2) Comping model (takes + comp segments)
# =========================

@dataclass(frozen=True)
class CompSegment:
    """
    Select timeline region [start_sample, end_sample) from a specific take lane.
    xfade_samples requests a crossfade at this segment boundary (to the next segment).
    """
    start_sample: int
    end_sample: int
    take_lane_id: str
    xfade_samples: int = 0


@dataclass
class Comp:
    """
    A comp is the "what you hear" selection across take lanes.

    segments should be:
      - non-overlapping
      - sorted by start_sample
      - typically contiguous (but you can allow gaps)
    """
    segments: List[CompSegment] = field(default_factory=list)
    active: bool = True


# =========================
# Render plan output (trackless)
# =========================

@dataclass(frozen=True)
class RenderAudioRegion:
    """
    Trackless render primitive: "play this file slice at this time".
    This is what your scheduler can hand to the renderer layer.

    timeline_start: when it starts on the project timeline
    length_samples: how long on the timeline
    file_offset: where to start reading from the file
    gain_db + fades: applied at render/mix time
    """
    file_id: str
    timeline_start: int
    length_samples: int
    file_offset: int
    gain_db: float = 0.0
    fade_in: int = 0
    fade_out: int = 0

    @property
    def timeline_end(self) -> int:
        return self.timeline_start + self.length_samples


# =========================
# 3) AudioTrack + Song container
# =========================

@dataclass
class AudioTrack:
    id: str
    name: str

    # "takes" are lanes used for comping
    take_lanes: Dict[str, AudioLane] = field(default_factory=dict)

    # optional regular lanes (non-take audio, imports, FX bounces, etc.)
    lanes: Dict[str, AudioLane] = field(default_factory=dict)

    comp: Optional[Comp] = None

    # -------------------------
    # lane/take management API
    # -------------------------

    def ensure_take_lane(self, lane_id: str) -> AudioLane:
        if lane_id not in self.take_lanes:
            self.take_lanes[lane_id] = AudioLane(id=lane_id)
        return self.take_lanes[lane_id]

    def ensure_lane(self, lane_id: str) -> AudioLane:
        if lane_id not in self.lanes:
            self.lanes[lane_id] = AudioLane(id=lane_id)
        return self.lanes[lane_id]

    def add_take_clip(self, take_lane_id: str, clip: AudioClip) -> None:
        lane = self.ensure_take_lane(take_lane_id)
        lane.clips.append(clip)

    def set_take_offset(self, take_lane_id: str, offset_samples: int) -> None:
        lane = self.ensure_take_lane(take_lane_id)
        lane.time_offset_samples = int(offset_samples)

    def set_comp(self, segments: List[CompSegment], *, active: bool = True) -> None:
        self.comp = Comp(segments=list(segments), active=active)

    # -------------------------
    # validation
    # -------------------------

    def validate(self) -> None:
        # Validate all clips in all lanes
        for lane in list(self.take_lanes.values()) + list(self.lanes.values()):
            for c in lane.clips:
                _validate_clip(c)

        # Validate comp structure
        if self.comp and self.comp.active:
            _validate_comp(self.comp, self.take_lanes)

    # -------------------------
    # compilation: comp -> regions
    # -------------------------

    def compile_to_regions(self, *, include_non_take_lanes: bool = True) -> List[RenderAudioRegion]:
        """
        Returns a list of RenderAudioRegion for this track, with comp applied if active.

        - If comp is active: emits regions corresponding to comp selection from take lanes.
        - If comp is not active or absent: emits all clips from non-take lanes (and optionally take lanes).
        - include_non_take_lanes: if comp is active, you probably still want extra imported clips to play;
          set False if you want "comp only".
        """
        self.validate()

        regions: List[RenderAudioRegion] = []

        if self.comp and self.comp.active:
            # 1) comp-derived regions
            regions.extend(_compile_comp_regions(self.comp, self.take_lanes))

            # 2) plus regular lanes, if desired
            if include_non_take_lanes:
                for lane in self.lanes.values():
                    regions.extend(_lane_clips_to_regions(lane))

        else:
            # no active comp: just emit regular lanes
            for lane in self.lanes.values():
                regions.extend(_lane_clips_to_regions(lane))
            # optionally emit take lanes too if you want "all takes audible"
            # (default: not doing it; you can add a flag if desired)

        # determinism
        regions.sort(key=lambda r: (r.timeline_start, r.file_id, r.file_offset))
        return regions


@dataclass
class Song:
    """
    Top-level project container (trackless renderer downstream).
    """
    sample_rate: int
    block_size: int = 128
    duration_samples: int = 0

    audio_files: Dict[str, AudioFileRef] = field(default_factory=dict)
    audio_tracks: Dict[str, AudioTrack] = field(default_factory=dict)

    def add_audio_file(self, file_id: str, path: str, **meta) -> AudioFileRef:
        if file_id in self.audio_files:
            raise ValueError(f"Audio file id already exists: {file_id}")
        ref = AudioFileRef(id=file_id, path=path, **meta)
        self.audio_files[file_id] = ref
        return ref

    def add_audio_track(self, track_id: str, name: Optional[str] = None) -> AudioTrack:
        if track_id in self.audio_tracks:
            raise ValueError(f"Audio track id already exists: {track_id}")
        t = AudioTrack(id=track_id, name=name or track_id)
        self.audio_tracks[track_id] = t
        return t

    def compile_all_audio_regions(self) -> List[RenderAudioRegion]:
        """
        Compile all audio tracks into a unified list of RenderAudioRegion.
        This is trackless data the renderer can consume.

        NOTE: you may later attach a destination bus/plugin input per region,
        but this keeps it simple and purely timeline-based.
        """
        out: List[RenderAudioRegion] = []
        for tr in self.audio_tracks.values():
            out.extend(tr.compile_to_regions(include_non_take_lanes=True))
        out.sort(key=lambda r: (r.timeline_start, r.file_id, r.file_offset))
        return out


# =========================
# Helpers: validation
# =========================

def _validate_clip(c: AudioClip) -> None:
    if c.start_sample < 0:
        raise ValueError(f"Clip {c.id}: start_sample must be >= 0")
    if c.length_samples <= 0:
        raise ValueError(f"Clip {c.id}: length_samples must be > 0")
    if c.file_offset_samples < 0:
        raise ValueError(f"Clip {c.id}: file_offset_samples must be >= 0")
    if c.fade.in_samples < 0 or c.fade.out_samples < 0:
        raise ValueError(f"Clip {c.id}: fades must be >= 0")


def _validate_comp(comp: Comp, take_lanes: Dict[str, AudioLane]) -> None:
    segs = list(comp.segments)
    if not segs:
        return

    # sort check and monotonic check
    segs_sorted = sorted(segs, key=lambda s: (s.start_sample, s.end_sample))
    if segs_sorted != segs:
        raise ValueError("Comp.segments must be sorted by start_sample (and stable)")

    prev_end = None
    for s in segs:
        if s.start_sample < 0:
            raise ValueError("CompSegment.start_sample must be >= 0")
        if s.end_sample <= s.start_sample:
            raise ValueError("CompSegment must have end_sample > start_sample")
        if s.take_lane_id not in take_lanes:
            raise ValueError(f"CompSegment references missing take lane: {s.take_lane_id}")
        if s.xfade_samples < 0:
            raise ValueError("CompSegment.xfade_samples must be >= 0")

        if prev_end is not None and s.start_sample < prev_end:
            raise ValueError("Comp.segments must not overlap")
        prev_end = s.end_sample


# =========================
# Helpers: lane -> regions
# =========================

def _lane_clips_to_regions(lane: AudioLane) -> List[RenderAudioRegion]:
    """
    Convert lane clips to render regions, applying lane time offset.
    """
    out: List[RenderAudioRegion] = []
    off = int(lane.time_offset_samples)

    for c in lane.clips:
        if c.mute:
            continue

        # clip timeline is shifted by lane offset
        timeline_start = c.start_sample + off
        if timeline_start < 0:
            # allow but clamp? for now: forbid
            raise ValueError(f"Lane {lane.id}: clip {c.id} shifts before t=0 due to lane offset")

        out.append(
            RenderAudioRegion(
                file_id=c.file_id,
                timeline_start=timeline_start,
                length_samples=c.length_samples,
                file_offset=c.file_offset_samples,
                gain_db=c.gain_db,
                fade_in=c.fade.in_samples,
                fade_out=c.fade.out_samples,
            )
        )

    return out


# =========================
# 4) Comp compilation: selection + lane offset + crossfades
# =========================

def _compile_comp_regions(comp: Comp, take_lanes: Dict[str, AudioLane]) -> List[RenderAudioRegion]:
    """
    Convert comp segments into concrete RenderAudioRegion(s) by slicing take-lane clips.

    Crossfades:
      For boundary between seg[i] and seg[i+1], we create an overlap of xfade_samples
      (limited by the adjacent segment lengths), and apply:
        - fade_out on the outgoing region in the overlap
        - fade_in  on the incoming region in the overlap

      This outputs *overlapping* regions. Your downstream mixing graph (fan-in) sums them,
      which is exactly how crossfades work.
    """
    segs = comp.segments
    out: List[RenderAudioRegion] = []

    if not segs:
        return out

    # Precompute per-segment effective fade overlap with the next segment
    # We'll compute for each boundary: overlap = min(requested, len(seg_i), len(seg_{i+1}))
    overlaps: List[int] = [0] * (len(segs) - 1)
    for i in range(len(segs) - 1):
        a = segs[i]
        b = segs[i + 1]
        req = int(a.xfade_samples)
        if req <= 0:
            overlaps[i] = 0
            continue
        len_a = a.end_sample - a.start_sample
        len_b = b.end_sample - b.start_sample
        overlaps[i] = max(0, min(req, len_a, len_b))

    # We'll emit regions per segment, but split around overlaps so we can attach fades.
    for i, seg in enumerate(segs):
        lane = take_lanes[seg.take_lane_id]
        lane_off = int(lane.time_offset_samples)

        # segment on timeline is [ts, te)
        ts = seg.start_sample
        te = seg.end_sample

        # Apply crossfade overlap logic:
        # - if there's an overlap with previous boundary, extend this segment backward by overlap
        # - if there's an overlap with next boundary, extend this segment forward by overlap
        # BUT: we do this by splitting actual emitted regions:
        #
        # Outgoing segment i has a tail overlap with i+1: [te-overlap, te)
        # Incoming segment i has a head overlap with i-1: [ts, ts+overlap_prev)
        #
        overlap_prev = overlaps[i - 1] if i > 0 else 0
        overlap_next = overlaps[i] if i < len(segs) - 1 else 0

        # 1) main non-overlapped body: [ts + overlap_prev, te - overlap_next)
        body_start = ts + overlap_prev
        body_end = te - overlap_next
        if body_end > body_start:
            out.extend(
                _slice_take_lane_into_regions(
                    lane=lane,
                    lane_offset=lane_off,
                    timeline_start=body_start,
                    timeline_end=body_end,
                    fade_in=0,
                    fade_out=0,
                )
            )

        # 2) head overlap (incoming) region: [ts, ts+overlap_prev) with fade-in
        if overlap_prev > 0:
            out.extend(
                _slice_take_lane_into_regions(
                    lane=lane,
                    lane_offset=lane_off,
                    timeline_start=ts,
                    timeline_end=ts + overlap_prev,
                    fade_in=overlap_prev,
                    fade_out=0,
                )
            )

        # 3) tail overlap (outgoing) region: [te-overlap_next, te) with fade-out
        if overlap_next > 0:
            out.extend(
                _slice_take_lane_into_regions(
                    lane=lane,
                    lane_offset=lane_off,
                    timeline_start=te - overlap_next,
                    timeline_end=te,
                    fade_in=0,
                    fade_out=overlap_next,
                )
            )

    # deterministic ordering helps downstream
    out.sort(key=lambda r: (r.timeline_start, r.file_id, r.file_offset))
    return out


def _slice_take_lane_into_regions(
    *,
    lane: AudioLane,
    lane_offset: int,
    timeline_start: int,
    timeline_end: int,
    fade_in: int,
    fade_out: int,
) -> List[RenderAudioRegion]:
    """
    Slices the take lane's clips to cover [timeline_start, timeline_end) on the project timeline.

    IMPORTANT: lane_offset shifts clips on the timeline.
      A clip with c.start_sample appears at (c.start_sample + lane_offset).

    This function finds clip overlaps and emits RenderAudioRegion slices, adjusting file_offset accordingly.
    """
    if timeline_end <= timeline_start:
        return []

    out: List[RenderAudioRegion] = []

    # Find overlaps across all clips in the lane.
    # In many projects, each take lane has one long clip; this will be fast.
    for c in lane.clips:
        if c.mute:
            continue

        clip_t0 = c.start_sample + lane_offset
        clip_t1 = c.end_sample + lane_offset

        # overlap of [timeline_start,timeline_end) with [clip_t0,clip_t1)
        ov0 = max(timeline_start, clip_t0)
        ov1 = min(timeline_end, clip_t1)
        if ov1 <= ov0:
            continue

        # The slice begins ov0 on timeline; in the file it begins at:
        # c.file_offset_samples + (ov0 - clip_t0)
        file_off = c.file_offset_samples + (ov0 - clip_t0)

        out.append(
            RenderAudioRegion(
                file_id=c.file_id,
                timeline_start=ov0,
                length_samples=(ov1 - ov0),
                file_offset=file_off,
                gain_db=c.gain_db,
                fade_in=min(int(fade_in), ov1 - ov0),
                fade_out=min(int(fade_out), ov1 - ov0),
            )
        )

    # If nothing overlapped, this segment is silent; that's allowed.
    out.sort(key=lambda r: (r.timeline_start, r.file_id, r.file_offset))
    return out