#!/usr/bin/env python3

#todo: one plugin isn't loading but it's not adding it to badPaths
#todo: change getinfo1 to get1info and readstr1 to read1str?
#todo: make camelCase more consistent

#note that the index you use to call functions other than loadPluginByIndex is a different index from that one. 
#the index isit's what's returned as 'lpindex' (loaded plugin index) when you load a plugin.
#similarly for parameters, to control them use their 'originalIndex'

#CC 1: Modulation wheel (vibrato, usually)
#CC 7: Channel volume
#CC 10: Pan
#CC 11: Expression (fine volume control)
#CC 64: Sustain pedal (on/off)
#CC 74: Filter cutoff (brightness)
#
#The Values
#Each CC sends a value from 0-127:
#
#CC 7 (volume) = 0 means silent
#CC 7 (volume) = 127 means full volume
#CC 10 (pan) = 0 means hard left
#CC 10 (pan) = 64 means center
#CC 10 (pan) = 127 means hard right

#Special MIDI notes outside the playable range trigger articulation changes
#For example, C0 might switch to legato, C#0 to staccato, D0 to pizzicato

import time, struct, platform, sys, json, os, threading, subprocess

class send_cmd:
  load_plugin, load_plugin_by_index, scan_plugins, list_plugins, get_plugin_info, show_plugin_ui, hide_plugin_ui, \
  set_parameter, get_parameter, connect_audio, connect_midi, start_playback, cmd_shutdown, remove_plugin, \
  list_bad_paths, get_params_info, get_channels_info, schedule_midi_note, schedule_midi_cc, clear_midi_schedule, \
  schedule_param_change, route_keyboard_input, unroute_keyboard_input, route_cc_to_param, unroute_cc_to_param, \
  show_virtual_keyboard, hide_virtual_keyboard, route_virtual_keyboard, unroute_virtual_keyboard = range(29)

class recv_cmd:
  param_change, param_changes_end, stop_playback, midi_note_event, midi_cc_event = range(5)

pipe_name = "juceclientserver"

sampleRate = 44100
blockSize = 64

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
    def __init__(self, pipe_name=pipe_name, server_exe_path=None):
        self.pipe_name = pipe_name
        self.commands_pipe_path = f"\\\\.\\pipe\\{pipe_name}_commands"
        self.commands_pipe_handle = None
        self.notifications_pipe_path = f"\\\\.\\pipe\\{pipe_name}_notifications"
        self.notifications_pipe_handle = None
        self.commands_connected = False
        self.notifications_connected = False
        self.server_process = None
        self.server_exe_path = server_exe_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "juce_gui_server.exe"
        )

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
                print("Connected successfully!")
                return True
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
          
    def loadplugin(self, path):
      #success, name, lpindex, uid, errmsg
      self.sendcmd(send_cmd.load_plugin)
      self.sendstr(path)
      self.sendinfo("I", id)
      self.commands_pipe_handle.flush()
      return self.readinfoc("I") + self.readstrs(1) + self.readinfoc("II") + self.readstrs(1)
                                   
    def loadpluginbyindex(self, index):
      #success, name, lpindex, uid, errmsg
      self.sendcmd(send_cmd.load_plugin_by_index)
      self.sendinfo("I", index)
      self.commands_pipe_handle.flush()
      return self.readinfoc("I") + self.readstrs(1) + self.readinfoc("II") + self.readstrs(1)

    def scanplugins(self, directories, badpaths=[]):
      #num_found
      self.sendcmd(send_cmd.scan_plugins)
      self.sendinfo("I", len(directories))
      self.sendstrs(directories)
      self.sendinfo("I", len(badpaths))
      print("sending badpaths") #debug
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
        p.isInstrument, p.uniqueId, p.numInputChannels, p.numOutputChannels = self.readinfoc("IIII") #todo: we could use "?" for booleans and cast to uint8_t in c++
        p.name, p.descriptiveName, p.pluginFormatName, p.category, p.manufacturerName, p.version, p.fileOrIdentifier, p.lastFileModTime, p.path = self.readstrs(9)
        plugins.append(p)
      return plugins

    def listbadpaths(self):
      badpaths = []
      self.sendcmd(send_cmd.list_bad_paths)
      self.commands_pipe_handle.flush()
      size = self.readinfo1c("I")
      for x in range(size):
        badpaths.append(self.readstr1())
      return badpaths
        
    def getParamsInfo(self, lpindex):
      self.sendcmd(send_cmd.get_params_info)
      self.sendinfo("I", lpindex)
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
    
    def getParamsInfo(self, id):
      self.sendcmd(send_cmd.get_params_info)
      self.sendinfo("I", id)
      self.commands_pipe_handle.flush()
      success, numParams = self.readinfoc("II")
      errmsg = self.readstr1()
      params = []
      for x in range(numParams):
          p = dummy()
          p.originalIndex = self.readinfo1c("I")
          p.name = self.readstr1()
          a = read_exact(self.commands_pipe_handle, struct.calcsize("<"+"ffffffIIIIII"))
          p.minValue, p.maxValue, p.interval, p.defaultValue, p.skewFactor, p.value, p.numSteps, p.isDiscrete, p.isBoolean, \
              p.isOrientationInverted, p.isAutomatable, p.isMetaParameter = struct.unpack("<ffffffIIIIII", a)
          for a in "name minValue maxValue interval skewFactor value numSteps isDiscrete isBoolean isOrientationInverted isAutomatable isMetaParameter".split():
            print(f"    {a}: {getattr(p, a)}")

          print("parameters data:")
          for x in a: 
              print(x, end=" ")
          print()
          params.append(p)
      return success, numParams, params, errmsg
   
    def getChannelsInfo(self, lpindex):
      self.sendcmd(send_cmd.get_channels_info)
      self.commands_pipe_handle.flush()
      self.sendinfo("I", lpindex)
      success, acceptsMidi, producesMidi = self.readinfoc("III")
      print(f"getChannelsInfo: {lpindex=} {success=} {acceptsMidi=} {producesMidi=}")
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
      print(f"number of output buses for lpindex {lpindex}: {a}")
      for x in range(a):
        print(f"reading input channel info for channel for bus {x} lpindex {lpindex}")
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

    def showpluginui(self, lpindex):
      #success, errmsg
      self.sendcmd(send_cmd.show_plugin_ui)
      self.sendinfo("I", lpindex)
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I"), self.readstr1()

    def schedulemidinote(self, *args):
      #in: index, note, velocity, startTime, duration, channel
      self.sendcmd(send_cmd.schedule_midi_note)
      self.sendinfo("IIfddI", *args)
      self.commands_pipe_handle.flush()
      
    def schedulemidicc(self, *args):
      #in: lpindex, controller, value, time, channel
      self.sendcmd(send_cmd.schedule_midi_cc)
      self.senfindo("IIId", *args)
      self.commands_pipe_handle_flush()
    
    def clearmidischedule(self):
      self.sendcmd(send_cmd.clear_midi_schedule)
      self.commands_pipe_handle_flush()

    def scheduleparamchange(self, *args):
      #in: lpindex, parameterIndex, value, atBlock)
      self.sendcmd(send_cmd.schedule_param_change)
      self.sendinfo("IIfQ", *args)
      self.commands_pipe_handle_flush()

    def connectaudio(self, sourcelpindex, sourcechan, destlpindex, destchan):
      self.sendcmd(send_cmd.connect_audio)
      self.sendinfo("IIII", sourcelpindex, sourcechan, destlpindex, destchan)

    def startplayback(self, endblock, tofile, filename):
      self.sendcmd(send_cmd.start_playback)
      self.sendinfo("QI", endblock, tofile)
      if tofile:
        self.sendstr(filename)
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I")

    def routekeyboardinput(self, lpindex, use_velocity=True, fixed_velocity=1.0):
      """Route MIDI keyboard input to a specific plugin

      Args:
        lpindex: Plugin index to route to
        use_velocity: If True, use actual key velocity; if False, use fixed_velocity
        fixed_velocity: Fixed velocity value (0.0-1.0) when use_velocity is False
      """
      self.sendcmd(send_cmd.route_keyboard_input)
      self.sendinfo("IIf", lpindex, int(use_velocity), fixed_velocity)
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I")

    def unroutekeyboardinput(self):
      """Disable keyboard routing"""
      self.sendcmd(send_cmd.unroute_keyboard_input)
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I")

    def routecctoparam(self, lpindex, param_index, cc_controller, midi_channel=-1):
      """Map a MIDI CC controller to a plugin parameter

      Args:
        lpindex: Plugin index
        param_index: Parameter index
        cc_controller: MIDI CC controller number (0-127)
        midi_channel: MIDI channel (1-16) or -1 for any channel
      """
      self.sendcmd(send_cmd.route_cc_to_param)
      self.sendinfo("IIIi", lpindex, param_index, cc_controller, midi_channel)
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I")

    def unroutecctoparam(self, lpindex, param_index, cc_controller):
      """Remove a MIDI CC to parameter mapping"""
      self.sendcmd(send_cmd.unroute_cc_to_param)
      self.sendinfo("III", lpindex, param_index, cc_controller)
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

    def routevirtualkeyboard(self, lpindex, use_velocity=True, fixed_velocity=1.0):
      """Route virtual keyboard input to a specific plugin

      Args:
        lpindex: Plugin index to route to
        use_velocity: If True, use actual key velocity; if False, use fixed_velocity
        fixed_velocity: Fixed velocity value (0.0-1.0) when use_velocity is False
      """
      self.sendcmd(send_cmd.route_virtual_keyboard)
      self.sendinfo("IIf", lpindex, int(use_velocity), fixed_velocity)
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I")

    def unroutevirtualkeyboard(self):
      """Disable virtual keyboard routing"""
      self.sendcmd(send_cmd.unroute_virtual_keyboard)
      self.commands_pipe_handle.flush()
      return self.readinfo1c("I")

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
          lpindex, parameterIndex, value = client.readinfon("IIf")
          atBlock = client.readinfo1n("Q")
          print(f"parameter change: {lpindex=}, {parameterIndex=}, {value=}, atBlock={atBlock}")
          p_c = dummy()
          p_c.lpindex = lpindex
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
   
  for x in range(5):
    print(f"plugin #{x}")
    client.loadpluginbyindex(x)
    client.showpluginui(x)
    success, numParams, params, errmsg = client.getParamsInfo(x)
    for p in params:
      print()
      for a in "name originalIndex minValue maxValue interval skewFactor value numSteps isDiscrete isBoolean isOrientationInverted isAutomatable isMetaParameter".split():
        print(f"{x}: {a}: {getattr(p, a)}")
  client.startplayback(15*sampleRate//blockSize, False, "")
  x = input()
  client.commands_pipe_handle.flush()  
  client.disconnect()  

if __name__ == "__main__":
  main()

