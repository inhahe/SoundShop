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

import time, struct, platform, sys, json, os

class cmd:
  load_plugin, load_plugin_by_index, scan_plugins, list_plugins, get_plugin_info, show_plugin_ui, hide_plugin_ui, set_parameter, get_parameter,  \
  connect_audio, connect_midi, start_playback, stop_playback, cmd_shutdown, remove_plugin, list_bad_paths, get_params_info, get_channels_info, \
  schedule_midi_note, schedule_midi_cc, clear_midi_schedule = range(21)

pipe_name = "juceclientserver"

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

def read_exact(pipe_handle, n):
  """Read exactly n bytes, blocking until all are received"""
  data = b''
  while len(data) < n:
    chunk = pipe_handle.read(n - len(data))
    if not chunk:  # EOF/pipe closed
      raise IOError("pipe closed before all data received")
    data += chunk
  return data

class JuceAudioClient:
    def __init__(self, pipe_name=pipe_name):
        self.pipe_path = f"\\\\.\\pipe\\{pipe_name}"
        self.pipe_handle = None
        self.connected = False
    
    def connect(self):
        """Connect to the JUCE server"""
        try:
            print(f"connecting to {pipe_name}...")
            self.pipe_handle = open(self.pipe_path, 'w+b', buffering=0)
            self.connected = True
            return True
        except Exception as e:
            print(f"connection failed: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from the server"""
        if self.connected and self.pipe_handle:
            try:
                self.pipe_handle.close()
                print("disconnected")
            except:
                pass
            self.connected = False
    
    def sendinfo(self, pattern, *args):
      if not self.connected:
        raise IOError
      else:
        s = struct.pack("<"+pattern, *args)
        self.pipe_handle.write(struct.pack("<"+pattern, *args))

    def readinfo(self, pattern):
      if not self.connected:
        raise IOError
      else:
        return struct.unpack("<"+pattern, read_exact(self.pipe_handle, struct.calcsize("<"+pattern)))

    def readinfo1(self, pattern):
      if not self.connected:
        raise IOError
      else:
        return struct.unpack("<"+pattern, read_exact(self.pipe_handle, struct.calcsize("<"+pattern)))[0]

    def readstr1(self):
      if not self.connected:
        raise IOError
      else:
        size = self.readinfo1("I")
        return read_exact(self.pipe_handle, size).decode("utf-8", errors="ignore") #debug, there should never be decoding errors so we shouldn't have errors="ignore"

    def readstrs(self, num):
      return tuple(self.readstr1() for _ in range(num))

    def sendstr(self, s):
      if not self.connected:      
        raise IOError
      else:
        self.pipe_handle.write(struct.pack("I", len(s)) + s.encode("utf-8"))

    def sendstrs(self, ss):
      for s in ss:
        self.sendstr(s)
        
    def sendcmd(self, command):
      self.sendinfo("B", command)
          
    def loadplugin(self, path):
      #success, name, lpindex, uid, errmsg
      self.sendcmd(cmd.load_plugin)
      self.sendstr(path)
      self.sendinfo("I", id)
      self.pipe_handle.flush()
      return self.readinfo("I") + self.readstrs(1) + self.readinfo("II") + self.readstrs(1)
                                   
    def loadpluginbyindex(self, index):
      #success, name, lpindex, uid, errmsg
      self.sendcmd(cmd.load_plugin_by_index)
      self.sendinfo("I", index)
      self.pipe_handle.flush()
      return self.readinfo("I") + self.readstrs(1) + self.readinfo("II") + self.readstrs(1)

    def scanplugins(self, directories, badpaths=[]):
      #num_found
      self.sendcmd(cmd.scan_plugins)
      self.sendinfo("I", len(directories))
      self.sendstrs(directories)
      self.sendinfo("I", len(badpaths))
      self.sendstrs(badpaths)
      self.pipe_handle.flush()
      return self.readinfo1("I")

    def listplugins(self):
      self.sendcmd(cmd.list_plugins)
      self.pipe_handle.flush()
      size = self.readinfo1("I")
      plugins = []
      for x in range(size):
        p = dummy()
        p.isInstrument, p.uniqueId, p.numInputChannels, p.numOutputChannels = self.readinfo("IIII") #todo: we could use "?" for booleans and cast to uint8_t in c++
        p.name, p.descriptiveName, p.pluginFormatName, p.category, p.manufacturerName, p.version, p.fileOrIdentifier, p.lastFileModTime, p.path = self.readstrs(9)
        plugins.append(p)
      return plugins

    def listbadpaths(self):
      badpaths = []
      self.sendcmd(cmd.list_bad_paths)
      self.pipe_handle.flush()
      size = self.readinfo1("I")
      for x in range(size):
        badpaths.append(self.readstr1())
      return badpaths
        
    def getParamsInfo(self, lpindex):
      self.sendcmd(cmd.get_params_info)
      self.sendinfo("I", lpindex)
      self.pipe_handle.flush()
      success, numParams = self.readinfo("II")
      print(f"{success=}{numParams=}")
      errmsg = self.readstr1()
      params = []
      for x in range(numParams):
        p = dummy()
        p.originalIndex = self.readinfo1("I")
        p.name = self.readstr1()
        a = read_exact(self.pipe_handle, struct.calcsize("<"+"ffffffIIIIII"))
        p.minValue, p.maxValue, p.interval, p.defaultValue, p.skewFactor, p.value, p.numSteps, p.isDiscrete, p.isBoolean, p.isOrientationInverted, \
          p.isAutomatable, p.isMetaParameter = struct.unpack("<ffffffIIIIII", a) #debug
        print("parameters data:")
        print(f"{p.originalIndex=} {p.name=} {p.minValue=} {p.maxValue=} {p.defaultvalue=} {p.skewFactor=} {p.value=} {p.numStemsp=} {p.isDiscrete=}"
        " {p.isBoolean=} {p.isOrientationInverted=} {p.isAutomatable=} {p.isMetaParameter=}")
      return success, numParams, params, errmsg
    
    def getParamsInfo(self, id):
      self.sendcmd(cmd.get_params_info)
      self.sendinfo("I", id)
      self.pipe_handle.flush()
      success, numParams = self.readinfo("II")
      errmsg = self.readstr1()
      params = []
      for x in range(numParams):
          p = dummy()
          p.originalIndex = self.readinfo1("I")
          p.name = self.readstr1()
          a = read_exact(self.pipe_handle, struct.calcsize("<"+"ffffffIIIIII"))
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
      self.sendcmd(cmd.get_channels_info)
      self.pipe_handle.flush()
      self.sendinfo("I", lpindex)
      success, acceptsMidi, producesMidi = self.readinfo("III")
      print(f"getChannelsInfo: {lpindex=} {success=} {acceptsMidi=} {producesMidi=}")
      inputBuses = []
      for x in range(self.readinfo1("I")):
        b = dummy()
        b.numChannels = self.readinfo1("I")
        b.channelTypes = self.readstrs(self.readinfo1("I"))
        b.isEnabled = self.readinfo1("I")
        b.mainBusLayout = self.readstr1()
        inputBuses.append(b)
      print("finished reading input buses")
      outputBuses = []
      a = self.readinfo1("I")
      print(f"number of output buses for lpindex {lpindex}: {a}")
      for x in range(a):
        print(f"reading input channel info for channel for bus {x} lpindex {lpindex}")
        b = dummy()
        b.numChannels = self.readinfo1("I")
        print(f"{b.numChannels=}")
        a = self.readinfo1("I")
        print(f"channel types size: {a}")
        b.channelTypes = self.readstrs(a)
        print(f"{b.channelTypes=}")
        b.isEnabled = self.readinfo1("I")
        print(f"{b.isEnabled=}")
        b.mainBusLayout = self.readstr1()
        print(f"{b.mainBusLayout=}")
        outputBuses.append(b)
      print("finished reading output buses")
      errmsg = self.readstr1()  
      return success, acceptsMidi, producesMidi, inputBuses, outputBuses, errmsg

    def showpluginui(self, lpindex):
      #success, errmsg
      self.sendcmd(cmd.show_plugin_ui)
      self.sendinfo("I", lpindex)
      self.pipe_handle.flush()
      return self.readinfo1("I"), self.readstr1()

    def schedulemidinote(self, *args):
      #in: index, note, velocity, startTime, duration, channel
      self.sendcmd(cmd.schedule_midi_note)
      self.sendinfo("IIfddI", *args)
      self.pipe_handle.flush()
      
    def schedulemidicc(self, *args):
      #in: lpindex, controller, value, time, channel
      self.sendcmd(cmd.schedule_midi_cc)
      self.senfindo("IIId", *args)
      self.pipe_handle_flush()
    
    def clearmidischedule(self):
      self.sendcmd(cmd.clear_midi_schedule)
      self.pipe_handle_flush()

    def scheduleparamchange(self, *args):
      #in: lpindex, parameterIndex, value, atBlock)
      self.sendcmd(cmd.schedule_param_change)
      self.writeinfo("IIfQ", *args)
      self.pipe_handle_flush()

    def connectaudio(self, sourcelpindex, sourcechan, destlpindex, destchan):
      self.sendcmd(cmd.connect_audio)
      self.sendinfo("IIII", sourcelpindex, sourcechan, destlpindex, destchan)

    def startplayback(self, endblock, tofile, filename):
      self.sendcmd(cmd.start_playback)
      self.writeinfo("II", endblock, tofile)
      self.writestr(filename)

def main():
  if len(sys.argv)>1:
    pipename = sys.argv[1]
    print(f"using pipe name: {pipename}")
    client = JuceAudioClient(sys.argv[1])
  else:
    client = JuceAudioClient()
  if not client.connect():
    print("failed to connect. make sure server is running: juce_gui_server.exe")
    return
  print("connected")
  print("scanning directories")
  plugindirs = defaultDirs + (r"d:\music creation\free vsts", r"(C:\Users\inhah\AppData\Local\Orchestral Tools\SINE Player\Content")
  #plugindirs = (r"d:\music creation\free vsts",)
  jf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "badpaths.json")
  badpaths = json.loads(open(jf).read()) if os.path.exists(jf) else []
  numfound = client.scanplugins(plugindirs, badpaths)
  print(f"numfound: {numfound}")
  print(f"bad paths loaded from file: {len(badpaths)}")
  badpathsfound = client.listbadpaths()
  print(f"bad paths found: {badpathsfound}")
  badpaths += badpathsfound
  badpaths = list(set(badpaths))
  open(jf, "w").write(json.dumps(badpaths))
  print(f"plugins found: {numfound}")
  plugins = client.listplugins()
  for n, p in enumerate(plugins):
    print(f"plugin {n} info:")
    for a in "isInstrument uniqueId numInputChannels numOutputChannels name descriptiveName pluginFormatName category manufacturerName version fileOrIdentifier lastFileModTime path".split():
      print(f"  {a}: {getattr(p, a)}")
  ids = []
  for n in range(numfound): #debug: skipping first plugin #todo: stop skipping this and see if it was the problem
    print(f"loading plugin {n}:")
    success, name, lpindex, uid, errmsg = client.loadpluginbyindex(n)
    print(f"{success=} {name=} {lpindex=} {uid=} {errmsg=}")
    if success:
      ids.append(lpindex)
    else:
      badpaths.append(plugins[n].path)
    print(f"{success=} {name=} {lpindex=} {uid=} {errmsg=}")
  badpaths = list(set(badpaths))
  open(jf, "w").write(json.dumps(badpaths))
  for n in ids[:5]:
    print(f"showing plugin ui lpindex = {n}")
    success, errmsg = client.showpluginui(n)
    print(f"  {success=} {errmsg=}")
    print("  parameters info:")
  for lpindex in ids:
    print(f"  lpindex {lpindex}")
    success, numParams, params, errmsg = client.getParamsInfo(lpindex)
    print(f"  {success=} {numParams=} {errmsg=}")
    for i, p in enumerate(params):
      print(f"    parameter {i}:")
      for a in "name originalIndex minValue maxValue interval skewFactor value numSteps isDiscrete isBoolean isOrientationInverted isAutomatable isMetaParameter".split():
        print(f"      {a}: {getattr(p, a)}")
  print("channels info:")
  for lpindex in ids:
    print(f"  lpindex {lpindex}")
    success, acceptsMidi, producesMidi, inputBuses, outputBuses, errmsg = client.getChannelsInfo(lpindex)
    print(f"{success=} {acceptsMidi=} {producesMidi=}")
    print("    input buses:")
    for n, i in enumerate(inputBuses):
      for a in "numChannels channelTypes isEnabled mainBusLayout".split():
        print(f"      {n}: {a}: {getattr(i, a)}")
    print("    output buses:")
    for n, i in enumerate(outputBuses):
      for a in "numChannels channelTypes isEnabled mainBusLayout".split():
        print(f"      {n}: {a}: {getattr(i, a)}")
  client.pipe_handle.flush()  
  client.disconnect()  
  #time.sleep(1000000)
if __name__ == "__main__":
  main()

