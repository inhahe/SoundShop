#semitone 0 = A4

modes = ["Ionian", "Dorian", "Phrygian", "Lydian", "Mixolydian", "Aeolian", "Locrian"]
modes_dict = dict(zip((mode.lower() for mode in modes), range(7)))
notes_sharp = "A A# B C C# D D# E F F# G G#".split()
notes_flat = "A Bb B C Db D Eb E F Gb G Ab".split()
notes_dict = dict(zip(notes_sharp, range(12)))
notes_dict.update(dict(zip(notes_flat, range(12))))

def get_semitones(notes):
  currentoctave = 4
  r = []
  for n in notes:
    n = n.upper()
    if n.startswith("O"):
      currentoctave = int(n[1:])
    else:
      if n[-1].isdigit():
        note, octave = notes_dict[n[:-1]], int(n[-1])
      else:
        note, octave = notes_dict[n], currentoctave
      r.append((octave-4)*12+note)
  return r

intervals = [2,2,1,2,2,2,1]

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

def get_keys(semis):
  semis_s = set(semis)
  modes = [list() for _ in range(7)]
  for scale_s, scale_l in scales:
    if semis_s.issubset(scale_s):
      for n, semi in enumerate(scale_l):
        modes[n].append(semi)
  return modes

def get_notes(semis, sharps=True):
  notes = []
  if sharps:
    for semi in semis:
      octave, semi2 = divmod(semi, 12)
      notes.append(notes_sharp[semi2]+str(octave))
    return " ".join(notes)
  for semi in semis:
    octave, semi2 = divmod(semi, 12)
    notes.append(notes_flat[semi2]+str(octave))
  return " ".join(notes)

def change_key(semis, key1, mode1, key2, mode2):
  # Build the source scale (key1, mode1)
  semi = key1
  source_scale = []
  for i in intervals[mode1:]+intervals[:mode1]:
    source_scale.append(semi%12)
    semi += i
  
  # Build the target scale (key2, mode2)
  semi = key2
  target_scale = []
  for i in intervals[mode2:]+intervals[:mode2]:
    target_scale.append(semi%12)
    semi += i
  
  # Convert each semitone
  semis2 = []
  for semi in semis:
    semi_mod = semi % 12
    octave_offset = semi // 12 * 12
    
    # Find which scale degree this note is in the source scale
    if semi_mod in source_scale:
      scale_degree = source_scale.index(semi_mod)
      # Map to the same scale degree in the target scale
      new_semi_mod = target_scale[scale_degree]
      semis2.append(octave_offset + new_semi_mod)
    else:
      return None  # Note not in source scale
    
  return semis2

print(get_notes(change_key(get_semitones(("O4", "C", "D", "E", "F", "G", "O5", "A", "B")), 3, 0, 3, 1), False))

  
 














      

