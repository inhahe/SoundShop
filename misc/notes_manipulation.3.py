#semitone 0 = A4

modes = ["Ionian", "Dorian", "Phrygian", "Lydian", "Mixolydian", "Aeolian", "Locrian"]
modes_dict = dict(zip((mode.lower() for mode in modes), range(7)))

intervals = [2,1,2,2,1,2,2]
notes_dict = {}
semi = 0
letters = "ABCDEFG"
for note, i in zip(letters, intervals):
  for a, acc in enumerate(("bb", "b", "", "#", "##")):
    notes_dict[note+acc] = semi+a-2
  for a, acc in enumerate(("♭♭", "♭", "", "♯", "♯♯")):
    notes_dict[note+acc] = semi+a-2
  semi += i
semitones_dict = {}

for key, value in notes_dict.items():
  semitones_dict.setdefault(value, []).append(key)

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

def get_notes(semis, key="C"):
  if len(key)==1:
    accidental = "#"
  else:
    accidental = key[-1]
  semi = notes_dict[key]
  notes = {}
  l_ind = letters.index(key[0])
  letters2 = letters[l_ind:]+letters[:l_ind]
  for i, letter in zip(intervals, letters2):
    for note in semitones_dict[semi%12]:
      if len(note)==1 or note[1:]==accidental:
        notes[semi] = note
    semi += i
  return " ".join(notes[semi] for semi in semis)

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

print(change_key(get_semitones(("O4", "C", "D", "E", "F", "G", "O5", "A", "B")), 3, 0, 3, 1))
print(get_notes(change_key(get_semitones(("O4", "C", "D", "E", "F", "G", "O5", "A", "B")), 3, 0, 3, 1), "C"))
 














      


