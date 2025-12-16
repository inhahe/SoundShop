from ast import Try, TryStar
from http.client import FOUND
from pickle import GET
from tkinter import FIRST
from webbrowser import get

modes = ["Ionian", "Dorian", "Phrygian", "Lydian", "Mixolydian", "Aeolian", "Locrian"]
modes_dict = dict(zip((mode.lower() for mode in modes), range(7)))

intervals = [2,1,2,2,1,2,2,2]  # Intervals from A to A

#todo: why are they redundant whole notes in semitones_dict entries?
#todo: e.g. key of F should be F3, G3, A3, Bb3, C4, D4, E4 instead of F4, G4, A4, Bb4, C4, D4, E4
#todo: should semitone 0 be A or C? Claude says A, but https://github.com/hmillerbakewell/musical-scales uses C.

notes_dict = {}
semitones_dict = {}
semi = 0
letters = "ABCDEFGA"

extra_scales= {'acoustic': [2, 14, 2, 1, 2, 1, -10], 'aeolian': [2, 13, 2, 2, 1, 2, -10], 'algerian': [2, 13, 3, 1, 1, 3, -11, 2, 13, 2], 'super locrian': [1, 14, 1, 2, 2, 2, -10], 'augmented': [15, 1, 3, 1, 3, -11], 'bebop dominant': [2, 14, 1, 2, 2, 1, 1, -11], 'blues': [15, 2, 1, 1, 3, -10], 'chromatic': [1, 1, 13, 1, 1, 1, 1, 1, 1, 1, 1, -11], 'dorian': [2, 13, 2, 2, 2, 1, -10], 'double harmonic': [1, 15, 1, 2, 1, 3, -11], 'enigmatic': [1, 15, 2, 2, 2, 1, -11], 'flamenco': [1, 15, 1, 2, 1, 3, -11], 'romani': [2, 13, 3, 1, 1, 2, -10], 'half-diminished': [2, 13, 2, 1, 2, 2, -10], 'harmonic major': [2, 14, 1, 2, 1, 3, -11], 'harmonic minor': [2, 13, 2, 2, 1, 3, -11], 'hijaroshi': [16, 2, 1, 4, -11], 'hungarian minor': [2, 13, 3, 1, 1, 3, -11], 'hungarian major': [15, 1, 2, 1, 2, 1, -10], 'in': [1, 16, 2, 1, -8], 'insen': [1, 16, 2, 3, -10], 'ionian': [2, 14, 1, 2, 2, 2, -11], 'iwato': [1, 16, 1, 4, -10], 'locrian': [1, 14, 2, 1, 2, 2, -10], 'lydian augmented': [2, 14, 2, 2, 1, 2, -11], 'lydian': [2, 14, 2, 1, 2, 2, -11], 'locrian major': [2, 14, 1, 1, 2, 2, -10], 'pentatonic major': [2, 14, 3, 2, -9], 'melodic minor ascending': [2, 13, 2, 2, 2, 2, -11], 'melodic minor descending': [2, 13, 2, 2, 2, 2, -11], 'pentatonic minor': [15, 2, 2, 3, -10], 'mixolydian': [2, 14, 1, 2, 2, 1, -10], 'neapolitan major': [1, 14, 2, 2, 2, 2, -11], 'neapolitan minor': [1, 14, 2, 2, 1, 3, -11], 'octatonic c-d': [2, 13, 2, 1, 2, 1, 2, -11], 'octatonic c-c#': [1, 14, 1, 2, 1, 2, 1], 'persian': [1, 15, 1, 1, 2, 3, -11], 'phrygian dominant': [1, 15, 1, 2, 1, 2, -10], 'phrygian': [1, 14, 2, 2, 1, 2, -10], 'prometheus': [2, 14, 2, 3, 1, -10], 'harmonics': [15, 1, 1, 2, 2, -9], 'tritone': [1, 15, 2, 1, 3, -10], 'two-semitone tritone': [1, 1, 16, 1, 1, -8], 'ukranian dorian': [2, 13, 3, 1, 2, 1, -10], 'whole-tone scale': [2, 14, 2, 2, 2, -10], 'yo': [15, 2, 2, 3, -10]}

for note, i in zip(letters, intervals):  
  for a, acc in enumerate(("bb", "b", "", "#", "##")):
    if not note+acc in notes_dict:
      notes_dict[note+acc] = semi+a-2
    semitones_dict.setdefault(semi+a-2, []).append(note+acc) 
  for a, acc in enumerate(("♭♭", "♭", "", "♯", "♯♯")):
    if not note+acc in notes_dict:
      notes_dict[note+acc] = semi+a-2
    semitones_dict.setdefault(semi+a-2, []).append(note+acc)
  semi += i

def get_semitones(notes):
  if type(notes) is str:
    notes = notes.split()
  currentoctave = 4
  r = []
  for n in notes:
    if n.startswith("O"):
      currentoctave = int(n[1:])
    else:
      if n[-1].isdigit():
        note, octave = notes_dict[n[:-1]], int(n[-1])
      else:
        note, octave = notes_dict[n], currentoctave
      r.append((octave-4)*12+note)
  return r

major_intervals = [2, 2, 1, 2, 2, 2, 1]
def build_table(key, mode=0):
  if type(key) is str:
    key = notes_dict[key]
  if type(mode) is str:
    mode = modes_dict[mode.lower()]
  table = []
  semi = key
  for i in major_intervals[mode:]+major_intervals[:mode]:
    table.append(semi)
    semi = (semi+i)%12
  return table

scales = []
for n in range(12):
  cur_semi = n
  scale_s = set()
  scale_l = []
  for i in major_intervals:
    scale_s.add(cur_semi)
    scale_l.append(cur_semi)
    cur_semi = (cur_semi+i)%12
  scales.append((scale_s, scale_l))

def get_keys(semis): #todo: doesn't work for extra_scales because of the %12. most extra scales span more than one octave. how the heck would I do this?
  semis_s = set((semi%12 for semi in semis))
  modes = [list() for _ in range(7)]
  for scale_s, scale_l in scales:
    if semis_s.issubset(scale_s):
      for n, semi in enumerate(scale_l):
        modes[n].append(semi)
  found_scales = []
  min_semis = min(semis)
  max_semis = max(semis)
  semis_min = set(semi-min_semis for semi in semis)
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
      if scale_name == "blues":
        print(f"{intervals=} {get_notes(scale)=} {get_notes(semis_min)=}")
      if semis_min.issubset(scale):
        found_keys.append(key+semis_key) #don't know if key+semis_key is correct, can't think
    if found_keys:
      found_scales.append((scale_name, found_keys))
  return modes, found_scales #todo: return a list of lists of modes just like the major keys and modes (except start each list with the name of the scale)
                             #or change the major scales to return lists of modes it actually found, and maybe even a list of those starting with the name of each scale, to make it consistent

start_dict = {"G": "", "D": "", "A": "", "E": "", "B": "", "F#": "#", "F♯": "♯", "C#": "#", "C♯": "♯", "F": "", "Bb": "b", "B♭": "♭", "Eb": "b", "E♭": "♭", "Ab": "b", "A♭": "♭", "Db": "b", "D♭": "♭", "Gb": "b", "G♭": "♭", "Cb": "b", "C♭": "♭" }
def get_notes(semis, key=None, mode=0, use_unicode=False, accidental=None):
  if accidental and accidental in "♯♭":
    use_unicode = True
  if key is None:
    if not accidental:
      accidental="♯" if use_unicode else "#"
    result = []
    for semi in semis:
      notes = semitones_dict[semi]
      for note in notes:
        if (not note[1:]) or note[1:]==accidental:
          result.append(note)
          break
      else:
        raise ValueError(f"No natural or sharp note found for semitone {semi}")
    return " ".join(result)    
  first_letter = None
  if type(key) is str:
    if key[1:] and key[1:] in "♯♭":
      use_unicode = True
    if accidental is None: 
      accidental = start_dict[key]
      first_letter = key[0]
    key = notes_dict[key]%12    
  table = build_table(key, mode)
  notes = {}
  backup_firs = None
  if not first_letter:
    for note in semitones_dict[key]:
      if accidental is None and note[1:]=="#":
        backup_first = note[0]
      if note[1:]==(accidental or ""): 
        first_letter = note[0]
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
  for semi, letter in zip(table, list(map(chr, range(ord(first_letter), ord("H"))))+list(map(chr, range(ord("A"), ord(first_letter))))):
    for note in semitones_dict[semi]:
      if note[0] == letter:
        if (note[1:] in "♯♭" and use_unicode) or (note[1:] in "#b" and not use_unicode):
          notes[semi] = note
          break
    else:
      raise ValueError(f"No natural, sharp or flat note not found for semitone {semi}")
    semi = (semi+i)%12
  result = []
  semi_mods = [semi%12 for semi in semis]
  for semi_mod in semi_mods:
    octave = (semi // 12) + 4
    try:
      result.append(notes[semi_mod] + str(octave))
    except KeyError:
      raise ValueError(f"No note found for semitone {semi_mod} in key {key}")
  return " ".join(result)

def change_key(semis, key1, mode1, key2, mode2):
  if type(mode1) is str:
    mode1 = modes_dict[mode1.lower()]
  if type(mode2) is str:
    mode2 = modes_dict[mode2.lower()]
  if type(key1) is str:
    key1 = notes_dict[key1]
  if type(key2) is str:
    key2 = notes_dict[key2]
  table1 = build_table(key1, mode1)
  table2 = build_table(key2, mode2)
  semis2 = []
  for semi in semis:
    octave, semi_mod = divmod(semi, 12)
    try: 
      semis2.append(octave*12+table2[table1.index(semi_mod)])
    except ValueError:
      raise ValueError(f"No note found for semitone {semi_mod} in key {key1} mode {modes_dict[mode1]}") 
  return semis2     

def shift_semitones(semitones, x):
  return [semitone+x for semitone in semitones]

def shift_octaves(semitones, octave):
  x = 12*octave
  return [semitone+x for semitone in semitones]

notes = ['B3', 'D4', 'E4', 'F4', 'F#4', 'A4', 'B4']
print(notes)
print(get_semitones(notes))
#print(get_keys(get_semitones(notes))) #todo: doesn't work. should return at least ['blues', [2]] (key of B)

