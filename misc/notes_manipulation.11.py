import re, pprint

use_unicode_accidentals = False

#note_re = re.compile(r"([a-zA-Z])([♯♭#b]*)([\-0-9]*)")
note_re = re.compile(r"([a-zA-Z])(♯|♯♯|♭|♭♭|#|##|b|bb|)(-1|[0-9]|)") #won't detect if an invalid note is given for octave -1 or 9
noteoro_re = re.compile(r"([a-zA-Z](?:♯|♯♯|♭|♭♭|#|##|b|bb|)(?:-1|[0-9]|))|[Oo](-1|[0-9])")

letters = "CDEFGAB"
intervals = [2,2,1,2,2,2,1]  
#start_dict = {'A': '', 'Ab': 'b', 'A♭': '♭', 'B': '', 'Bb': 'b', 'B♭': '♭', 'C#': '#', 'Cb': 'b', 'C♭': '♭', 'C♯': '♯', 'D': '', 'Db': 'b', 'D♭': '♭', 'E': '', 'Eb': 'b', "F#": '#', "F♯": '#', 'E♭': '♭', 'F': '', 'F#': '#', 'F♯': '♯', 'G': '', 'Gb': 'b', 'G♭': '♭'}
start_dict = {'A': '', 'Ab': 'b', 'B': '', 'Bb': 'b', 'C#': '#', 'Cb': 'b', 'D': '', 'D#': '#', 'Db': 'b', 'E': '', 'Eb': 'b', "F#": '#', 'F': '', 'G': '', 'Gb': 'b'}
modes = ["Ionian", "Dorian", "Phrygian", "Lydian", "Mixolydian", "Aeolian", "Locrian"]
modes_dict = dict(zip((mode.lower() for mode in modes), range(7)))
notes_dict = {}
semitones_dict = {}
key_tables = {}

#todo: don't build the ♯'s and ♭'s in the start dict, convert them to #'s and b's before looking them up
#should semitone 0 be A4 or C4? Claude says A, but https://github.com/hmillerbakewell/musical-scales uses C. apparently midi uses A4 = 69. that makes A0 = 21.
#note: some manufacturers label middle C as C3.
#todo: D# is only a theoretical scale. if i have that one, i ought to add other theoretical scales too.
#todo: detect keys not just as semitones but also according to the notes' enharmonic accidentals to return enharmonic keys
#todo: add minor keys
#todo: allow notes to be specified in lowercase

extra_scales= {'acoustic': [2, 14, 2, 1, 2, 1, -10], 'aeolian': [2, 13, 2, 2, 1, 2, -10], 'algerian': [2, 13, 3, 1, 1, 3, -11, 2, 13, 2], 'super locrian': [1, 14, 1, 2, 2, 2, -10], 'augmented': [15, 1, 3, 1, 3, -11], 'bebop dominant': [2, 14, 1, 2, 2, 1, 1, -11], 'blues': [15, 2, 1, 1, 3, -10], 'chromatic': [1, 1, 13, 1, 1, 1, 1, 1, 1, 1, 1, -11], 'dorian': [2, 13, 2, 2, 2, 1, -10], 'double harmonic': [1, 15, 1, 2, 1, 3, -11], 'enigmatic': [1, 15, 2, 2, 2, 1, -11], 'flamenco': [1, 15, 1, 2, 1, 3, -11], 'romani': [2, 13, 3, 1, 1, 2, -10], 'half-diminished': [2, 13, 2, 1, 2, 2, -10], 'harmonic major': [2, 14, 1, 2, 1, 3, -11], 'harmonic minor': [2, 13, 2, 2, 1, 3, -11], 'hijaroshi': [16, 2, 1, 4, -11], 'hungarian minor': [2, 13, 3, 1, 1, 3, -11], 'hungarian major': [15, 1, 2, 1, 2, 1, -10], 'in': [1, 16, 2, 1, -8], 'insen': [1, 16, 2, 3, -10], 'ionian': [2, 14, 1, 2, 2, 2, -11], 'iwato': [1, 16, 1, 4, -10], 'locrian': [1, 14, 2, 1, 2, 2, -10], 'lydian augmented': [2, 14, 2, 2, 1, 2, -11], 'lydian': [2, 14, 2, 1, 2, 2, -11], 'locrian major': [2, 14, 1, 1, 2, 2, -10], 'pentatonic major': [2, 14, 3, 2, -9], 'melodic minor ascending': [2, 13, 2, 2, 2, 2, -11], 'melodic minor descending': [2, 13, 2, 2, 2, 2, -11], 'pentatonic minor': [15, 2, 2, 3, -10], 'mixolydian': [2, 14, 1, 2, 2, 1, -10], 'neapolitan major': [1, 14, 2, 2, 2, 2, -11], 'neapolitan minor': [1, 14, 2, 2, 1, 3, -11], 'octatonic c-d': [2, 13, 2, 1, 2, 1, 2, -11], 'octatonic c-c#': [1, 14, 1, 2, 1, 2, 1], 'persian': [1, 15, 1, 1, 2, 3, -11], 'phrygian dominant': [1, 15, 1, 2, 1, 2, -10], 'phrygian': [1, 14, 2, 2, 1, 2, -10], 'prometheus': [2, 14, 2, 3, 1, -10], 'harmonics': [15, 1, 1, 2, 2, -9], 'tritone': [1, 15, 2, 1, 3, -10], 'two-semitone tritone': [1, 1, 16, 1, 1, -8], 'ukranian dorian': [2, 13, 3, 1, 2, 1, -10], 'whole-tone scale': [2, 14, 2, 2, 2, -10], 'yo': [15, 2, 2, 3, -10]}

def convert_accidental(accidental, use_unicode=use_unicode_accidentals):
  if accidental is None:
    return accidental #should we return '' instead?
  if type(accidental) is Note:
    if use_unicode:
      accidental.accidental = accidental.accidental.replace("#", "♯").replace("b", "♭") 
    else:
      accidental.accidental = accidental.accidental.replace("♯", "#").replace("♭", "b") 
    return accidental
  if use_unicode:
    return accidental.replace("#", "♯").replace("b", "♭") 
  else:
    return accidental.replace("♯", "#").replace("♭", "b") 

class Note: #todo: make properties to change things. for example, if you change degree or key, then note, letter, accidental, pitch class and midi change too. we could cheat and use Note(self, degree=new_degree)
            #maybe we should allow each note property to be passed as a parameter for this. 
  def __init__(self, note=None, accidental=None, octave=None, key=None, mode=0, degree=None, duration=1, sampleNumber=None, sampleInterval=None, beat=None, beatInterval=None, time=None, timeInterval=None, playOrder=None):
    assert sum(x is not None for x in (sampleNumber, sampleInterval, beat, beatInterval, time, timeInterval)) <= 1
    self.sampleNumber = sampleNumber
    self.playOrder = playOrder
    self.octave = octave
    accidental = convert_accidental(accidental)
    self.accidental = accidental
    if type(key) is str:
      key = Note(key).pitch_class
    elif type(key) is Note:
      key = key.pitch_class
    if type(note) is str:
      self.letter, self.accidental, octave2 = note_re.match(note).group(1,2,3)
      if octave is not None:
        self.octave = octave
      else:
        if octave2:
          self.octave = int(octave2)
        else:
          self.octave = 4
    if type(note) is Note:
      self.octave = note.octave if octave is None else octave
      self.midi = note.midi
      self.letter = note.letter
      self.key = key if key else note.key
      self.accidental = note.accidental
    elif type(note) is int:
      if octave is None:
        self.midi = note
      else:
        self.midi = note%12+octave*12
    if self.octave is None:
      self.octave = 4 if note.letter<"C" else 5
    else:
      if self.octave is None:
        if octave is None:
          self.octave = 4 if self.letter < "C" else 5
        else:
          self.octave = octave
    if note is None:
      if key is None or degree is None:
        raise ValueError("Can't infer note because key and/or degree isn't set")
      self.midi = key_tables[convert_accidental(key, False)][mode][degree]+self.octave*4 #todo:octave needs to increase at C
      self.letter = Note(self.midi, key=key, mode=mode).letter
    self.note = self.letter+self.accidental+str(self.octave)
    self.midi = notes_dict[self.note]
    self.key = key
    self.pitch_class = self.letter+self.accidental 
    if type(mode) is str:
      self.mode = modes_dict[mode]
    else:
      self.mode = mode
    if key is not None:
      self.degree = build_table(key, mode).index(self.midi%12) #todo: maybe we should compute these tables beforehand?
      table = key_tables[convert_accidental(self.key, False)][self.mode]
      if self.midi%12 not in table:
        raise ValueError(f"Semitone {self.midi%12} not in scale {self.key} {modes[self.mode]}")
      self.note = table[self.midi%12].pitch_class+str(self.octave)
    else:
      self.degree = degree

  def __str__(self):
    return self.note

  def __repr__(self):
    return self.note

class Notes(list): #todo: __repr__ and __str__
  def __init__(self, notes=None, key=None, bpm=None, mode=0, accidental=None, sampleOffset=None, beatOffset=None, timeOffset=None, sampleInterval=None, beatInterval=None, timeInterval=None, orderOffset = 0):
    assert (beatOffset is not None) + (timeOffset is not None) + (sampleOffset is not None) <= 1 and (sampleInterval is not None) + (beatInterval is not None) + (timeInterval is not None) <= 1
    if type(notes) is str:
      notes2 = []
      octave = None
      for m in noteoro_re.findall(notes):
        if m[0]:
          notes2.append(Note(m[0], octave=octave, key=key, bpm=bpm, accidental=accidental, mode=mode))
        else:
          octave = int(m[1])
    elif type(notes) in (Notes, list):
      for note in notes:
        if note.playOrder is not None:
          note.playOrder += orderOffset
        if note.sampleNumber:
          note.sampleNumber += sampleOffset
        note.key = key
        note.mode = mode
        note.accidental = accidental
        if note.sampleNumber:
          note.sampleNumber += sampleOffset
        if note.playOrder:
          note.playOrder += orderOffset
        notes2.append(note)
      super().init(notes2)
    self.key = key
    self.mode = mode
    self.bpm = bpm
    self.accidental = accidental
    self.beatOffset = beatOffset
    self.timeOffset = timeOffset
    self.sampleOffset = sampleOffset
    self.sampleInterval = sampleInterval
    self.beatInterval = beatInterval
    self.timeInterval = self.timeInterval

def merge_notes(*notess):
  return sorted(sum([], (Notes(x) for x in notess)), lambda y: (y.sampleNumber, y.playOrder))

def make_tables():
  semi = -2
  i=-2
  while semi <  129:
    interval = intervals[i%7]
    stro = str((semi-12)//12)
    letter = letters[i%7]
    for n, acc in enumerate(("bb", "b", "", "#", "##")):
      notes_dict[letter+acc+stro] = semi+n-2
      semitones_dict.setdefault(semi+n-2, []).append(letter+acc+stro)
    for n, acc in enumerate(("♭♭", "♭", "", "♯", "♯♯")):
      notes_dict[letter+acc+stro] = semi+n-2
    semi += interval
    i += 1 

  semi = 0
  for interval, letter in zip(intervals, letters):
    for n, acc in enumerate(("bb", "b", "", "#", "##")):
      notes_dict[letter+acc] = semi+n-2
    for n, acc in enumerate(("♭♭", "♭", "", "♯", "♯♯")):
      notes_dict[letter+acc] = semi+n-2
    semi += interval
    i += 1

  for key, value in semitones_dict.items():
    semitones_dict[key] = [Note(n) for n in value]

  for key in start_dict:
    mode_tables = []
    for mode in range(7):
      mode_tables.append(build_notes(key, mode, start_dict[key]))
    key_tables[key]=mode_tables
    
def build_table(key, mode=0):
  key = Note(key)
  if type(mode) is str:
    mode = modes_dict[mode.lower()]
  table = []
  semi = key.midi%12
  for i in intervals[mode:]+intervals[:mode]:
    table.append(semi)
    semi = (semi+i)%12
  return table

scales = []
for n in range(12):
  cur_semi = n
  scale_s = set()
  scale_l = []
  for i in intervals:
    scale_s.add(cur_semi)
    scale_l.append(cur_semi)
    cur_semi = (cur_semi+i)%12
  scales.append((scale_s, scale_l))

def get_keys(notes):
  if type(notes) is str:
    notes = notes.split()
  semis_s = set((Note(note).midi%12 for note in notes))
  modes = [list() for _ in range(7)]
  for scale_s, scale_l in scales:
    if semis_s.issubset(scale_s):
      for n, semi in enumerate(scale_l):
        modes[n].append(semi)
  found_scales = []
  min_semis = min(semis_s)
  max_semis = max(semis_s)
  semis_min = set(semi-min_semis for semi in semis_s)
  semis_key = min_semis%12
  max_semis -= min_semis
  for scale_name, intervals in extra_scales.items():
    found_keys = []
    for key in range(len(intervals)): 
      scale = []
      note = key
      for i in range(max(len(semis_min), len(scale))):
        interval = intervals[i%len(intervals)]
        scale.append(note)
        note += i
      if semis_min.issubset(scale):
        found_keys.append(key+semis_key) #don't know if key+semis_key is correct, can't think
    if found_keys:
      found_scales.append((scale_name, found_keys))
  return modes, found_scales #todo: return a list of lists of modes just like the major keys and modes (except start each list with the name of the scale)
                             #or change the major scales to return lists of modes it actually found, and maybe even a list of those starting with the name of each scale, to make it consistent

def build_notes(key, mode, accidental):
  notes = {}
  first_letter = None
  key = Note(key)
  first_letter = key.letter
  table = build_table(key, mode)
  backup_first = None
  if not first_letter:
    for note in semitones_dict[key.midi]:
      if accidental is None and note.accidental=="#":
        backup_first = note[0]
      if note.accidental==(accidental or ""): 
        first_letter = note.letter
        break
    else:
      if accidental is None:
        if backup_first == None:
          raise ValueError(f"No whole or sharp note found for semitone {key}")
        else:
          first_letter = backup_first
      else:
        if accidental:
          raise ValueError(f"No {accidental} note found for semitone {key}")
        else:
          raise ValueError(f"No whole note found for semitone {key}")
  lifl = letters.index(first_letter)
  for semi, letter in zip(table, letters[lifl:]+letters[:lifl]):
    for note in semitones_dict[semi]:
      if note.letter == letter:
        notes[semi] = note
        break
    else:
      raise ValueError(f"No note found for semitone {semi} letter {letter} key {key.letter+key.accidental} mode {mode}")
    semi = (semi+i)%12
  return notes

def get_notes(notes, key=None, mode=0, accidental=None):
  if type(notes) is str:
    notes = notes.split()
  return [Note(note, key=key, mode=mode, accidental=accidental) for note in notes]

def change_key(notes, key1=None, mode1=None, key2=None, mode2=None):
  if type(notes) is str:
    notes = notes.split()
  if type(mode1) is str:
    mode1 = modes_dict[mode1.lower()]
  if type(mode2) is str:
    mode2 = modes_dict[mode2.lower()]
  if type(key1) is Note:
    key1 = key1.letter+key1.accidental
  if type(key2) is str:
    key2 = key2.letter+key2.accidental
  semis2 = []
  if key1 is not None:
    table1 = key_tables[key1][mode1]
    table2 = key_tables[key2][mode2]
    for note in notes:
      if type(note) is Note:
        semi = note.midi
      elif type(note) is str:
        semi = semitones_dict[note]
      elif type(note) is int:
        semi = note
      octave, semi_mod = divmod(semi, 12)
      try: 
        semis2.append(octave*12+table2[table1.index(semi_mod)])
      except ValueError:
        raise ValueError(f"No note found for semitone {semi_mod} in key {key1} mode {modes_dict[mode1]}") 
  else:
    for note in notes:
      if note.key is None:
        raise ValueError(f"Note {note} doesn't have a key and no key specified so can't infer degree")
      
  return semis2     

def shift_semitones(notes, x):
  results = []
  if type(notes) is str:
    notes = notes.split()
  for note in notes:
    if type(note) is str:
      semi = notes_dict[str]
    elif type(note) is Note:
      semi = note.midi
    elif type(note) is int:
      semi = note
  results.append(semi+x)
  
def shift_octaves(notes, octave):
  x = 12*octave
  return shift_semitones(notes, octave*12)

make_tables()

notes = "C#5 D#5 E#5 G5 G#5 A#5 B#5"
print(notes)
a = get_notes(notes, key="C♯", mode="lydian")
print(a)

